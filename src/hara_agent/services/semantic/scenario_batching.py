from __future__ import annotations

import json

from hara_agent.contracts import CausalMechanismDefinition
from hara_agent.models import MalfunctionCandidate, ScenarioCandidate

from .scenario_contract import RISK_DIMENSION_VALUES
from .scenario_evidence import (
    CausalBreakpoint, EvidenceBasisType, FactRegistry, build_fact_registry,
)
from .scenario_provider_contract import (
    SCENARIO_V1_ASSESSMENT_CONTRACT, SCENARIO_EVIDENCE_V2_CONTRACT_VERSION,
    mechanism_catalog_payload, v2_registry_prompt_snapshot,
)
from .scenario_provider_schema import scenario_v2_serialization_instructions


DEFAULT_SCENARIO_BATCH_MAX_CHARS = 12000
DEFAULT_SCENARIO_BATCH_MAX_ITEMS = 12


class ScenarioBatchSizeError(ValueError):
    """A scenario request cannot fit within the configured prompt budget."""


class ScenarioAdaptiveBatchError(RuntimeError):
    """An adaptive batch cannot be split further after runtime pressure."""


class ScenarioSchemaContractError(ValueError):
    """A Scenario assessment violates the declared item-level JSON contract."""


class ScenarioFactConsistencyError(ValueError):
    """Authoritative structured Scenario facts are internally inconsistent."""


