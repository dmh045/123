"""Governed, deterministic Scenario coverage-rule evaluation."""

from __future__ import annotations

import re
from typing import Any

from hara_agent.contracts import MethodContract
from hara_agent.models import FunctionDefinition


class ScenarioCoverageRuleService:
    """Evaluate only APPROVED MethodContract coverage rules.

    This service never resolves terminology to atoms and never promotes a
    Function-context field into a Method dimension.
    """

    def __init__(self, method: MethodContract):
        self.method = method
        self.dimensions = tuple(
            item.canonical_name for item in method.scenario_model.dimensions
        )
        raw_rules = method.metadata.get("scenario_coverage_rules", [])
        self.rules = tuple(
            dict(item) for item in raw_rules if isinstance(item, dict)
        )

    @property
    def has_approved_rules(self) -> bool:
        return bool(self.rules)

    @staticmethod
    def _normalize(value: str) -> str:
        return re.sub(r"[^\w]+", "", str(value).casefold(), flags=re.UNICODE)

    def _matches(self, rule: dict[str, Any], function: FunctionDefinition, operating_mode: str) -> bool:
        scope = rule.get("scope", {})
        if not isinstance(scope, dict):
            return False
        selector = scope.get("function_selector", {})
        if not isinstance(selector, dict):
            return False
        values = {
            "name": [function.name],
            "output": [function.output],
            "description": [function.description],
            "preconditions": list(function.preconditions),
            "triggers": list(function.triggers),
            "odd_constraints": list(function.odd_constraints),
        }
        for field, terms in selector.items():
            if field not in values or not isinstance(terms, list) or not terms:
                return False
            normalized_terms = [self._normalize(item) for item in terms]
            normalized_values = [self._normalize(item) for item in values[field]]
            if not any(
                term and term in value
                for term in normalized_terms for value in normalized_values
            ):
                return False
        modes = scope.get("operating_modes", [])
        if modes:
            return self._normalize(operating_mode) in {
                self._normalize(item) for item in modes
            }
        return True

    def matching_rules(
        self, function: FunctionDefinition, operating_mode: str,
    ) -> tuple[dict[str, Any], ...]:
        """Return only compiled, approved rules matching the Function context."""
        return tuple(
            rule for rule in self.rules
            if self._matches(rule, function, operating_mode)
        )

    def evaluate(
        self,
        function: FunctionDefinition,
        bindings: dict[str, dict[str, Any]],
        operating_mode: str,
    ) -> dict[str, Any]:
        matches = self.matching_rules(function, operating_mode)
        if len(matches) != 1:
            reason = (
                "PENDING_NO_MATCHING_APPROVED_COVERAGE_RULE"
                if not matches else "PENDING_AMBIGUOUS_APPROVED_COVERAGE_RULE"
            )
            return {
                "status": reason,
                "matched_rule_ids": [str(item.get("coverage_rule_id", "")) for item in matches],
                "dimensions": {
                    dimension: {"status": "PENDING", "reason": reason}
                    for dimension in self.dimensions
                },
            }

        rule = matches[0]
        required = set(rule.get("required_dimensions", []))
        optional = set(rule.get("optional_dimensions", []))
        not_applicable = set(rule.get("not_applicable_dimensions", []))
        dimensions: dict[str, dict[str, str]] = {}
        for dimension in self.dimensions:
            binding = bindings.get(dimension, {})
            resolved = binding.get("resolution_status") == "RESOLVED"
            if dimension in not_applicable:
                dimensions[dimension] = {
                    "status": "NOT_APPLICABLE",
                    "reason": "APPROVED_COVERAGE_RULE",
                }
            elif dimension in required:
                dimensions[dimension] = {
                    "status": "RESOLVED" if resolved else "PENDING",
                    "reason": (
                        "" if resolved else "REQUIRED_DIMENSION_MISSING_OR_UNRESOLVED"
                    ),
                }
            elif dimension in optional:
                dimensions[dimension] = {
                    "status": "RESOLVED" if resolved else "OPTIONAL_UNRESOLVED",
                    "reason": "" if resolved else "OPTIONAL_DIMENSION_NOT_BOUND",
                }
            else:
                dimensions[dimension] = {
                    "status": "PENDING",
                    "reason": "DIMENSION_NOT_DECLARED_BY_APPROVED_COVERAGE_RULE",
                }
        return {
            "status": "APPROVED_COVERAGE_RULE_APPLIED",
            "matched_rule_ids": [str(rule["coverage_rule_id"])],
            "dimensions": dimensions,
            "provenance": dict(rule.get("provenance", {})),
            "approval": dict(rule.get("approval", {})),
        }
