"""Discord transport and durable outbox acknowledgements."""
import logging
import math
import time

import requests

logger = logging.getLogger(__name__)


class DeliveryError(Exception):
    def __init__(self, message: str, retry_after: float = 0):
        super().__init__(message)
        self.retry_after = retry_after


def _retry_after(response) -> float:
    value = response.headers.get("Retry-After", 0)
    try:
        value = response.json().get("retry_after", value)
    except (ValueError, AttributeError):
        pass
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return 0
    return seconds if math.isfinite(seconds) and seconds >= 0 else 0


def notify_server(name: str, hook_url: str, event_type: str,
                  timeout: tuple[float, float] = (5, 30)) -> None:
    title = ("Note geändert: " if event_type == "changed" else "Neue Note: ") + name
    description = (
        f"Für {name} " + ("wurde eine Note geändert." if event_type == "changed"
                           else "wurden neue Ergebnisse veröffentlicht.")
        + "\nGehe zu https://dualis.dhbw.de/, um den aktuellen Stand einzusehen."
    )
    payload = {"username": "DUALIS", "allowed_mentions": {"parse": []}, "embeds": [
        {"title": title[:256], "description": description[:4096], "url": "https://dualis.dhbw.de"}
    ]}
    try:
        response = requests.post(hook_url, params={"wait": "true"}, json=payload,
                                 timeout=timeout, allow_redirects=False)
    except requests.RequestException as exc:
        # Request exception strings may include the secret webhook URL.
        raise DeliveryError("Discord ist derzeit nicht erreichbar.") from exc
    with response:
        if response.status_code != 200:
            raise DeliveryError(f"Discord-Versand fehlgeschlagen (HTTP {response.status_code}).",
                                _retry_after(response) if response.status_code == 429 else 0)
        try:
            message = response.json()
        except ValueError as exc:
            raise DeliveryError("Discord hat den Versand nicht bestätigt.") from exc
        if not isinstance(message, dict) or not message.get("id"):
            raise DeliveryError("Discord hat den Versand nicht bestätigt.")


def deliver_pending(store, grades, pending, hook_url, timeout=(5, 30), *, now=None) -> bool:
    """At most one attempt per event/run. Stop a failed batch without losing it."""
    clock = time.time if now is None else lambda: now
    while pending:
        event = pending[0]
        if event["next_attempt_at"] > clock():
            return False
        try:
            notify_server(event["name"], hook_url, event["type"], timeout)
        except DeliveryError as exc:
            event["attempts"] += 1
            delay = max(min(60 * 2 ** min(event["attempts"] - 1, 6), 3600), exc.retry_after)
            event["next_attempt_at"] = clock() + delay
            store.save(grades, pending)
            logger.warning("%s Nachricht bleibt gespeichert; %d ausstehend.", exc, len(pending))
            return False
        remaining = pending[1:]
        store.save(grades, remaining)
        pending[:] = remaining
        logger.info("Discord-Nachricht bestätigt; %d ausstehend.", len(pending))
    return True
