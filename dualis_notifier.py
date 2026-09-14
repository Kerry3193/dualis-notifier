from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
import os
import re

import pandas as pd
import requests

from config import load_config


config = load_config()
CACHE_FILE = Path("grades.csv")
LAST_SEEN_COLUMN = "_notifier_last_seen"
MISSING_SINCE_COLUMN = "_notifier_missing_since"
STATE_COLUMNS = {LAST_SEEN_COLUMN, MISSING_SINCE_COLUMN}
IDENTIFIER_COLUMNS = ("Nr.", "Name")


def get_session(user, passwd) -> dict:
    url = "https://dualis.dhbw.de/scripts/mgrqispi.dll"

    payload = f"usrname={user}%40student.dhbw-mannheim.de&pass={passwd}&APPNAME=CampusNet&PRGNAME=LOGINCHECK&ARGUMENTS=clino%2Cusrname%2Cpass%2Cmenuno%2Cmenu_type%2Cbrowser%2Cplatform&clino=000000000000001&menuno=000324&menu_type=classic&browser=&platform="
    headers = {
        "User-Agent": config["user_agent"],
        "Content-Type": "application/x-www-form-urlencoded",
    }

    response = requests.request("POST", url, headers=headers, data=payload)

    regex_pattern_session = r"ARGUMENTS=(.*?),"

    return {
        "cookie": response.headers["Set-Cookie"].split(";")[0].replace(" ", ""),
        "session": re.search(regex_pattern_session, response.headers["REFRESH"]).group(
            1
        ),
    }


def get_grades(cookie, session, semester_id) -> str:
    url = f"https://dualis.dhbw.de/scripts/mgrqispi.dll?APPNAME=CampusNet&PRGNAME=COURSERESULTS&ARGUMENTS={session},-N000307,{semester_id}"

    headers = {
        "User-Agent": config["user_agent"],
        "Cookie": f"{cookie}; {cookie}",
    }

    response = requests.get(url, headers=headers)

    with open("grades.html", "w") as f:
        f.write(response.text)

    return response.text


def extract_data_from_html(raw_html) -> pd.DataFrame:
    tables = pd.read_html(StringIO(raw_html))
    df = tables[0]
    df.drop(
        df.columns[df.columns.str.contains("unnamed", case=False)], axis=1, inplace=True
    )
    return df


def normalise_value(value) -> str:
    """Create stable CSV values independent of pandas' inferred data types."""
    if pd.isna(value):
        return ""
    if isinstance(value, float):
        return format(value, "g")
    return str(value).strip()


def prepare_grades(table: pd.DataFrame) -> pd.DataFrame:
    """Remove summary rows and normalise every grade value before comparison."""
    grades = table.copy()
    if "Name" in grades.columns:
        grades = grades[grades["Name"].map(normalise_value) != "Semester-GPA"]

    return grades.map(normalise_value).reset_index(drop=True)


def identifier_column(grades: pd.DataFrame) -> str:
    """Prefer the Dualis module number and only fall back to a unique module name."""
    for column in IDENTIFIER_COLUMNS:
        if column not in grades.columns:
            continue
        identifiers = grades[column]
        if identifiers.ne("").all() and identifiers.is_unique:
            return column

    raise ValueError(
        "Keine eindeutige Modulkennung gefunden. Erwartet wird eine eindeutige Spalte "
        "'Nr.' oder 'Name'."
    )


def load_cache(cache_file: Path) -> pd.DataFrame:
    """Load both legacy caches and the current stateful cache format."""
    cache = pd.read_csv(cache_file, dtype=str, keep_default_na=False)
    return cache.loc[:, ~cache.columns.str.contains("^Unnamed")]


