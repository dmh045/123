from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from enum import Enum
from typing import Any

from hara_agent.evaluation.metrics.extraction import extraction_metrics, gap_counts
from hara_agent.evaluation.models import (
    ExpectedProjectFact,
    ExtractionEvaluationInput,
    FactEvaluation,
    GapClassification,
)
from hara_agent.models import FactProvenance
from hara_agent.services.analysis import canonical_operating_mode
from hara_agent.services.extraction import (
    DeterministicSourceVerifier,
    RequiredFactQuery,
    normalize_source_text,
)


def _plain(value: Any) -> Any:
    if is_dataclass(value):
        value = asdict(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_plain(item) for item in value]
    return value


def _normalized_text(value: Any) -> str:
    if isinstance(value, (dict, list, tuple)):
        value = json.dumps(_plain(value), ensure_ascii=False, sort_keys=True)
    return normalize_source_text(value)


def _number_equal(left: Any, right: Any) -> bool:
    try:
        return abs(float(left) - float(right)) <= 1e-9
    except (TypeError, ValueError):
        return False


def _subset_match(expected: Any, observed: Any) -> bool:
    if isinstance(expected, dict):
        if not isinstance(observed, dict):
            return False
        for key, expected_value in expected.items():
            aliases = {
                "mode": ("mode", "operating_mode"),
                "operating_mode": ("operating_mode", "mode"),
                "min_kph": ("min_kph", "speed_min_kph"),
                "max_kph": ("max_kph", "speed_max_kph"),
                "speed_min_kph": ("speed_min_kph", "min_kph"),
                "speed_max_kph": ("speed_max_kph", "max_kph"),
            }.get(key, (key,))
            actual = next((observed[name] for name in aliases if name in observed), None)
            if key in {"mode", "operating_mode"}:
                if canonical_operating_mode(str(expected_value)) != canonical_operating_mode(str(actual)):
                    return False
            elif not _subset_match(expected_value, actual):
                return False
        return True
    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        return _number_equal(expected, observed)
    if isinstance(expected, bool):
        return observed is expected
    if expected is None:
        return observed is None
    expected_text, observed_text = _normalized_text(expected), _normalized_text(observed)
    return expected_text == observed_text or expected_text in observed_text


def _context_match(expected: ExpectedProjectFact, candidate: dict[str, Any]) -> bool:
    if not expected.context:
        return True
    nested = candidate.get("context")
    return _subset_match(
        expected.context,
        nested if isinstance(nested, dict) else candidate,
    )


def _source_refs(candidate: dict[str, Any], source_id: str) -> list[dict[str, str]]:
    refs = []
    for source in [*candidate.get("sources", []), *candidate.get("source_refs", [])]:
        if isinstance(source, dict):
            refs.append({
                "source_id": str(source.get("source_id", "")),
                "location": str(source.get("location", "")),
                "excerpt": str(source.get("excerpt", "")),
            })
    location = candidate.get("source_location", "")
    excerpt = candidate.get("source_excerpt", "")
    if location or excerpt:
        if isinstance(location, list):
            location = ";".join(str(item) for item in location)
        refs.append({
            "source_id": source_id,
            "location": str(location),
            "excerpt": str(excerpt),
        })
    return refs


def _source_ref_match(
    expected: ExpectedProjectFact, candidate: dict[str, Any], source_id: str,
) -> bool:
    if expected.source is None and not expected.source_block_ids:
        return True
    for source in _source_refs(candidate, source_id):
        source_ok = expected.source is None or source["source_id"] == expected.source.source_id
        expected_locations = set(expected.source_block_ids)
        if expected.source and expected.source.location:
            expected_locations.add(expected.source.location)
        location_ok = not expected_locations or any(
            item in source["location"] for item in expected_locations
        )
        excerpt_ok = (
            expected.source is None
            or not expected.source.excerpt
            or _normalized_text(expected.source.excerpt) in _normalized_text(source["excerpt"])
            or _normalized_text(source["excerpt"]) in _normalized_text(expected.source.excerpt)
        )
        if source_ok and location_ok and excerpt_ok:
            return True
    return False


