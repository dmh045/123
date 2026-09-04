from __future__ import annotations

from typing import Any

from hara_agent.contracts import CalculationStatus, MethodContract

from .deterministic_risk_executor import (
    ExposureMethodExecutor, SeverityMethodExecutor,
    StructuredControllabilityExecutor,
)
from .risk_calculation_input_service import RiskCalculationInputService


class StructuredRiskScoringService:
    """Capability-driven structured S/E/C execution over MethodContract data."""

    def __init__(self, method: MethodContract):
        if method.structured_risk_method is None:
            raise ValueError("StructuredRiskScoringService requires a structured risk method")
        self.method = method
        self.structured = method.structured_risk_method
        self.inputs = RiskCalculationInputService()
        self.severity = SeverityMethodExecutor()
        self.exposure = ExposureMethodExecutor()
        self.controllability = StructuredControllabilityExecutor()

    def _result(
        self, *, value_key: str, value: str, status: CalculationStatus,
        reason: str, rule_id: str, source_ref, inputs_used: tuple[str, ...],
        **extra: Any,
    ) -> dict[str, Any]:
        return {
            value_key: value,
            "reasoning": reason,
            "engineering_status": (
                "FINALIZED" if status is CalculationStatus.FINALIZED else "PENDING"
            ),
            "calculation_status": status.value,
            "engineering_source_type": "method_contract",
            "engineering_source": self.structured.method_source_hash,
            "engineering_location": f"{source_ref.sheet}!{source_ref.range}",
            "engineering_excerpt": source_ref.raw_text,
            "engineering_rule_id": rule_id,
            "engineering_rule_version": (
                f"{self.method.contract_version}/{self.method.compiler_version}"
            ),
            "engineering_basis": reason,
            "inputs_used": list(inputs_used),
            "missing_fact_types": [],
            "fact_sources": [],
            "_source": "structured_method_contract_executor",
            **extra,
        }

    def score(self, scenario: dict[str, Any], hazard_event: str) -> dict[str, dict[str, Any]]:
        malfunction_id = str(scenario.get("malfunction_id", "UNKNOWN"))
        scenario_id = str(scenario.get("scenario_id", "UNKNOWN"))
        severity_input = self.inputs.severity(
            malfunction_id, scenario_id, scenario,
            self.structured.severity.speed_semantic,
        )
        s = self.severity.lookup(severity_input, self.structured.severity)
        severity = self._result(
            value_key="severity_score", value=s["value"], status=s["status"],
            reason=s["reason"], rule_id=s["rule_id"], source_ref=s["source_ref"],
            inputs_used=s["inputs_used"],
        )
        if s["status"] is CalculationStatus.FINALIZED and s["value"] == "S0":
            source = s["source_ref"]
            exposure = self._result(
                value_key="exposure_score", value="E0",
                status=CalculationStatus.FINALIZED,
                reason="S0 activated the approved risk short-circuit.",
                rule_id="ASIL-ZERO-SHORT-CIRCUIT", source_ref=source,
                inputs_used=("severity_score",), exposure_method="SHORT_CIRCUIT",
            )
            controllability = self._result(
                value_key="controllability_score", value="C0",
                status=CalculationStatus.FINALIZED,
                reason="S0 activated the approved risk short-circuit.",
                rule_id="ASIL-ZERO-SHORT-CIRCUIT", source_ref=source,
                inputs_used=("severity_score",),
            )
            return {"severity": severity, "exposure": exposure, "controllability": controllability}

        e = self.exposure.lookup(scenario, self.structured.exposure)
        exposure = self._result(
            value_key="exposure_score", value=e["value"], status=e["status"],
            reason=e["reason"], rule_id=e["rule_id"], source_ref=e["source_ref"],
            inputs_used=e["inputs_used"], exposure_method=e["domain"],
        )
        c_input = self.inputs.controllability(malfunction_id, scenario_id, scenario)
        c = self.controllability.lookup(c_input, self.structured)
        c_source = self.structured.controllability_profile.source_ref
        for item in self.structured.controllability_overrides:
            if item.rule_id == c.rule_id:
                c_source = item.source_ref
        for item in self.structured.controllability_profile.bands:
            if item.rule_id == c.rule_id:
                c_source = item.source_ref
        controllability = self._result(
            value_key="controllability_score", value=c.value, status=c.status,
            reason=c.reason, rule_id=c.rule_id, source_ref=c_source,
            inputs_used=c.inputs_used, selected_profile_id=c.profile_id,
        )
        return {"severity": severity, "exposure": exposure, "controllability": controllability}
