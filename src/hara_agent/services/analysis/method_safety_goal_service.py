from __future__ import annotations

import hashlib
from typing import Any

from hara_agent.contracts import MethodContract


class MethodSafetyGoalService:
    """Create deterministic, review-gated SG/Safe-State proposals."""

    ASIL_RANK = {"QM": 0, "A": 1, "B": 2, "C": 3, "D": 4}

    def __init__(self, method: MethodContract):
        self.method = method
        self.catalog: dict[str, dict[str, Any]] = {}

    @property
    def is_approved(self) -> bool:
        return not (
            self.method.safety_goal_method.semantic_derivation_required
            or self.method.safe_state_method.semantic_derivation_required
        )

    @staticmethod
    def _intent_id(function_name: str, guideword: str, malfunction: str) -> str:
        material = "\n".join(
            item.strip().casefold() for item in (function_name, guideword, malfunction)
        )
        digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:10].upper()
        return f"SG-METHOD-{digest}"

    def register_intent(
        self,
        *,
        function_name: str,
        malfunction: str,
        guideword: str,
        scenario_id: str,
        hazard_event: str,
        asil: str,
        ftti_result: dict[str, Any] | None = None,
    ) -> dict[str, str]:
        sg_id = self._intent_id(function_name, guideword, malfunction)
        safety_goal = f"避免因{guideword}{function_name}而导致{hazard_event}。"
        safe_state = (
            f"使{function_name}进入能够防止或缓解“{hazard_event}”的受控状态。"
        )
        entry = self.catalog.setdefault(sg_id, {
            "sg_id": sg_id,
            "safety_goal": safety_goal,
            "safety_state": safe_state,
            "max_asil": asil,
            "functions": [],
            "associations": [],
            "method_contract_hash": self.method.metadata["template_hash"],
            "derivation_status": (
                "FINALIZED" if self.is_approved else "NEEDS_REVIEW"
            ),
        })
        if function_name not in entry["functions"]:
            entry["functions"].append(function_name)
        if self.ASIL_RANK.get(asil, -1) > self.ASIL_RANK.get(entry["max_asil"], -1):
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
            "safety_goal": str(entry["safety_goal"]),
            "safety_state": str(entry["safety_state"]),
        }

    def to_dict(self) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for sg_id in sorted(self.catalog):
            entry = self.catalog[sg_id]
            ftti_items = [
                item["ftti"] for item in entry["associations"] if item.get("ftti")
            ]
            values = [
                float(item["ftti_value_s"])
                for item in ftti_items if item.get("ftti_value_s") is not None
            ]
            entry["ftti_value_s"] = min(values) if values else None
            result[sg_id] = entry
        return result