def _scenario_json(
    malfunction: MalfunctionCandidate,
    scenario: ScenarioCandidate,
    project_registry: FactRegistry | None = None,
    assessment_contract_version: str = SCENARIO_V1_ASSESSMENT_CONTRACT,
) -> str:
    registry = build_fact_registry(malfunction, scenario, project_registry)
    return json.dumps(
        {
            "scenario_id": scenario.scenario_id,
            "source_scenario_id": scenario.source_scenario_id,
            "atomic_variant": scenario.atomic_variant,
            "semantic_fingerprint": scenario.semantic_fingerprint,
            "facts": scenario.facts,
            "fact_registry": (
                v2_registry_prompt_snapshot(registry)
                if assessment_contract_version == SCENARIO_EVIDENCE_V2_CONTRACT_VERSION
                else registry.to_prompt_dict()
            ),
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _prompt_parts(
    malfunction: MalfunctionCandidate,
    assessment_contract_version: str = SCENARIO_V1_ASSESSMENT_CONTRACT,
    mechanism_definitions: tuple[CausalMechanismDefinition, ...] = (),
) -> tuple[str, str]:
    allowed_dimensions = json.dumps(list(RISK_DIMENSION_VALUES), ensure_ascii=False)
    mechanism_section = (
        "AVAILABLE_CAUSAL_MECHANISMS="
        + json.dumps(
            mechanism_catalog_payload(mechanism_definitions),
            ensure_ascii=False, separators=(",", ":"), sort_keys=True,
        )
        + "\n"
        if assessment_contract_version == SCENARIO_EVIDENCE_V2_CONTRACT_VERSION
        else ""
    )
    prefix = (
        f"Malfunction={malfunction.description}\n"
        f"FunctionalEffect={malfunction.functional_effect}\n"
        f"VehicleLevelHazard={malfunction.vehicle_level_hazard}\n"
        f"{mechanism_section}"
        "Scenarios="
    )
    if assessment_contract_version == SCENARIO_EVIDENCE_V2_CONTRACT_VERSION:
        return prefix, scenario_v2_serialization_instructions()
    suffix = (
        "\n这里只评估当前batch列出的Scenario。必须逐一返回，不得遗漏，不得增加未提供Scenario。"
        "返回assessments数组，每项包含scenario_id、physically_feasible、functionally_relevant、"
        "causally_relevant、breakpoint、causal_chain、risk_dimension_changes、rationale、hazardous_event、potential_harm、"
        "confidence、status。保留场景的hazardous_event必须结合当前失效和具体场景，potential_harm"
        "必须描述可能伤害；不得复制其他子系统模板。"
        "risk_dimension_changes必须为JSON object array；每项包含dimension、evidence_refs、reason。"
        "dimension必须严格从以下canonical allowed values选择："
        f"{allowed_dimensions}。无变化返回[]；"
        f"causal_chain包含m_to_b、b_to_i、i_to_h、h_to_harm；basis_type只能是"
        f"{json.dumps([item.value for item in EvidenceBasisType])}；breakpoint只能是"
        f"{json.dumps([item.value for item in CausalBreakpoint])}。"
        "evidence_refs只能逐字引用当前Scenario对象内fact_registry已给出的key；禁止自造ref。"
        "禁止使用缩写或别名（例如S/E/C），除非该字符串本身位于上述canonical allowed values中。"
        "confidence必须为0.0到1.0范围内的JSON number；禁止使用high、medium、low等文字等级，"
        "禁止字符串数字、百分数、null或缺失。"
    )
    return prefix, suffix


def build_scenario_user_prompt(
    malfunction: MalfunctionCandidate,
    scenarios: list[ScenarioCandidate],
    project_registry: FactRegistry | None = None,
    assessment_contract_version: str = SCENARIO_V1_ASSESSMENT_CONTRACT,
    mechanism_definitions: tuple[CausalMechanismDefinition, ...] = (),
) -> str:
    prefix, suffix = _prompt_parts(
        malfunction, assessment_contract_version, mechanism_definitions
    )
    payload = "[" + ",".join(
        _scenario_json(
            malfunction, item, project_registry, assessment_contract_version
        ) for item in scenarios
    ) + "]"
    return prefix + payload + suffix


def estimate_scenario_prompt_chars(
    malfunction: MalfunctionCandidate,
    scenarios: list[ScenarioCandidate],
    *,
    system_prompt: str,
    project_registry: FactRegistry | None = None,
    assessment_contract_version: str = SCENARIO_V1_ASSESSMENT_CONTRACT,
    mechanism_definitions: tuple[CausalMechanismDefinition, ...] = (),
) -> int:
    return len(system_prompt) + len(build_scenario_user_prompt(
        malfunction, scenarios, project_registry,
        assessment_contract_version, mechanism_definitions,
    ))


def build_scenario_batches(
    malfunction: MalfunctionCandidate,
    scenarios: list[ScenarioCandidate],
    *,
    max_chars: int,
    max_items: int | None = None,
    system_prompt: str = "",
    project_registry: FactRegistry | None = None,
    assessment_contract_version: str = SCENARIO_V1_ASSESSMENT_CONTRACT,
    mechanism_definitions: tuple[CausalMechanismDefinition, ...] = (),
) -> list[list[ScenarioCandidate]]:
    """Stable prompt-budget batching; every input appears exactly once."""
    if max_chars <= 0:
        raise ValueError("scenario batch max_chars必须大于0")
    if max_items is not None and max_items <= 0:
        raise ValueError("scenario batch max_items必须大于0")
    prefix, suffix = _prompt_parts(
        malfunction, assessment_contract_version, mechanism_definitions
    )
    fixed_chars = len(system_prompt) + len(prefix) + len(suffix) + 2
    batches: list[list[ScenarioCandidate]] = []
    current: list[ScenarioCandidate] = []
    current_payload_chars = 0
    for scenario in scenarios:
        serialized_chars = len(_scenario_json(
            malfunction, scenario, project_registry, assessment_contract_version
        ))
        single_chars = fixed_chars + serialized_chars
        if single_chars > max_chars:
            raise ScenarioBatchSizeError(
                "单个Scenario超过请求预算: "
                f"malfunction_id={malfunction.malfunction_id} "
                f"scenario_id={scenario.scenario_id} "
                f"estimated_chars={single_chars} max_chars={max_chars}"
            )
        next_payload_chars = current_payload_chars + serialized_chars + (1 if current else 0)
        exceeds_chars = fixed_chars + next_payload_chars > max_chars
        exceeds_items = max_items is not None and len(current) >= max_items
        if current and (exceeds_chars or exceeds_items):
            batches.append(current)
            current = []
            current_payload_chars = 0
        current.append(scenario)
        current_payload_chars += serialized_chars + (1 if len(current) > 1 else 0)
    if current:
        batches.append(current)
    return batches
