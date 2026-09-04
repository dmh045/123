from __future__ import annotations

from collections.abc import Sequence

from hara_agent.contracts import (
    CalculationStatus, ControllabilityAssessmentInput, ControllabilityBand,
    ControllabilityJudgement, ControllabilityProfile, ExposureAssessment,
    ExposureAtom, ExposureMethod, ExposureMethodDomain,
    ExposureCombinationRule, ExposureCombinationStep,
    ExposureDimensionAssessment, SeverityAssessmentInput, SeverityMethod,
    StructuredRiskMethod,
)
from hara_agent.models import ReviewStatus


class ExposureCombinationExecutor:
    """Execute an explicit, ordered, template-sourced E combination plan."""

    @staticmethod
    def _level(item: ExposureDimensionAssessment) -> str:
        if item.selected_domain is ExposureMethodDomain.TIME:
            return item.duration_level
        if item.selected_domain is ExposureMethodDomain.FREQUENCY:
            return item.frequency_level
        return ""

    @staticmethod
    def combine_policy(
        levels: Sequence[str], *, dependent: bool, policy,
    ) -> str:
        """Apply the compiled MethodContract aggregation policy.

        The policy is supplied by the method source; this helper deliberately
        contains no FUSA levels or matrix values of its own.
        """
        values = [int(value[1:]) for value in levels]
        if all(value == int(policy.all_highest_operand[1:]) for value in values):
            return policy.all_highest_result
        if set(policy.mixed_high_operands).issubset(set(levels)):
            return policy.mixed_high_result
        if min(values) != max(values):
            if policy.mixed_strategy != "MINIMUM":
                raise ValueError(f"Unsupported Exposure mixed strategy: {policy.mixed_strategy}")
            return f"E{min(values)}"
        decrement = policy.dependent_decrement if dependent else policy.independent_decrement
        return f"E{max(policy.minimum_level, values[0] - decrement)}"

    def combine(
        self,
        scenario_id: str,
        dimensions: Sequence[ExposureDimensionAssessment],
        steps: Sequence[ExposureCombinationStep],
        rules: Sequence[ExposureCombinationRule],
    ) -> ExposureAssessment:
        levels = {item.dimension: self._level(item) for item in dimensions}
        if len(levels) != len(dimensions):
            return ExposureAssessment(
                scenario_id=scenario_id, dimensions=tuple(dimensions),
                status=CalculationStatus.PENDING_METHOD_SEMANTICS,
                reason="Exposure dimension identities must be unique.",
            )
        if not levels or any(not value for value in levels.values()):
            return ExposureAssessment(
                scenario_id=scenario_id, dimensions=tuple(dimensions),
                status=CalculationStatus.PENDING_INPUT,
                reason="One or more dimension-level E values or Z/F selections are missing.",
            )
        if len(levels) > 1 and not steps:
            return ExposureAssessment(
                scenario_id=scenario_id, dimensions=tuple(dimensions),
                status=CalculationStatus.PENDING_METHOD_SEMANTICS,
                reason="Multiple Exposure dimensions require an explicit ordered plan.",
            )
        applied: list[str] = []
        for step in steps:
            left = levels.get(step.left_dimension, "")
            right = levels.get(step.right_dimension, "")
            if not left or not right:
                return ExposureAssessment(
                    scenario_id=scenario_id, dimensions=tuple(dimensions),
                    combination_rule_ids=tuple(applied),
                    status=CalculationStatus.PENDING_METHOD_SEMANTICS,
                    reason="The ordered Exposure combination plan references an unavailable operand.",
                )
            matches = [
                rule for rule in rules
                if rule.review_status is ReviewStatus.FINALIZED
                and rule.relation is step.relation
                and {rule.left_level, rule.right_level} == {left, right}
                and (not rule.left_dimension or rule.left_dimension == step.left_dimension)
                and (not rule.right_dimension or rule.right_dimension == step.right_dimension)
            ]
            if len(matches) != 1:
                return ExposureAssessment(
                    scenario_id=scenario_id, dimensions=tuple(dimensions),
                    combination_rule_ids=tuple(applied),
                    status=CalculationStatus.PENDING_METHOD_SEMANTICS,
                    reason=(
                        "The active MethodContract does not provide exactly one approved "
                        "Exposure rule for an ordered combination step."
                    ),
                )
            rule = matches[0]
            levels[step.output_dimension] = rule.result_level
            applied.append(rule.rule_id)
        final_dimension = steps[-1].output_dimension if steps else next(iter(levels))
        value = levels.get(final_dimension, "")
        return ExposureAssessment(
            scenario_id=scenario_id, dimensions=tuple(dimensions),
            combination_rule_ids=tuple(applied), value=value,
            status=CalculationStatus.FINALIZED,
            reason="Executed the approved ordered Exposure combination plan.",
        )


