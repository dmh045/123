from __future__ import annotations

import json
import time
from dataclasses import asdict
from typing import Sequence

from hara_agent.infrastructure.llm import LLMClient, LLMRequest
from hara_agent.services.extraction.project_fact_normalization import (
    ProjectFactNormalizationResult, ProjectFactNormalizer,
)
from hara_agent.services.extraction.project_fact_specs import RequiredProjectFactSpec

from .item_supplement_agent import RoutedDocumentBlocks


class TargetedProjectFactExtractionAgent:
    """One bounded schema-guided call for one Project Fact category."""

    PROMPT_VERSION = "targeted-project-facts-v1"

    def __init__(self, client: LLMClient, normalizer: ProjectFactNormalizer | None = None):
        self.client = client
        self.normalizer = normalizer or ProjectFactNormalizer()

    def extract(
        self,
        category: str,
        specs: Sequence[RequiredProjectFactSpec],
        routed: RoutedDocumentBlocks,
        source_id: str,
    ) -> tuple[ProjectFactNormalizationResult, dict]:
        diagnostics = {
            str(item.get("fact_type", "")): {
                str(block_id) for block_id in item.get("selected_block_ids", [])
            }
            for item in routed.diagnostics.get("facts", [])
            if isinstance(item, dict)
        }
        schema = [{
            "fact_type": item.fact_type,
            "output_type": item.output_type.value,
            "aliases": list(item.aliases),
            "unit_hints": list(item.unit_hints),
            "context_hints": list(item.context_hints),
            "required_fields": list(item.required_fields),
            "retrieval_candidate_block_ids": sorted(diagnostics.get(item.fact_type, set())),
        } for item in specs]
        request = LLMRequest(
            task=f"extract_targeted_project_facts:{category}",
            schema_name=f"TargetedProjectFacts:{category}",
            prompt_version=self.PROMPT_VERSION,
            system_prompt=(
                "你是来源约束的原子项目事实抽取器。只可使用提供的带ID证据块，不得猜测、换算或补充常识。"
                "每个FOUND结果只能表达一个原子事实；数值必须是JSON number，运算符只能是LT/LE/EQ/GE/GT/RANGE。"
                "source_block_id必须逐字使用给定ID，source_excerpt若提供必须是该块原文的连续精确子串。"
                "相邻表头或邻行仅用于理解上下文；source_block_id必须取当前spec的retrieval_candidate_block_ids之一，"
                "且该块正文必须包含当前spec的任一alias；不得选择support-only块。"
            ),
            user_prompt=(
                "严格返回JSON对象 {\"results\":[...]}。每个spec必须恰好返回一项，status只能是FOUND或NOT_FOUND。"
                "FOUND需返回该spec的required_fields及fact_type/status；NOT_FOUND只返回fact_type/status。"
                "parameter使用aliases中的第一个英文标识；operating_mode和condition使用context_hints中的第一个英文标识；"
                "源文未明确control_mode或condition时返回空字符串，不要因此把明确的driver_location判为NOT_FOUND。"
                "不得把多个数值或多个驾驶员位置合并到同一项，不得返回解释或Markdown。\n"
                "specs=" + json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
                + "\nevidence_blocks=\n" + routed.text
            ),
            metadata={"source_id": source_id, "block_ids": routed.block_ids, "category": category},
            max_tokens=min(int(getattr(getattr(self.client, "config", None), "max_tokens", 4096)), 4096),
        )
        started = time.monotonic()
        response = self.client.complete_json(request)
        normalized = self.normalizer.normalize(
            specs, response.data.get("results"), routed.source_blocks, source_id,
            diagnostics,
        )
        audit = {
            "task": request.task,
            "category": category,
            "prompt_version": request.prompt_version,
            "model": response.model,
            "request_id": response.request_id,
            "usage": response.usage,
            "selected_block_ids": routed.block_ids,
            "selected_source_blocks": list(routed.source_blocks),
            "routing_diagnostics": dict(routed.diagnostics),
            "raw_results": response.data.get("results"),
            "coverage": normalized.coverage,
            "normalization_failures": [asdict(item) for item in normalized.failures],
            "llm_call_count": 1,
            "elapsed_seconds": round(time.monotonic() - started, 3),
        }
        return normalized, audit
