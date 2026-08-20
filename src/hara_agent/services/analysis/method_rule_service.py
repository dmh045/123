from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from hara_agent.contracts import (
    CategoricalPredicate,
    CompiledRule,
    FactType,
    MethodContract,
    ParseStatus,
    Predicate,
    PredicateOperator,
    RangePredicate,
)


@dataclass(frozen=True)
class RuleEvaluation:
    value: str
    status: str
    reason: str
    rules: tuple[CompiledRule, ...] = ()
    missing_facts: tuple[FactType, ...] = ()
    fact_sources: tuple[dict[str, Any], ...] = ()


class MethodRuleScoringService:
    """Execute compiled S/E/C rules against explicit canonical scenario facts.

    The service intentionally performs no prose/keyword inference and never
    substitutes a Domain value. A matched but unresolved template rule is a
    traceable proposal with ``PENDING`` status.
    """

    FACT_KEYS = {
        FactType.COLLISION_TYPE: "collision_type",
        FactType.ROAD_USER_TYPE: "road_user_type",
        FactType.SPEED_UNSPECIFIED: "speed_unspecified_kph",
        FactType.DURATION_PERCENT: "duration_percent",
        FactType.OCCURRENCE_FREQUENCY: "occurrence_frequency",
        FactType.AVOIDABILITY_PERCENT: "avoidability_percent",
    }
    FINAL_AUTHORITIES = {"PROJECT_INPUT", "METHOD_CONTRACT", "DERIVED"}

    def __init__(self, method: MethodContract):
        if not method.engineering_rules_compiled:
            raise ValueError("MethodContract engineering rules are not compiled")
        self.method = method

    def score(
        self, scenario: dict[str, Any], hazard_event: str
    ) -> dict[str, dict[str, Any]]:
        severity = self._evaluate(self.method.severity.rules, scenario)
        exposure_method = str(scenario.get("exposure_method", "")).strip().upper()
        if exposure_method == "T":
            exposure = self._evaluate(self.method.exposure.duration_rules, scenario)
        elif exposure_method == "F":
            exposure = self._evaluate(self.method.exposure.frequency_rules, scenario)
        else:
            exposure = RuleEvaluation(
                "", "PENDING",
                "Missing canonical exposure_method; expected T or F from project evidence.",
                missing_facts=(FactType.EXPOSURE,),
            )
        if (
            exposure.status == "FINALIZED"
            and not self._canonical_fact_final("exposure_method", scenario)
        ):
            exposure = RuleEvaluation(
                exposure.value,
                "PENDING",
                exposure.reason
                + " Canonical exposure_method lacks finalized grounded provenance.",
                rules=exposure.rules,
                missing_facts=exposure.missing_facts,
                fact_sources=exposure.fact_sources,
            )
        controllability = self._evaluate(
            self.method.controllability.criteria, scenario
        )
        return {
            "severity": self._result(
                severity, "severity_score", hazard_event=hazard_event
            ),
            "exposure": self._result(
                exposure,
                "exposure_score",
                scenario=str(
                    scenario.get("situational_description")
                    or scenario.get("scenario_summary")
                    or ""
                ),
                exposure_method=exposure_method,
            ),
            "controllability": self._result(
                controllability,
                "controllability_score",
                hazard_event=hazard_event,
            ),
        }

    def _evaluate(
        self, rules: Iterable[CompiledRule], scenario: dict[str, Any]
    ) -> RuleEvaluation:
        matches: list[CompiledRule] = []
        unresolved: list[tuple[CompiledRule, tuple[FactType, ...], str]] = []
        for rule in rules:
            if not rule.executable:
                continue
            outcome, missing, reason = self._rule_matches(rule, scenario)
            if outcome == "MATCH":
                matches.append(rule)
            elif outcome in {"MISSING", "AMBIGUOUS"}:
                unresolved.append((rule, missing, reason))

        if matches:
            values = {rule.result for rule in matches if rule.result}
            alternatives = {
                alternative for rule in matches for alternative in rule.alternatives
            }
            if len(values) != 1 or alternatives:
                return RuleEvaluation(
                    "", "PENDING",
                    "Matching template rules contain conflicting or alternative results.",
                    rules=tuple(matches),
                    fact_sources=self._fact_sources(matches, scenario),
                )
            value = next(iter(values))
            rules_compiled = all(
                rule.parse_status is ParseStatus.COMPILED for rule in matches
            )
            facts_final = self._facts_final(matches, scenario)
            finalized = rules_compiled and facts_final
            reason = (
                f"Matched compiled template rule(s): "
                f"{', '.join(rule.rule_id for rule in matches)}."
            )
            if not rules_compiled:
                requirements = sorted({
                    rule.resolution_requirement
                    for rule in matches if rule.resolution_requirement
                })
                reason += " Template ambiguity remains unresolved"
                if requirements:
                    reason += f": {'; '.join(requirements)}"
                reason += "."
            if not facts_final:
                reason += " One or more canonical facts lack finalized grounded provenance."
            return RuleEvaluation(
                value,
                "FINALIZED" if finalized else "PENDING",
                reason,
                rules=tuple(matches),
                fact_sources=self._fact_sources(matches, scenario),
            )

        if unresolved:
            missing = tuple(sorted(
                {field for _, fields, _ in unresolved for field in fields},
                key=lambda item: item.value,
            ))
            reasons = sorted({reason for _, _, reason in unresolved if reason})
            return RuleEvaluation(
                "", "PENDING",
                "; ".join(reasons) or "Required canonical facts are unresolved.",
                rules=tuple(item[0] for item in unresolved),
                missing_facts=missing,
            )
        return RuleEvaluation(
            "", "PENDING", "No compiled template rule matches the supplied canonical facts."
        )

    def _rule_matches(
        self, rule: CompiledRule, scenario: dict[str, Any]
    ) -> tuple[str, tuple[FactType, ...], str]:
        missing: set[FactType] = set()
        ambiguous = False
        for predicate in rule.predicates:
            key = self.FACT_KEYS.get(predicate.field)
            if (
                not key
                or key not in scenario
                or scenario[key] is None
                or scenario[key] == ""
            ):
                missing.add(predicate.field)
                continue
            outcome = self._predicate_matches(predicate, scenario[key])
            if outcome == "NO_MATCH":
                return "NO_MATCH", (), ""
            if outcome == "AMBIGUOUS":
                ambiguous = True
        if missing:
            names = ", ".join(sorted(item.value for item in missing))
            return "MISSING", tuple(missing), f"Missing canonical fact(s): {names}."
        if ambiguous:
            return (
                "AMBIGUOUS", (),
                "A fact lies on a template range boundary whose inclusivity is unspecified.",
            )
        return "MATCH", (), ""

    @staticmethod
    def _predicate_matches(predicate, value: Any) -> str:
        if isinstance(predicate, CategoricalPredicate):
            actual = str(value).strip().upper()
            return "MATCH" if actual in predicate.values else "NO_MATCH"
        if isinstance(predicate, RangePredicate):
            try:
                number = float(value)
            except (TypeError, ValueError):
                return "NO_MATCH"
            if predicate.lower is not None:
                if number < predicate.lower:
                    return "NO_MATCH"
                if number == predicate.lower:
                    if predicate.lower_inclusive is None:
                        return "AMBIGUOUS"
                    if not predicate.lower_inclusive:
                        return "NO_MATCH"
            if predicate.upper is not None:
                if number > predicate.upper:
                    return "NO_MATCH"
                if number == predicate.upper:
                    if predicate.upper_inclusive is None:
                        return "AMBIGUOUS"
                    if not predicate.upper_inclusive:
                        return "NO_MATCH"
            return "MATCH"
        if isinstance(predicate, Predicate):
            actual = str(value).strip().upper()
            expected = str(predicate.value).strip().upper()
            if predicate.operator is PredicateOperator.EQ:
                return "MATCH" if actual == expected else "NO_MATCH"
            if predicate.operator is PredicateOperator.NE:
                return "MATCH" if actual != expected else "NO_MATCH"
            if predicate.operator is PredicateOperator.EXISTS:
                return "MATCH" if actual else "NO_MATCH"
        return "NO_MATCH"

    def _facts_final(
        self, rules: Iterable[CompiledRule], scenario: dict[str, Any]
    ) -> bool:
        provenance = scenario.get("_fact_provenance", {})
        for field in {predicate.field for rule in rules for predicate in rule.predicates}:
            key = self.FACT_KEYS.get(field, "")
            metadata = provenance.get(key, {}) if isinstance(provenance, dict) else {}
            if not isinstance(metadata, dict):
                return False
            approval = str(metadata.get("approval", metadata.get("status", ""))).upper()
            authority = str(metadata.get("provenance", metadata.get("authority", ""))).upper()
            sources = metadata.get("source_refs", metadata.get("sources", []))
            if (
                approval not in {"FINALIZED", "APPROVED"}
                or authority not in self.FINAL_AUTHORITIES
                or not sources
            ):
                return False
        return True

    def _canonical_fact_final(self, key: str, scenario: dict[str, Any]) -> bool:
        provenance = scenario.get("_fact_provenance", {})
        metadata = provenance.get(key, {}) if isinstance(provenance, dict) else {}
        if not isinstance(metadata, dict):
            return False
        approval = str(metadata.get("approval", metadata.get("status", ""))).upper()
        authority = str(metadata.get("provenance", metadata.get("authority", ""))).upper()
        sources = metadata.get("source_refs", metadata.get("sources", []))
        return (
            approval in {"FINALIZED", "APPROVED"}
            and authority in self.FINAL_AUTHORITIES
            and bool(sources)
        )

    def _fact_sources(
        self, rules: Iterable[CompiledRule], scenario: dict[str, Any]
    ) -> tuple[dict[str, Any], ...]:
        provenance = scenario.get("_fact_provenance", {})
        collected: list[dict[str, Any]] = []
        if not isinstance(provenance, dict):
            return ()
        for field in {predicate.field for rule in rules for predicate in rule.predicates}:
            key = self.FACT_KEYS.get(field, "")
            metadata = provenance.get(key, {})
            if not isinstance(metadata, dict):
                continue
            for source in metadata.get("source_refs", metadata.get("sources", [])):
                if isinstance(source, dict) and source not in collected:
                    collected.append(source)
        return tuple(collected)

    def _result(
        self, evaluation: RuleEvaluation, value_key: str, **extra: Any
    ) -> dict[str, Any]:
        sources = [source for rule in evaluation.rules for source in rule.source_refs]
        locations = ",".join(
            dict.fromkeys(f"{source.sheet}!{source.range}" for source in sources)
        )
        excerpts = " | ".join(dict.fromkeys(rule.raw_text for rule in evaluation.rules))
        result = {
            value_key: evaluation.value,
            "reasoning": evaluation.reason,
            "engineering_status": evaluation.status,
            "engineering_source_type": "method_contract",
            "engineering_source": str(self.method.metadata["template_hash"]),
            "engineering_location": locations,
            "engineering_excerpt": excerpts,
            "engineering_rule_id": ",".join(
                rule.rule_id for rule in evaluation.rules
            ),
            "engineering_rule_version": (
                f"{self.method.contract_version}/{self.method.compiler_version}"
            ),
            "engineering_basis": evaluation.reason,
            "missing_fact_types": [item.value for item in evaluation.missing_facts],
            "fact_sources": list(evaluation.fact_sources),
            "_source": "compiled_method_rule_engine",
            **extra,
        }
        return result
