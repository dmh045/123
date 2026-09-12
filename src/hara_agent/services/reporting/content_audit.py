from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
import re
from typing import Any


_MACHINE_REASON_TOKENS = (
    "MISSING_RELATIVE_SPEED", "MISSING_ROAD_USER_TYPE", "MISSING_COLLISION_TYPE",
    "EXPOSURE_DIMENSION_COVERAGE", "CONTROLLABILITY_UNKNOWN_BRANCH_POLICY_UNSPECIFIED",
    "METHOD_BRANCH_UNRESOLVED", "PENDING_METHOD_SEMANTICS", "PENDING_UPSTREAM_RISK_VALUE",
    "NO_ITEM_FACT", "NO_EXPLICIT_METHOD_ALIAS", "AMBIGUOUS_BINDING", "RANGE_CONTAINMENT",
    "NOT_EVALUATED",
)
_DEBUG_TOKENS = ("candidate_atom_ids", "rule_id", "binding_status")
_RATIONAL_FIELDS = (
    "severity_rationale", "exposure_rationale", "controllability_rationale",
    "asil_rationale", "ftti_rationale",
)


def _lengths(rows: list[Any], field: str) -> dict[str, float | int]:
    values = [str(getattr(row, field, "") or "") for row in rows]
    lengths = [len(value) for value in values]
    return {
        "coverage": sum(bool(value) for value in values),
        "avg_length": round(sum(lengths) / len(lengths), 2) if lengths else 0,
        "max_length": max(lengths, default=0),
        "unique_count": len(set(values)),
    }


def audit_content_presentation(
    view_model: Any,
    potential_harm_path: Mapping[str, Any],
) -> dict[str, Any]:
    """Audit the reviewer-facing HARA rows without inspecting Excel styles."""
    rows = list(view_model.rows)
    values = [str(value or "") for row in rows for value in row.to_dict().values()]
    explanatory_fields = (
        "operational_scenario", "scenario_detail", *_RATIONAL_FIELDS, "remark",
    )
    explanatory_values = [str(getattr(row, field, "") or "") for row in rows for field in explanatory_fields]
    language_mix = sum(
        bool(re.search(r"[A-Za-z]{4,}", value))
        for value in explanatory_values
        if not any(token in value for token in ("AVP", "ASIL", "FTTI", "TTC", "UNKNOWN", "Exposure", "Pending"))
    )
    raw_json = sum(value.lstrip().startswith(("{", "[")) for value in values)
    machine_leaks = sum(any(token in value for token in _MACHINE_REASON_TOKENS) for value in values)
    debug_leaks = sum(any(token in value for token in _DEBUG_TOKENS) for value in values)
    critical_fields = (
        "hazardous_event", "potential_harm", "operational_scenario", "scenario_detail",
        "severity", "severity_rationale", "exposure", "exposure_rationale",
        "controllability", "controllability_rationale", "asil", "asil_rationale",
        "ftti", "ftti_rationale",
    )
    critical_blanks = sum(not str(getattr(row, field, "") or "") for row in rows for field in critical_fields)
    return {
        "language_mode": "ZH_ENGINEERING",
        "main_hara_rows": len(rows),
        "language_mix_count": language_mix,
        "raw_json_leakage_count": raw_json,
        "raw_machine_status_leakage_count": machine_leaks,
        "debug_term_leakage_count": debug_leaks,
        "critical_blank_count": critical_blanks,
        "scenario_description": _lengths(rows, "operational_scenario"),
        "scenario_detail": _lengths(rows, "scenario_detail"),
        **{field: _lengths(rows, field) for field in _RATIONAL_FIELDS},
        "remark_duplicate_information_count": sum(bool(str(row.remark or "").strip()) for row in rows),
        "hazardous_event_and_potential_harm_separated": all(
            row.hazardous_event != row.potential_harm for row in rows
        ),
        "potential_harm": {
            "resolved": sum(not str(row.potential_harm).startswith("Pending") for row in rows),
            "pending": sum(str(row.potential_harm).startswith("Pending") for row in rows),
            "path_classification": str(potential_harm_path.get("classification", "")),
        },
        "quality_gate": "PASS" if not any((raw_json, machine_leaks, debug_leaks, critical_blanks)) else "FAIL",
    }


