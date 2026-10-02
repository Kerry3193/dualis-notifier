"""HTML validation and pure grade reconciliation."""
from io import StringIO
from pathlib import Path
from decimal import Decimal
import re

import pandas as pd

LAST_SEEN_COLUMN = "_notifier_last_seen"
MISSING_SINCE_COLUMN = "_notifier_missing_since"
STATE_COLUMNS = {LAST_SEEN_COLUMN, MISSING_SINCE_COLUMN}
IDENTIFIER_COLUMNS = ("Nr.", "Name")
REQUIRED_COLUMNS = {"Name", "Endnote"}
GRADE_COLUMNS = ("Nr.", "Name", "Endnote", "Credits", "Status")


def extract_data_from_html(raw_html: str) -> pd.DataFrame:
    try:
        tables = pd.read_html(
            StringIO(raw_html), flavor="lxml", thousands=None,
            converters={column: str for column in GRADE_COLUMNS},
        )
    except (ValueError, TypeError) as exc:
        raise ValueError("Dualis hat keine lesbare Notentabelle geliefert.") from exc
    candidates = []
    for table in tables:
        if isinstance(table.columns, pd.MultiIndex):
            table.columns = table.columns.get_level_values(-1)
        table.columns = [str(column).strip() for column in table.columns]
        if REQUIRED_COLUMNS.issubset(table.columns):
            candidates.append(table)
    if len(candidates) != 1:
        raise ValueError("Keine eindeutige Notentabelle mit Name und Endnote gefunden.")
    table = candidates[0]
    if not table.columns.is_unique:
        raise ValueError("Die Notentabelle enthält doppelte Spalten.")
    table = table.loc[:, [column for column in GRADE_COLUMNS if column in table.columns]]
    return prepare_grades(table)


def normalise_value(value) -> str:
    """Create stable CSV values independent of pandas' inferred data types."""
    if pd.isna(value):
        return ""
    if isinstance(value, float):
        return format(value, "g")
    return str(value).strip()


def comparison_value(column, value) -> str:
    value = normalise_value(value)
    if column in {"Endnote", "Credits"} and re.fullmatch(r"\d+(?:[.,]\d+)?", value):
        return str(Decimal(value.replace(",", ".")).normalize())
    return value


def prepare_grades(table: pd.DataFrame) -> pd.DataFrame:
    """Remove summary rows and normalise every grade value before comparison."""
    if not REQUIRED_COLUMNS.issubset(table.columns) or not table.columns.is_unique:
        raise ValueError("Die Notentabelle benötigt eindeutige Spalten Name und Endnote.")
    grades = table.copy()
    if "Name" in grades.columns:
        grades = grades[grades["Name"].map(normalise_value) != "Semester-GPA"]

    grades = grades.map(normalise_value).reset_index(drop=True)
    if grades["Name"].eq("").any():
        raise ValueError("Die Notentabelle enthält ein Modul ohne Namen.")
    if "Nr." in grades:
        numbers = grades.loc[grades["Nr."].ne(""), "Nr."]
        if not numbers.is_unique:
            raise ValueError("Die Notentabelle enthält doppelte Modulnummern.")
    identifier_column(grades)
    return grades


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
    if previous.empty:
        previous = pd.DataFrame(columns=[*current.columns, *STATE_COLUMNS])
    else:
        previous = prepare_grades(previous)

    value_columns = list(dict.fromkeys([*current.columns, *previous.columns]))
    value_columns = [column for column in value_columns if column not in STATE_COLUMNS]

    old_records = {index: row for index, row in enumerate(previous.to_dict("records"))}
    by_number = {row["Nr."]: index for index, row in old_records.items() if row.get("Nr.")}
    unique_names = previous["Name"].value_counts() if "Name" in previous else pd.Series(dtype=int)
    by_name = {row["Name"]: index for index, row in old_records.items()
               if unique_names[row["Name"]] == 1}
    current_names = current["Name"].value_counts()
    events: list[dict[str, str]] = []
    state_records: list[dict[str, str]] = []

    for current_record in current.to_dict("records"):
        module_id = current_record.get("Nr.", "")
        old_index = by_number.get(module_id) if module_id else None
        if old_index is None and current_names[current_record["Name"]] == 1:
            candidate = by_name.get(current_record["Name"])
            old_number = old_records.get(candidate, {}).get("Nr.", "")
            if not module_id or not old_number:
                old_index = candidate
        old_record = old_records.pop(old_index, None)
        record = {column: current_record.get(column, (old_record or {}).get(column, ""))
                  for column in value_columns}
        if old_record and not record.get("Nr.") and old_record.get("Nr."):
            record["Nr."] = old_record["Nr."]
        record[LAST_SEEN_COLUMN] = observed_at
        record[MISSING_SINCE_COLUMN] = ""

        if old_record is None:
            events.append({"type": "new", "name": record.get("Name", module_id)})
        else:
            changed = any(
                comparison_value(column, old_record.get(column, ""))
                != comparison_value(column, record.get(column, ""))
                for column in GRADE_COLUMNS if column != "Nr."
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
    prepare_grades(state)
    return state, events
