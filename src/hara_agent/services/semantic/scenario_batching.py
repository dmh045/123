from __future__ import annotations

import json

from hara_agent.models import MalfunctionCandidate, ScenarioCandidate

from .scenario_contract import RISK_DIMENSION_VALUES
from .scenario_evidence import (
    CausalBreakpoint, CausalEvidenceSelection, CausalEvidenceSelector,
    DEFAULT_CAUSAL_EVIDENCE_BUDGET, EvidenceBasisType, FactRegistry,
    build_fact_registry,
)


DEFAULT_SCENARIO_BATCH_MAX_CHARS = 12000
DEFAULT_SCENARIO_BATCH_MAX_ITEMS = 12


class ScenarioBatchSizeError(ValueError):
    """A scenario request cannot fit within the configured prompt budget."""


class ScenarioAdaptiveBatchError(RuntimeError):
    """An adaptive batch cannot be split further after runtime pressure."""


class ScenarioSchemaContractError(ValueError):
    """A Scenario assessment violates the declared item-level JSON contract."""


class ScenarioCoverageContractError(ValueError):
    """A Scenario response omits or adds identities relative to its request."""


class ScenarioFactConsistencyError(ValueError):
    """Authoritative structured Scenario facts are internally inconsistent."""


def _scenario_json(
    malfunction: MalfunctionCandidate,
    scenario: ScenarioCandidate,
    project_registry: FactRegistry | None = None,
    causal_evidence_budget: int = DEFAULT_CAUSAL_EVIDENCE_BUDGET,
) -> str:
    registry = build_fact_registry(malfunction, scenario, project_registry)
    selection = CausalEvidenceSelector(
        max_evidence=causal_evidence_budget,
    ).select(registry)
    selected_facts = {}
    for ref in selection.selected_refs:
        if ref.startswith("SCN."):
            key = ref[4:]
        elif ref.startswith("DERIVED."):
            key = ref[8:]
        else:
            continue
        if key in scenario.facts and scenario.facts[key] not in (None, ""):
            selected_facts[key] = scenario.facts[key]
    return json.dumps(
        {
            "scenario_id": scenario.scenario_id,
            "source_scenario_id": scenario.source_scenario_id,
            "atomic_variant": scenario.atomic_variant,
            "semantic_fingerprint": scenario.semantic_fingerprint,
            # Compatibility field: this is only the selected causal subset,
            # never the complete Scenario facts map.
            "facts": selected_facts,
            "causal_evidence_view": selection.compact_view,
            **(
                {
                    "method_template_context": scenario.context_resolution[
                        "malfunction_template_context"
                    ]
                }
                if isinstance(scenario.context_resolution.get(
                    "malfunction_template_context"
                ), dict) else {}
            ),
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _prompt_parts(
    malfunction: MalfunctionCandidate,
) -> tuple[str, str]:
    allowed_dimensions = json.dumps(list(RISK_DIMENSION_VALUES), ensure_ascii=False)
    prefix = (
        f"Malfunction={malfunction.description}\n"
        f"FunctionalEffect={malfunction.functional_effect}\n"
        f"VehicleLevelHazard={malfunction.vehicle_level_hazard}\n"
        "Scenarios="
    )
    suffix = (
        "The causal_evidence_view is a compact selected subset of the complete EvidenceRegistry. "
        "Use only exact evidence_ref keys shown in that view; full provenance, source excerpts, "
        "and downstream risk-method metadata are intentionally omitted from this prompt. "
        "When method_template_context is present, it is qualified METHOD_TEMPLATE Scenario context "
        "only: it is not a direct fact, is not registered in causal_evidence_view, and MUST NOT be "
        "cited as evidence for any causal hop or risk-dimension change. Do not convert it into a "
        "new Scenario fact or combine multiple template options into one atomic world. "
        "For m_to_b, finalized MF.description and MF.functional_effect are mandatory "
        "DIRECT_FACT anchors for the malfunction's defined behavior/effect when present. "
        "Do not treat missing Scenario trigger or applicability context as an M_TO_B failure; "
        "that context may affect applicability or the downstream B_TO_I transition. "
        "Never cite MF.vehicle_level_hazard as the m_to_b anchor. "
        "\n这里只评估当前batch列出的Scenario。必须逐一返回，不得遗漏，不得增加未提供Scenario。"
        "返回assessments数组，每项包含scenario_id、physically_feasible、functionally_relevant、"
        "causally_relevant、breakpoint、causal_chain、risk_dimension_changes、rationale、hazardous_event、"
        "confidence、status。保留场景的hazardous_event必须结合当前失效和具体场景；Potential Harm"
        "由下游确定性 MethodContract 计算，不在此阶段生成。不得复制其他子系统模板。"
        "risk_dimension_changes必须为JSON object array；每项包含dimension、evidence_refs、reason。"
        "dimension必须严格从以下canonical allowed values选择："
        f"{allowed_dimensions}。无变化返回[]；"
        f"causal_chain只包含m_to_b、b_to_i、i_to_h；后续潜在伤害由下游确定性计算；basis_type只能是"
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
    causal_evidence_budget: int = DEFAULT_CAUSAL_EVIDENCE_BUDGET,
) -> str:
    prefix, suffix = _prompt_parts(malfunction)
    payload = "[" + ",".join(
        _scenario_json(
            malfunction, item, project_registry, causal_evidence_budget,
        ) for item in scenarios
    ) + "]"
    return prefix + payload + suffix


def estimate_scenario_prompt_chars(
    malfunction: MalfunctionCandidate,
    scenarios: list[ScenarioCandidate],
    *,
    system_prompt: str,
    project_registry: FactRegistry | None = None,
    causal_evidence_budget: int = DEFAULT_CAUSAL_EVIDENCE_BUDGET,
) -> int:
    return len(system_prompt) + len(build_scenario_user_prompt(
        malfunction, scenarios, project_registry, causal_evidence_budget,
    ))


def build_scenario_batches(
    malfunction: MalfunctionCandidate,
    scenarios: list[ScenarioCandidate],
    *,
    max_chars: int,
    max_items: int | None = None,
    system_prompt: str = "",
    project_registry: FactRegistry | None = None,
    causal_evidence_budget: int = DEFAULT_CAUSAL_EVIDENCE_BUDGET,
) -> list[list[ScenarioCandidate]]:
    """Stable prompt-budget batching; every input appears exactly once."""
    if max_chars <= 0:
        raise ValueError("scenario batch max_chars必须大于0")
    if max_items is not None and max_items <= 0:
        raise ValueError("scenario batch max_items必须大于0")
    prefix, suffix = _prompt_parts(malfunction)
    fixed_chars = len(system_prompt) + len(prefix) + len(suffix) + 2
    batches: list[list[ScenarioCandidate]] = []
    current: list[ScenarioCandidate] = []
    current_payload_chars = 0
    for scenario in scenarios:
        serialized_chars = len(_scenario_json(
            malfunction, scenario, project_registry, causal_evidence_budget,
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


def select_scenario_causal_evidence(
    malfunction: MalfunctionCandidate,
    scenario: ScenarioCandidate,
    project_registry: FactRegistry | None = None,
    causal_evidence_budget: int = DEFAULT_CAUSAL_EVIDENCE_BUDGET,
) -> CausalEvidenceSelection:
    """Return the same deterministic selection used by prompt serialization."""

    registry = build_fact_registry(malfunction, scenario, project_registry)
    return CausalEvidenceSelector(max_evidence=causal_evidence_budget).select(registry)
