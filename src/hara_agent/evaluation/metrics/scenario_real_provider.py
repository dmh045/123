from __future__ import annotations

from collections import Counter
from itertools import combinations, product
from typing import Any, Iterable


MECHANISM_ERROR_CODES = {
    "INVALID_MECHANISM_APPLICATION", "MISSING_MECHANISM_APPLICATION",
    "INVALID_MECHANISM_APPLICATION_SHAPE",
}
SUPPORT_ERROR_CODES = {
    "INVALID_SUPPORT_SHAPE", "MISSING_EDGE_SUPPORT", "DUPLICATE_SUPPORT_REF",
    "UNRESOLVED_SUPPORT_REF", "SUPPORT_KIND_MISMATCH", "INVALID_DERIVATION_METADATA",
    "UNRESOLVED_EVIDENCE_REF", "INVALID_EVIDENCE_REF_SHAPE",
}
AUTHORITY_ERROR_CODES = {
    "UNAPPROVED_EVIDENCE_AUTHORITY", "ASSUMPTION_IN_POSITIVE_CHAIN",
}


def summarize_provider_attempts(
    attempts: list[dict[str, Any]],
    *,
    gold: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Summarize one semantic identity without voting or semantic repair."""
    valid = [item for item in attempts if item.get("contract_valid")]
    schema_count = sum(bool(item.get("schema_valid")) for item in attempts)
    contract_count = len(valid)
    provider_verdicts = [
        item.get("provider_causal_verdict") for item in attempts
        if isinstance(item.get("provider_causal_verdict"), bool)
    ]
    valid_verdicts = [
        item.get("provider_causal_verdict") for item in valid
        if isinstance(item.get("provider_causal_verdict"), bool)
    ]
    error_codes = Counter(
        str((item.get("error") or {}).get("code", "UNKNOWN_ERROR"))
        for item in attempts if not item.get("contract_valid")
    )
    mechanism_errors = Counter()
    support_errors = Counter()
    authority_errors = Counter()
    for item in attempts:
        if item.get("contract_valid"):
            continue
        error = item.get("error", {})
        code = str(error.get("code", "UNKNOWN_ERROR"))
        mechanism_code = str(error.get("mechanism_code", ""))
        if code in MECHANISM_ERROR_CODES or mechanism_code:
            mechanism_errors[mechanism_code or code] += 1
        if code in SUPPORT_ERROR_CODES:
            support_errors[code] += 1
        if code in AUTHORITY_ERROR_CODES:
            authority_errors[code] += 1

    dimension_sets = [
        set(item.get("provider_assessment", {}).get("risk_dimensions", []))
        for item in valid
    ]
    dimension_jaccards = _pairwise_jaccards(dimension_sets)
    support = _support_stability(valid)
    binding_stability = _binding_stability(valid)
    result: dict[str, Any] = {
        "attempt_count": len(attempts),
        "schema_valid_attempt_count": schema_count,
        "schema_valid_rate": _rate(schema_count, len(attempts)),
        "contract_valid_attempt_count": contract_count,
        "contract_valid_rate": _rate(contract_count, len(attempts)),
        "valid_attempt_count": contract_count,
        "valid_attempt_rate": _rate(contract_count, len(attempts)),
        "stability_sample_sufficient": contract_count >= 2,
        "provider_causal_distribution": _bool_distribution(provider_verdicts),
        "provider_causal_true_count": sum(value is True for value in provider_verdicts),
        "provider_causal_false_count": sum(value is False for value in provider_verdicts),
        "valid_causal_distribution": _bool_distribution(valid_verdicts),
        "valid_causal_true_count": sum(value is True for value in valid_verdicts),
        "valid_causal_false_count": sum(value is False for value in valid_verdicts),
        "causal_agreement": _agreement(valid_verdicts),
        "breakpoint_distribution": _distribution(
            item.get("provider_assessment", {}).get("breakpoint", "") for item in valid
        ),
        "error_code_distribution": dict(sorted(error_codes.items())),
        "mechanism_error_distribution": dict(sorted(mechanism_errors.items())),
        "support_error_distribution": dict(sorted(support_errors.items())),
        "authority_error_distribution": dict(sorted(authority_errors.items())),
        "authority_violation_count": sum(authority_errors.values()),
        "risk_dimension_distribution": _distribution(
            dimension
            for item in valid
            for dimension in item.get("provider_assessment", {}).get("risk_dimensions", [])
        ),
        "risk_dimension_jaccard_min": min(dimension_jaccards) if dimension_jaccards else None,
        "risk_dimension_jaccard_avg": _average(dimension_jaccards),
        **support,
        **binding_stability,
        "mechanism_id_distribution": _mechanism_distribution(valid, "mechanism_id"),
        "mechanism_version_distribution": _mechanism_distribution(
            valid, "mechanism_version"
        ),
        "unknown_mechanism_invention_count": sum(
            str((item.get("error") or {}).get("mechanism_code", ""))
            == "UNKNOWN_MECHANISM"
            for item in attempts
        ),
        "no_applicable_approved_mechanism_count": sum(
            bool(item.get("no_applicable_approved_mechanism")) for item in attempts
        ),
    }
    if gold is not None:
        result["gold"] = _gold_metrics(attempts, gold)
    return result


def compare_order_attempts(
    left: list[dict[str, Any]], right: list[dict[str, Any]],
) -> dict[str, Any]:
    """Cross-compare orders by scenario_id + fingerprint, never array position."""
    left_groups = _by_identity(left)
    right_groups = _by_identity(right)
    shared = sorted(set(left_groups) & set(right_groups))
    causal_values: list[bool] = []
    mechanism_values: list[bool] = []
    binding_values: list[bool] = []
    support_values: list[float] = []
    comparisons = []
    for identity in shared:
        left_valid = [item for item in left_groups[identity] if item.get("contract_valid")]
        right_valid = [item for item in right_groups[identity] if item.get("contract_valid")]
        pair_count = 0
        for left_item, right_item in product(left_valid, right_valid):
            pair_count += 1
            left_assessment = left_item.get("provider_assessment", {})
            right_assessment = right_item.get("provider_assessment", {})
            causal_values.append(
                left_item.get("provider_causal_verdict")
                == right_item.get("provider_causal_verdict")
            )
            mechanism_values.append(
                _mechanism_signature(left_assessment)
                == _mechanism_signature(right_assessment)
            )
            binding_values.append(
                _binding_signature(left_assessment) == _binding_signature(right_assessment)
            )
            support_values.append(
                _jaccard(
                    _all_support_refs(left_assessment),
                    _all_support_refs(right_assessment),
                )
            )
        comparisons.append({
            "scenario_id": identity[0],
            "semantic_fingerprint": identity[1],
            "left_valid": len(left_valid),
            "right_valid": len(right_valid),
            "cross_order_pair_count": pair_count,
        })
    return {
        "shared_identity_count": len(shared),
        "missing_from_left": [list(item) for item in sorted(set(right_groups) - set(left_groups))],
        "missing_from_right": [list(item) for item in sorted(set(left_groups) - set(right_groups))],
        "order_causal_agreement": _boolean_rate(causal_values),
        "order_mechanism_agreement": _boolean_rate(mechanism_values),
        "order_binding_agreement": _boolean_rate(binding_values),
        "order_support_jaccard_min": min(support_values) if support_values else None,
        "order_support_jaccard_avg": _average(support_values),
        "comparisons": comparisons,
    }


def build_v1_v2_differential(
    v1: dict[str, Any], v2: dict[str, Any],
) -> dict[str, Any]:
    return {
        "v1_valid_attempt_rate": v1.get("valid_attempt_rate"),
        "v2_valid_attempt_rate": v2.get("valid_attempt_rate"),
        "v1_causal_distribution": v1.get("provider_causal_distribution", {}),
        "v2_causal_distribution": v2.get("provider_causal_distribution", {}),
        "v1_error_code_distribution": v1.get("error_code_distribution", {}),
        "v2_error_code_distribution": v2.get("error_code_distribution", {}),
        "v1_dimension_jaccard": {
            "min": v1.get("risk_dimension_jaccard_min"),
            "avg": v1.get("risk_dimension_jaccard_avg"),
        },
        "v2_dimension_jaccard": {
            "min": v2.get("risk_dimension_jaccard_min"),
            "avg": v2.get("risk_dimension_jaccard_avg"),
        },
        "v2_support_jaccard": {
            "min": v2.get("support_jaccard_min"),
            "avg": v2.get("support_jaccard_avg"),
        },
        "v2_binding_stability": v2.get("binding_exact_match_rate"),
        "semantic_correctness_conclusion": None,
        "classification": "OBSERVATIONAL_ONLY",
    }


def _gold_metrics(attempts: list[dict[str, Any]], gold: dict[str, Any]) -> dict[str, Any]:
    expected_causal = gold.get("causally_relevant")
    causal_matches = sum(
        item.get("provider_causal_verdict") is expected_causal for item in attempts
    )
    expected_breakpoint = gold.get("breakpoint")
    breakpoint_matches = sum(
        item.get("provider_assessment", {}).get("breakpoint") == expected_breakpoint
        for item in attempts
    )
    required_edges = list(gold.get("required_edges", []))
    expected_mechanism = gold.get("mechanism_id")
    expected_version = gold.get("mechanism_version")
    expected_bindings = dict(gold.get("bindings", {}))
    mechanism_attempt_matches = 0
    version_attempt_matches = 0
    binding_matches = 0
    binding_total = len(attempts) * len(required_edges) * len(expected_bindings)
    required_edge_matches = 0
    mixed_attempts = 0
    for item in attempts:
        assessment = item.get("provider_assessment", {})
        edges = {edge.get("edge_id"): edge for edge in assessment.get("edges", [])}
        required_edge_matches += set(edges) == set(required_edges)
        applications = [
            edges.get(edge_id, {}).get("mechanism_application") or {}
            for edge_id in required_edges
        ]
        if expected_mechanism is not None and required_edges and all(
            app.get("mechanism_id") == expected_mechanism for app in applications
        ):
            mechanism_attempt_matches += 1
        if expected_version is not None and required_edges and all(
            app.get("mechanism_version") == expected_version for app in applications
        ):
            version_attempt_matches += 1
        for app in applications:
            bindings = app.get("bindings", {})
            binding_matches += sum(
                bindings.get(key) == value for key, value in expected_bindings.items()
            )
        mixed_attempts += any(
            {support.get("support_type") for support in edge.get("supports", [])}
            >= {"DIRECT_FACT", "DERIVED_PHYSICS"}
            for edge in edges.values()
        )
    return {
        "causal_gold_agreement": _rate(causal_matches, len(attempts)),
        "breakpoint_agreement": _rate(breakpoint_matches, len(attempts)),
        "required_edge_accuracy": _rate(required_edge_matches, len(attempts)),
        "mechanism_selection_agreement": (
            _rate(mechanism_attempt_matches, len(attempts))
            if expected_mechanism is not None else None
        ),
        "mechanism_version_agreement": (
            _rate(version_attempt_matches, len(attempts))
            if expected_version is not None else None
        ),
        "required_binding_accuracy": (
            _rate(binding_matches, binding_total) if expected_bindings else None
        ),
        "mixed_direct_derived_attempt_rate": _rate(mixed_attempts, len(attempts)),
    }


def _support_stability(valid: list[dict[str, Any]]) -> dict[str, Any]:
    if len(valid) < 2:
        return {
            "support_jaccard_min": None,
            "support_jaccard_avg": None,
            "support_type_agreement": None,
            "support_stability_by_edge": {},
        }
    edge_ids = sorted({
        edge.get("edge_id", "")
        for item in valid
        for edge in item.get("provider_assessment", {}).get("edges", [])
        if edge.get("edge_id")
    })
    all_jaccards: list[float] = []
    all_type_matches: list[bool] = []
    by_edge = {}
    for edge_id in edge_ids:
        signatures = [
            _edge_support_signature(item.get("provider_assessment", {}), edge_id)
            for item in valid
        ]
        jaccards = []
        type_matches = []
        for left, right in combinations(signatures, 2):
            # Reference stability and support-type stability are intentionally
            # independent metrics.  A type change for the same evidence must
            # not make the evidence reference itself look unstable.
            left_refs = {reference for reference, _support_type in left}
            right_refs = {reference for reference, _support_type in right}
            jaccards.append(_jaccard(left_refs, right_refs))
            type_matches.append(left == right)
        all_jaccards.extend(jaccards)
        all_type_matches.extend(type_matches)
        by_edge[edge_id] = {
            "support_ref_jaccard_min": min(jaccards) if jaccards else None,
            "support_ref_jaccard_avg": _average(jaccards),
            "support_type_agreement": _boolean_rate(type_matches),
        }
    return {
        "support_jaccard_min": min(all_jaccards) if all_jaccards else None,
        "support_jaccard_avg": _average(all_jaccards),
        "support_type_agreement": _boolean_rate(all_type_matches),
        "support_stability_by_edge": by_edge,
    }


def _binding_stability(valid: list[dict[str, Any]]) -> dict[str, Any]:
    if len(valid) < 2:
        return {"binding_exact_match_rate": None}
    signatures = [
        _binding_signature(item.get("provider_assessment", {})) for item in valid
    ]
    return {
        "binding_exact_match_rate": _boolean_rate([
            left == right for left, right in combinations(signatures, 2)
        ])
    }


def _mechanism_distribution(valid: list[dict[str, Any]], field: str) -> dict[str, int]:
    values = []
    for item in valid:
        apps = [
            edge.get("mechanism_application") or {}
            for edge in item.get("provider_assessment", {}).get("edges", [])
        ]
        selected = sorted({str(app.get(field, "")) for app in apps if app.get(field)})
        values.append("|".join(selected) if selected else "NONE")
    return _distribution(values)


def _mechanism_signature(assessment: dict[str, Any]) -> tuple:
    return tuple(sorted(
        (
            str(edge.get("edge_id", "")),
            str((edge.get("mechanism_application") or {}).get("mechanism_id", "")),
            str((edge.get("mechanism_application") or {}).get("mechanism_version", "")),
        )
        for edge in assessment.get("edges", [])
    ))


def _binding_signature(assessment: dict[str, Any]) -> tuple:
    return tuple(sorted(
        (
            str(edge.get("edge_id", "")),
            tuple(sorted(
                (str(key), str(value))
                for key, value in (
                    (edge.get("mechanism_application") or {}).get("bindings", {})
                ).items()
            )),
        )
        for edge in assessment.get("edges", [])
    ))


def _edge_support_signature(assessment: dict[str, Any], edge_id: str) -> tuple:
    for edge in assessment.get("edges", []):
        if edge.get("edge_id") == edge_id:
            return tuple(sorted(
                (str(item.get("evidence_ref", "")), str(item.get("support_type", "")))
                for item in edge.get("supports", [])
            ))
    return ()


def _all_support_refs(assessment: dict[str, Any]) -> set[str]:
    return {
        str(item.get("evidence_ref", ""))
        for edge in assessment.get("edges", [])
        for item in edge.get("supports", [])
        if item.get("evidence_ref")
    }


def _by_identity(attempts: list[dict[str, Any]]) -> dict[tuple[str, str], list[dict]]:
    result: dict[tuple[str, str], list[dict]] = {}
    for item in attempts:
        key = (str(item.get("scenario_id", "")), str(item.get("semantic_fingerprint", "")))
        result.setdefault(key, []).append(item)
    return result


def _pairwise_jaccards(values: list[set[str]]) -> list[float]:
    return [_jaccard(left, right) for left, right in combinations(values, 2)]


def _jaccard(left: set[str], right: set[str]) -> float:
    return len(left & right) / len(left | right) if left | right else 1.0


def _agreement(values: list[Any]) -> float | None:
    return max(Counter(values).values()) / len(values) if len(values) >= 2 else None


def _bool_distribution(values: Iterable[bool]) -> dict[str, int]:
    counter = Counter("true" if item else "false" for item in values)
    return {key: counter[key] for key in ("true", "false") if counter[key]}


def _distribution(values: Iterable[Any]) -> dict[str, int]:
    counter = Counter(str(item) for item in values if str(item))
    return dict(sorted(counter.items()))


def _rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _boolean_rate(values: list[bool]) -> float | None:
    return sum(values) / len(values) if values else None


def _average(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None
