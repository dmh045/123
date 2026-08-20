from __future__ import annotations

from typing import Any, Optional

from hara_agent.domains import DomainProfile


class SafetyGoalCatalogService:
    """Aggregate event-level findings into stable vehicle-level safety goals."""

    ASIL_RANK = {"QM": 0, "A": 1, "B": 2, "C": 3, "D": 4}

    def __init__(self, profile: DomainProfile):
        self.profile = profile
        safety_goals = profile.section("safety_goals")
        self.definitions = safety_goals.get("definitions", {})
        self.function_to_sg = safety_goals.get("function_mapping", {})
        self.token_mapping = safety_goals.get("token_mapping", [])
        if not self.definitions or not self.function_to_sg:
            raise ValueError("Domain Profile缺少Safety Goal定义或功能映射")
        referenced = set(self.function_to_sg.values())
        referenced.update(item[1] for item in self.token_mapping if len(item) == 2)
        unknown = sorted(referenced - set(self.definitions))
        if unknown:
            raise ValueError(f"Domain Profile引用未定义SG: {', '.join(unknown)}")
        self.catalog: dict[str, dict[str, Any]] = {}

    def classify(self, function_name: str) -> Optional[str]:
        if function_name in self.function_to_sg:
            return self.function_to_sg[function_name]
        for token, sg_id in self.token_mapping:
            if token in function_name:
                return sg_id
        return None

    @property
    def is_approved(self) -> bool:
        return self.profile.is_approved

    def register(self, sg_id: str, function_name: str, malfunction: str,
                 guideword: str, scenario_id: str, hazard_event: str,
                 asil: str, ftti_result: Optional[dict[str, Any]] = None) -> dict[str, str]:
        definition = self.definitions[sg_id]
        entry = self.catalog.setdefault(sg_id, {
            "sg_id": sg_id,
            **definition,
            "max_asil": asil,
            "functions": [],
            "associations": [],
        })
        if function_name not in entry["functions"]:
            entry["functions"].append(function_name)
        if self.ASIL_RANK.get(asil, 0) > self.ASIL_RANK.get(entry["max_asil"], 0):
            entry["max_asil"] = asil
        association = {
            "function": function_name,
            "malfunction": malfunction,
            "guideword": guideword,
            "scenario_id": scenario_id,
            "hazard_event": hazard_event,
            "asil": asil,
        }
        if ftti_result:
            association["ftti"] = dict(ftti_result)
        entry["associations"].append(association)
        return {
            "sg_id": sg_id,
            "safety_goal": definition["safety_goal"],
            "safety_state": definition["safety_state"],
        }

    def to_dict(self) -> dict[str, dict[str, Any]]:
        result = {}
        for sg_id in sorted(self.catalog):
            entry = self.catalog[sg_id]
            ftti_items = [
                association["ftti"]
                for association in entry.get("associations", [])
                if association.get("ftti")
            ]
            entry.update(self._aggregate_ftti(ftti_items))
            result[sg_id] = entry
        return result

    @staticmethod
    def _aggregate_ftti(results: list[dict[str, Any]]) -> dict[str, Any]:
        values = [float(item["ftti_value_s"]) for item in results if item.get("ftti_value_s") is not None]
        statuses = [str(item.get("ftti_status", "")) for item in results]
        value_s = min(values) if values else None
        finalized = bool(statuses) and all(status == "FINALIZED" for status in statuses)
        status = "FINALIZED" if finalized else "NEEDS_REVIEW"
        requirement = "NEEDS_REVIEW" if value_s is None else f"≤ {value_s:.2f} s"
        basis = (
            f"最严值≤{value_s:.2f}s；" + ("已批准。" if finalized else "公式/参数待评审。")
            if value_s is not None
            else "无可用候选值；需补充项目FTTI时序与边界条件。"
        )
        return {
            "ftti_value_s": value_s,
            "ftti_requirement": requirement,
            "ftti_status": status,
            "ftti_basis": basis,
        }
