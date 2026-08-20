from __future__ import annotations

from dataclasses import replace
import os
import sys
import time
from typing import Any

from hara_agent.infrastructure.llm import LLMClient, LLMOutputLimitError, LLMRequest
from hara_agent.models import FunctionDefinition, ItemDefinitionFacts, ReviewStatus
from hara_agent.services.validation import FunctionValidator

from .function_agent import FunctionExtractionAgent
from .item_definition_agent import ItemDefinitionExtractionAgent
from .parsing import CONFIDENCE_PROMPT_CONTRACT


class ItemArtifactExtractionAgent:
    """Extract core Item facts and Functions once from the full document."""

    PROMPT_VERSION = "item-artifacts-v2"
    SYSTEM_PROMPT = """你是汽车功能安全HARA的相关项定义抽取Agent。只提取文档明确陈述的事实。
一次返回核心Item Definition和车辆级Functions。不得把标题、条件、步骤、后果或质量要求识别为Function；
不得用常识补写ODD、速度或功能。复杂的性能、驾驶员控制和Exposure证据由后续局部抽取处理，本次不要展开。
所有结论必须有可定位来源；证据不足时返回null或空数组并标记PENDING。"""

    def __init__(self, client: LLMClient, validator: FunctionValidator | None = None):
        self.client = client
        self.validator = validator or FunctionValidator()

    def extract(
        self, document_text: str, source_id: str,
    ) -> tuple[ItemDefinitionFacts, list[FunctionDefinition], dict[str, Any]]:
        if not document_text.strip():
            raise ValueError("Item Definition文本为空")
        configured_limit = int(getattr(getattr(self.client, "config", None), "max_tokens", 32768))
        initial_budget = min(
            int(os.getenv("HARA_ITEM_ARTIFACT_MAX_TOKENS", "16384")),
            configured_limit,
        )
        if initial_budget <= 0 or configured_limit <= 0:
            raise ValueError("Item Artifact token预算必须大于0")
        request = LLMRequest(
            task="extract_core_item_artifacts",
            system_prompt=self.SYSTEM_PROMPT,
            user_prompt=(
                "只返回JSON对象，顶层必须包含item_definition和functions。"
                "item_definition仅包含system_description、item_boundary、operating_modes、odd、"
                "source_location、source_excerpt、confidence、status；odd包含locations、road_types、"
                "weather_conditions、road_surfaces、speed_range_kph。functions每项包含function_id、name、"
                "output、description、preconditions、triggers、odd_constraints、fallback_behavior、"
                "consequences、source_location、source_excerpt、confidence、status。最多20个Function；"
                "每项列表最多10条，所有source_excerpt最多120字符。不得输出Markdown、解释或额外字段。\n\n"
                + CONFIDENCE_PROMPT_CONTRACT + "\n"
                + document_text
            ),
            schema_name="CoreItemArtifacts",
            prompt_version=self.PROMPT_VERSION,
            metadata={"source_id": source_id},
            max_tokens=initial_budget,
        )
        started = time.monotonic()
        output_limit_retry = False
        llm_call_count = 1
        try:
            response = self.client.complete_json(request)
        except LLMOutputLimitError:
            first_attempt_elapsed = time.monotonic() - started
            if initial_budget >= configured_limit:
                raise
            output_limit_retry = True
            print(
                "[HARA] core item artifact output limit "
                f"old_max_tokens={initial_budget} new_max_tokens={configured_limit} "
                f"first_attempt_elapsed={first_attempt_elapsed:.1f}s",
                file=sys.stderr,
                flush=True,
            )
            request = replace(request, max_tokens=configured_limit)
            llm_call_count += 1
            response = self.client.complete_json(request)
        item_raw = response.data.get("item_definition")
        function_raw = response.data.get("functions")
        if not isinstance(item_raw, dict):
            raise ValueError("主抽取缺少item_definition对象")
        if not isinstance(function_raw, list):
            raise ValueError("主抽取缺少functions数组")

        normalized_item = dict(item_raw)
        # Evidence-heavy fields belong exclusively to the routed local
        # supplement; provider-added extras must not bypass that contract.
        normalized_item["performance_parameters"] = []
        normalized_item["driver_contexts"] = []
        normalized_item["exposure_inputs"] = []
        facts, warnings = ItemDefinitionExtractionAgent._parse_with_warnings(
            normalized_item, source_id
        )
        functions = [FunctionExtractionAgent._parse(item, source_id) for item in function_raw]
        FunctionExtractionAgent._validate_unique(functions)
        self.validator.ensure_valid(functions)

        facts.status = ReviewStatus.PENDING
        for function in functions:
            function.status = ReviewStatus.PENDING
        elapsed_seconds = time.monotonic() - started
        return facts, functions, {
            "task": request.task,
            "prompt_version": request.prompt_version,
            "model": response.model,
            "request_id": response.request_id,
            "usage": response.usage,
            "function_count": len(functions),
            "normalization_warnings": warnings,
            "output_limit_retry": output_limit_retry,
            "max_tokens": request.max_tokens,
            "elapsed_seconds": round(elapsed_seconds, 3),
            "llm_call_count": llm_call_count,
            "input_characters": len(request.system_prompt) + len(request.user_prompt),
        }
