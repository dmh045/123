from __future__ import annotations

import json
import sys
import time
from typing import Any

from hara_agent.contracts import CausalBreakpoint
from hara_agent.infrastructure.llm import (
    LLMClient, LLMJSONContractError, LLMOutputLimitError, LLMRequest,
    LLMSchemaContractError, LLMTimeoutError,
)
from hara_agent.models import (
    MalfunctionCandidate,
    ReviewStatus,
    ScenarioCandidate,
    ScenarioFeasibilityAssessment,
)

from .parsing import parse_confidence
from .scenario_contract import RISK_DIMENSION_VALUES
from .scenario_evidence import (
    SCENARIO_ASSESSMENT_CONTRACT_VERSION,
    FactRegistry, build_fact_registry, compile_causal_assessment,
    ScenarioEvidenceContractError, validate_evidence_contract,
)
from .scenario_batching import (
    DEFAULT_SCENARIO_BATCH_MAX_CHARS,
    DEFAULT_SCENARIO_BATCH_MAX_ITEMS,
    ScenarioAdaptiveBatchError, ScenarioCoverageContractError,
    ScenarioBatchSizeError,
    ScenarioSchemaContractError,
    build_scenario_batches,
    build_scenario_user_prompt,
)


class ScenarioFeasibilityAgent:
    """Assess every ODD-valid candidate and retain risk-distinguishing cases."""

    PROMPT_VERSION = "scenario-feasibility-v10"
    ALLOWED_RISK_DIMENSIONS = frozenset(RISK_DIMENSION_VALUES)
    SYSTEM_PROMPT = """ROLE
你是汽车功能安全HARA工程因果相关性分类器，不是事故故事生成器。输入候选已通过确定性ODD过滤。你的任务是独立判断给定Malfunction在每个明确Scenario中，是否存在由已提供工程事实支持的可信因果链；不是先假设每个Scenario都有危险再寻找解释。负面结论是正常且预期的输出，不得按固定数量保留场景，也不得为了提高覆盖率强行生成Hazard。

STRICT FACT BOUNDARY
只能使用请求中明确提供的：1) Malfunction description、FunctionalEffect与VehicleLevelHazard；2) Scenario facts；3) 请求中明确给出的Item facts；4) 请求中明确给出的approved engineering rules；以及由这些事实直接支持的基本物理推理。未知事实不得默认存在。不得为了建立危险链新增独立世界状态，包括未提供的交通参与者、后车、突然出现的行人或障碍物、天气、湿滑/低附着路面、坡道、驾驶员恐慌或误操作、远程操作员失误、通信/传感器/执行器/转向等新增故障、机械滑移、车辆前窜或侧滑。若这些事实明确存在于输入中才可使用。

CAUSAL CHAIN TEST
仅当以下链条每一跳连续且有输入依据时，causally_relevant才可为true：Malfunction(M) → direct system/vehicle behavior(B) → interaction with explicit Scenario facts(I) → Hazardous Event(H) → direct Potential Harm。M→B必须来自给定失效语义；B→I必须与明确Scenario条件直接交互；I→H不得依赖新发明的initiating event；H→Harm允许直接工程推理，但不得增加第二条新故障链。可信因果可能性不要求事故100%必然发生，但必须有完整、工程上可信且由已知条件支持的路径。physical feasibility、functional relevance与causal relevance必须分别判断；physical=true或functional=true不推出causal=true。

COUNTERFACTUAL TEST
设置causally_relevant=true前，内部检查：禁止加入任何输入未提供的新事件时，仅依靠当前明确事实，Hazardous Event是否仍能由Malfunction合理产生？若否，必须返回causally_relevant=false、risk_dimensions_changed=[]、hazardous_event=""、potential_harm=""，并在rationale说明M→B、B→I、I→H或H→Harm在哪一跳断裂。

RISK DIMENSION TEST
risk_dimensions_changed表示“相对该Malfunction的基准运行条件，当前Scenario的哪些明确事实使风险分析维度发生实质变化”，不是列出所有涉及或理论上可能相关的维度。每个选择的dimension都必须能在rationale中对应一个明确Scenario fact及因果解释；无法指出支持事实就不得选择。禁止blanket selection。severity只有在可信H→Harm链成立后才可因明确对象/条件改变；exposure必须有明确暴露条件和已提供规则；controllability只可依据明确的driver position、direct control、remote monitoring或intervention channel等事实，不得假设恐慌；ftti/safe_state只可依据明确时间、距离、速度、干预通道或safe-state reachability事实。

CONTRACT ILLUSTRATIONS
无rear vehicle事实时，“unexpected braking→rear vehicle collision”无效。无driver panic/steering error事实时，“unexpected braking→driver panics→steering error→pedestrian collision”无效。相反，“failure to brake + explicit obstacle ahead + closing speed→distance continues decreasing→collision”是没有新增独立事件的有效链示例。这些仅说明证据规则，不预设当前输入结论。

OUTPUT PRINCIPLES
rationale对true必须清楚表达M→B→I→H以及每个risk dimension的事实依据；对false必须说明断裂点。confidence表示“当前分类判断由给定证据支持”的置信度，不是对生成故事详细程度的信心。不要生成无输入依据的概率、频率或可能性等级。此步骤只筛选候选，不直接计算S/E/C/ASIL。"""

    SYSTEM_PROMPT += """

ATOMIC SCENARIO AND FACT PRECEDENCE
Each Scenario is one atomic, internally consistent world state. Scenario labels and names are descriptive only and MUST NOT override structured facts. Structured explicit facts take precedence over approved derived facts, natural-language summaries, and labels. Do not infer a replacement numeric or state value from a Scenario name; in particular, a label containing "standstill" does not make ego_speed_kph zero when the structured field says otherwise.

BATCH INDEPENDENCE
Each assessment is an independent engineering classification. Do NOT compare scenarios with one another. Do NOT use another Scenario in the same batch as evidence. Do NOT normalize one Scenario relative to another, assume a progression or severity ladder from batch order, or carry facts between Scenarios. For each assessment use only the shared Malfunction facts, that Scenario's own explicit facts, and approved project or method facts. The Malfunction baseline is never another Scenario in the current batch; do not fabricate an unavailable baseline.
"""

    SYSTEM_PROMPT += """

STRUCTURED CAUSAL EVIDENCE CONTRACT
The readable rationale is not authoritative causal evidence. Return breakpoint, a structured causal_chain with m_to_b, b_to_i, i_to_h, and h_to_harm hops, and structured risk_dimension_changes. Every hop contains claim, basis_type, and evidence_refs. Cite only exact keys in that Scenario's read-only fact_registry. A fact's existence does not automatically prove a causal hop: the claim must explain how those cited facts support that exact transition. A causal claim without resolvable evidence cannot support causally_relevant=true.

DIRECT_FACT cites explicit facts. DERIVED_PHYSICS cites only a precomputed DERIVED fact in the registry; raw speed, distance, or actor facts do not license arbitrary vehicle dynamics or human behavior. APPROVED_RULE cites only a registry entry explicitly marked approved. ASSUMPTION identifies a necessary unsupported condition. If any hop needed for a true chain is an ASSUMPTION, return causally_relevant=false at the appropriate breakpoint. Do not reduce confidence to preserve an unsupported chain.

For causally_relevant=true, all four hops are complete and breakpoint=NONE. For false, breakpoint identifies the first unsupported transition; do not fabricate negative facts or a completed hazard chain. risk_dimension_changes identifies only dimensions requiring downstream re-evaluation and supplies exact evidence_refs and a reason; do not calculate S/E/C/FTTI levels here. risk_dimensions_changed is not returned because Python derives it from validated risk_dimension_changes.

Every returned causal_chain hop object MUST contain a non-empty claim. At the false-case breakpoint, an ASSUMPTION claim must explicitly name the necessary unsupported condition; never return an empty claim as a placeholder. Omit hop objects after the breakpoint instead of returning blank hop objects.

causal_chain MUST be a JSON object, never an array, string, or null. Its exact container shape is {"m_to_b":{"claim":"...","basis_type":"...","evidence_refs":[]},"b_to_i":{"claim":"...","basis_type":"...","evidence_refs":[]},"i_to_h":{"claim":"...","basis_type":"...","evidence_refs":[]},"h_to_harm":{"claim":"...","basis_type":"...","evidence_refs":[]}}. This illustrates structure only; claims and evidence must still be derived solely from the supplied facts. For a false result, include every hop through the declared breakpoint and omit later hops.
"""

    MACHINE_OUTPUT_RULE = """MACHINE OUTPUT RULE
Return exactly one JSON object matching the requested schema. Do not use Markdown. Do not wrap the response in ```json or any code fence. Do not add explanatory text before or after the JSON. The first non-whitespace character must be { and the last non-whitespace character must be }. All strings must be valid JSON strings. Do not include comments or trailing commas. The top-level object must remain {"assessments": [...]}.
"""

    def __init__(
        self, client: LLMClient, *,
        batch_max_chars: int | None = None,
        batch_max_items: int | None = None,
        max_split_depth: int | None = None,
    ):
        self.client = client
        self.prompt_version = self.PROMPT_VERSION
        self.assessment_contract_version = SCENARIO_ASSESSMENT_CONTRACT_VERSION
        self.schema_name = "ScenarioFeasibilityAssessmentList"
        self.system_prompt = self.SYSTEM_PROMPT
        config = getattr(client, "config", None)
        self.batch_max_chars = batch_max_chars or getattr(
            config, "scenario_batch_max_chars", DEFAULT_SCENARIO_BATCH_MAX_CHARS,
        )
        self.batch_max_items = batch_max_items or getattr(
            config, "scenario_batch_max_items", DEFAULT_SCENARIO_BATCH_MAX_ITEMS,
        )
        self.max_split_depth = (
            max_split_depth if max_split_depth is not None
            else getattr(config, "scenario_max_split_depth", 8)
        )

    def assess(self, malfunction: MalfunctionCandidate,
               scenarios: list[ScenarioCandidate],
               project_registry: FactRegistry | None = None,
               ) -> tuple[list[ScenarioFeasibilityAssessment], dict[str, Any]]:
        ids = [item.scenario_id for item in scenarios]
        if len(ids) != len(set(ids)):
            raise ValueError("场景候选ID重复")
        batches = build_scenario_batches(
            malfunction, scenarios,
            max_chars=self.batch_max_chars,
            max_items=self.batch_max_items,
            system_prompt=self.system_prompt + "\n" + self.MACHINE_OUTPUT_RULE,
            project_registry=project_registry,
        )
        batch_sizes = [
            len(self.system_prompt + self.MACHINE_OUTPUT_RULE) + len(build_scenario_user_prompt(
                malfunction, batch, project_registry,
            ))
            for batch in batches
        ]
        print(
            "[HARA] scenario batching "
            f"malfunction={malfunction.malfunction_id} scenario_count={len(scenarios)} "
            f"batch_count={len(batches)} max_batch_chars={max(batch_sizes, default=0)} "
            f"max_batch_items={max((len(batch) for batch in batches), default=0)}",
            file=sys.stderr,
            flush=True,
        )
        assessments: list[ScenarioFeasibilityAssessment] = []
        batch_usages, models, request_ids = [], [], []
        stats: dict[str, Any] = {
            "adaptive_split_count": 0,
            "actual_llm_calls": 0,
            "retry_calls": 0,
            "output_limit_count": 0,
            "timeout_count": 0,
            "schema_error_count": 0,
            "coverage_error_count": 0,
            "item_contract_repair_count": 0,
            "item_contract_repair_failure_count": 0,
            "single_item_failures": 0,
            "leaf_items": [],
            "leaf_chars": [],
            "leaf_elapsed": [],
            "completion_tokens": [],
            "reasoning_characters": [],
            "transport_error_counts": {},
            "json_contract_errors": 0,
            "format_retry_calls": 0,
            "format_retry_successes": 0,
            "format_retry_failures": 0,
            "markdown_fence_normalizations": 0,
        }
        started = time.monotonic()
        for batch_index, batch in enumerate(batches, start=1):
            assessments.extend(self._assess_batch_adaptively(
                malfunction, batch,
                parent_batch=f"{batch_index}/{len(batches)}",
                split_path="root",
                depth=0,
                stats=stats,
                batch_usages=batch_usages,
                models=models,
                request_ids=request_ids,
                project_registry=project_registry,
            ))
        self._validate(assessments, ids)
        order = {scenario_id: index for index, scenario_id in enumerate(ids)}
        assessments.sort(key=lambda item: order[item.scenario_id])
        elapsed = time.monotonic() - started
        print(
            "[HARA] scenario feasibility completed "
            f"malfunction={malfunction.malfunction_id} scenario_count={len(scenarios)} "
            f"batch_count={len(batches)} received={len(assessments)} "
            f"feasible={sum(item.retain for item in assessments)} "
            f"infeasible={sum(not item.retain for item in assessments)} elapsed={elapsed:.1f}s",
            file=sys.stderr,
            flush=True,
        )
        return assessments, {
            "task": "assess_scenario_feasibility",
            "malfunction_id": malfunction.malfunction_id,
            "prompt_version": self.prompt_version,
            "assessment_contract_version": self.assessment_contract_version,
            "schema_name": self.schema_name,
            "models": models,
            "request_ids": request_ids,
            "usage": {"batches": batch_usages},
            "candidate_count": len(scenarios),
            "retained_count": sum(item.retain for item in assessments),
            "batch_count": len(batches),
            "initial_batch_count": len(batches),
            "adaptive_split_count": stats["adaptive_split_count"],
            "leaf_batch_count": len(stats["leaf_items"]),
            "llm_calls": stats["actual_llm_calls"],
            "actual_llm_calls": stats["actual_llm_calls"],
            "retry_calls": stats["retry_calls"],
            "batch_input_chars": stats["leaf_chars"],
            "initial_batch_input_chars": batch_sizes,
            "max_batch_chars": max(batch_sizes, default=0),
            "elapsed_seconds": round(elapsed, 3),
            **{key: stats[key] for key in (
                "output_limit_count", "timeout_count", "schema_error_count",
                "coverage_error_count", "item_contract_repair_count",
                "item_contract_repair_failure_count", "single_item_failures",
            )},
            "leaf_items": stats["leaf_items"],
            "leaf_input_chars": stats["leaf_chars"],
            "leaf_elapsed_seconds": stats["leaf_elapsed"],
            "completion_tokens": stats["completion_tokens"],
            "reasoning_characters": stats["reasoning_characters"],
            "transport_error_counts": stats["transport_error_counts"],
            "transport_failures": 0,
            **{key: stats[key] for key in (
                "json_contract_errors", "format_retry_calls", "format_retry_successes",
                "format_retry_failures", "markdown_fence_normalizations",
            )},
        }

    def _assess_batch_adaptively(
        self,
        malfunction: MalfunctionCandidate,
        scenarios: list[ScenarioCandidate],
        *,
        parent_batch: str,
        split_path: str,
        depth: int,
        stats: dict[str, Any],
        batch_usages: list[dict[str, Any]],
        models: list[str],
        request_ids: list[str],
        project_registry: FactRegistry | None = None,
    ) -> list[ScenarioFeasibilityAssessment]:
        user_prompt = build_scenario_user_prompt(
            malfunction, scenarios, project_registry,
        )
        input_chars = len(self.system_prompt + self.MACHINE_OUTPUT_RULE) + len(user_prompt)
        if input_chars > self.batch_max_chars:
            raise ScenarioBatchSizeError(
                "Scenario child batch超过请求预算且已在Provider调用前阻断: "
                f"malfunction_id={malfunction.malfunction_id} batch={parent_batch} "
                f"split_path={split_path} estimated_chars={input_chars} "
                f"max_chars={self.batch_max_chars}"
            )
        print(
            "[HARA] scenario batch start "
            f"malfunction={malfunction.malfunction_id} batch={parent_batch} "
            f"split_path={split_path} split_depth={depth} "
            f"scenario_count={len(scenarios)} input_chars={input_chars}",
            file=sys.stderr,
            flush=True,
        )
        request = LLMRequest(
            task="assess_scenario_feasibility",
            system_prompt=self.system_prompt + "\n" + self.MACHINE_OUTPUT_RULE,
            user_prompt=user_prompt,
            schema_name=self.schema_name,
            prompt_version=self.prompt_version,
            metadata={
                "malfunction_id": malfunction.malfunction_id,
                "parent_batch": parent_batch,
                "split_path": split_path,
                "split_depth": depth,
                "scenario_count": len(scenarios),
                "assessment_contract_version": self.assessment_contract_version,
                "provider_response_constraint": "NONE",
            },
            max_tokens=8192,
        )
        batch_started = time.monotonic()
        stats["actual_llm_calls"] += 1
        try:
            response = self.client.complete_json(request)
        except (LLMOutputLimitError, LLMTimeoutError) as exc:
            if isinstance(exc, LLMOutputLimitError):
                reason = "output_limit"
                stats["output_limit_count"] += 1
                diagnostics = exc.diagnostics
                if isinstance(diagnostics.get("reasoning_characters"), int):
                    stats["reasoning_characters"].append(diagnostics["reasoning_characters"])
                if isinstance(diagnostics.get("completion_tokens"), (int, float)):
                    stats["completion_tokens"].append(diagnostics["completion_tokens"])
            else:
                reason = "timeout"
                stats["timeout_count"] += 1
                stats["retry_calls"] += max(0, exc.attempts - 1)
            return self._split_or_fail(
                malfunction, scenarios, parent_batch=parent_batch,
                split_path=split_path, depth=depth, reason=reason,
                input_chars=input_chars, error=exc, stats=stats,
                batch_usages=batch_usages, models=models,
                request_ids=request_ids, project_registry=project_registry,
            )
        except (LLMJSONContractError, LLMSchemaContractError) as exc:
            stats["schema_error_count"] += 1
            return self._split_or_fail(
                malfunction, scenarios, parent_batch=parent_batch,
                split_path=split_path, depth=depth, reason="provider_schema",
                input_chars=input_chars, error=exc, stats=stats,
                batch_usages=batch_usages, models=models,
                request_ids=request_ids, project_registry=project_registry,
            )
        self._record_response_diagnostics(
            response, stats, batch_usages, models, request_ids,
        )
        scenario_by_id = {item.scenario_id: item for item in scenarios}
        try:
            raw = response.data.get("assessments")
            if not isinstance(raw, list):
                raise ScenarioSchemaContractError("LLM输出缺少assessments数组")
            parsed = [self._parse(
                malfunction, item, scenario=scenario_by_id.get(str(item.get("scenario_id", "")).strip()),
                batch=parent_batch, split_path=split_path, split_depth=depth,
                project_registry=project_registry,
            ) for item in raw]
            self._validate(parsed, [item.scenario_id for item in scenarios])
        except ScenarioCoverageContractError as exc:
            stats["coverage_error_count"] += 1
            if len(scenarios) == 1:
                return self._repair_single_item_contract(
                    malfunction, scenarios[0], parent_batch=parent_batch,
                    split_path=split_path, depth=depth, reason="coverage_contract",
                    error=exc, stats=stats, batch_usages=batch_usages,
                    models=models, request_ids=request_ids,
                    project_registry=project_registry,
                    previous_assessment=self._previous_assessment(raw, scenarios[0]),
                )
            return self._split_or_fail(
                malfunction, scenarios, parent_batch=parent_batch,
                split_path=split_path, depth=depth, reason="coverage_contract",
                input_chars=input_chars, error=exc, stats=stats,
                batch_usages=batch_usages, models=models,
                request_ids=request_ids, project_registry=project_registry,
            )
        except (ScenarioSchemaContractError, ScenarioEvidenceContractError) as exc:
            stats["schema_error_count"] += 1
            if len(scenarios) == 1:
                return self._repair_single_item_contract(
                    malfunction, scenarios[0], parent_batch=parent_batch,
                    split_path=split_path, depth=depth, reason="item_schema_contract",
                    error=exc, stats=stats, batch_usages=batch_usages,
                    models=models, request_ids=request_ids,
                    project_registry=project_registry,
                    previous_assessment=self._previous_assessment(raw, scenarios[0]),
                )
            return self._split_or_fail(
                malfunction, scenarios, parent_batch=parent_batch,
                split_path=split_path, depth=depth, reason="item_schema_contract",
                input_chars=input_chars, error=exc, stats=stats,
                batch_usages=batch_usages, models=models,
                request_ids=request_ids, project_registry=project_registry,
            )
        elapsed = time.monotonic() - batch_started
        stats["leaf_items"].append(len(scenarios))
        stats["leaf_chars"].append(input_chars)
        stats["leaf_elapsed"].append(round(elapsed, 3))
        print(
            "[HARA] scenario batch completed "
            f"malfunction={malfunction.malfunction_id} batch={parent_batch} "
            f"split_path={split_path} expected={len(scenarios)} received={len(parsed)} "
            f"elapsed={elapsed:.1f}s",
            file=sys.stderr,
            flush=True,
        )
        return parsed

    def _repair_single_item_contract(
        self,
        malfunction: MalfunctionCandidate,
        scenario: ScenarioCandidate,
        *,
        parent_batch: str,
        split_path: str,
        depth: int,
        reason: str,
        error: Exception,
        stats: dict[str, Any],
        batch_usages: list[dict[str, Any]],
        models: list[str],
        request_ids: list[str],
        project_registry: FactRegistry | None,
        previous_assessment: dict[str, Any] | None,
    ) -> list[ScenarioFeasibilityAssessment]:
        stats["item_contract_repair_count"] += 1
        error_code = (
            error.code.value
            if isinstance(error, ScenarioEvidenceContractError)
            else type(error).__name__
        )
        error_detail = {
            "code": error_code,
            "reason": getattr(error, "reason", str(error))[:1200],
            "hop": getattr(error, "hop", ""),
            "invalid_evidence_refs": getattr(error, "invalid_evidence_refs", []),
        }
        targeted_correction = self._single_item_repair_instruction(error)
        allowed_breakpoints = [item.value for item in CausalBreakpoint]
        previous_payload = (
            json.dumps(previous_assessment, ensure_ascii=False, separators=(",", ":"))
            if previous_assessment is not None else "<unavailable>"
        )
        print(
            "[HARA] scenario single-item contract repair "
            f"malfunction={malfunction.malfunction_id} scenario={scenario.scenario_id} "
            f"batch={parent_batch} split_path={split_path} split_depth={depth} "
            f"reason={reason} error_code={error_code} attempt=1/1",
            file=sys.stderr,
            flush=True,
        )
        user_prompt = build_scenario_user_prompt(
            malfunction, [scenario], project_registry,
        ) + (
            "\nSINGLE-ITEM CONTRACT REPAIR (attempt 1/1): Re-evaluate the same supplied "
            "Malfunction and Scenario. The previous result violated the declared contract. "
            "Return exactly one complete assessment for the requested scenario. Do not preserve "
            "an invalid field merely to resemble the previous response. Do not add facts, evidence "
            "references, actors, events, or engineering rules. If evidence cannot support a causal "
            "hop, return causally_relevant=false and identify the first unsupported breakpoint. "
            "Every returned hop object through the breakpoint must have a non-empty claim; an "
            "ASSUMPTION claim must name the unsupported condition. Omit hops after the breakpoint "
            "rather than returning blank objects.\n"
            f"AllowedBreakpointValues={json.dumps(allowed_breakpoints, separators=(',', ':'))}. "
            "The breakpoint value must exactly equal one of these strings; do not combine or "
            "rename enum values. Preserve valid fields from PreviousAssessment and change only "
            "the invalid field plus fields that must change to maintain cross-field consistency.\n"
            f"TargetedCorrection={targeted_correction}\n"
            f"PreviousContractError={json.dumps(error_detail, ensure_ascii=False, separators=(',', ':'))}\n"
            f"PreviousAssessment={previous_payload}"
        )
        request = LLMRequest(
            task="assess_scenario_feasibility",
            system_prompt=self.system_prompt + "\n" + self.MACHINE_OUTPUT_RULE,
            user_prompt=user_prompt,
            schema_name=self.schema_name,
            prompt_version=self.prompt_version,
            metadata={
                "malfunction_id": malfunction.malfunction_id,
                "parent_batch": parent_batch,
                "split_path": split_path,
                "split_depth": depth,
                "scenario_count": 1,
                "assessment_contract_version": self.assessment_contract_version,
                "provider_response_constraint": "NONE",
                "item_contract_repair": True,
                "item_contract_repair_attempt": 1,
            },
            max_tokens=8192,
        )
        repair_started = time.monotonic()
        stats["actual_llm_calls"] += 1
        try:
            response = self.client.complete_json(request)
            self._record_response_diagnostics(
                response, stats, batch_usages, models, request_ids,
            )
            raw = response.data.get("assessments")
            if not isinstance(raw, list) or len(raw) != 1:
                raise ScenarioCoverageContractError(
                    "single-item contract repair must return exactly one assessment"
                )
            parsed = [self._parse(
                malfunction,
                raw[0],
                scenario=(
                    scenario
                    if str(raw[0].get("scenario_id", "")).strip() == scenario.scenario_id
                    else None
                ),
                batch=parent_batch,
                split_path=split_path + "C",
                split_depth=depth,
                project_registry=project_registry,
            )]
            self._validate(parsed, [scenario.scenario_id])
        except Exception as repair_error:
            stats["item_contract_repair_failure_count"] += 1
            stats["single_item_failures"] += 1
            raise ScenarioAdaptiveBatchError(
                "Scenario single-item contract repair failed: "
                f"malfunction_id={malfunction.malfunction_id} "
                f"scenario_id={scenario.scenario_id} failure_type={reason} "
                f"original_error_code={error_code} attempt=1/1"
            ) from repair_error
        stats["leaf_items"].append(1)
        stats["leaf_chars"].append(
            len(self.system_prompt + self.MACHINE_OUTPUT_RULE) + len(user_prompt)
        )
        repair_elapsed = time.monotonic() - repair_started
        stats["leaf_elapsed"].append(round(repair_elapsed, 3))
        print(
            "[HARA] scenario single-item contract repair completed "
            f"malfunction={malfunction.malfunction_id} scenario={scenario.scenario_id} "
            f"error_code={error_code} attempt=1/1 elapsed={repair_elapsed:.1f}s",
            file=sys.stderr,
            flush=True,
        )
        return parsed

    @staticmethod
    def _single_item_repair_instruction(error: Exception) -> str:
        error_code = (
            error.code.value
            if isinstance(error, ScenarioEvidenceContractError)
            else type(error).__name__
        )
        hop = getattr(error, "hop", "")
        breakpoint = hop.upper() if hop in {
            "m_to_b", "b_to_i", "i_to_h", "h_to_harm",
        } else ""
        if error_code == "INVALID_CAUSAL_CHAIN_SHAPE":
            return (
                "Return causal_chain as one JSON object keyed only by m_to_b, b_to_i, "
                "i_to_h, and h_to_harm. NEVER return causal_chain as an array, string, "
                "number, boolean, or null. Every included hop value must itself be a JSON "
                "object containing claim, basis_type, and evidence_refs."
            )
        if error_code in {"EMPTY_HOP_CLAIM", "REQUIRED_HOP_MISSING"}:
            return (
                "Every hop through the declared breakpoint must exist and contain a non-empty "
                "claim. If the hop is unsupported, use basis_type ASSUMPTION, name the missing "
                "condition in claim, use evidence_refs [], and set causally_relevant=false."
            )
        if error_code in {"INVALID_EVIDENCE_REF_SHAPE", "UNRESOLVED_EVIDENCE_REF"}:
            return (
                "evidence_refs must be a JSON string array containing only exact keys from the "
                "supplied fact_registry. If no allowed key supports a necessary hop, classify "
                "that hop as an ASSUMPTION instead of inventing a reference."
            )
        if error_code in {
            "DERIVED_PHYSICS_KIND_MISMATCH", "DIRECT_FACT_KIND_MISMATCH",
            "APPROVED_RULE_KIND_MISMATCH", "MISSING_EVIDENCE_REF",
            "ASSUMPTION_IN_POSITIVE_CHAIN",
        }:
            return (
                "Do not merely relabel an invalid evidence reference. basis_type must match the "
                "referenced fact_registry record kind. DERIVED_PHYSICS may cite only a record whose "
                "kind is DERIVED_PHYSICS; an environment or scenario label does not prove a concrete "
                "actor, object, collision, or harm. If no registered fact of the required kind "
                f"supports hop {hop!r}, set causally_relevant=false, breakpoint={breakpoint or '<the failed hop>'}, "
                "represent that hop as ASSUMPTION with evidence_refs [], clear risk_dimension_changes, "
                "hazardous_event, and potential_harm, and omit later hops."
            )
        if error_code in {"INVALID_BREAKPOINT", "CAUSAL_BREAKPOINT_MISMATCH"}:
            return (
                "Use exactly one AllowedBreakpointValues enum. causally_relevant=true requires "
                "breakpoint=NONE; causally_relevant=false requires the enum for the first unsupported "
                "hop. I_TO_HARM is invalid and must never be returned."
            )
        return (
            "Rebuild the complete assessment against the declared machine schema and supplied "
            "fact_registry; correct the reported contract field without adding new facts."
        )

    @staticmethod
    def _previous_assessment(
        raw: Any, scenario: ScenarioCandidate,
    ) -> dict[str, Any] | None:
        if not isinstance(raw, list):
            return None
        return next((
            item for item in raw
            if isinstance(item, dict)
            and str(item.get("scenario_id", "")).strip() == scenario.scenario_id
        ), None)

    @staticmethod
    def _record_response_diagnostics(
        response,
        stats: dict[str, Any],
        batch_usages: list[dict[str, Any]],
        models: list[str],
        request_ids: list[str],
    ) -> None:
        usage = response.usage
        stats["actual_llm_calls"] += int(usage.get("format_retry_calls", 0))
        for metric in (
            "json_contract_errors", "format_retry_calls", "format_retry_successes",
            "format_retry_failures", "markdown_fence_normalizations",
        ):
            stats[metric] += int(usage.get(metric, 0))
        for category, count in usage.get("transient_error_counts", {}).items():
            stats["transport_error_counts"][category] = (
                stats["transport_error_counts"].get(category, 0) + int(count)
            )
        stats["retry_calls"] += max(0, int(usage.get("transport_attempts", 1)) - 1)
        completion_tokens = usage.get("completion_tokens", usage.get("output_tokens"))
        if isinstance(completion_tokens, (int, float)):
            stats["completion_tokens"].append(completion_tokens)
        reasoning_chars = usage.get("reasoning_characters")
        if isinstance(reasoning_chars, int):
            stats["reasoning_characters"].append(reasoning_chars)
        batch_usages.append(usage)
        models.append(response.model)
        request_ids.append(response.request_id)

    def _split_or_fail(
        self,
        malfunction: MalfunctionCandidate,
        scenarios: list[ScenarioCandidate],
        *,
        parent_batch: str,
        split_path: str,
        depth: int,
        reason: str,
        input_chars: int,
        error: Exception,
        stats: dict[str, Any],
        batch_usages: list[dict[str, Any]],
        models: list[str],
        request_ids: list[str],
        project_registry: FactRegistry | None,
    ) -> list[ScenarioFeasibilityAssessment]:
        if len(scenarios) == 1 or depth >= self.max_split_depth:
            stats["single_item_failures"] += int(len(scenarios) == 1)
            scenario_id = scenarios[0].scenario_id if len(scenarios) == 1 else "<multiple>"
            raise ScenarioAdaptiveBatchError(
                "Scenario adaptive batch无法继续拆分: "
                f"malfunction_id={malfunction.malfunction_id} scenario_id={scenario_id} "
                f"failure_type={reason} input_chars={input_chars} split_depth={depth}"
            ) from error
        midpoint = len(scenarios) // 2
        left, right = scenarios[:midpoint], scenarios[midpoint:]
        stats["adaptive_split_count"] += 1
        print(
            "[HARA] scenario batch split "
            f"malfunction={malfunction.malfunction_id} batch={parent_batch} "
            f"split_path={split_path} split_depth={depth} reason={reason} "
            f"scenario_count={len(scenarios)} left_count={len(left)} "
            f"right_count={len(right)} input_chars={input_chars}",
            file=sys.stderr,
            flush=True,
        )
        return self._assess_batch_adaptively(
            malfunction, left, parent_batch=parent_batch,
            split_path="L" if split_path == "root" else split_path + "L",
            depth=depth + 1, stats=stats, batch_usages=batch_usages,
            models=models, request_ids=request_ids,
            project_registry=project_registry,
        ) + self._assess_batch_adaptively(
            malfunction, right, parent_batch=parent_batch,
            split_path="R" if split_path == "root" else split_path + "R",
            depth=depth + 1, stats=stats, batch_usages=batch_usages,
            models=models, request_ids=request_ids,
            project_registry=project_registry,
        )

    @staticmethod
    def _parse(
        malfunction: MalfunctionCandidate,
        item: Any,
        *,
        scenario: ScenarioCandidate | None = None,
        batch: str = "unknown",
        split_path: str = "root",
        split_depth: int = 0,
        project_registry: FactRegistry | None = None,
    ) -> ScenarioFeasibilityAssessment:
        if not isinstance(item, dict):
            raise ScenarioSchemaContractError("Scenario assessment必须为JSON object")
        if any(field in item for field in ("edges", "supports", "mechanism_application")):
            raise ScenarioSchemaContractError(
                "Scenario assessment contains unsupported experimental contract fields"
            )
        if "risk_dimensions_changed" in item:
            raise ScenarioSchemaContractError(
                "risk_dimensions_changed属于v8旧schema；v9必须使用risk_dimension_changes"
            )
        boolean_fields = ("physically_feasible", "functionally_relevant", "causally_relevant")
        if any(not isinstance(item.get(field), bool) for field in boolean_fields):
            raise ScenarioSchemaContractError(
                "physically_feasible、functionally_relevant、causally_relevant必须为JSON boolean"
            )
        scenario_id = str(item.get("scenario_id", "")).strip()
        if scenario is None:
            raise ScenarioSchemaContractError(
                f"Scenario assessment引用未知scenario_id={scenario_id!r}"
            )
        causal = item["causally_relevant"]
        hazardous_event = str(item.get("hazardous_event", "")).strip()
        potential_harm = str(item.get("potential_harm", "")).strip()
        normalized_dimensions, _ = validate_evidence_contract(
            malfunction=malfunction, scenario=scenario, item=item,
            registry=build_fact_registry(malfunction, scenario, project_registry),
            prompt_version=ScenarioFeasibilityAgent.PROMPT_VERSION,
            batch=batch, split_path=split_path, split_depth=split_depth,
        )
        checks = (
            ((not causal and bool(normalized_dimensions)), "risk_dimensions_changed", normalized_dimensions, "[] when causally_relevant=false"),
            ((not causal and bool(hazardous_event)), "hazardous_event", hazardous_event, "empty string when causally_relevant=false"),
            ((not causal and bool(potential_harm)), "potential_harm", potential_harm, "empty string when causally_relevant=false"),
            ((causal and not normalized_dimensions), "risk_dimensions_changed", normalized_dimensions, "non-empty when causally_relevant=true"),
            ((causal and not hazardous_event), "hazardous_event", hazardous_event, "non-empty when causally_relevant=true"),
            ((causal and not potential_harm), "potential_harm", potential_harm, "non-empty when causally_relevant=true"),
        )
        for violated, field, actual, expected in checks:
            if violated:
                print(
                    "[HARA] scenario invariant failure "
                    f"malfunction={malfunction.malfunction_id} scenario={scenario_id} "
                    f"causal={causal} violating_field={field} actual={actual!r} "
                    f"expected={expected} batch={batch} split_path={split_path} "
                    f"split_depth={split_depth}",
                    file=sys.stderr, flush=True,
                )
                raise ScenarioSchemaContractError(
                    "Scenario cross-field invariant violation: "
                    f"malfunction_id={malfunction.malfunction_id} scenario_id={scenario_id} "
                    f"field={field} causally_relevant={causal} actual={actual!r} "
                    f"expected={expected} batch={batch} split_path={split_path} "
                    f"split_depth={split_depth}"
                )
        causal_assessment = compile_causal_assessment(
            malfunction=malfunction,
            scenario=scenario,
            item=item,
            registry=build_fact_registry(malfunction, scenario, project_registry),
        )
        # Provider labels are ignored.  The typed causal/evidence compiler is
        # the approval gate for both retained and deterministically rejected
        # combinations.
        status = causal_assessment.review_status
        return ScenarioFeasibilityAssessment(
            malfunction_id=malfunction.malfunction_id,
            scenario_id=scenario_id,
            physically_feasible=item["physically_feasible"],
            functionally_relevant=item["functionally_relevant"],
            causally_relevant=item["causally_relevant"],
            risk_dimensions_changed=[value for value in normalized_dimensions if value],
            rationale=str(item.get("rationale", "")).strip(),
            hazardous_event=hazardous_event,
            potential_harm=potential_harm,
            status=status,
            confidence=parse_confidence(
                item.get("confidence"),
                field_name=(
                    "Scenario confidence validation failed: "
                    f"malfunction={malfunction.malfunction_id} scenario={item.get('scenario_id', '')}"
                ),
            ),
            breakpoint=str(item.get("breakpoint", "")),
            causal_chain=dict(item.get("causal_chain", {})),
            risk_dimension_changes=list(item.get("risk_dimension_changes", [])),
            evidence_contract_version=SCENARIO_ASSESSMENT_CONTRACT_VERSION,
            causal_assessment=causal_assessment,
        )

    def _validate(self, assessments: list[ScenarioFeasibilityAssessment], expected_ids: list[str]):
        actual = [item.scenario_id for item in assessments]
        if len(actual) != len(set(actual)):
            raise ValueError("场景评估存在重复项")
        missing = [item for item in expected_ids if item not in actual]
        extra = [item for item in actual if item not in expected_ids]
        if missing or extra:
            raise ScenarioCoverageContractError(
                f"场景评估覆盖不完整: missing={missing}, extra={extra}"
            )
