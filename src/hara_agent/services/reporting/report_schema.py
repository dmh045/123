from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import yaml


class ReportSchemaError(ValueError):
    """Raised when the canonical report schema is not self-consistent."""


@dataclass(frozen=True)
class ReportField:
    canonical_field: str
    sheet: str
    header: str
    order: int
    required: bool
    value_type: str
    display_policy: str
    availability_policy: str
    source_hint: str = ""
    width: float = 18
    wrap_text: bool = False
    hidden: bool = False
    human_readable: bool = True


@dataclass(frozen=True)
class ReportSheet:
    name: str
    order: int


@dataclass(frozen=True)
class ReportSchema:
    schema_version: str
    report_id: str
    sheets: tuple[ReportSheet, ...]
    fields: tuple[ReportField, ...]
    ordering: tuple[str, ...]
    requiredness: dict[str, str]
    display_policies: dict[str, Any]
    schema_hash: str
    presentation: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ReportSchema":
        raw_fields = value.get("fields", [])
        if not isinstance(raw_fields, list):
            raise ReportSchemaError("fields must be a list")
        fields = tuple(
            ReportField(
                canonical_field=str(item["canonical_field"]),
                sheet=str(item["sheet"]),
                header=str(item["header"]),
                order=int(item["order"]),
                required=bool(item.get("required", False)),
                value_type=str(item["value_type"]),
                display_policy=str(item["display_policy"]),
                availability_policy=str(item["availability_policy"]),
                source_hint=str(item.get("source_hint", "")),
                width=float(item.get("width", 18)),
                wrap_text=bool(item.get("wrap_text", False)),
                hidden=bool(item.get("hidden", False)),
                human_readable=bool(item.get("human_readable", True)),
            )
            for item in raw_fields
        )
        sheets = tuple(
            ReportSheet(str(item["name"]), int(item.get("order", index)))
            for index, item in enumerate(value.get("sheets", []))
        )
        canonical = {
            "schema_version": str(value.get("schema_version", "")),
            "report_id": str(value.get("report_id", "")),
            "sheets": [{"name": item.name, "order": item.order} for item in sheets],
            "fields": [item.__dict__ for item in fields],
            "ordering": list(value.get("ordering", [])),
            "requiredness": dict(value.get("requiredness", {})),
            "display_policies": dict(value.get("display_policies", {})),
            "presentation": dict(value.get("presentation", {})),
        }
        digest = hashlib.sha256(
            json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        schema = cls(
            schema_version=canonical["schema_version"],
            report_id=canonical["report_id"],
            sheets=sheets,
            fields=fields,
            ordering=tuple(str(item) for item in canonical["ordering"]),
            requiredness={str(k): str(v) for k, v in canonical["requiredness"].items()},
            display_policies=canonical["display_policies"],
            schema_hash=digest,
            presentation=canonical["presentation"],
        )
        ReportSchemaValidator().validate(schema).raise_for_errors()
        return schema

    @classmethod
    def load(cls, path: str | Path) -> "ReportSchema":
        source = Path(path)
        with source.open("r", encoding="utf-8") as stream:
            value = yaml.safe_load(stream) or {}
        return cls.from_dict(value)

    def fields_for_sheet(self, sheet: str) -> tuple[ReportField, ...]:
        return tuple(sorted((item for item in self.fields if item.sheet == sheet), key=lambda item: item.order))

    def field(self, canonical_field: str) -> ReportField:
        matches = [item for item in self.fields if item.canonical_field == canonical_field]
        if len(matches) != 1:
            raise ReportSchemaError(f"canonical field is not unique: {canonical_field}")
        return matches[0]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "report_id": self.report_id,
            "sheets": [{"name": item.name, "order": item.order} for item in self.sheets],
            "fields": [item.__dict__ for item in self.fields],
            "ordering": list(self.ordering),
            "requiredness": dict(self.requiredness),
            "display_policies": dict(self.display_policies),
            "presentation": dict(self.presentation),
        }


@dataclass(frozen=True)
class ReportSchemaValidation:
    errors: tuple[str, ...]
    warnings: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.errors

    def raise_for_errors(self) -> None:
        if self.errors:
            raise ReportSchemaError("; ".join(self.errors))


