from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable
import unicodedata


@dataclass(frozen=True)
class RequiredFactQuery:
    fact_id: str
    source_block_ids: tuple[str, ...] = ()
    source_location: str = ""
    source_excerpt: str = ""
    match_terms: tuple[str, ...] = ()


@dataclass(frozen=True)
class VerifiedSourceBlock:
    block_id: str
    location: str
    text: str


def block_value(block: Any, field_name: str) -> str:
    if isinstance(block, dict):
        return str(block.get(field_name, ""))
    return str(getattr(block, field_name, ""))


def normalize_source_text(value: Any) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(value)).split()).casefold()


def verify_missing_fact(
    required_fact: RequiredFactQuery,
    source_blocks: Iterable[Any],
) -> list[VerifiedSourceBlock]:
    """Deterministically verify whether source blocks contain a required fact.

    This interface does not infer engineering values. It only returns traceable
    blocks satisfying the requested locator and text constraints.
    """
    matches: list[VerifiedSourceBlock] = []
    requested_ids = set(required_fact.source_block_ids)
    excerpt = normalize_source_text(required_fact.source_excerpt)
    terms = [normalize_source_text(term) for term in required_fact.match_terms]
    for block in source_blocks:
        block_id = block_value(block, "block_id")
        location = block_value(block, "location")
        text = block_value(block, "text")
        normalized_text = normalize_source_text(text)
        if requested_ids and block_id not in requested_ids:
            continue
        if required_fact.source_location and location != required_fact.source_location:
            continue
        if excerpt and excerpt not in normalized_text:
            continue
        if terms and not all(term in normalized_text for term in terms):
            continue
        matches.append(VerifiedSourceBlock(block_id, location, text))
    return matches


class DeterministicSourceVerifier:
    def verify_missing_fact(
        self, required_fact: RequiredFactQuery, source_blocks: Iterable[Any],
    ) -> list[VerifiedSourceBlock]:
        return verify_missing_fact(required_fact, source_blocks)
