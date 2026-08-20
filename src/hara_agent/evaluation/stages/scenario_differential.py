from __future__ import annotations

from collections.abc import Callable
from typing import Any


class ScenarioContractDifferentialHarness:
    """Compare deterministic v1/v2 contract calls without invoking a Provider."""

    def evaluate(
        self,
        v1_validation: Callable[[], Any],
        v2_validation: Callable[[], Any],
        *,
        fixture_id: str,
        v1_provider_schema_version: str = "ScenarioFeasibilityAssessmentList",
        v1_assessment_contract_version: str = "scenario-evidence-v1",
        v2_provider_schema_version: str = "ScenarioFeasibilityAssessmentV2List",
        v2_assessment_contract_version: str = "scenario-evidence-v2",
    ) -> dict[str, Any]:
        v1_valid, v1_codes = self._run(v1_validation)
        v2_valid, v2_codes = self._run(v2_validation)
        return {
            "stage": "scenario_contract_differential",
            "classification": "EVALUATION_ONLY",
            "fixture_id": fixture_id,
            "contracts": {
                "v1": {
                    "provider_schema_version": v1_provider_schema_version,
                    "assessment_contract_version": v1_assessment_contract_version,
                },
                "v2": {
                    "provider_schema_version": v2_provider_schema_version,
                    "assessment_contract_version": v2_assessment_contract_version,
                },
            },
            "v1_valid": v1_valid,
            "v2_valid": v2_valid,
            "v1_error_codes": v1_codes,
            "v2_error_codes": v2_codes,
        }

    @staticmethod
    def _run(validation: Callable[[], Any]) -> tuple[bool, list[str]]:
        try:
            validation()
        except Exception as error:
            code = getattr(error, "code", type(error).__name__)
            return False, [getattr(code, "value", str(code))]
        return True, []
