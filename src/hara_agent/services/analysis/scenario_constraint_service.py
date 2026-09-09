from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Sequence

from hara_agent.contracts import (
    NormativeStrength, ScenarioConstraintDisposition, ScenarioConstraintRule,
)


class ScenarioConstraintStatus(str, Enum):
    KEEP = "KEEP"
    KEEP_RARE = "KEEP_RARE"
    DROP = "DROP"
    CONFLICT = "CONFLICT"
    UNRESOLVED_NO_RULES = "UNRESOLVED_NO_RULES"
    PENDING_COMPATIBILITY = "PENDING_COMPATIBILITY"


@dataclass(frozen=True)
class ScenarioConstraintEvaluation:
    status: ScenarioConstraintStatus
    matched_rule_ids: tuple[str, ...]
    reason: str


class ScenarioConstraintExecutor:
    """Apply exact compiled cross-dimension constraints without rarity guessing."""

    @staticmethod
    def _value(binding: Any) -> str:
        if isinstance(binding, dict):
            return str(
                binding.get("method_value") or binding.get("project_value") or ""
            ).strip()
        return str(binding or "").strip()

    def evaluate(
        self,
        bindings: dict[str, Any],
        rules: Sequence[ScenarioConstraintRule],
    ) -> ScenarioConstraintEvaluation:
        executable = [
            rule for rule in rules
            if rule.executable and rule.normative_strength is NormativeStrength.NORMATIVE
        ]
        if not executable:
            return ScenarioConstraintEvaluation(
                ScenarioConstraintStatus.UNRESOLVED_NO_RULES, (),
                "No normative executable cross-dimension constraint is compiled.",
            )
        matched = [
            rule for rule in executable
            if all(
                self._value(bindings.get(predicate.dimension)) in predicate.values
                for predicate in rule.predicates
            )
        ]
        if not matched:
            return ScenarioConstraintEvaluation(
                ScenarioConstraintStatus.KEEP, (),
                "No compiled exclusion or rarity rule matches this combination.",
            )
        dispositions = {rule.disposition for rule in matched}
        impossible = dispositions & {
            ScenarioConstraintDisposition.PHYSICALLY_IMPOSSIBLE,
            ScenarioConstraintDisposition.SEMANTICALLY_INCOMPATIBLE,
        }
        if impossible and (
            ScenarioConstraintDisposition.EXPLICITLY_ALLOWED in dispositions
            or ScenarioConstraintDisposition.RARE_BUT_FEASIBLE in dispositions
        ):
            return ScenarioConstraintEvaluation(
                ScenarioConstraintStatus.CONFLICT,
                tuple(rule.rule_id for rule in matched),
                "Matched normative scenario constraints have conflicting dispositions.",
            )
        if impossible:
            return ScenarioConstraintEvaluation(
                ScenarioConstraintStatus.DROP,
                tuple(rule.rule_id for rule in matched),
                "; ".join(rule.reason for rule in matched if rule.disposition in {
                    ScenarioConstraintDisposition.PHYSICALLY_IMPOSSIBLE,
                    ScenarioConstraintDisposition.SEMANTICALLY_INCOMPATIBLE,
                }),
            )
        if ScenarioConstraintDisposition.RARE_BUT_FEASIBLE in dispositions:
            return ScenarioConstraintEvaluation(
                ScenarioConstraintStatus.KEEP_RARE,
                tuple(rule.rule_id for rule in matched),
                "Rare but feasible combinations remain in HARA and are handled by E.",
            )
        return ScenarioConstraintEvaluation(
            ScenarioConstraintStatus.KEEP,
            tuple(rule.rule_id for rule in matched),
            "The combination is explicitly allowed by the compiled method.",
        )

    @staticmethod
    def pending(
        unresolved_dimensions: Sequence[str], bindings: dict[str, Any],
    ) -> ScenarioConstraintEvaluation:
        reasons = {
            str(name): str(
                bindings.get(name, {}).get("unresolved_reason", "PENDING_BINDING")
            )
            for name in unresolved_dimensions
            if isinstance(bindings.get(name), dict)
        }
        return ScenarioConstraintEvaluation(
            ScenarioConstraintStatus.PENDING_COMPATIBILITY,
            (),
            "Compatibility remains pending until every required dimension is bound: "
            + ", ".join(
                f"{name}={reason}" for name, reason in sorted(reasons.items())
            ),
        )