def reconcile_grades(
    previous: pd.DataFrame, current: pd.DataFrame, observed_at: str
) -> tuple[pd.DataFrame, list[dict[str, str]]]:
    """Merge the latest Dualis response into state and return meaningful events.

    Missing modules stay in the cache and only receive a missing timestamp. If they
    reappear unchanged, no event is created. New modules and real value changes are
    returned as notification events.
    """
    current = prepare_grades(current)
    key_column = identifier_column(current)
    previous = previous.copy().fillna("")

    if key_column not in previous.columns:
        previous = pd.DataFrame(columns=[*current.columns, *STATE_COLUMNS])
    else:
        previous = previous.loc[previous[key_column].ne("")].copy()
        if not previous[key_column].is_unique:
            raise ValueError("Der Noten-Cache enthält doppelte Modulkennungen.")

    value_columns = list(dict.fromkeys([*current.columns, *previous.columns]))
    value_columns = [column for column in value_columns if column not in STATE_COLUMNS]

    old_records = previous.set_index(key_column, drop=False).to_dict("index")
    current_records = current.set_index(key_column, drop=False).to_dict("index")
    events: list[dict[str, str]] = []
    state_records: list[dict[str, str]] = []

    for module_id, current_record in current_records.items():
        old_record = old_records.pop(module_id, None)
        record = {column: current_record.get(column, "") for column in value_columns}
        record[LAST_SEEN_COLUMN] = observed_at
        record[MISSING_SINCE_COLUMN] = ""

        if old_record is None:
            events.append({"type": "new", "name": record.get("Name", module_id)})
        else:
            changed = any(
                normalise_value(old_record.get(column, ""))
                != normalise_value(current_record.get(column, ""))
                for column in value_columns
            )
            if changed:
                events.append(
                    {"type": "changed", "name": record.get("Name", module_id)}
                )

        state_records.append(record)

    for old_record in old_records.values():
        record = {column: normalise_value(old_record.get(column, "")) for column in value_columns}
        record[LAST_SEEN_COLUMN] = normalise_value(old_record.get(LAST_SEEN_COLUMN, ""))
        record[MISSING_SINCE_COLUMN] = (
            normalise_value(old_record.get(MISSING_SINCE_COLUMN, "")) or observed_at
        )
        state_records.append(record)

    state_columns = [*value_columns, LAST_SEEN_COLUMN, MISSING_SINCE_COLUMN]
    state = pd.DataFrame(state_records, columns=state_columns)
    return state, events


def notify_server(name: str, hook_url: str, event_type: str) -> None:
    if event_type == "changed":
        title = f"Note geändert: {name}"
        description = (
            f"Für {name} wurde eine Note geändert.\n"
            "Gehe zu https://dualis.dhbw.de/, um den aktuellen Stand einzusehen."
        )
    else:
        title = f"Neue Note: {name}"
        description = (
            f"Für {name} wurden neue Ergebnisse veröffentlicht.\n"
            "Gehe zu https://dualis.dhbw.de/, um deine Note einzusehen."
        )

    data = {
        "username": "DUALIS",
        "embeds": [{"description": description, "title": title, "url": "https://dualis.dhbw.de"}],
    }
    requests.post(hook_url, json=data)


def main() -> None:
    creds = get_session(config["user"], config["passwd"])
    raw_html = get_grades(
        cookie=creds["cookie"],
        session=creds["session"],
        semester_id=config["semester_id"],
    )
    current = extract_data_from_html(raw_html)
    observed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    if not CACHE_FILE.exists():
        baseline = prepare_grades(current)
        key_column = identifier_column(baseline)
        baseline[LAST_SEEN_COLUMN] = observed_at
        baseline[MISSING_SINCE_COLUMN] = ""
        baseline.to_csv(CACHE_FILE, index=False)
        print("W: No cache found")
        print("I: Created cache")
        return

    state, events = reconcile_grades(load_cache(CACHE_FILE), current, observed_at)
    state.to_csv(CACHE_FILE, index=False)

    if not events:
        print("I: No changes")
        return

    for event in events:
        print(f"I: {event['type'].capitalize()} grade for {event['name']}")
        notify_server(event["name"], config["hook_url"], event["type"])


if __name__ == "__main__":
    main()
