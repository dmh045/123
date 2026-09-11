"""Offline S/C scoreability projection for existing analytical options."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from hara_agent.models import evaluate_risk_eligibility_payload


class RiskScoreabilityService:
    """Classify existing option facts without creating facts or scoring a risk.

    Analytical template values remain controlled assumptions.  This service
    only shows which additional point facts and differential validation would
    be needed before they could become a risk input.
    """

    @staticmethod
    def _point(value: object) -> bool:
        return isinstance(value, (int, float)) and not isinstance(value, bool)

    @classmethod
    def _approved_parent_point(cls, facts: dict[str, Any], field: str) -> bool:
        provenance = facts.get("_fact_provenance", facts.get("fact_provenance", {}))
        metadata = provenance.get(field, {}) if isinstance(provenance, dict) else {}
        return (
            cls._point(facts.get(field))
            and isinstance(metadata, dict)
            and str(metadata.get("approval", "")).upper() == "FINALIZED"
            and bool(metadata.get("source_refs"))
        )

    @staticmethod
    def _option_values(option: dict[str, Any]) -> dict[str, Any]:
        instance = option.get("analysis_instance", {})
        return dict(instance.get("source_option_values", {})) if isinstance(instance, dict) else {}

    @staticmethod
    def _option_assumptions(option: dict[str, Any]) -> dict[str, Any]:
        instance = option.get("analysis_instance", {})
        assumptions = instance.get("assumptions", []) if isinstance(instance, dict) else []
        return {
            str(item.get("field", "")): item.get("value")
            for item in assumptions if isinstance(item, dict)
        }

    @staticmethod
    def _control_state(facts: dict[str, Any]) -> tuple[dict[str, bool], str]:
        fields = (
            "driver_in_vehicle", "remote_intervention_available",
            "other_road_user_avoidance_possible",
        )
        ready = {field: isinstance(facts.get(field), bool) for field in fields}
        if not all(ready.values()):
            return ready, "UNKNOWN"
        # This projection intentionally does not calculate a C value.  A
        # complete control context is only enough to enter the existing C
        # method and establish whether its override branch matches.
        return ready, "READY_FOR_OVERRIDE_EVALUATION"

    @staticmethod
    def _fingerprint(
        *, malfunction_id: str, malfunction: dict[str, Any], assessment: dict[str, Any],
        values: dict[str, Any], facts: dict[str, Any],
    ) -> tuple[str, ...]:
        control_context = "/".join(
            f"{field}={facts.get(field, 'UNKNOWN')}" for field in (
                "driver_in_vehicle", "remote_intervention_available",
                "other_road_user_avoidance_possible",
            )
        )
        return (
            malfunction_id,
            str(assessment.get("hazardous_event") or malfunction.get("functional_effect", "")),
            str(values.get("obj_type", "")),
            str(values.get("collision_type", "")),
            str(facts.get("ego_motion_relation", facts.get("vehicle_state", "UNKNOWN"))),
            str(facts.get("object_motion_relation", "UNKNOWN")),
            control_context,
        )

    def generate(
        self, *, checkpoint: dict[str, Any], supplement: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        scenarios = {
            str(item.get("scenario_id", "")): item
            for item in checkpoint.get("scenarios", []) if isinstance(item, dict)
        }
        malfunctions = {
            str(item.get("malfunction_id", "")): item
            for item in checkpoint.get("malfunctions", []) if isinstance(item, dict)
        }
        assessments = {
            (str(item.get("malfunction_id", "")), str(item.get("scenario_id", ""))): item
            for item in checkpoint.get("item_definition", {}).get("scenario_assessments", [])
            if isinstance(item, dict) and evaluate_risk_eligibility_payload(item).eligible
        }
        records: list[dict[str, Any]] = []
        groups: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
        raw_options = supplement.get("analytical_options_pending_validation", [])
        for option in raw_options:
            if not isinstance(option, dict):
                continue
            malfunction_id = str(option.get("malfunction_id", ""))
            parent_id = str(option.get("parent_scenario_id", ""))
            assessment = assessments.get((malfunction_id, parent_id))
            scenario = scenarios.get(parent_id)
            if assessment is None or scenario is None:
                continue
            facts = dict(scenario.get("facts", {}))
            values = self._option_values(option)
            assumptions = self._option_assumptions(option)
            ego_speed_ready = self._approved_parent_point(facts, "ego_speed_kph")
            object_speed_ready = self._point(values.get("obj_v_kph"))
            motion_ready = bool(
                facts.get("ego_motion_relation")
                and facts.get("object_motion_relation")
            )
            relative_speed_derivable = ego_speed_ready and object_speed_ready and motion_ready
            s_blockers = []
            if not assumptions.get("road_user_type"):
                s_blockers.append("ROAD_USER_TYPE_UNMAPPED")
            if not assumptions.get("collision_type"):
                s_blockers.append("COLLISION_TYPE_UNMAPPED")
            if not ego_speed_ready:
                s_blockers.append("EGO_POINT_SPEED_ENGINEERING_ASSUMPTION_REQUIRED")
            if not object_speed_ready:
                s_blockers.append("OBJECT_POINT_SPEED_MISSING")
            if not motion_ready:
                s_blockers.append("MOTION_RELATION_MISSING")

            controls, override_state = self._control_state(facts)
            distance_ready = self._point(values.get("obj_distance_m"))
            relative_speed_ready = self._approved_parent_point(facts, "relative_speed_kph")
            ttc_ready = (
                override_state == "NO_MATCH" and distance_ready and relative_speed_ready
            )
            c_blockers = [
                f"{field.upper()}_ENGINEERING_ASSUMPTION_REQUIRED"
                for field, ready in controls.items() if not ready
            ]
            if override_state == "UNKNOWN":
                c_blockers.append("OVERRIDE_BRANCH_UNRESOLVED")
            elif not relative_speed_ready:
                c_blockers.append("RELATIVE_SPEED_MISSING_FOR_TTC")
            elif not distance_ready:
                c_blockers.append("DISTANCE_MISSING_FOR_TTC")

            instance = option.get("analysis_instance", {})
            fingerprint = self._fingerprint(
                malfunction_id=malfunction_id,
                malfunction=malfunctions.get(malfunction_id, {}),
                assessment=assessment, values=values, facts=facts,
            )
            row = {
                "malfunction_id": malfunction_id,
                "parent_scenario_id": parent_id,
                "template_id": str(instance.get("source_template_id", "")) if isinstance(instance, dict) else "",
                "option_id": str(instance.get("source_option_id", "")) if isinstance(instance, dict) else "",
                "S": {
                    "road_user_type_ready": bool(assumptions.get("road_user_type")),
                    "collision_type_ready": bool(assumptions.get("collision_type")),
                    "ego_speed_ready": ego_speed_ready,
                    "object_speed_ready": object_speed_ready,
                    "relative_speed_derivable": relative_speed_derivable,
                    "blocker": s_blockers,
                },
                "C": {
                    "driver_in_vehicle_ready": controls["driver_in_vehicle"],
                    "remote_intervention_ready": controls["remote_intervention_available"],
                    "other_road_user_avoidance_ready": controls["other_road_user_avoidance_possible"],
                    "distance_ready": distance_ready,
                    "relative_speed_ready": relative_speed_ready,
                    "override_state": override_state,
                    "ttc_ready": ttc_ready,
                    "blocker": c_blockers,
                },
                "causal_revalidation_required": True,
                "reason": str(option.get("causal_status", "PENDING_DIFFERENTIAL_VALIDATION")),
                "semantic_fingerprint": list(fingerprint),
            }
            records.append(row)
            groups[fingerprint].append(row)

        queue_groups = []
        classification_counts: Counter[str] = Counter()
        for fingerprint, items in sorted(groups.items()):
            all_blockers = [
                blocker for item in items for section in ("S", "C")
                for blocker in item[section]["blocker"]
            ]
            engineering_codes = {
                blocker for blocker in all_blockers
                if "ENGINEERING_ASSUMPTION_REQUIRED" in blocker
                or blocker == "OVERRIDE_BRANCH_UNRESOLVED"
            }
            only_engineering = bool(all_blockers) and set(all_blockers) <= engineering_codes
            classification = (
                "BLOCKED_ENGINEERING_ASSUMPTION" if only_engineering
                else "PROVIDER_REQUIRED" if any(item["causal_revalidation_required"] for item in items)
                else "DETERMINISTIC_ONLY"
            )
            classification_counts[classification] += 1
            queue_groups.append({
                "semantic_fingerprint": list(fingerprint),
                "classification": classification,
                "option_count": len(items),
                "options": [item["option_id"] for item in items],
                "causal_revalidation_required": any(
                    item["causal_revalidation_required"] for item in items
                ),
                "blockers": sorted(set(all_blockers)),
            })

        summary = {
            "historical_eligible_relations": len(assessments),
            "template_options": len(records),
            "relative_speed_ready": sum(item["S"]["relative_speed_derivable"] for item in records),
            "requires_ego_point_assumption": sum(
                "EGO_POINT_SPEED_ENGINEERING_ASSUMPTION_REQUIRED" in item["S"]["blocker"]
                for item in records
            ),
            "override_resolved": sum(item["C"]["override_state"] == "NO_MATCH" for item in records),
            "ttc_ready": sum(item["C"]["ttc_ready"] for item in records),
            "requires_control_context_assumption": sum(
                item["C"]["override_state"] == "UNKNOWN" for item in records
            ),
            "other_s_blockers": Counter(
                blocker for item in records for blocker in item["S"]["blocker"]
                if blocker != "EGO_POINT_SPEED_ENGINEERING_ASSUMPTION_REQUIRED"
            ),
            "other_c_blockers": Counter(
                blocker for item in records for blocker in item["C"]["blocker"]
                if "ENGINEERING_ASSUMPTION_REQUIRED" not in blocker
            ),
        }
        payload = {
            "artifact_version": "risk-scoreability-v1",
            "provider_calls": 0,
            "records": records,
            "summary": {key: dict(value) if isinstance(value, Counter) else value for key, value in summary.items()},
        }
        queue = {
            "artifact_version": "differential-validation-queue-v1",
            "provider_calls": 0,
            "total_options": len(records),
            "deduplicated_semantic_groups": len(queue_groups),
            "provider_required_groups": classification_counts["PROVIDER_REQUIRED"],
            "deterministic_only_groups": classification_counts["DETERMINISTIC_ONLY"],
            "groups_blocked_only_by_missing_engineering_assumption": classification_counts["BLOCKED_ENGINEERING_ASSUMPTION"],
            "groups": queue_groups,
        }
        return payload, queue
