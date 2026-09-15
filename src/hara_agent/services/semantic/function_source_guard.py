from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any, Sequence

from hara_agent.models import FunctionDefinition
from hara_agent.services.extraction import block_value, normalize_source_text


_TABLE_ROW_LOCATION = re.compile(r"^table\[(\d+)]\.row\[(\d+)]$")
_PARAGRAPH_LOCATION = re.compile(r"^paragraph\[(\d+)]$")
_NUMBERED_ITEM = re.compile(r"^\s*\d+\s*[.、．)）-]\s*(\S.*)$")


def _header_key(value: str) -> str:
    normalized = normalize_source_text(value).replace("‑", "-").replace("–", "-")
    return re.sub(r"[\s_-]+", " ", normalized).strip()


_FUNCTION_HEADER_ALIASES = frozenset(
    _header_key(value)
    for value in (
        "整车功能",
        "整车级功能",
        "车辆级功能",
        "vehicle-level function",
        "vehicle-level functions",
        "vehicle level function",
        "vehicle level functions",
    )
)
_NUMBER_HEADER_ALIASES = frozenset(
    _header_key(value) for value in ("序号", "编号", "no", "no.", "number")
)


@dataclass(frozen=True)
class ExplicitFunctionSource:
    names: tuple[str, ...]
    authoritative_source_location: str


class FunctionSourceMismatchError(ValueError):
    code = "FUNCTION_SOURCE_MISMATCH"

    def __init__(self, details: dict[str, Any]):
        self.details = details
        super().__init__(
            f"{self.code}: {json.dumps(details, ensure_ascii=False, sort_keys=True)}"
        )


def _split_cells(text: str) -> list[str]:
    return [value.strip() for value in re.split(r"\s*\|\s*", text)]


def _table_sources(source_blocks: Sequence[Any]) -> list[ExplicitFunctionSource]:
    tables: dict[int, list[tuple[int, str, list[str]]]] = {}
    for block in source_blocks:
        if block_value(block, "kind") != "table_row":
            continue
        location = block_value(block, "location")
        match = _TABLE_ROW_LOCATION.fullmatch(location)
        if not match:
            continue
        tables.setdefault(int(match.group(1)), []).append(
            (int(match.group(2)), location, _split_cells(block_value(block, "text")))
        )

    sources: list[ExplicitFunctionSource] = []
    for table_id, rows in tables.items():
        ordered_rows = sorted(rows)
        for position, (header_row, header_location, header_cells) in enumerate(ordered_rows):
            function_columns = [
                index
                for index, value in enumerate(header_cells)
                if _header_key(value) in _FUNCTION_HEADER_ALIASES
            ]
            if len(function_columns) != 1:
                continue
            function_column = function_columns[0]
            number_columns = [
                index
                for index, value in enumerate(header_cells)
                if _header_key(value) in _NUMBER_HEADER_ALIASES
            ]
            number_column = number_columns[0] if len(number_columns) == 1 else None
            names: list[str] = []
            last_row = header_row
            for row_number, _, cells in ordered_rows[position + 1 :]:
                if row_number <= header_row or function_column >= len(cells):
                    continue
                if number_column is not None:
                    if number_column >= len(cells) or not cells[number_column].isdigit():
                        if names:
                            break
                        continue
                name = cells[function_column].strip()
                if not name or _header_key(name) in _FUNCTION_HEADER_ALIASES:
                    continue
                names.append(name)
                last_row = row_number
            if names:
                sources.append(ExplicitFunctionSource(
                    names=tuple(names),
                    authoritative_source_location=(
                        f"table[{table_id}].row[{header_row}:{last_row}] "
                        f"(header={header_location})"
                    ),
                ))
    return sources


def _list_sources(source_blocks: Sequence[Any]) -> list[ExplicitFunctionSource]:
    blocks = list(source_blocks)
    sources: list[ExplicitFunctionSource] = []
    for index, block in enumerate(blocks):
        if block_value(block, "kind") != "paragraph":
            continue
        heading_location = block_value(block, "location")
        heading_match = _PARAGRAPH_LOCATION.fullmatch(heading_location)
        if (
            not heading_match
            or _header_key(block_value(block, "text")) not in _FUNCTION_HEADER_ALIASES
        ):
            continue
        names: list[str] = []
        last_location = heading_location
        expected_paragraph = int(heading_match.group(1)) + 1
        for candidate in blocks[index + 1 :]:
            if block_value(candidate, "kind") != "paragraph":
                break
            location = block_value(candidate, "location")
            location_match = _PARAGRAPH_LOCATION.fullmatch(location)
            if not location_match or int(location_match.group(1)) != expected_paragraph:
                break
            item_match = _NUMBERED_ITEM.fullmatch(block_value(candidate, "text"))
            if not item_match:
                break
            names.append(item_match.group(1).strip())
            last_location = location
            expected_paragraph += 1
        if names:
            sources.append(ExplicitFunctionSource(
                names=tuple(names),
                authoritative_source_location=(
                    f"{heading_location}:{last_location}"
                ),
            ))
    return sources


def detect_explicit_function_sources(
    source_blocks: Sequence[Any],
) -> tuple[ExplicitFunctionSource, ...]:
    return tuple(_table_sources(source_blocks) + _list_sources(source_blocks))


def validate_function_source_parity(
    functions: Sequence[FunctionDefinition],
    source_blocks: Sequence[Any],
) -> dict[str, Any]:
    sources = detect_explicit_function_sources(source_blocks)
    if not sources:
        return {"status": "NOT_APPLICABLE", "authoritative_source_count": 0}
    if len(sources) != 1:
        return {
            "status": "AMBIGUOUS_NOT_ENFORCED",
            "authoritative_source_count": len(sources),
            "candidate_locations": [
                source.authoritative_source_location for source in sources
            ],
        }

    source = sources[0]
    expected_names = list(source.names)
    actual_names = [function.name for function in functions]
    expected_keys = [normalize_source_text(name) for name in expected_names]
    actual_keys = [normalize_source_text(name) for name in actual_names]
    missing_names = [
        name for name, key in zip(expected_names, expected_keys) if key not in actual_keys
    ]
    unexpected_names = [
        name for name, key in zip(actual_names, actual_keys) if key not in expected_keys
    ]
    if expected_keys != actual_keys:
        raise FunctionSourceMismatchError({
            "expected_count": len(expected_names),
            "actual_count": len(actual_names),
            "missing_names": missing_names,
            "unexpected_names": unexpected_names,
            "expected_names": expected_names,
            "actual_names": actual_names,
            "order_matches": expected_keys == actual_keys,
            "authoritative_source_location": source.authoritative_source_location,
        })

    return {
        "status": "PASS",
        "expected_count": len(expected_names),
        "actual_count": len(actual_names),
        "authoritative_source_location": source.authoritative_source_location,
    }
