from hara_agent.contracts import (
    NormativeStrength, ScenarioConstraintDisposition,
    ScenarioConstraintPredicate, ScenarioConstraintRule, SourceRef,
)
from hara_agent.services.analysis import (
    ScenarioConstraintExecutor, ScenarioConstraintStatus,
)


def _source(name: str) -> SourceRef:
    return SourceRef.create(
        workbook="method.xlsx", template_hash="hash", sheet="Scenario Rules",
        range=name, raw_text=name,
    )


def _rule(rule_id: str, disposition: ScenarioConstraintDisposition):
    return ScenarioConstraintRule(
        rule_id=rule_id,
        predicates=(
            ScenarioConstraintPredicate("ROAD", ("HIGHWAY",)),
            ScenarioConstraintPredicate("OBJECT", ("PEDESTRIAN",)),
        ),
        disposition=disposition,
        reason=rule_id,
        normative_strength=NormativeStrength.NORMATIVE,
        source_ref=_source(rule_id),
    )


def test_rare_but_feasible_combination_is_not_deleted():
    result = ScenarioConstraintExecutor().evaluate(
        {"ROAD": "HIGHWAY", "OBJECT": "PEDESTRIAN"},
        (_rule("RARE-1", ScenarioConstraintDisposition.RARE_BUT_FEASIBLE),),
    )

    assert result.status is ScenarioConstraintStatus.KEEP_RARE
    assert result.matched_rule_ids == ("RARE-1",)


def test_only_explicit_normative_impossibility_is_deleted():
    result = ScenarioConstraintExecutor().evaluate(
        {"ROAD": "HIGHWAY", "OBJECT": "PEDESTRIAN"},
        (_rule("IMP-1", ScenarioConstraintDisposition.PHYSICALLY_IMPOSSIBLE),),
    )

    assert result.status is ScenarioConstraintStatus.DROP


def test_conflicting_normative_constraints_fail_closed():
    result = ScenarioConstraintExecutor().evaluate(
        {"ROAD": "HIGHWAY", "OBJECT": "PEDESTRIAN"},
        (
            _rule("IMP-1", ScenarioConstraintDisposition.PHYSICALLY_IMPOSSIBLE),
            _rule("RARE-1", ScenarioConstraintDisposition.RARE_BUT_FEASIBLE),
        ),
    )

    assert result.status is ScenarioConstraintStatus.CONFLICT
