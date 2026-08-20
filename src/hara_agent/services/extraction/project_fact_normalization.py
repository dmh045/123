from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Sequence

from hara_agent.models import (
    ConstraintOperator, DriverContextFact, DriverLocation, FactProvenance,
    NumericConstraintFact, ProjectFactOutputType, ReviewStatus, SourceRef,
    SpeedEnvelope,
)

from .project_fact_specs import RequiredProjectFactSpec


FOUND = "FOUND"
NOT_FOUND = "NOT_FOUND"


@dataclass(frozen=True)
class ProjectFactNormalizationFailure:
    fact_type: str
    code: str
    reason: str


@dataclass
class ProjectFactNormalizationResult:
    speed_envelopes: list[SpeedEnvelope] = field(default_factory=list)
    numeric_constraints: list[NumericConstraintFact] = field(default_factory=list)
    driver_context_facts: list[DriverContextFact] = field(default_factory=list)
    coverage: list[dict[str, Any]] = field(default_factory=list)
    failures: list[ProjectFactNormalizationFailure] = field(default_factory=list)

    def to_cache_dict(self) -> dict[str, Any]:
        return {
            "speed_envelopes": [asdict(item) for item in self.speed_envelopes],
            "numeric_constraints": [asdict(item) for item in self.numeric_constraints],
            "driver_context_facts": [asdict(item) for item in self.driver_context_facts],
            "coverage": self.coverage,
            "failures": [asdict(item) for item in self.failures],
        }