class ExtractionEvaluationHarness:
    """Evaluate Source -> extraction -> normalized ProjectFacts integrity."""

    def __init__(self, verifier=None):
        self.verifier = verifier or DeterministicSourceVerifier()

    def evaluate(self, request: ExtractionEvaluationInput) -> dict[str, Any]:
        project = _plain(request.project_facts)
        if not isinstance(project, dict):
            raise TypeError("project_facts must be ItemDefinitionFacts or a mapping")
        expected_speed_modes = {
            canonical_operating_mode(str(item.context.get("operating_mode", item.context.get("mode", ""))))
            for item in request.expected_facts if item.field == "speed_envelopes"
        }
        expected_speed_modes.discard("")
        evaluations = [
            self._evaluate_fact(
                item,
                request,
                project,
                multiple_speed_modes=len(expected_speed_modes) > 1,
            )
            for item in request.expected_facts
        ]
        metrics = extraction_metrics(evaluations)
        production_context = self._production_context_metrics(
            request.expected_facts,
            request.production_speed_resolutions,
        )
        return {
            "stage": "extraction",
            "classification": "EVALUATION_ONLY",
            "input": {
                "source_id": request.source_id,
                "source_block_count": len(request.source_blocks),
                "expected_fact_count": len(request.expected_facts),
                **request.metadata,
            },
            "field_metrics": metrics,
            "production_context": production_context,
            "gap_counts": gap_counts(evaluations),
            "facts": [
                {
                    **_plain(item),
                    "classification": item.classification.value,
                }
                for item in evaluations
            ],
        }

    @staticmethod
    def _production_context_metrics(
        expected_facts: list[ExpectedProjectFact],
        resolutions: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Compare fresh normalized facts with the values used by production."""
        expected_by_mode = {}
        for item in expected_facts:
            if item.field != "speed_envelopes":
                continue
            mode = canonical_operating_mode(str(
                item.context.get("operating_mode", item.context.get("mode", ""))
            ))
            value = item.value if isinstance(item.value, dict) else {}
            if mode and value.get("speed_max_kph") is not None:
                expected_by_mode[mode] = float(value["speed_max_kph"])
        observed_by_mode = {
            canonical_operating_mode(str(item.get("operating_mode", ""))): item
            for item in resolutions
            if isinstance(item, dict) and item.get("operating_mode")
        }
        mode_results = []
        loss_count = 0
        for mode, expected_value in expected_by_mode.items():
            observed = observed_by_mode.get(mode)
            value_match = bool(
                observed is not None
                and _number_equal(expected_value, observed.get("resolved_value"))
            )
            contextual = bool(
                observed is not None
                and observed.get("resolution_source") == "SpeedEnvelope"
                and observed.get("fallback_used") is False
            )
            normalization_match = value_match and contextual
            if not normalization_match:
                loss_count += 1
            mode_results.append({
                "operating_mode": mode,
                "expected_speed_kph": expected_value,
                "observed_speed_kph": (
                    observed.get("resolved_value") if observed is not None else None
                ),
                "provenance": observed.get("provenance") if observed is not None else None,
                "resolution_source": (
                    observed.get("resolution_source") if observed is not None else None
                ),
                "fallback_used": observed.get("fallback_used") if observed is not None else None,
                "normalization_match": normalization_match,
            })
        return {
            "evaluated": bool(resolutions),
            "requested_mode_count": len(expected_by_mode),
            "resolved_mode_count": sum(
                1 for item in mode_results if item["observed_speed_kph"] is not None
            ),
            "normalization_loss_count": loss_count if resolutions else None,
            "mode_results": mode_results if resolutions else [],
        }

    def _evaluate_fact(
        self,
        expected: ExpectedProjectFact,
        request: ExtractionEvaluationInput,
        project: dict[str, Any],
        *,
        multiple_speed_modes: bool,
    ) -> FactEvaluation:
        query = RequiredFactQuery(
            fact_id=expected.fact_id,
            source_block_ids=expected.source_block_ids,
            source_location=expected.source.location if expected.source else "",
            source_excerpt=expected.source.excerpt if expected.source else "",
            match_terms=expected.match_terms,
        )
        grounded = bool(self.verifier.verify_missing_fact(query, request.source_blocks))
        candidates = self._candidates(expected, project)
        candidate = next(
            (item for item in candidates if _context_match(expected, item)),
            candidates[0] if candidates else None,
        )
        context_ok = candidate is not None and _context_match(expected, candidate)
        value_ok = candidate is not None and _subset_match(expected.value, candidate)
        unit_ok = self._unit_match(expected, candidate)
        normalization_ok = bool(candidate is not None and context_ok and value_ok and unit_ok)
        source_ok = bool(
            candidate is not None
            and _source_ref_match(expected, candidate, request.source_id)
        )
        routing_diagnostic = self._routing_diagnostic(expected, request)

        if expected.provenance is FactProvenance.METHOD_CONTRACT:
            classification = GapClassification.METHOD_DEFINED
            reason = "fact belongs to the HARA method contract, not project input"
        elif normalization_ok:
            if expected.derivable:
                classification = GapClassification.PRESENT_DERIVABLE
                reason = "grounded source fact is present through deterministic derivation"
            else:
                classification = GapClassification.PRESENT_EXPLICIT
                reason = "context, value and unit are preserved"
        elif expected.field == "speed_envelopes" and (
            candidate is not None
            or (multiple_speed_modes and self._has_aggregate_speed(project))
        ):
            classification = GapClassification.NORMALIZATION_LOSS
            reason = "mode-specific speed was collapsed or normalized incorrectly"
        elif not grounded:
            classification = GapClassification.SOURCE_NOT_PROVIDED
            reason = "required fact was not verified in the supplied source blocks"
        elif expected.route_task and expected.source_block_ids and not self._was_routed(
            expected, request
        ):
            classification = GapClassification.ROUTING_MISSED
            reason = f"source fact was not routed to {expected.route_task}"
        elif candidate is not None:
            classification = GapClassification.ATOMICIZATION_FAILED
            reason = "grounded semantic output exists but did not satisfy the typed atomic contract"
        else:
            classification = GapClassification.EXTRACTOR_MISSED
            reason = "source fact was available but absent from normalized ProjectFacts"

        return FactEvaluation(
            fact_id=expected.fact_id,
            field=expected.field,
            classification=classification,
            value_match=value_ok,
            grounded=grounded,
            source_ref_match=source_ok,
            context_match=context_ok,
            normalization_match=normalization_ok,
            expected_provenance=expected.provenance,
            expected_source_locator=(
                expected.source.location if expected.source else ";".join(expected.source_block_ids)
            ),
            selected_block_ids=tuple(routing_diagnostic.get(
                "selected_block_ids",
                request.routed_block_ids_by_task.get(
                    expected.route_task, request.routed_block_ids
                ),
            )),
            retrieval_diagnostics=routing_diagnostic,
            observed=candidate,
            reason=reason,
        )

    @staticmethod
    def _was_routed(
        expected: ExpectedProjectFact, request: ExtractionEvaluationInput,
    ) -> bool:
        routed = request.routed_block_ids_by_task.get(
            expected.route_task, request.routed_block_ids
        )
        return bool(set(expected.source_block_ids) & set(routed))

    @staticmethod
    def _routing_diagnostic(
        expected: ExpectedProjectFact, request: ExtractionEvaluationInput,
    ) -> dict[str, Any]:
        task_diagnostic = request.routing_diagnostics_by_task.get(expected.route_task, {})
        facts = task_diagnostic.get("facts", []) if isinstance(task_diagnostic, dict) else []
        for diagnostic in facts:
            if isinstance(diagnostic, dict) and diagnostic.get("fact_type") == expected.fact_id:
                return dict(diagnostic)
        return {
            "task": expected.route_task,
            "selected_block_ids": list(task_diagnostic.get(
                "selected_block_ids", request.routed_block_ids_by_task.get(
                    expected.route_task, request.routed_block_ids
                )
            )) if isinstance(task_diagnostic, dict) else [],
        }

    @staticmethod
    def _has_aggregate_speed(project: dict[str, Any]) -> bool:
        return (
            project.get("speed_min_kph") is not None
            and project.get("speed_max_kph") is not None
        )

    @staticmethod
    def _candidates(
        expected: ExpectedProjectFact, project: dict[str, Any],
    ) -> list[dict[str, Any]]:
        values = project.get(expected.field, [])
        if not isinstance(values, list):
            return []
        candidates = [item for item in values if isinstance(item, dict)]
        typed_matches = [
            item for item in candidates
            if str(item.get("fact_type", "")) == expected.fact_id
        ]
        if typed_matches:
            candidates = typed_matches
        if expected.match_terms:
            matched = [
                item for item in candidates
                if all(
                    _normalized_text(term) in _normalized_text(item)
                    for term in expected.match_terms
                )
            ]
            if matched:
                return matched
            if expected.field != "speed_envelopes":
                return []
        if expected.field == "speed_envelopes":
            requested_mode = expected.context.get(
                "operating_mode", expected.context.get("mode", "")
            )
            mode_matches = [
                item for item in candidates
                if canonical_operating_mode(str(item.get("operating_mode", item.get("mode", ""))))
                == canonical_operating_mode(str(requested_mode))
            ]
            return mode_matches
        return candidates

    @staticmethod
    def _unit_match(
        expected: ExpectedProjectFact, candidate: dict[str, Any] | None,
    ) -> bool:
        if not expected.unit:
            return True
        if candidate is None:
            return False
        actual = str(candidate.get("unit", ""))
        if not actual:
            actual = _normalized_text(candidate.get("value", ""))
        aliases = {
            "km/h": {"km/h", "kph", "kmh"},
            "m/s²": {"m/s²", "m/s2"},
            "ms": {"ms", "millisecond", "milliseconds"},
            "deg": {"deg", "°"},
        }
        expected_aliases = aliases.get(expected.unit, {expected.unit})
        return any(alias.casefold() in actual.casefold() for alias in expected_aliases)
