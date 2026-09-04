from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from hara_agent.contracts import ScenarioCausalAssessment
from hara_agent.models import ItemDefinitionFacts, ReviewStatus

if TYPE_CHECKING:
    from hara_agent.workflow.state import HARAState


@dataclass(frozen=True)
class ReleaseGateResult:
    ready_for_release: bool
    blockers: tuple[str, ...]
    checks: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ready_for_release": self.ready_for_release,
            "blockers": list(self.blockers),
            "checks": self.checks,
        }


class ReleaseGateValidator:
    """Read-only release validation; it never repairs or computes HARA results."""

    _APPROVED = {ReviewStatus.FINALIZED, ReviewStatus.NOT_APPLICABLE}

    def evaluate(self, state: "HARAState") -> ReleaseGateResult:
        blockers: list[str] = []
        method_ok = self._method_contract_bound(state.method_contract)
        if not method_ok:
            blockers.append("METHOD_CONTRACT_NOT_BOUND")

        project_structural, project_approved = self._project_facts(state)
        if not project_structural:
            blockers.append("PROJECT_FACTS_INVALID")
        elif not project_approved:
            blockers.append("PROJECT_FACTS_PENDING_APPROVAL")

        causal = self._risk_causal_contracts(state)
        if causal["missing_count"]:
            blockers.append("RISK_CAUSAL_ASSESSMENT_MISSING")
        if causal["invalid_count"]:
            blockers.append("RISK_CAUSAL_ASSESSMENT_INVALID")
        if causal["unapproved_count"]:
            blockers.append("SCENARIO_CAUSAL_ASSESSMENT_PENDING_APPROVAL")
        if causal["invalid_exclusion_count"]:
            blockers.append("SCENARIO_EXCLUSION_INVALID")

        risk = self._risk_checks(state)
        if risk["incomplete_field_count"]:
            blockers.append("RISK_ASSESSMENT_INCOMPLETE")
        if risk["unapproved_field_count"]:
            blockers.append("RISK_ASSESSMENT_PENDING_APPROVAL")

        goals = self._safety_goal_checks(state)
        if goals["missing_link_count"]:
            blockers.append("SAFETY_GOAL_LINK_MISSING")
        if goals["invalid_count"]:
            blockers.append("SAFETY_GOAL_INVALID")
        if goals["unapproved_count"]:
            blockers.append("SAFETY_GOAL_PENDING_APPROVAL")

        if state.pending_reviews:
            blockers.append("PENDING_ENGINEERING_REVIEW")
        if state.errors:
            blockers.append("WORKFLOW_ERRORS_PRESENT")
        blockers = list(dict.fromkeys(blockers))
        return ReleaseGateResult(
            ready_for_release=not blockers,
            blockers=tuple(blockers),
            checks={
                "method_contract_bound": method_ok,
                "project_facts_structurally_valid": project_structural,
                "project_facts_approved": project_approved,
                "risk_causal_assessments": causal,
                "risk_assessments": risk,
                "safety_goals": goals,
                "pending_review_count": len(state.pending_reviews),
                "workflow_error_count": len(state.errors),
            },
        )

    @staticmethod
    def _method_contract_bound(value: dict[str, Any]) -> bool:
        return all((
            bool(str(value.get("template_hash", "")).strip()),
            bool(str(value.get("contract_version", "")).strip()),
            value.get("engineering_rules_compiled") is True,
            str(value.get("compile_status", "")) in {"READY", "READY_WITH_WARNINGS"},
        ))

    @staticmethod
    def _project_facts(state: "HARAState") -> tuple[bool, bool]:
        value = state.item_definition.get("typed")
        if not isinstance(value, dict):
            return False, False
        try:
            facts = ItemDefinitionFacts.from_dict(value)
        except (KeyError, TypeError, ValueError):
            return False, False
        template_hash = str(state.method_contract.get("template_hash", ""))
        binding_hashes_match = all(
            item.method_contract_hash == template_hash
            for item in facts.method_risk_fact_bindings
        )
        if not binding_hashes_match:
            return False, False
        approvals = (
            [facts.status]
            + [item.status for item in facts.speed_envelopes]
            + [item.approval for item in facts.risk_facts]
            + [item.approval for item in facts.method_risk_fact_bindings]
        )
        return True, all(item is ReviewStatus.FINALIZED for item in approvals)

    @staticmethod
    def _assessment_index(state: "HARAState") -> dict[tuple[str, str], dict[str, Any]]:
        result: dict[tuple[str, str], dict[str, Any]] = {}
        for value in state.item_definition.get("scenario_assessments", []):
            if not isinstance(value, dict):
                continue
            key = (
                str(value.get("malfunction_id", "")),
                str(value.get("scenario_id", "")),
            )
            if key in result:
                # Duplicate identities are invalid and cannot be silently overwritten.
                result[key] = {}
            else:
                result[key] = value
        return result

    def _risk_causal_contracts(self, state: "HARAState") -> dict[str, int]:
        assessments = self._assessment_index(state)
        risk_keys = {
            (risk.malfunction_id, risk.scenario_id) for risk in state.risk_results
        }
        missing = 0
        invalid = 0
        validated = 0
        unapproved = 0
        invalid_exclusions = 0
        for risk in state.risk_results:
            key = (risk.malfunction_id, risk.scenario_id)
            value = assessments.get(key)
            if not value or not isinstance(value.get("causal_assessment"), dict):
                missing += 1
                continue
            try:
                causal = ScenarioCausalAssessment.from_dict(value["causal_assessment"])
            except (KeyError, TypeError, ValueError):
                invalid += 1
                continue
            compatible = all((
                causal.is_validated,
                causal.scenario_id == risk.scenario_id,
                causal.hazardous_event == risk.hazardous_event,
                causal.potential_harm == risk.potential_harm,
            ))
            if compatible:
                validated += 1
                if (
                    causal.review_status is not ReviewStatus.FINALIZED
                    or any(
                        binding.status is not ReviewStatus.FINALIZED
                        for binding in causal.evidence_bindings
                    )
                ):
                    unapproved += 1
            else:
                invalid += 1
        for key, value in assessments.items():
            if key in risk_keys or not value:
                continue
            payload = value.get("causal_assessment")
            if not isinstance(payload, dict):
                invalid_exclusions += 1
                continue
            try:
                causal = ScenarioCausalAssessment.from_dict(payload)
            except (KeyError, TypeError, ValueError):
                invalid_exclusions += 1
                continue
            if causal.status.value not in {"VALIDATED", "CAUSAL_GAP"}:
                invalid_exclusions += 1
            if causal.review_status is not ReviewStatus.FINALIZED:
                unapproved += 1
        return {
            "required_count": len(state.risk_results),
            "validated_count": validated,
            "missing_count": missing,
            "invalid_count": invalid,
            "unapproved_count": unapproved,
            "invalid_exclusion_count": invalid_exclusions,
        }

    def _risk_checks(self, state: "HARAState") -> dict[str, int]:
        incomplete = 0
        unapproved = 0
        for risk in state.risk_results:
            for field in ("severity", "exposure", "controllability", "asil"):
                evidence = getattr(risk, field)
                if evidence.value in {None, ""}:
                    incomplete += 1
                if evidence.status not in self._APPROVED:
                    unapproved += 1
            if not str(risk.potential_harm).strip():
                incomplete += 1
        return {
            "assessment_count": len(state.risk_results),
            "incomplete_field_count": incomplete,
            "unapproved_field_count": unapproved,
        }

    def _safety_goal_checks(self, state: "HARAState") -> dict[str, int]:
        goal_by_id = {item.sg_id: item for item in state.safety_goals}
        missing_links = 0
        for risk in state.risk_results:
            if risk.asil.value in {"A", "B", "C", "D"}:
                if not risk.safety_goal_id or risk.safety_goal_id not in goal_by_id:
                    missing_links += 1
        invalid = sum(
            not all((goal.text.strip(), goal.safe_state.strip(), goal.max_asil.strip()))
            for goal in state.safety_goals
        )
        unapproved = sum(
            goal.status not in self._APPROVED for goal in state.safety_goals
        )
        return {
            "goal_count": len(state.safety_goals),
            "missing_link_count": missing_links,
            "invalid_count": invalid,
            "unapproved_count": unapproved,
        }