def audit_potential_harm_path(state: Any, view_model: Any) -> dict[str, Any]:
    """Trace committed Potential Harm evidence through its report projection."""
    scoring_event = next(
        (
            item for item in reversed(state.audit_trail)
            if item.get("event") == "structured_risk_scoring_completed"
        ),
        {},
    )
    calculations = list(scoring_event.get("risk_calculation_inputs", []))
    by_pair = {
        (str(item.get("malfunction_id", "")), str(item.get("scenario_id", ""))): item
        for item in calculations if isinstance(item, Mapping)
    }
    risks = list(state.risk_results)
    resolver_results = [
        by_pair.get((risk.malfunction_id, risk.scenario_id), {}).get("potential_harm")
        for risk in risks
    ]
    invoked = [item for item in resolver_results if isinstance(item, Mapping)]
    status_reasons = Counter(
        f"{item.get('status', 'UNKNOWN')}: {item.get('reason', '')}" for item in invoked
    )
    resolved = sum(bool(str(risk.potential_harm or "").strip()) for risk in risks)
    persisted_projection_gap = any(
        bool(str(risk.potential_harm or "").strip())
        and row.potential_harm != risk.potential_harm
        for risk, row in zip(risks, view_model.rows, strict=True)
    )
    resolver_produced_but_not_persisted = any(
        bool(str(item.get("potential_harm", "") or "").strip())
        and not str(risk.potential_harm or "").strip()
        for risk, item in zip(risks, resolver_results, strict=True)
        if isinstance(item, Mapping)
    )
    severity_pending = sum(str(getattr(risk.severity.status, "value", risk.severity.status)) != "FINALIZED" for risk in risks)
    if len(invoked) != len(risks):
        classification = "RUNTIME_NOT_EXECUTED"
    elif resolver_produced_but_not_persisted:
        classification = "RESULT_NOT_PERSISTED"
    elif persisted_projection_gap:
        classification = "PROJECTION_NOT_MAPPED"
    elif resolved == 0 and severity_pending == len(risks):
        classification = "UPSTREAM_RISK_NOT_READY"
    elif resolved == 0:
        classification = "METHOD_SEMANTICS_UNAVAILABLE"
    else:
        classification = "RUNTIME_DEFECT"
    return {
        "resolver_class": "PotentialHarmResolver",
        "resolver_entrypoint": "PotentialHarmResolver.resolve(method, severity_result, scenario, registry)",
        "resolver_invocation_count": len(invoked),
        "eligible_he_count": len(risks),
        "resolved_count": resolved,
        "pending_count": len(risks) - resolved,
        "not_invoked_count": len(risks) - len(invoked),
        "runtime_storage_path": "score_risks -> RiskAssessment.potential_harm",
        "committed_state_field": "HARAState.risk_results[].potential_harm",
        "projection_source_field": "RiskAssessment.potential_harm",
        "view_model_field": "HARAReportRowView.potential_harm",
        "excel_field": f"04_HARA!J6:J{5 + len(risks)}",
        "per_status_reason_counts": dict(sorted(status_reasons.items())),
        "upstream_severity_status_counts": dict(sorted(Counter(
            str(getattr(risk.severity.status, "value", risk.severity.status)) for risk in risks
        ).items())),
        "upstream_severity_review_reason_counts": dict(sorted(Counter(
            str(getattr(risk.severity, "review_reason", "")) for risk in risks
        ).items())),
        "resolver_execution_evidence": "HARAState.audit_trail[].risk_calculation_inputs[].potential_harm",
        "classification": classification,
        "runtime_to_projection_wiring_gap": classification in {"RESULT_NOT_PERSISTED", "PROJECTION_NOT_MAPPED"},
    }


__all__ = ["audit_content_presentation", "audit_potential_harm_path"]
