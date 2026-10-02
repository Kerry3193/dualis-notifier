"""Validate configuration before touching the network or stored grades."""
import math
import os
from pathlib import Path
import re
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from dotenv import load_dotenv

PROJECT_DIR = Path(__file__).resolve().parent


class ConfigError(ValueError):
    pass


def _positive_number(env, key, default):
    try:
        value = float(env.get(key, default))
    except (ValueError, TypeError) as exc:
        raise ConfigError(f"{key} muss eine positive Zahl sein.") from exc
    if not math.isfinite(value) or value <= 0:
        raise ConfigError(f"{key} muss eine positive Zahl sein.")
    return value


def _webhook_url(value):
    try:
        url = urlsplit(value)
        valid = (
            url.scheme == "https" and url.hostname in {"discord.com", "discordapp.com"}
            and url.port in (None, 443) and not url.username and not url.password
            and not url.fragment
            and re.fullmatch(r"/api(?:/v\d+)?/webhooks/\d+/[A-Za-z0-9._-]+", url.path)
        )
        query = parse_qs(url.query, keep_blank_values=True)
        if not valid or set(query) - {"thread_id", "wait"}:
            raise ValueError
        if "thread_id" in query and (len(query["thread_id"]) != 1 or not query["thread_id"][0].isdigit()):
            raise ValueError
        # The sender always asks for confirmation; remove conflicting wait parameters.
        query.pop("wait", None)
        return urlunsplit((url.scheme, url.netloc, url.path, urlencode(query, doseq=True), ""))
    except (ValueError, TypeError) as exc:
        raise ConfigError("DISCORD_WEBHOOK muss eine gültige Discord-HTTPS-Webhook-URL sein.") from exc


def load_config(env=None):
    if env is None:
        load_dotenv(PROJECT_DIR / ".env", override=False, interpolate=False)
        env = os.environ
    for key in ("DUALIS_USER", "DUALIS_PASSWD", "DISCORD_WEBHOOK"):
        if not env.get(key) or not env[key].strip():
            raise ConfigError(f"{key} fehlt. Bitte .env beziehungsweise Umgebung prüfen.")
    user = env["DUALIS_USER"].strip()
    if not re.fullmatch(r"[A-Za-z0-9._-]+", user):
        raise ConfigError("DUALIS_USER muss die Kennung ohne E-Mail-Domain enthalten.")
    semester = env.get("SEMESTER_ID", "").strip()
    if semester and not re.fullmatch(r"-N\d+", semester):
        raise ConfigError("SEMESTER_ID muss leer sein oder das Format -N123456 haben.")
    agent = env.get("AGENT_NAME", "Dualis Notifier").strip() or "Dualis Notifier"
    if any(ord(character) < 32 or ord(character) > 255 for character in agent):
        raise ConfigError("AGENT_NAME enthält ungültige HTTP-Header-Zeichen.")
    data_dir = Path(env.get("DATA_DIR", "data")).expanduser()
    if not data_dir.is_absolute():
        data_dir = PROJECT_DIR / data_dir
    return {
        "user": user, "passwd": env["DUALIS_PASSWD"], "semester_id": semester,
        "hook_url": _webhook_url(env["DISCORD_WEBHOOK"].strip()), "user_agent": agent,
        "data_dir": data_dir.resolve(),
        "timeout": (_positive_number(env, "CONNECT_TIMEOUT", 5),
                    _positive_number(env, "READ_TIMEOUT", 30)),
        "interval": _positive_number(env, "CHECK_INTERVAL", 900),
    }
