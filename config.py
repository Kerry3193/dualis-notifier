import os
from pathlib import Path

from dotenv import load_dotenv


# Load local credentials when the notifier is run without Docker. Environment
# variables supplied by Docker, systemd, or cron retain precedence.
load_dotenv(Path(__file__).with_name(".env"))


def load_config():
    user = os.getenv("DUALIS_USER")
    passwd = os.getenv("DUALIS_PASSWD")
    semester_id = os.getenv("SEMESTER_ID")
    hook_url = os.getenv("DISCORD_WEBHOOK")
    user_agent = os.getenv("AGENT_NAME", "Dualis Notifier")

    return {
        "user": user,
        "passwd": passwd,
        "semester_id": semester_id,
        "hook_url": hook_url,
        "user_agent": user_agent,
    }