class ControllabilityProfileExecutor:
    """Perform deterministic TTC lookup against one explicitly selected profile."""

    @staticmethod
    def _matches(band: ControllabilityBand, ttc_s: float) -> bool:
        if band.lower_ttc_s is not None:
            if ttc_s < band.lower_ttc_s:
                return False
            if ttc_s == band.lower_ttc_s and not band.lower_inclusive:
                return False
        if band.upper_ttc_s is not None:
            if ttc_s > band.upper_ttc_s:
                return False
            if ttc_s == band.upper_ttc_s and not band.upper_inclusive:
                return False
        return True

    def lookup(
        self,
        assessment: ControllabilityAssessmentInput,
        profile: ControllabilityProfile | None,
    ) -> ControllabilityJudgement:
        if profile is None or profile.review_status is not ReviewStatus.FINALIZED:
            return ControllabilityJudgement(
                value="", profile_id=profile.profile_id if profile else "",
                rule_id="", inputs_used=assessment.inputs_used,
                status=CalculationStatus.PENDING_METHOD_SEMANTICS,
                reason="No approved Controllability profile was explicitly selected.",
            )
        if assessment.ttc_s is None:
            return ControllabilityJudgement(
                value="", profile_id=profile.profile_id, rule_id="",
                inputs_used=assessment.inputs_used,
                status=CalculationStatus.PENDING_INPUT,
                reason="The selected profile requires a source-grounded TTC input.",
            )
        matches = [
            band for band in profile.bands
            if band.review_status is ReviewStatus.FINALIZED
            and self._matches(band, assessment.ttc_s)
        ]
        if len(matches) != 1:
            return ControllabilityJudgement(
                value="", profile_id=profile.profile_id, rule_id="",
                inputs_used=("ttc_s",),
                status=CalculationStatus.PENDING_METHOD_SEMANTICS,
                reason="The selected profile has a TTC gap or overlap at the supplied input.",
            )
        band = matches[0]
        return ControllabilityJudgement(
            value=band.result, profile_id=profile.profile_id,
            rule_id=band.rule_id, inputs_used=("ttc_s",),
            status=CalculationStatus.FINALIZED,
            reason="Deterministic TTC lookup matched exactly one approved profile band.",
        )


class SeverityMethodExecutor:
    """Evaluate a structured severity table without textual rule inference."""

    @staticmethod
    def _matches_band(band, value: float) -> bool:
        if band.lower_kph is not None and (
            value < band.lower_kph
            or (value == band.lower_kph and not band.lower_inclusive)
        ):
            return False
        if band.upper_kph is not None and (
            value > band.upper_kph
            or (value == band.upper_kph and not band.upper_inclusive)
        ):
            return False
        return True

    def lookup(self, assessment: SeverityAssessmentInput, method: SeverityMethod) -> dict:
        consequence = assessment.consequence
        value_by_semantic = {
            "EGO_SPEED": consequence.ego_speed_kph,
            "RELATIVE_SPEED": consequence.relative_speed_kph,
            "IMPACT_SPEED": consequence.impact_speed_kph,
            "DELTA_V": consequence.delta_v_kph,
        }
        semantic = method.speed_semantic.value
        value = value_by_semantic.get(semantic)
        if value is None:
            return {"value": "", "status": CalculationStatus.PENDING_INPUT,
                    "reason": f"Severity requires the configured {semantic} input.",
                    "rule_id": "", "source_ref": method.source_ref, "inputs_used": ()}
        group = dict(method.road_user_groups).get(
            consequence.road_user_type.strip().upper(), ""
        )
        if not group:
            return {"value": "", "status": CalculationStatus.PENDING_INPUT,
                    "reason": "Severity requires a canonical road-user type.",
                    "rule_id": "", "source_ref": method.source_ref,
                    "inputs_used": (semantic.casefold(),)}
        collision_type = dict(method.collision_types).get(
            consequence.collision_type.strip().upper(), ""
        )
        if group != "vehicle" and collision_type == "any":
            collision_type = "any"
        if not collision_type:
            return {"value": "", "status": CalculationStatus.PENDING_INPUT,
                    "reason": "Vehicle severity lookup requires a canonical collision type.",
                    "rule_id": "", "source_ref": method.source_ref,
                    "inputs_used": (semantic.casefold(), "road_user_type")}
        matches = [
            band for band in method.bands
            if band.collision_group == group
            and band.collision_type == collision_type
            and self._matches_band(band, value)
        ]
        if len(matches) != 1:
            return {"value": "", "status": CalculationStatus.PENDING_METHOD_SEMANTICS,
                    "reason": "Severity table has a range gap or overlap at the supplied input.",
                    "rule_id": "", "source_ref": method.source_ref,
                    "inputs_used": (semantic.casefold(), "road_user_type", "collision_type")}
        band = matches[0]
        return {"value": band.result, "status": CalculationStatus.FINALIZED,
                "reason": "Deterministic structured severity lookup matched one approved band.",
                "rule_id": band.rule_id, "source_ref": band.source_ref,
                "inputs_used": (semantic.casefold(), "road_user_type", "collision_type")}


