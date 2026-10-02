"""Run one check for cron, or watch continuously for Docker."""
import argparse
from datetime import datetime, timezone
import logging
from pathlib import Path
import signal
import threading
from uuid import uuid4

from config import ConfigError, load_config
from dualis_client import DualisClient, DualisError
from grade_state import (
    LAST_SEEN_COLUMN, MISSING_SINCE_COLUMN, extract_data_from_html,
    load_cache, prepare_grades, reconcile_grades,
)
from notifications import deliver_pending
from storage import AlreadyRunning, StateError, StateStore, run_lock

logger = logging.getLogger(__name__)


def run_once(config, *, import_cache=None) -> bool:
    store = StateStore(config["data_dir"], config["user"], config["semester_id"])
    try:
        with run_lock(store.lock_path):
            loaded = store.load()
            if import_cache is not None:
                if loaded is not None:
                    raise StateError("Import abgelehnt: Für dieses Konto/Semester existiert bereits ein Zustand.")
                grades = prepare_grades(load_cache(Path(import_cache)))
                store.save(grades, [])
                loaded = (grades, [])
                logger.info("Bisheriger CSV-Cache importiert.")
            delivery_ok = True
            if loaded is not None:
                grades, pending = loaded
                # Retry even if the subsequent Dualis check fails.
                delivery_ok = deliver_pending(store, grades, pending, config["hook_url"], config["timeout"])
            with DualisClient(config["user_agent"], config["timeout"]) as client:
                client.login(config["user"], config["passwd"])
                current = extract_data_from_html(client.get_grades(config["semester_id"]))
            observed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            if loaded is None:
                grades = current.copy()
                grades[LAST_SEEN_COLUMN] = observed_at
                grades[MISSING_SINCE_COLUMN] = ""
                pending = []
                events = []
                logger.info("Ausgangsstand angelegt; beim ersten Lauf wird nichts versendet.")
            else:
                grades, events = reconcile_grades(grades, current, observed_at)
                pending.extend({**event, "id": str(uuid4()), "attempts": 0, "next_attempt_at": 0}
                               for event in events)
            # Grades and pending events commit in one atomic file replacement.
            store.save(grades, pending)
            store.export_csv(grades)
            if delivery_ok:
                delivery_ok = deliver_pending(store, grades, pending, config["hook_url"], config["timeout"])
            logger.info("Prüfung abgeschlossen: %d Änderungen, %d ausstehend. CSV: %s",
                        len(events), len(pending), store.csv_path)
            return delivery_ok
    except AlreadyRunning:
        logger.info("Prüfung übersprungen: Ein Lauf für dieses Konto/Semester ist bereits aktiv.")
        return True


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Dualis-Noten prüfen und Discord benachrichtigen.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--watch", action="store_true", help="Fortlaufend prüfen; erster Lauf sofort.")
    parser.add_argument("--import-cache", type=Path, help="Bisherigen CSV-Cache einmalig übernehmen.")
    mode.add_argument("--status", action="store_true", help="Datenpfad und ausstehende Nachrichten anzeigen.")
    args = parser.parse_args(argv)
    if args.import_cache is not None and (args.watch or args.status):
        parser.error("--import-cache muss als einzelner Lauf ohne --watch/--status ausgeführt werden.")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        config = load_config()
    except ConfigError as exc:
        logger.error("%s", exc)
        return 2
    if args.status:
        store = StateStore(config["data_dir"], config["user"], config["semester_id"])
        try:
            loaded = store.load()
        except (StateError, OSError) as exc:
            logger.error("Zustand konnte nicht gelesen werden (%s).", type(exc).__name__)
            return 1
        print(f"CSV: {store.csv_path}\nZustand: {store.path}\n"
              f"Ausstehend: {len(loaded[1]) if loaded else 0}")
        return 0
    stopped = threading.Event()
    if args.watch:
        for signum in (signal.SIGTERM, signal.SIGINT):
            signal.signal(signum, lambda *_: stopped.set())
    import_cache = args.import_cache
    while not stopped.is_set():
        try:
            ok = run_once(config, import_cache=import_cache)
        except (DualisError, StateError, ValueError) as exc:
            logger.error("%s", exc)
            ok = False
        except OSError:
            logger.error("Daten konnten nicht gelesen/gespeichert werden; Rechte und freien Speicher prüfen.")
            ok = False
        if not args.watch:
            return 0 if ok else 1
        stopped.wait(config["interval"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
