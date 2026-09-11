"""Provider-free assembly shared by online orchestration and offline scoring."""

from dataclasses import dataclass

from hara_agent.contracts import MethodContract
from .method_rule_service import MethodRuleScoringService
from .asil_service import MethodContractASILService
from .method_risk_fact_service import MethodRiskFactBindingService


@dataclass(frozen=True)
class RiskScoringServices:
    scoring: MethodRuleScoringService
    asil: MethodContractASILService
    binding: MethodRiskFactBindingService

    @classmethod
    def from_method(cls, method: MethodContract) -> "RiskScoringServices":
        return cls(MethodRuleScoringService(method), MethodContractASILService(method),
                   MethodRiskFactBindingService(method))