class ExposureMethodExecutor:
    @staticmethod
    def _level(atom: ExposureAtom, domain: ExposureMethodDomain) -> str:
        return atom.duration_level if domain is ExposureMethodDomain.TIME else atom.frequency_level

    @staticmethod
    def _aggregate(levels: list[str], dependent: bool, policy) -> str:
        return ExposureCombinationExecutor.combine_policy(
            levels, dependent=dependent, policy=policy,
        )

    def lookup(self, scenario: dict, method: ExposureMethod) -> dict:
        category = str(scenario.get("component_category", "")).strip()
        matches = [rule for rule in method.domain_rules if category in rule.component_categories]
        if len(matches) != 1:
            return {"value": "", "status": CalculationStatus.PENDING_METHOD_SEMANTICS,
                    "reason": "Component category does not resolve to exactly one approved Z/F rule.",
                    "rule_id": "", "source_ref": method.source_refs[0],
                    "inputs_used": ("component_category",), "domain": ""}
        domain = matches[0].domain
        raw_ids = scenario.get("scenario_atom_ids", ())
        atom_ids = tuple(str(value) for value in raw_ids) if isinstance(raw_ids, (list, tuple)) else ()
        by_id = {atom.atom_id: atom for atom in method.atoms}
        atoms = [by_id[value] for value in atom_ids if value in by_id]
        if not atoms:
            return {"value": "", "status": CalculationStatus.PENDING_INPUT,
                    "reason": "Exposure requires scenario atom IDs bound to the baseline catalog.",
                    "rule_id": matches[0].rule_id, "source_ref": matches[0].source_ref,
                    "inputs_used": ("component_category", "scenario_atom_ids"),
                    "domain": domain.value}
        levels = [self._level(atom, domain) for atom in atoms]
        selected = domain
        if not any(levels):
            selected = (
                ExposureMethodDomain.FREQUENCY
                if domain is ExposureMethodDomain.TIME else ExposureMethodDomain.TIME
            )
            levels = [self._level(atom, selected) for atom in atoms]
        usable = [value for value in levels if value in {"E0", "E1", "E2", "E3", "E4"}]
        if not usable:
            return {"value": "", "status": CalculationStatus.PENDING_INPUT,
                    "reason": "Selected Z/F domain and scenario-level fallback both have no usable atom E values.",
                    "rule_id": matches[0].rule_id, "source_ref": matches[0].source_ref,
                    "inputs_used": ("component_category", "scenario_atom_ids"),
                    "domain": selected.value}
        dimensions = {dimension for atom in atoms for dimension in atom.dimensions}
        dependent = any(
            left in dimensions and right in dimensions
            for left, right in method.strong_couplings
        )
        return {"value": self._aggregate(usable, dependent, method.aggregation_policy),
                "status": CalculationStatus.FINALIZED,
                "reason": "Executed approved Z/F selection, atom lookup and dependency aggregation.",
                "rule_id": matches[0].rule_id,
                "source_ref": matches[0].source_ref,
                "inputs_used": ("component_category", "scenario_atom_ids"),
                "domain": selected.value}


class StructuredControllabilityExecutor:
    def __init__(self):
        self.profile_executor = ControllabilityProfileExecutor()

    @staticmethod
    def _override_state(rule, scenario: dict) -> bool | None:
        def value(condition):
            actual = scenario.get(condition.field)
            return None if not isinstance(actual, bool) else actual is condition.expected
        if rule.all_of:
            values = [value(item) for item in rule.all_of]
            if False in values:
                return False
            return True if all(item is True for item in values) else None
        values = [value(item) for item in rule.any_of]
        if True in values:
            return True
        return False if all(item is False for item in values) else None

    def lookup(self, assessment: ControllabilityAssessmentInput, method: StructuredRiskMethod) -> ControllabilityJudgement:
        scenario = assessment.to_dict()
        for rule in sorted(method.controllability_overrides, key=lambda item: item.priority):
            state = self._override_state(rule, scenario)
            if state is True:
                fields = tuple(item.field for item in (*rule.all_of, *rule.any_of))
                return ControllabilityJudgement(
                    value=rule.result, profile_id=method.controllability_profile.profile_id,
                    rule_id=rule.rule_id, inputs_used=fields,
                    status=CalculationStatus.FINALIZED,
                    reason="A higher-priority approved controllability override matched.",
                )
            if state is None:
                return ControllabilityJudgement(
                    value="", profile_id=method.controllability_profile.profile_id,
                    rule_id=rule.rule_id,
                    inputs_used=tuple(item.field for item in (*rule.all_of, *rule.any_of)),
                    status=CalculationStatus.PENDING_INPUT,
                    reason="A higher-priority controllability override cannot be resolved from available inputs.",
                )
        return self.profile_executor.lookup(assessment, method.controllability_profile)
