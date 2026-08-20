from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass, field, replace
from typing import Any

from hara_agent.infrastructure.llm import LLMClient, LLMOutputLimitError, LLMRequest
from hara_agent.models import SourceRef
from hara_agent.services.extraction import (
    DEFAULT_CONTEXT_CHARACTER_BUDGET,
    PROJECT_EVIDENCE_SPECS,
    CoverageFirstContextAssembler,
    DeterministicEvidenceRetriever,
    FactRetrievalSpec,
    RoutingDiagnostics,
)


@dataclass(frozen=True)
class RoutedDocumentBlocks:
    task: str
    block_ids: list[str]
    text: str
    source_blocks: list[dict[str, str]] = field(default_factory=list)
    diagnostics: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EvidenceRoutingResult:
    routed: RoutedDocumentBlocks | None
    diagnostics: RoutingDiagnostics


class ItemEvidenceRouter:
    """Route blocks by schema semantics; routing never asserts an engineering fact."""

    ROUTES = {
        "odd_repair": (
            "odd", "运行", "模式", "速度", "km/h", "位置", "道路", "路面", "天气",
            "场地", "坡度", "operating", "speed", "location", "road", "weather",
        ),
        "project_evidence": (
            "性能", "响应", "延迟", "精度", "超时", "驾驶员", "用户", "车内", "车外",
            "遥控", "手机", "接管", "干预", "频率", "概率", "占比", "时长", "performance",
            "response", "latency", "driver", "remote", "intervention", "frequency", "exposure",
        ),
    }

    def route(
        self,
        blocks: list[dict[str, Any]],
        task: str,
        max_characters: int = DEFAULT_CONTEXT_CHARACTER_BUDGET,
        required_specs: tuple[FactRetrievalSpec, ...] | None = None,
    ) -> RoutedDocumentBlocks | None:
        return self.retrieve(
            blocks,
            task,
            max_characters=max_characters,
            required_specs=required_specs,
        ).routed

    def retrieve(
        self,
        blocks: list[dict[str, Any]],
        task: str,
        max_characters: int = DEFAULT_CONTEXT_CHARACTER_BUDGET,
        required_specs: tuple[FactRetrievalSpec, ...] | None = None,
    ) -> EvidenceRoutingResult:
        if task == "project_evidence":
            specs = PROJECT_EVIDENCE_SPECS if required_specs is None else required_specs
            rankings = DeterministicEvidenceRetriever().rank(blocks, specs)
            assembly = CoverageFirstContextAssembler().assemble(
                blocks,
                specs,
                rankings,
                task=task,
                max_characters=max_characters,
            )
            routed = RoutedDocumentBlocks(
                task=task,
                block_ids=list(assembly.block_ids),
                text=assembly.text,
                source_blocks=list(assembly.source_blocks),
                diagnostics=assembly.diagnostics.to_dict(),
            ) if assembly.block_ids else None
            return EvidenceRoutingResult(routed, assembly.diagnostics)
        if task != "odd_repair":
            raise ValueError(f"unknown Item Evidence routing task: {task}")
        return self._legacy_odd_route(blocks, task, max_characters)

    def _legacy_odd_route(
        self,
        blocks: list[dict[str, Any]],
        task: str,
        max_characters: int,
    ) -> EvidenceRoutingResult:
        keywords = self.ROUTES[task]
        matched = set()
        for index, block in enumerate(blocks):
            lowered = str(block.get("text", "")).lower()
            if any(keyword.lower() in lowered for keyword in keywords):
                matched.update({max(0, index - 1), index, min(len(blocks) - 1, index + 1)})
        selected, total = [], 0
        for index in sorted(matched):
            block = blocks[index]
            line = f"[{block.get('block_id')}] {block.get('location')}: {block.get('text')}"
            cost = len(line) + (1 if selected else 0)
            if total + cost > max_characters:
                break
            selected.append((str(block.get("block_id", "")), line))
            total += cost
        source_blocks = [{
            "block_id": str(blocks[index].get("block_id", "")),
            "kind": str(blocks[index].get("kind", "")),
            "location": str(blocks[index].get("location", "")),
            "text": str(blocks[index].get("text", "")),
        } for index in sorted(matched) if str(blocks[index].get("block_id", "")) in {
            item[0] for item in selected
        }]
        diagnostics = RoutingDiagnostics(
            task=task,
            max_characters=max_characters,
            selected_characters=len("\n".join(item[1] for item in selected)),
            selected_block_ids=tuple(item[0] for item in selected),
            facts=(),
        )
        routed = RoutedDocumentBlocks(
            task=task,
            block_ids=[item[0] for item in selected],
            text="\n".join(item[1] for item in selected),
            source_blocks=source_blocks,
            diagnostics=diagnostics.to_dict(),
        ) if selected else None
        return EvidenceRoutingResult(routed, diagnostics)