class ReportSchemaValidator:
    CANONICAL_FIELDS = frozenset({
        "hara_id", "malfunction_id", "scenario_id", "hazardous_event_id",
        "function_id", "function_name", "function_output", "guideword",
        "malfunction", "hazard", "operational_scenario", "scenario_detail",
        "hazardous_event", "potential_harm", "severity", "severity_rationale",
        "exposure", "exposure_rationale", "controllability",
        "controllability_rationale", "asil", "asil_rationale", "ftti",
        "ftti_rationale", "sg_id", "safety_goal", "safe_state",
        "assessment_status", "clarification_ids", "remark",
    })
    SUPPORTED_DISPLAY_POLICIES = frozenset(
        {"literal", "availability", "pending_if_unavailable", "human_rationale"}
    )

    def validate(
        self,
        schema: ReportSchema,
        *,
        renderer_fields: set[str] | None = None,
        method_capabilities: Mapping[str, Any] | None = None,
    ) -> ReportSchemaValidation:
        errors: list[str] = []
        warnings: list[str] = []
        fields = schema.fields
        names = [item.canonical_field for item in fields]
        if len(names) != len(set(names)):
            errors.append("duplicate canonical mapping")
        unknown_fields = sorted(set(names) - self.CANONICAL_FIELDS)
        if unknown_fields:
            errors.append(f"unknown field: {unknown_fields}")
        coordinates = [(item.sheet, item.order) for item in fields]
        if len(coordinates) != len(set(coordinates)):
            errors.append("duplicate column")
        sheet_names = {item.name for item in schema.sheets}
        unknown_sheets = sorted({item.sheet for item in fields} - sheet_names)
        if unknown_sheets:
            errors.append(f"unknown sheet: {unknown_sheets}")
        unknown_policies = sorted(
            {item.display_policy for item in fields} - self.SUPPORTED_DISPLAY_POLICIES
        )
        if unknown_policies:
            errors.append(f"unsupported display policy: {unknown_policies}")
        main_presentation = schema.presentation.get("main_hara")
        if not isinstance(main_presentation, Mapping):
            errors.append("main_hara presentation is missing")
        else:
            visible_fields = [str(item) for item in main_presentation.get("visible_fields", ())]
            if not visible_fields:
                errors.append("main_hara presentation has no visible fields")
            if len(visible_fields) != len(set(visible_fields)):
                errors.append("main_hara presentation has duplicate fields")
            unknown_visible = sorted(set(visible_fields) - set(names))
            if unknown_visible:
                errors.append(f"main_hara presentation has unknown fields: {unknown_visible}")
            grouped_fields = [
                str(field)
                for group in main_presentation.get("groups", ())
                for field in group.get("fields", ())
            ]
            unknown_grouped = sorted(set(grouped_fields) - set(visible_fields))
            if unknown_grouped:
                errors.append(f"main_hara presentation groups unknown fields: {unknown_grouped}")
        required = {item.canonical_field for item in fields if item.required}
        requiredness_required = {name for name, value in schema.requiredness.items() if value == "required"}
        missing_required = sorted(requiredness_required - set(names))
        if missing_required:
            errors.append(f"required field missing: {missing_required}")
        if renderer_fields is not None:
            missing_renderer = sorted(set(names) - renderer_fields)
            if missing_renderer:
                errors.append(f"missing renderer: {missing_renderer}")
        capabilities = method_capabilities or {}
        if capabilities.get("ftti_source_present") and "ftti" not in names:
            errors.append("REPORT_FIELD_MISSING: FTTI")
        for name in required:
            if name not in schema.ordering:
                errors.append(f"required field has no ordering entry: {name}")
        return ReportSchemaValidation(tuple(dict.fromkeys(errors)), tuple(dict.fromkeys(warnings)))


def load_report_schema(path: str | Path | None = None) -> ReportSchema:
    return ReportSchema.load(path or Path("report_assets/hara_report_v1.yaml"))


__all__ = [
    "ReportField", "ReportSchema", "ReportSchemaError", "ReportSchemaValidation",
    "ReportSchemaValidator", "ReportSheet", "load_report_schema",
]
