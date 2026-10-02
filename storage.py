"""Atomic state (grades and outbox together) and a process-wide run lock."""
from contextlib import contextmanager
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import tempfile

import pandas as pd

from grade_state import prepare_grades


class StateError(ValueError):
    pass


class AlreadyRunning(Exception):
    pass


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        if os.name == "posix":
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


@contextmanager
def run_lock(path: Path):
    """OS locks are released on exit/crash; never delete the lock inode."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    with os.fdopen(descriptor, "r+b") as handle:
        if os.name == "nt":
            import msvcrt

            if os.fstat(handle.fileno()).st_size == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise AlreadyRunning from exc
        else:
            import fcntl

            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise AlreadyRunning from exc
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


class StateStore:
    def __init__(self, data_dir: Path, user: str, semester_id: str):
        self.scope = {"user": user, "semester_id": semester_id}
        scope_id = sha256(json.dumps(self.scope, sort_keys=True).encode()).hexdigest()[:20]
        self.directory = data_dir / scope_id
        self.path = self.directory / "state.json"
        self.csv_path = self.directory / "grades.csv"
        self.lock_path = self.directory / "notifier.lock"

    def load(self):
        if not self.path.exists():
            return None
        try:
            document = json.loads(self.path.read_text(encoding="utf-8"))
            if type(document["version"]) is not int or document["version"] != 1 or document["scope"] != self.scope:
                raise ValueError
            columns, records, pending = (
                document["columns"], document["grades"], document["pending"]
            )
            if not isinstance(columns, list) or not columns or len(set(columns)) != len(columns):
                raise ValueError
            if not all(isinstance(column, str) for column in columns):
                raise ValueError
            if not isinstance(records, list) or not isinstance(pending, list):
                raise ValueError
            for record in records:
                if not isinstance(record, dict) or set(record) != set(columns):
                    raise ValueError
                if not all(isinstance(value, str) for value in record.values()):
                    raise ValueError
            ids = set()
            for event in pending:
                if (not isinstance(event, dict) or event["type"] not in {"new", "changed"}
                        or not isinstance(event["name"], str) or not event["name"]
                        or not isinstance(event["id"], str) or not event["id"]
                        or event["id"] in ids):
                    raise ValueError
                ids.add(event["id"])
                if type(event["attempts"]) is not int or event["attempts"] < 0:
                    raise ValueError
                due = event["next_attempt_at"]
                if type(due) not in (int, float) or not math.isfinite(due) or due < 0:
                    raise ValueError
            grades = pd.DataFrame(records, columns=columns)
            prepare_grades(grades)
            return grades, pending
        except (ValueError, KeyError, TypeError) as exc:
            raise StateError("Zustandsdatei ungültig; sie wurde nicht überschrieben.") from exc

    def save(self, grades: pd.DataFrame, pending: list[dict]) -> None:
        document = {
            "version": 1, "scope": self.scope, "columns": list(grades.columns),
            "grades": grades.fillna("").to_dict("records"), "pending": pending,
        }
        atomic_write(self.path, json.dumps(document, ensure_ascii=False, allow_nan=False) + "\n")

    def export_csv(self, grades: pd.DataFrame) -> None:
        atomic_write(self.csv_path, grades.to_csv(index=False))
