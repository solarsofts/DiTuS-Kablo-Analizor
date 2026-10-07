"""Spreadsheet export hardening against formula (CSV/DDE) injection.

Project text (project name, object IDs, notes, override rationale) is copied
into CSV/XLSX deliverables that are opened by third parties such as cable
suppliers.  A cell that starts with ``=``, ``+``, ``-``, ``@``, TAB or CR may
be evaluated as a formula by spreadsheet applications, so such text cells are
prefixed with a single quote.  Plain numeric text (``"-5"``, ``"+1.25"``) and
non-string values are left unchanged.
"""

from __future__ import annotations

import csv
from collections.abc import Iterable, Mapping
from typing import Any

FORMULA_TRIGGER_PREFIXES: tuple[str, ...] = ("=", "+", "-", "@", "\t", "\r")


def spreadsheet_safe_text(value: Any) -> Any:
    if not isinstance(value, str) or not value.startswith(FORMULA_TRIGGER_PREFIXES):
        return value
    try:
        float(value)
    except ValueError:
        return "'" + value
    return value


def spreadsheet_safe_row(values: Iterable[Any]) -> list[Any]:
    return [spreadsheet_safe_text(value) for value in values]


def spreadsheet_safe_mapping(row: Mapping[str, Any]) -> dict[str, Any]:
    return {key: spreadsheet_safe_text(value) for key, value in row.items()}


class SpreadsheetSafeDictWriter(csv.DictWriter):
    """``csv.DictWriter`` that neutralises formula-like text cells."""

    def writerow(self, rowdict: Mapping[str, Any]) -> Any:
        return super().writerow(spreadsheet_safe_mapping(rowdict))

    def writerows(self, rowdicts: Iterable[Mapping[str, Any]]) -> Any:
        return super().writerows(spreadsheet_safe_mapping(row) for row in rowdicts)


__all__ = [
    "FORMULA_TRIGGER_PREFIXES",
    "SpreadsheetSafeDictWriter",
    "spreadsheet_safe_mapping",
    "spreadsheet_safe_row",
    "spreadsheet_safe_text",
]
