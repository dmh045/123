from __future__ import annotations

import math
import re
from typing import Any

from hara_agent.infrastructure.llm import LLMClient, LLMRequest
from hara_agent.models import (
    FactProvenance,
    ItemDefinitionFacts,
    ReviewStatus,
    SourceRef,
    SpeedEnvelope,
)

from .parsing import CONFIDENCE_PROMPT_CONTRACT, parse_confidence


class ItemDefinitionExtractionAgent:
    PROMPT_VERSION = "item-definition-v4"
    SYSTEM_PROMPT = """你是汽车功能安全HARA的Item Definition抽取Agent。只提取输入文档明确陈述的项目事实，
包括系统描述、Item边界、运行模式、ODD位置/道路/天气/路面、各子阶段速度、性能参数、驾驶员位置与可用干预方式。
分别提取驾驶员在车内/车外时的直接车辆控制权限，不得把某一种驾驶员状态应用到所有场景。Exposure仅提取文档明确给出的
平均运行时间占比(T)或发生频率(F)，没有定量或分级依据时不得猜测E等级。速度范围必须遍历全部
运行模式后取全局最小值和最大值；不得用常识或Domain默认值补写。所有结论必须携带来源位置和原文摘录；证据不足时
返回null或空数组并标记PENDING。"""

    def __init__(self, client: LLMClient):
        self.client = client

    def extract(self, document_text: str, source_id: str) -> tuple[ItemDefinitionFacts, dict[str, Any]]:
        if not document_text.strip():
            raise ValueError("Item Definition文本为空")
        request = LLMRequest(
            task="extract_typed_item_definition",
            system_prompt=self.SYSTEM_PROMPT,
            user_prompt=(
                "只返回JSON对象，顶层必须且只能包含item_definition。item_definition字段包括"
                "system_description、item_boundary、operating_modes、"
                "odd.locations、odd.road_types、odd.weather_conditions、odd.road_surfaces、"
                "odd.speed_range_kph（[全局min,全局max]或null）、performance_parameters、driver_contexts、"
                "exposure_inputs、"
                "source_location、source_excerpt、confidence、status。"
                "driver_contexts每项包含context_id、driver_position(inside/outside)、driver_state、"
                "direct_vehicle_control(boolean或null)、intervention_channels、source_location、source_excerpt。"
                "exposure_inputs每项仅在原文有证据时填写scenario_variant、method(T/F)、level(E0-E4，如原文未给则空)、"
                "basis、source_location、source_excerpt；不得根据常识填写level。"
                "数量限制：operating_modes最多20项、performance_parameters最多30项、driver_contexts最多6项、"
                "exposure_inputs最多20项；所有source_excerpt最多160个字符。不得输出解释、Markdown或额外字段。\n\n"
                + CONFIDENCE_PROMPT_CONTRACT + "\n"
                + document_text
            ),
            schema_name="ItemDefinitionFacts",
            prompt_version=self.PROMPT_VERSION,
            metadata={"source_id": source_id},
            max_tokens=16384,
        )
        response = self.client.complete_json(request)
        raw = self._unwrap_payload(response.data)
        facts, normalization_warnings = self._parse_with_warnings(raw, source_id)
        return facts, {
            "task": request.task,
            "prompt_version": request.prompt_version,
            "model": response.model,
            "request_id": response.request_id,
            "usage": response.usage,
            "status": facts.status.value,
            "speed_range_kph": [facts.speed_min_kph, facts.speed_max_kph]
            if facts.speed_min_kph is not None else None,
            "normalization_warnings": normalization_warnings,
        }

    @staticmethod
    def _unwrap_payload(payload: dict[str, Any]) -> dict[str, Any]:
        """Accept the canonical envelope and safe equivalent provider shapes."""
        raw = payload.get("item_definition")
        if isinstance(raw, dict):
            return raw

        data = payload.get("data")
        if isinstance(data, dict) and isinstance(data.get("item_definition"), dict):
            return data["item_definition"]

        # Some models omit the requested envelope but return the schema fields
        # directly. Only accept this when it is unmistakably Item Definition
        # content; never treat an arbitrary JSON object as valid extraction.
        signature = {"system_description", "item_boundary", "operating_modes", "odd"}
        if len(signature.intersection(payload)) >= 2:
            return payload

        keys = sorted(str(key) for key in payload)[:12]
        raise ValueError(f"LLM输出缺少item_definition对象；顶层字段: {keys}")

    @staticmethod
    def _parse(raw: dict[str, Any], source_id: str) -> ItemDefinitionFacts:
        facts, _ = ItemDefinitionExtractionAgent._parse_with_warnings(raw, source_id)
        return facts

    @staticmethod
    def _parse_with_warnings(
        raw: dict[str, Any], source_id: str,
    ) -> tuple[ItemDefinitionFacts, list[str]]:
        odd = raw.get("odd", {})
        if not isinstance(odd, dict):
            raise ValueError("item_definition.odd必须为object")
        speed_min, speed_max, speed_warning = ItemDefinitionExtractionAgent._speed_range(
            odd.get("speed_range_kph")
        )
        normalization_warnings = [speed_warning] if speed_warning else []
        status = (
            ReviewStatus.FINALIZED
            if str(raw.get("status", "")).upper() == "FINALIZED" and not normalization_warnings
            else ReviewStatus.PENDING
        )
        driver_contexts, driver_warnings = ItemDefinitionExtractionAgent._driver_contexts(
            raw.get("driver_contexts")
        )
        normalization_warnings.extend(driver_warnings)
        speed_envelopes, envelope_warnings = ItemDefinitionExtractionAgent._speed_envelopes(
            raw.get("speed_envelopes"), source_id
        )
        normalization_warnings.extend(envelope_warnings)
        if driver_warnings or envelope_warnings:
            status = ReviewStatus.PENDING
        facts = ItemDefinitionFacts(
            system_description=str(raw.get("system_description", "")).strip(),
            item_boundary=str(raw.get("item_boundary", "")).strip(),
            operating_modes=[str(value) for value in ItemDefinitionExtractionAgent._list(raw.get("operating_modes"), "operating_modes")],
            odd_locations=[str(value) for value in ItemDefinitionExtractionAgent._list(odd.get("locations"), "odd.locations")],
            odd_road_types=[str(value) for value in ItemDefinitionExtractionAgent._list(odd.get("road_types"), "odd.road_types")],
            odd_weather_conditions=[str(value) for value in ItemDefinitionExtractionAgent._list(odd.get("weather_conditions"), "odd.weather_conditions")],
            odd_road_surfaces=[str(value) for value in ItemDefinitionExtractionAgent._list(odd.get("road_surfaces"), "odd.road_surfaces")],
            speed_min_kph=speed_min,
            speed_max_kph=speed_max,
            speed_envelopes=speed_envelopes,
            performance_parameters=ItemDefinitionExtractionAgent._list(raw.get("performance_parameters"), "performance_parameters"),
            driver_contexts=driver_contexts,
            exposure_inputs=ItemDefinitionExtractionAgent._list(raw.get("exposure_inputs"), "exposure_inputs"),
             sources=[SourceRef(
                 "item_definition", source_id,
                 str(raw.get("source_location", "")),
                 str(raw.get("source_excerpt", "")),
             )],
             status=status,
             confidence=parse_confidence(
                 raw.get("confidence"), field_name="Item Definition confidence",
             ),
        )
        return facts, normalization_warnings

    @staticmethod
    def _speed_envelopes(
        value: Any, source_id: str,
    ) -> tuple[list[SpeedEnvelope], list[str]]:
        """Normalize optional typed envelopes without changing the LLM prompt contract."""
        items = ItemDefinitionExtractionAgent._list(value, "speed_envelopes")
        envelopes: list[SpeedEnvelope] = []
        warnings: list[str] = []
        for index, item in enumerate(items, start=1):
            if not isinstance(item, dict):
                warnings.append(f"speed_envelopes[{index}] is not an object and was ignored")
                continue
            try:
                provenance = FactProvenance(
                    item.get("provenance", FactProvenance.PROJECT_INPUT.value)
                )
                envelope_status = ReviewStatus(
                    item.get("status", ReviewStatus.PENDING.value)
                )
                sources = [SourceRef(**source) for source in item.get("sources", [])]
                if not sources and (item.get("source_location") or item.get("source_excerpt")):
                    sources = [SourceRef(
                        "item_definition",
                        source_id,
                        str(item.get("source_location", "")),
                        str(item.get("source_excerpt", "")),
                    )]
                envelopes.append(SpeedEnvelope(
                    operating_mode=str(item.get("operating_mode", item.get("mode", ""))),
                    speed_min_kph=(
                        None if item.get("speed_min_kph", item.get("min_kph")) is None
                        else float(item.get("speed_min_kph", item.get("min_kph")))
                    ),
                    speed_max_kph=(
                        None if item.get("speed_max_kph", item.get("max_kph")) is None
                        else float(item.get("speed_max_kph", item.get("max_kph")))
                    ),
                    condition=str(item.get("condition", "")),
                    unit=str(item.get("unit", "km/h")),
                    sources=sources,
                    provenance=provenance,
                    status=envelope_status,
                ))
            except (TypeError, ValueError) as error:
                warnings.append(f"speed_envelopes[{index}] invalid: {error}")
        return envelopes, warnings

    @staticmethod
    def _driver_contexts(value: Any) -> tuple[list[dict[str, Any]], list[str]]:
        contexts = ItemDefinitionExtractionAgent._list(value, "driver_contexts")
        normalized, warnings = [], []
        for index, context in enumerate(contexts, start=1):
            if not isinstance(context, dict):
                warnings.append(f"driver_contexts[{index}]不是object，已忽略并要求评审")
                continue
            item = dict(context)
            direct_control = item.get("direct_vehicle_control")
            if isinstance(direct_control, str):
                literal = direct_control.strip().lower()
                if literal in {"true", "false"}:
                    item["direct_vehicle_control"] = literal == "true"
                    warnings.append(
                        f"driver_contexts[{index}].direct_vehicle_control由字符串"
                        "JSON literal规范化为boolean并要求评审"
                    )
                elif not literal or literal == "null":
                    item["direct_vehicle_control"] = None
                else:
                    item["direct_vehicle_control"] = None
                    context_id = str(item.get("context_id", index))
                    warnings.append(
                        f"driver_contexts[{context_id}].direct_vehicle_control为描述性文本，"
                        "无法安全推断boolean，已置为null并要求评审"
                    )
            elif direct_control is not None and not isinstance(direct_control, bool):
                item["direct_vehicle_control"] = None
                context_id = str(item.get("context_id", index))
                warnings.append(
                    f"driver_contexts[{context_id}].direct_vehicle_control类型无效，"
                    "已置为null并要求评审"
                )
            normalized.append(item)
        return normalized, warnings

    @staticmethod
    def _speed_range(value: Any) -> tuple[float | None, float | None, str]:
        if value is None or value == [None, None]:
            return None, None, ""
        candidate = value
        if isinstance(value, dict):
            candidate = [value.get("min"), value.get("max")]
        elif isinstance(value, str):
            match = re.search(
                r"^\s*[\[(]?\s*(-?\d+(?:\.\d+)?)\s*"
                r"(?:,|，|~|～|至|-)\s*(-?\d+(?:\.\d+)?)",
                value,
            )
            candidate = [match.group(1), match.group(2)] if match else None
        if not isinstance(candidate, list) or len(candidate) != 2:
            return None, None, "speed_range_kph格式无效，已置空并要求评审"
        if candidate[0] is None or candidate[1] is None:
            return None, None, "speed_range_kph不完整，已置空并要求评审"
        try:
            first, second = float(candidate[0]), float(candidate[1])
        except (TypeError, ValueError):
            return None, None, "speed_range_kph不是数值，已置空并要求评审"
        if not math.isfinite(first) or not math.isfinite(second) or first < 0 or second < 0:
            return None, None, "speed_range_kph包含负值或非有限数，已置空并要求评审"
        if first > second:
            return second, first, "speed_range_kph顺序颠倒，已按[min,max]规范化并要求评审"
        return first, second, ""

    @staticmethod
    def _list(value: Any, field: str) -> list[Any]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError(f"{field}必须为array或null")
        return value
