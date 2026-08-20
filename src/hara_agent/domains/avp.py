from __future__ import annotations

from copy import deepcopy
from typing import Any

from .profile import DomainProfile


class AVPDomainPolicy:
    """Versioned AVP engineering candidates backed by a Domain Profile."""

    def __init__(self, profile: DomainProfile):
        if profile.name.lower() != "avp":
            raise ValueError(f"AVPDomainPolicy不能加载Domain: {profile.name}")
        self.profile = profile
        self.detection = profile.section("detection")
        self.scenario = profile.section("scenario_policy")
        self.risk_candidates = profile.section("risk_candidates")
        self.risk_mapping = profile.section("risk_mapping_policy")

    def matches(self, text: str) -> bool:
        normalized = str(text).lower()
        return any(str(token).lower() in normalized for token in self.detection.get("keywords", []))

    @staticmethod
    def is_structured_scenario(scenario: dict[str, Any]) -> bool:
        return bool(str(scenario.get("scenario_variant", "")).strip())

    def candidate(self, candidate_id: str, ego_speed_kph: float) -> dict[str, Any]:
        if candidate_id not in self.risk_candidates:
            raise KeyError(f"AVP Domain Profile缺少场景候选: {candidate_id}")
        item = deepcopy(self.risk_candidates[candidate_id])
        item.pop("atomic_variants", None)
        return self._derive_candidate(candidate_id, item, ego_speed_kph)

    def atomic_candidates(self, candidate_id: str, ego_speed_kph: float) -> list[tuple[str, dict[str, Any]]]:
        """Expand profile-declared semantic variants in stable profile order."""
        if candidate_id not in self.risk_candidates:
            raise KeyError(f"AVP Domain Profile缺少场景候选: {candidate_id}")
        prototype = deepcopy(self.risk_candidates[candidate_id])
        variants = prototype.pop("atomic_variants", None)
        if variants is None:
            variants = [{"variant_id": "default", "facts": {}}]
        if not isinstance(variants, list) or not variants:
            raise ValueError(f"Scenario atomic_variants无效: {candidate_id}")
        expanded: list[tuple[str, dict[str, Any]]] = []
        seen: set[str] = set()
        for variant in variants:
            if not isinstance(variant, dict):
                raise ValueError(f"Scenario atomic variant必须为object: {candidate_id}")
            variant_id = str(variant.get("variant_id", "")).strip()
            facts = variant.get("facts", {})
            if not variant_id or variant_id in seen or not isinstance(facts, dict):
                raise ValueError(f"Scenario atomic variant ID/facts无效: {candidate_id}/{variant_id}")
            seen.add(variant_id)
            item = deepcopy(prototype)
            item.update(deepcopy(facts))
            expanded.append((variant_id, self._derive_candidate(candidate_id, item, ego_speed_kph)))
        return expanded

    def _derive_candidate(
        self, candidate_id: str, item: dict[str, Any], ego_speed_kph: float,
    ) -> dict[str, Any]:
        mode = item.pop("relative_speed_mode", "ego")
        target_speed = float(item.get("target_speed_kph", 0.0) or 0.0)
        if mode == "zero":
            relative_speed = 0.0
        elif mode == "ego_plus_target":
            relative_speed = abs(float(ego_speed_kph)) + abs(target_speed)
        elif mode == "ego":
            relative_speed = abs(float(ego_speed_kph))
        else:
            raise ValueError(f"不支持的relative_speed_mode: {mode}")
        item["scenario_variant"] = candidate_id
        item["relative_speed_mode"] = mode
        item["relative_speed_kph"] = relative_speed
        item["engineering_status"] = self.scenario.get("evidence_status", "PENDING")
        item["rule_version"] = self.profile.version
        item["review_reason"] = self.scenario.get("review_reason", "")
        return item

    @staticmethod
    def _first_matching_band(bands: list[dict[str, Any]], speed_kph: float) -> dict[str, Any] | None:
        speed = abs(float(speed_kph))
        for band in bands:
            minimum = float(band.get("min_inclusive_kph", 0.0))
            maximum = band.get("max_exclusive_kph")
            if speed >= minimum and (maximum is None or speed < float(maximum)):
                return deepcopy(band)
        return None

    def severity_decision(self, scenario: dict[str, Any]) -> dict[str, Any] | None:
        object_type = str(scenario.get("object_type", "")).lower()
        geometry = str(scenario.get("collision_geometry", "")).lower()
        relative_speed = scenario.get("relative_speed_kph")
        if relative_speed in (None, ""):
            return None
        for rule in self.risk_mapping.get("severity", {}).get("object_rules", []):
            object_tokens = [str(v).lower() for v in rule.get("object_tokens", [])]
            geometry_tokens = [str(v).lower() for v in rule.get("geometry_tokens", [])]
            if object_tokens and not any(token in object_type for token in object_tokens):
                continue
            if geometry_tokens and not any(token in geometry for token in geometry_tokens):
                continue
            band = self._first_matching_band(rule.get("bands", []), float(relative_speed))
            if band:
                return self._decision(rule, band["score"], band.get("basis", rule.get("basis", "")))
        return None

    def exposure_decision(self, scenario: dict[str, Any]) -> dict[str, Any] | None:
        score = str(scenario.get("exposure_level_candidate", "")).upper()
        method = str(scenario.get("exposure_method", "")).upper()
        if score not in {"E0", "E1", "E2", "E3", "E4"}:
            return None
        if method not in {"T", "F"}:
            return None
        decision = self._decision(
            {"rule_id": str(scenario.get("scenario_variant", "AVP-E-SCENARIO"))},
            score,
            str(scenario.get("exposure_basis", "")).strip(),
        )
        decision["exposure_method"] = method
        return decision

    def controllability_decision(self, scenario: dict[str, Any]) -> dict[str, Any] | None:
        position = str(scenario.get("driver_position", "")).strip().lower()
        direct_control = scenario.get("direct_vehicle_control")
        if not position or not isinstance(direct_control, bool):
            return None
        rules = self.risk_mapping.get("controllability", {}).get("control_context_rules", [])
        for rule in rules:
            if (
                position == str(rule.get("driver_position", "")).strip().lower()
                and direct_control is rule.get("direct_vehicle_control")
            ):
                return self._decision(rule, rule["score"], rule.get("basis", ""))
        return None

    def _decision(self, rule: dict[str, Any], score: str, basis: str) -> dict[str, Any]:
        return {
            "score": score,
            "rule_id": rule["rule_id"],
            "basis": basis,
            "rule_version": self.profile.version,
            "engineering_status": self.risk_mapping.get("evidence_status", "PENDING"),
        }
