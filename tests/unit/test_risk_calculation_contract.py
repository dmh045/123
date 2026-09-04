import pytest

from hara_agent.contracts import (
    CalculationStatus, ControllabilityAssessmentInput, ControllabilityBand,
    ControllabilityProfile, ExposureAssessment, ExposureCombinationRelation,
    ExposureCombinationRule, ExposureCombinationStep,
    ExposureDimensionAssessment, ExposureMethodDomain, PhysicalConsequence,
    RiskFactResolution, RiskFactResolutionStatus, SeverityAssessmentInput,
    SourceRef, SpeedSemantic,
)
from hara_agent.models import ReviewStatus
from hara_agent.services.analysis import (
    ControllabilityProfileExecutor, ExposureCombinationExecutor,
)


def test_risk_calculation_inputs_preserve_unresolved_engineering_semantics():
    severity = SeverityAssessmentInput(
        malfunction_id="MF-1", scenario_id="SCN-1",
        consequence=PhysicalConsequence(collision_type="FRONTAL"),
        speed_semantic=SpeedSemantic.UNRESOLVED,
        status=CalculationStatus.PENDING_METHOD_SEMANTICS,
        reason="Template variable v has not been approved as relative or impact speed.",
    )
    controllability = ControllabilityAssessmentInput(
        malfunction_id="MF-1", scenario_id="SCN-1", ttc_s=1.8,
        reaction_time_reference_s=1.3,
        driver_in_vehicle=False, has_remote_app=True,
        function_type="remote_parking",
        status=CalculationStatus.PENDING_METHOD_SEMANTICS,
        reason="TTC-to-C thresholds and modifiers are not compiled.",
    )
    exposure = ExposureAssessment(
        scenario_id="SCN-1", dimensions=(),
        status=CalculationStatus.PENDING_METHOD_SEMANTICS,
        reason="VDA catalog rows remain reference data without combination rules.",
    )

    assert severity.speed_semantic is SpeedSemantic.UNRESOLVED
    assert controllability.ttc_s == 1.8
    assert controllability.to_dict()["driver_in_vehicle"] is False
    assert exposure.value == ""


def test_found_risk_fact_resolution_requires_fact_identity():
    with pytest.raises(ValueError, match="requires fact_id"):
        RiskFactResolution(
            malfunction_id="MF-1", scenario_id="SCN-1",
            fact_type="COLLISION_TYPE",
            status=RiskFactResolutionStatus.FOUND,
            reason="found",
        )


def _method_source(name: str) -> SourceRef:
    return SourceRef.create(
        workbook="method.xlsx", template_hash="hash", sheet="Rules",
        range=name, raw_text=name,
    )


def test_exposure_executor_uses_only_explicit_ordered_approved_rules():
    dimensions = (
        ExposureDimensionAssessment(
            "ROAD", "parking", (), duration_level="E4",
            selected_domain=ExposureMethodDomain.TIME,
            status=CalculationStatus.FINALIZED, reason="bound",
        ),
        ExposureDimensionAssessment(
            "SPEED", "low", (), duration_level="E4",
            selected_domain=ExposureMethodDomain.TIME,
            status=CalculationStatus.FINALIZED, reason="bound",
        ),
    )
    step = ExposureCombinationStep(
        "ROAD", "SPEED", "ROAD_SPEED", ExposureCombinationRelation.DEPENDENT,
    )
    rule = ExposureCombinationRule(
        "E-DEP-E4-E4", ExposureCombinationRelation.DEPENDENT,
        "E4", "E4", "E4", _method_source("E1"),
        review_status=ReviewStatus.FINALIZED,
    )

    result = ExposureCombinationExecutor().combine(
        "SCN-1", dimensions, (step,), (rule,),
    )

    assert result.status is CalculationStatus.FINALIZED
    assert result.value == "E4"
    assert result.combination_rule_ids == ("E-DEP-E4-E4",)


def test_controllability_executor_returns_rule_bound_profile_lookup():
    source = _method_source("C1")
    profile = ControllabilityProfile(
        "project-profile",
        (
            ControllabilityBand(
                "C-TTC-LOW", "C3", source, lower_ttc_s=0, upper_ttc_s=3,
                review_status=ReviewStatus.FINALIZED,
            ),
            ControllabilityBand(
                "C-TTC-MID", "C2", source, lower_ttc_s=3, upper_ttc_s=4,
                review_status=ReviewStatus.FINALIZED,
            ),
        ),
        source,
        review_status=ReviewStatus.FINALIZED,
    )
    assessment = ControllabilityAssessmentInput(
        "MF-1", "SCN-1", ttc_s=3.0, inputs_used=("ttc_s",),
        reason="derived TTC",
    )

    judgement = ControllabilityProfileExecutor().lookup(assessment, profile)

    assert judgement.status is CalculationStatus.FINALIZED
    assert judgement.value == "C2"
    assert judgement.profile_id == "project-profile"
    assert judgement.rule_id == "C-TTC-MID"
    assert judgement.inputs_used == ("ttc_s",)


def test_controllability_boolean_router_inputs_reject_string_coercion():
    with pytest.raises(ValueError, match="driver_in_vehicle must be boolean"):
        ControllabilityAssessmentInput(
            "MF-1", "SCN-1", driver_in_vehicle="false",  # type: ignore[arg-type]
            reason="invalid input",
        )
