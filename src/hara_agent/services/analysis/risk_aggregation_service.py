from __future__ import annotations

from typing import Any, Optional


class RiskAggregationService:
    """Aggregate scored scenarios only when their safety-relevant signatures match."""

    @staticmethod
    def _object_class(scenario: dict[str, Any]) -> str:
        object_type = str(scenario.get("object_type", "")).lower()
        if "行人" in object_type or "pedestrian" in object_type:
            return "pedestrian"
        if any(token in object_type for token in ("车辆", "vehicle", "car")):
            return "vehicle"
        if any(token in object_type for token in ("墙", "柱", "wall", "column")):
            return "fixed_object"
        if any(token in object_type for token in ("无近距离", "none", "无冲突")):
            return "no_conflict_object"
        return object_type or "unspecified"

    @staticmethod
    def _speed_band(value: Any) -> str:
        try:
            speed = abs(float(value))
        except (TypeError, ValueError):
            return "unknown"
        if speed == 0:
            return "standstill"
        if speed <= 5:
            return "avp_low"
        if speed <= 15:
            return "approaching_low"
        return "approaching_high"

    @classmethod
    def _motion_class(cls, scenario: dict[str, Any]) -> str:
        return "|".join((
            str(scenario.get("maneuver", "")).strip().lower(),
            str(scenario.get("parking_direction", "")).strip().lower(),
            cls._speed_band(scenario.get("ego_speed_kph")),
            cls._speed_band(scenario.get("target_speed_kph")),
        ))

    @staticmethod
    def _driver_class(scenario: dict[str, Any]) -> str:
        state = str(scenario.get("driver_state", "")).lower()
        if any(token in state for token in ("车外", "已离车", "无法直接", "outside", "remote")):
            return "remote_no_direct_control"
        if any(token in state for token in ("车内", "可接管", "inside", "takeover")):
            return "direct_intervention_available"
        return "unspecified"

    def select(self, scenarios: list[dict[str, Any]], severity: list[dict[str, Any]],
               exposure: list[dict[str, Any]], controllability: list[dict[str, Any]],
               asil: list[dict[str, Any]], safety_goals: list[dict[str, Any]],
               ftti_results: Optional[list[dict[str, Any]]] = None
               ) -> tuple[list[int], list[dict[str, Any]]]:
        seen = {}
        selected = []
        groups = []
        for idx, scenario in enumerate(scenarios):
            s = severity[idx].get("severity_score", "") if idx < len(severity) else ""
            e = exposure[idx].get("exposure_score", "") if idx < len(exposure) else ""
            c = controllability[idx].get("controllability_score", "") if idx < len(controllability) else ""
            a = asil[idx].get("ASIL", "") if idx < len(asil) else ""
            sg = safety_goals[idx] if idx < len(safety_goals) else {}
            sg_key = sg.get("sg_id") or sg.get("safety_goal", "")
            ftti = ftti_results[idx] if ftti_results and idx < len(ftti_results) else {}
            signature = (
                self._object_class(scenario),
                str(scenario.get("collision_geometry", "")).lower(),
                self._motion_class(scenario),
                self._driver_class(scenario),
                s, e, c, a, sg_key,
                (ftti.get("ftti_status", ""), ftti.get("ftti_value_s"), ftti.get("formula_id", "")),
            )
            scenario_id = scenario.get("scenario_id", "")
            if signature in seen:
                groups[seen[signature]]["covered_scenario_ids"].append(scenario_id)
                continue
            seen[signature] = len(groups)
            selected.append(idx)
            groups.append({
                "risk_signature": list(signature),
                "representative_scenario_id": scenario_id,
                "covered_scenario_ids": [scenario_id],
            })
        return selected, groups

