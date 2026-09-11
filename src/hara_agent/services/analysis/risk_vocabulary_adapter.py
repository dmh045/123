"""Exact MethodContract-bound vocabulary normalization for risk facts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from hara_agent.contracts import MethodContract


@dataclass(frozen=True)
class RiskVocabularyResolution:
    """One auditable exact mapping result; unmapped values never fall back."""

    field: str
    raw_value: str
    canonical_value: str
    mapping_rule_id: str
    mapping_source: str
    source_ref: dict[str, str]
    method_contract_hash: str

    @property
    def mapped(self) -> bool:
        return bool(self.canonical_value)

    def to_dict(self) -> dict[str, Any]:
        return {
            "raw_value": self.raw_value,
            "canonical_value": self.canonical_value,
            "mapping_rule_id": self.mapping_rule_id,
            "mapping_source": self.mapping_source,
            "source_ref": dict(self.source_ref),
            "method_contract_hash": self.method_contract_hash,
        }


class RiskVocabularyAdapter:
    """Resolve only exact mappings compiled into the active MethodContract."""

    _FIELDS = ("road_user_type", "collision_type")

    def __init__(self, method: MethodContract):
        if method.structured_risk_method is None:
            raise ValueError("RiskVocabularyAdapter requires a structured risk method")
        self.method_contract_hash = str(method.metadata.get("method_source_hash", ""))
        self._mappings: dict[tuple[str, str], RiskVocabularyResolution] = {}
        for item in method.metadata.get("risk_vocabulary_mappings", []):
            if not isinstance(item, dict):
                raise ValueError("MethodContract risk vocabulary mapping must be an object")
            field = str(item.get("field", "")).strip()
            raw_value = str(item.get("raw_value", "")).strip()
            canonical_value = str(item.get("canonical_value", "")).strip()
            mapping_rule_id = str(item.get("mapping_rule_id", "")).strip()
            mapping_source = str(item.get("mapping_source", "")).strip()
            source_ref = item.get("source_ref", {})
            if (
                field not in self._FIELDS
                or not raw_value
                or not canonical_value
                or not mapping_rule_id
                or not mapping_source
                or not isinstance(source_ref, dict)
            ):
                raise ValueError("MethodContract risk vocabulary mapping is incomplete")
            key = (field, raw_value.casefold())
            if key in self._mappings:
                raise ValueError(f"Duplicate MethodContract risk vocabulary mapping: {key!r}")
            self._mappings[key] = RiskVocabularyResolution(
                field=field,
                raw_value=raw_value,
                canonical_value=canonical_value,
                mapping_rule_id=mapping_rule_id,
                mapping_source=mapping_source,
                source_ref={str(key): str(value) for key, value in source_ref.items()},
                method_contract_hash=self.method_contract_hash,
            )

        severity = method.structured_risk_method.severity
        source = severity.source_ref
        source_ref = {
            "workbook": source.workbook,
            "template_hash": source.template_hash,
            "sheet": source.sheet,
            "range": source.range,
            "raw_text": source.raw_text,
            "source_hash": source.source_hash,
        }
        for field, values in (
            ("road_user_type", severity.road_user_groups),
            ("collision_type", severity.collision_types),
        ):
            for canonical_value, source_value in values:
                for raw_value in (canonical_value, source_value):
                    key = (field, raw_value.casefold())
                    self._mappings.setdefault(
                        key,
                        RiskVocabularyResolution(
                            field=field,
                            raw_value=raw_value,
                            canonical_value=canonical_value,
                            mapping_rule_id=(
                                f"ACTIVE_SEVERITY_{field.upper()}_EXACT_VALUE"
                            ),
                            mapping_source="ACTIVE_SEVERITY_SEMANTICS",
                            source_ref=source_ref,
                            method_contract_hash=self.method_contract_hash,
                        ),
                    )

    def resolve(self, *, field: str, raw_value: object) -> RiskVocabularyResolution:
        value = raw_value.strip() if isinstance(raw_value, str) else ""
        result = self._mappings.get((field, value.casefold())) if value else None
        if result is None:
            return RiskVocabularyResolution(
                field=field,
                raw_value=value,
                canonical_value="",
                mapping_rule_id="",
                mapping_source="",
                source_ref={},
                method_contract_hash=self.method_contract_hash,
            )
        return RiskVocabularyResolution(
            field=field,
            raw_value=value,
            canonical_value=result.canonical_value,
            mapping_rule_id=result.mapping_rule_id,
            mapping_source=result.mapping_source,
            source_ref=dict(result.source_ref),
            method_contract_hash=self.method_contract_hash,
        )