class ProjectFactNormalizer:
    """Fail-closed atomic normalizer; it never parses arbitrary prose."""

    OPERATOR_ALIASES = {
        "LT": ConstraintOperator.LT, "<": ConstraintOperator.LT,
        "LE": ConstraintOperator.LE, "<=": ConstraintOperator.LE, "≤": ConstraintOperator.LE, "≦": ConstraintOperator.LE,
        "EQ": ConstraintOperator.EQ, "=": ConstraintOperator.EQ,
        "GE": ConstraintOperator.GE, ">=": ConstraintOperator.GE, "≥": ConstraintOperator.GE, "≧": ConstraintOperator.GE,
        "GT": ConstraintOperator.GT, ">": ConstraintOperator.GT, "＞": ConstraintOperator.GT,
        "RANGE": ConstraintOperator.RANGE,
    }
    UNIT_ALIASES = {
        "km/h": "km/h", "kph": "km/h", "kmh": "km/h",
        "m/s²": "m/s²", "m/s2": "m/s²",
        "ms": "ms", "millisecond": "ms", "milliseconds": "ms",
        "deg": "deg", "°": "deg",
    }
    DRIVER_ALIASES = {
        "INSIDE": DriverLocation.INSIDE, "inside": DriverLocation.INSIDE,
        "driver inside": DriverLocation.INSIDE,
        "在驾驶位": DriverLocation.INSIDE, "驾驶员在车内": DriverLocation.INSIDE,
        "OUTSIDE": DriverLocation.OUTSIDE, "outside": DriverLocation.OUTSIDE,
        "driver outside": DriverLocation.OUTSIDE,
        "不在驾驶位": DriverLocation.OUTSIDE, "驾驶员在车外": DriverLocation.OUTSIDE,
    }

    def normalize(
        self,
        specs: Sequence[RequiredProjectFactSpec],
        raw_results: Any,
        source_blocks: Sequence[dict[str, str]],
        source_id: str,
        candidate_block_ids_by_fact: dict[str, set[str]] | None = None,
    ) -> ProjectFactNormalizationResult:
        result = ProjectFactNormalizationResult()
        if not isinstance(raw_results, list):
            for spec in specs:
                self._fail(result, spec.fact_type, "INVALID_COVERAGE", "results must be an array")
            return result
        by_type: dict[str, list[dict[str, Any]]] = {}
        for item in raw_results:
            if not isinstance(item, dict):
                self._fail(result, "", "INVALID_FACT_TYPE", "each result requires fact_type")
                continue
            candidate = dict(item)
            identity_resolution = "EXPLICIT"
            if not isinstance(candidate.get("fact_type"), str):
                resolved_type = self._resolve_driver_fact_type(candidate, specs)
                if resolved_type is None:
                    self._fail(result, "", "INVALID_FACT_TYPE", "each result requires fact_type")
                    continue
                candidate["fact_type"] = resolved_type
                identity_resolution = "DETERMINISTIC_DRIVER_ENUM"
            candidate["_identity_resolution"] = identity_resolution
            by_type.setdefault(candidate["fact_type"], []).append(candidate)
        expected_types = {spec.fact_type for spec in specs}
        for unknown in sorted(set(by_type) - expected_types):
            self._fail(result, unknown, "INVALID_FACT_TYPE", "fact_type is not in the requested batch")
        block_map = {str(item.get("block_id", "")): item for item in source_blocks}
        seen_atomic: set[tuple[Any, ...]] = set()
        for spec in specs:
            candidates = by_type.get(spec.fact_type, [])
            if len(candidates) != 1:
                self._fail(result, spec.fact_type, "INVALID_COVERAGE", "exactly one FOUND/NOT_FOUND result is required")
                result.coverage.append({"fact_type": spec.fact_type, "status": "INVALID"})
                continue
            candidate = candidates[0]
            status = str(candidate.get("status", "")).upper()
            if status == NOT_FOUND:
                result.coverage.append({
                    "fact_type": spec.fact_type,
                    "status": "NOT_FOUND_IN_EVIDENCE",
                    "source_truth_verified": False,
                })
                continue
            if status != FOUND:
                self._fail(result, spec.fact_type, "INVALID_COVERAGE", "status must be FOUND or NOT_FOUND")
                result.coverage.append({"fact_type": spec.fact_type, "status": "INVALID"})
                continue
            missing = [name for name in spec.required_fields if candidate.get(name) is None]
            if missing:
                self._fail(result, spec.fact_type, "MISSING_REQUIRED_FIELD", ",".join(missing))
                result.coverage.append({"fact_type": spec.fact_type, "status": "ATOMICIZATION_FAILED"})
                continue
            try:
                source = self._source_ref(
                    candidate, block_map, source_id, spec,
                    (candidate_block_ids_by_fact or {}).get(spec.fact_type),
                )
                fact = self._atomic_fact(spec, candidate, source)
                key = self._identity(fact)
                if key in seen_atomic:
                    raise ValueError("DUPLICATE_FACT: duplicate normalized atomic fact")
                seen_atomic.add(key)
                if isinstance(fact, SpeedEnvelope):
                    result.speed_envelopes.append(fact)
                elif isinstance(fact, NumericConstraintFact):
                    result.numeric_constraints.append(fact)
                else:
                    result.driver_context_facts.append(fact)
                result.coverage.append({
                    "fact_type": spec.fact_type,
                    "status": FOUND,
                    "identity_resolution": candidate.get("_identity_resolution", "EXPLICIT"),
                })
            except ValueError as error:
                code, _, reason = str(error).partition(":")
                self._fail(result, spec.fact_type, code, reason.strip() or code)
                result.coverage.append({"fact_type": spec.fact_type, "status": "ATOMICIZATION_FAILED"})
        return result

    def _atomic_fact(self, spec, candidate, source):
        if spec.output_type in {ProjectFactOutputType.SPEED_ENVELOPE, ProjectFactOutputType.NUMERIC_CONSTRAINT}:
            operator = self.OPERATOR_ALIASES.get(str(candidate.get("operator", "")))
            if operator is None:
                raise ValueError("INVALID_OPERATOR: unsupported operator")
            value = candidate.get("value")
            value_max = candidate.get("value_max")
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError("NON_ATOMIC_CANDIDATE: value must be one JSON number")
            if operator is ConstraintOperator.RANGE and (isinstance(value_max, bool) or not isinstance(value_max, (int, float))):
                raise ValueError("NON_ATOMIC_CANDIDATE: RANGE requires one numeric value_max")
            if operator is not ConstraintOperator.RANGE and value_max is not None:
                raise ValueError("NON_ATOMIC_CANDIDATE: non-RANGE candidate cannot contain value_max")
            raw_unit = str(candidate.get("unit", ""))
            unit = self.UNIT_ALIASES.get(raw_unit)
            if unit is None or (spec.unit_hints and raw_unit not in spec.unit_hints and unit not in {
                self.UNIT_ALIASES.get(item, item) for item in spec.unit_hints
            }):
                raise ValueError("INVALID_UNIT: unit is not allowed by the spec")
            if spec.output_type is ProjectFactOutputType.SPEED_ENVELOPE:
                mode = str(candidate.get("operating_mode", "")).strip()
                if not mode:
                    raise ValueError("MISSING_CONTEXT: operating_mode is required")
                if spec.context_hints:
                    mode = spec.context_hints[0]
                if operator is ConstraintOperator.RANGE:
                    minimum, maximum = float(value), float(value_max)
                elif operator in {ConstraintOperator.LE, ConstraintOperator.LT, ConstraintOperator.EQ}:
                    minimum, maximum = (float(value), float(value)) if operator is ConstraintOperator.EQ else (0.0, float(value))
                else:
                    raise ValueError("INVALID_OPERATOR: speed lower-bound-only facts are unsupported")
                return SpeedEnvelope(mode, minimum, maximum, str(candidate.get("condition", "")), unit, [source])
            condition = str(candidate.get("condition", ""))
            if spec.context_hints:
                condition = spec.context_hints[0]
            parameter = str(candidate.get("parameter", ""))
            if not parameter:
                raise ValueError("MISSING_REQUIRED_FIELD: parameter")
            if spec.aliases:
                parameter = spec.aliases[0]
            context = {"condition": condition}
            qualification = candidate.get("qualification")
            if qualification is not None:
                if not isinstance(qualification, str):
                    raise ValueError("NON_ATOMIC_CANDIDATE: qualification must be a string")
                context["qualification"] = qualification
            return NumericConstraintFact(
                spec.fact_type, parameter, operator,
                float(value), unit, context,
                float(value_max) if value_max is not None else None,
                [source], FactProvenance.PROJECT_INPUT, ReviewStatus.PENDING, "LLM",
            )
        location = self.DRIVER_ALIASES.get(str(candidate.get("driver_location", "")))
        if location is None:
            raise ValueError("INVALID_DRIVER_LOCATION: expected INSIDE or OUTSIDE")
        return DriverContextFact(
            spec.fact_type, location, str(candidate.get("control_mode", "")),
            str(candidate.get("condition", "")), [source],
            FactProvenance.PROJECT_INPUT, ReviewStatus.PENDING, "LLM",
        )

    @staticmethod
    def _source_ref(candidate, block_map, source_id, spec, allowed_block_ids=None):
        block_id = str(candidate.get("source_block_id", ""))
        block = block_map.get(block_id)
        if block is None:
            raise ValueError("UNKNOWN_SOURCE_REF: source_block_id was not routed")
        if allowed_block_ids is not None and block_id not in allowed_block_ids:
            raise ValueError("UNKNOWN_SOURCE_REF: source_block_id is support-only for this spec")
        excerpt = str(candidate.get("source_excerpt", ""))
        source_text = str(block.get("text", ""))
        if not any(alias.casefold() in source_text.casefold() for alias in spec.aliases):
            raise ValueError("UNKNOWN_SOURCE_REF: source block contains no exact spec alias")
        if excerpt and excerpt not in source_text:
            raise ValueError("UNKNOWN_SOURCE_REF: excerpt is not an exact substring of the source block")
        return SourceRef(
            "item_definition", source_id, str(block.get("location", "")),
            excerpt or source_text,
        )

    @staticmethod
    def _identity(fact):
        if isinstance(fact, SpeedEnvelope):
            return ("speed", fact.operating_mode.casefold())
        return (type(fact).__name__, fact.fact_type)

    def _resolve_driver_fact_type(self, candidate, specs):
        location = self.DRIVER_ALIASES.get(str(candidate.get("driver_location", "")))
        if location is None:
            return None
        suffix = ".inside" if location is DriverLocation.INSIDE else ".outside"
        matches = [
            spec.fact_type for spec in specs
            if spec.output_type is ProjectFactOutputType.DRIVER_CONTEXT
            and spec.fact_type.casefold().endswith(suffix)
        ]
        return matches[0] if len(matches) == 1 else None

    @staticmethod
    def _fail(result, fact_type, code, reason):
        result.failures.append(ProjectFactNormalizationFailure(fact_type, code, reason))