class ItemSupplementAgent:
    PROMPT_VERSION = "item-supplement-v3"
    PROJECT_EVIDENCE_LIMITS = {
        "performance_parameters": 12,
        "driver_contexts": 8,
        "exposure_inputs": 12,
    }

    def __init__(self, client: LLMClient):
        self.client = client

    def extract(self, routed: RoutedDocumentBlocks, source_id: str) -> tuple[dict[str, Any], dict]:
        if routed.task == "odd_repair":
            fields = (
                "operating_modes和odd；odd包含locations、road_types、weather_conditions、"
                "road_surfaces、speed_range_kph([min,max]或null)"
            )
        elif routed.task == "project_evidence":
            fields = (
                "performance_parameters、driver_contexts、exposure_inputs。driver_contexts每项包含"
                "context_id、driver_position、driver_state、direct_vehicle_control、intervention_channels、"
                "source_location、source_excerpt；exposure_inputs仅提取原文明确的T/F依据，不得猜E等级"
            )
            output_constraints = (
                "\n输出上限：performance_parameters最多12项，driver_contexts最多8项，"
                "exposure_inputs最多12项。相同语义的证据必须合并，禁止逐句机械展开；"
                "优先保留明确数值、状态条件、角色、时间/频率、系统边界和可追溯证据。"
                "direct_vehicle_control必须且只能是JSON true、false或null；权限、接管或干预的"
                "描述性文字必须写入driver_state或intervention_channels，不得写入该boolean字段。"
            )
        else:
            raise ValueError(f"未知局部抽取任务: {routed.task}")
        if routed.task != "project_evidence":
            output_constraints = ""
        configured_limit = int(
            getattr(getattr(self.client, "config", None), "max_tokens", 32768)
        )
        requested_budget = int(os.getenv("HARA_ITEM_SUPPLEMENT_MAX_TOKENS", "4096"))
        if requested_budget <= 0:
            raise ValueError("HARA_ITEM_SUPPLEMENT_MAX_TOKENS必须大于0")
        initial_budget = min(requested_budget, configured_limit)
        request = LLMRequest(
            task=f"supplement_{routed.task}",
            system_prompt=(
                "你是Item Definition局部证据抽取Agent。只使用给定的带ID文档块，"
                "不得扩展到常识；没有证据的字段返回空数组或null。"
            ),
            user_prompt=(
                f"返回JSON对象，字段为{fields}。每个source_excerpt最多120字符，"
                "不得输出解释、Markdown或额外字段。" + output_constraints
                + "\nsource_id=" + source_id + "\n" + routed.text
            ),
            schema_name=f"ItemSupplement:{routed.task}",
            prompt_version=self.PROMPT_VERSION,
            metadata={"source_id": source_id, "block_ids": routed.block_ids},
            max_tokens=initial_budget,
        )
        started = time.monotonic()
        print(
            "[HARA] supplement start "
            f"task={routed.task} blocks={len(routed.block_ids)} "
            f"input_chars={len(request.system_prompt) + len(request.user_prompt)} "
            f"max_tokens={initial_budget}",
            file=sys.stderr,
            flush=True,
        )
        output_limit_retry = False
        llm_call_count = 1
        try:
            response = self.client.complete_json(request)
        except LLMOutputLimitError:
            first_attempt_elapsed = time.monotonic() - started
            retry_budget = min(configured_limit, max(initial_budget * 2, 8192))
            if retry_budget <= initial_budget:
                print(
                    "[HARA] supplement failed "
                    f"task={routed.task} elapsed={first_attempt_elapsed:.1f}s "
                    f"type=LLMOutputLimitError max_tokens={initial_budget}",
                    file=sys.stderr,
                    flush=True,
                )
                raise
            print(
                "[HARA] supplement output limit "
                f"task={routed.task} old_max_tokens={initial_budget} "
                f"new_max_tokens={retry_budget} "
                f"first_attempt_elapsed={first_attempt_elapsed:.1f}s",
                file=sys.stderr,
                flush=True,
            )
            request = replace(request, max_tokens=retry_budget)
            output_limit_retry = True
            llm_call_count += 1
            response = self.client.complete_json(request)
        data = self._bounded_output(routed.task, response.data)
        normalization_warnings = []
        if routed.task == "project_evidence":
            from .item_definition_agent import ItemDefinitionExtractionAgent
            contexts, normalization_warnings = ItemDefinitionExtractionAgent._driver_contexts(
                data.get("driver_contexts")
            )
            data["driver_contexts"] = contexts
            unresolved_source_ref_count = self._attach_source_refs(data, routed, source_id)
        else:
            unresolved_source_ref_count = 0
        elapsed_seconds = time.monotonic() - started
        item_counts = {
            field: len(data.get(field, [])) if isinstance(data.get(field), list) else 0
            for field in self.PROJECT_EVIDENCE_LIMITS
        } if routed.task == "project_evidence" else {}
        print(
            "[HARA] supplement completed "
            f"task={routed.task} elapsed={elapsed_seconds:.1f}s "
            f"llm_calls={llm_call_count} output_counts={item_counts}",
            file=sys.stderr,
            flush=True,
        )
        return data, {
            "task": request.task,
            "prompt_version": request.prompt_version,
            "model": response.model,
            "request_id": response.request_id,
            "usage": response.usage,
            "selected_block_ids": routed.block_ids,
            "selected_source_blocks": list(routed.source_blocks or []),
            "routing_diagnostics": dict(routed.diagnostics or {}),
            "max_tokens": request.max_tokens,
            "output_limit_retry": output_limit_retry,
            "elapsed_seconds": round(elapsed_seconds, 3),
            "llm_call_count": llm_call_count,
            "block_count": len(routed.block_ids),
            "input_characters": len(request.system_prompt) + len(request.user_prompt),
            "output_item_counts": item_counts,
            "normalization_warnings": normalization_warnings,
            "unresolved_source_ref_count": unresolved_source_ref_count,
        }

    @staticmethod
    def _attach_source_refs(
        data: dict[str, Any], routed: RoutedDocumentBlocks, source_id: str,
    ) -> int:
        """Resolve supplement locators back to routed blocks without guessing."""
        unresolved = 0
        for field_name in ("performance_parameters", "driver_contexts", "exposure_inputs"):
            values = data.get(field_name, [])
            if not isinstance(values, list):
                continue
            for item in values:
                if not isinstance(item, dict):
                    continue
                raw_location = item.get("source_location", "")
                if isinstance(raw_location, list):
                    location = ";".join(str(value) for value in raw_location)
                else:
                    location = str(raw_location)
                excerpt = str(item.get("source_excerpt", "")).strip()
                matches = []
                for block in routed.source_blocks:
                    block_id = str(block.get("block_id", ""))
                    block_location = str(block.get("location", ""))
                    block_text = str(block.get("text", ""))
                    locator_match = bool(
                        location and (
                            block_id in location or block_location in location
                        )
                    )
                    excerpt_match = bool(
                        excerpt and (
                            excerpt in block_text or block_text in excerpt
                        )
                    )
                    if locator_match or excerpt_match:
                        matches.append(SourceRef(
                            "item_definition", source_id, block_location,
                            excerpt if excerpt else block_text[:120],
                        ))
                if matches:
                    item["sources"] = [
                        {
                            "source_type": source.source_type,
                            "source_id": source.source_id,
                            "location": source.location,
                            "excerpt": source.excerpt,
                        }
                        for source in matches
                    ]
                else:
                    item["sources"] = []
                    unresolved += 1
        return unresolved

    @classmethod
    def _bounded_output(cls, task: str, data: dict[str, Any]) -> dict[str, Any]:
        if task != "project_evidence" or not isinstance(data, dict):
            return data
        bounded = dict(data)
        for field, limit in cls.PROJECT_EVIDENCE_LIMITS.items():
            value = data.get(field)
            if not isinstance(value, list):
                continue
            unique, seen = [], set()
            for item in value:
                marker = json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)
                if marker in seen:
                    continue
                seen.add(marker)
                unique.append(item)
                if len(unique) >= limit:
                    break
            bounded[field] = unique
        return bounded
