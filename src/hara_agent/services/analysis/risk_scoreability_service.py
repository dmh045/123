"""Offline, source-grounded S/C scoreability and differential-queue projection."""

from __future__ import annotations

from collections import Counter, defaultdict
import json
from typing import Any

from hara_agent.contracts import MethodContract
from hara_agent.models import evaluate_risk_eligibility_payload

from .hazardous_event_risk_context_service import HazardousEventRiskContextService
from .risk_vocabulary_adapter import RiskVocabularyAdapter, RiskVocabularyResolution
from .scenario_physics import source_is_accepted_for


class RiskScoreabilityService:
    """Classify analytical options without inventing inputs or calling a Provider."""

    _CONTROL_FIELDS = (
        "driver_in_vehicle",
        "remote_intervention_available",
        "other_road_user_avoidance_possible",
    )
    _PROJECT_FACT_FIELDS = (
        "ego_speed_kph",
        "driver_in_vehicle",
        "remote_intervention_available",
        "other_road_user_avoidance_possible",
        "direct_control_available",
        "emergency_braking_available",
        "vehicle_stability",
    )
    _RISK_ONLY_FIELDS = {
        "road_user_type",
        "collision_type",
        "object_type",
        "object_position",
        "object_speed_kph",
        "relative_distance_m",
    }
    def __init__(self, method: MethodContract):
        self.vocabulary = RiskVocabularyAdapter(method)
        self.contexts = HazardousEventRiskContextService(method)
        physics = method.metadata.get("risk_vocabulary_physics", {})
        if not isinstance(physics, dict):
            raise ValueError("MethodContract risk vocabulary physics must be an object")
        self._longitudinal_collisions = frozenset(
            str(value) for value in physics.get("longitudinal_collision_types", [])
        )
        self._ego_directions = frozenset(
            str(value) for value in physics.get("ego_longitudinal_directions", [])
        )
        self._object_directions = frozenset(
            str(value) for value in physics.get("object_longitudinal_directions", [])
        )
        self._motion_rule_id = str(physics.get("derivation_rule_id", ""))
        if not all((
            self._longitudinal_collisions, self._ego_directions,
            self._object_directions, self._motion_rule_id,
        )):
            raise ValueError("MethodContract risk vocabulary physics is incomplete")

    @staticmethod
    def _point(value: object) -> bool:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0

    @staticmethod
    def _metadata(facts: dict[str, Any], field: str) -> dict[str, Any]:
        provenance = facts.get("_fact_provenance", facts.get("fact_provenance", {}))
        value = provenance.get(field, {}) if isinstance(provenance, dict) else {}
        return dict(value) if isinstance(value, dict) else {}

    def _accepted_fact(
        self, *, facts: dict[str, Any], field: str, malfunction_id: str,
        parent_scenario_id: str,
    ) -> dict[str, Any]:
        value = facts.get(field)
        metadata = self._metadata(facts, field)
        valid = source_is_accepted_for(
            metadata,
            malfunction_id=malfunction_id,
            scenario_id=parent_scenario_id,
        )
        type_valid = (
            self._point(value) if field.endswith("_kph") else
            isinstance(value, bool) if field in self._CONTROL_FIELDS
            or field in {"direct_control_available", "emergency_braking_available"}
            else isinstance(value, str) and bool(value.strip())
        )
        if valid and type_valid:
            origin = str(metadata.get("origin", metadata.get("provenance", ""))).upper()
            return {
                "status": (
                    "EXISTING_PROJECT_FACT"
                    if origin in {"PROJECT_INPUT", "HUMAN_CONFIRMATION"}
                    else "SCENARIO_DEFINED"
                ),
                "value": value,
                "provenance": metadata,
            }
        if field in facts or metadata:
            return {"status": "CONFLICT", "value": None, "provenance": metadata}
        return {"status": "MISSING", "value": None, "provenance": {}}

    @staticmethod
    def _option_values(option: dict[str, Any]) -> dict[str, Any]:
        instance = option.get("analysis_instance", {})
        return dict(instance.get("source_option_values", {})) if isinstance(instance, dict) else {}

    @staticmethod
    def _instance(option: dict[str, Any]) -> dict[str, Any]:
        value = option.get("analysis_instance", {})
        return dict(value) if isinstance(value, dict) else {}

    @staticmethod
    def _canonical_json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def _motion_readiness(
        self, *, facts: dict[str, Any], parent_scenario_id: str,
        malfunction_id: str, object_speed_kph: Any, collision: RiskVocabularyResolution,
    ) -> dict[str, Any]:
        ego = self._accepted_fact(
            facts=facts, field="ego_speed_kph", malfunction_id=malfunction_id,
            parent_scenario_id=parent_scenario_id,
        )
        if not self._point(object_speed_kph):
            return {"status": "MISSING", "reason": "OBJECT_POINT_SPEED_MISSING"}
        if ego["status"] != "EXISTING_PROJECT_FACT":
            return {
                "status": "MISSING",
                "reason": "EGO_POINT_SPEED_ENGINEERING_ASSUMPTION_REQUIRED",
                "ego": ego,
            }
        if collision.canonical_value not in self._longitudinal_collisions:
            return {
                "status": "MISSING",
                "reason": "LATERAL_OR_UNMAPPED_COLLISION_PHYSICS_UNRESOLVED",
                "ego": ego,
            }
        direction_fields = ("ego_longitudinal_direction", "object_longitudinal_direction")
        directions = [
            self._accepted_fact(
                facts=facts, field=field, malfunction_id=malfunction_id,
                parent_scenario_id=parent_scenario_id,
            )
            for field in direction_fields
        ]
        if any(item["status"] not in {"EXISTING_PROJECT_FACT", "SCENARIO_DEFINED"} for item in directions):
            return {
                "status": "MISSING",
                "reason": "LONGITUDINAL_DIRECTION_MISSING",
                "ego": ego,
            }
        ego_direction, object_direction = (
            str(item["value"]).strip().upper() for item in directions
        )
        if (
            ego_direction not in self._ego_directions
            or object_direction not in self._object_directions
        ):
            return {
                "status": "MISSING",
                "reason": "LONGITUDINAL_DIRECTION_UNSUPPORTED",
                "ego": ego,
            }
        opposing = object_direction != "STATIONARY" and ego_direction != object_direction
        speed = (
            float(ego["value"]) + float(object_speed_kph)
            if opposing else abs(float(ego["value"]) - float(object_speed_kph))
        )
        return {
            "status": "DERIVED_PHYSICS",
            "value": round(speed, 6),
            "derivation_rule_id": self._motion_rule_id,
            "input_refs": [
                "SCN.ego_speed_kph", "SCN.ego_longitudinal_direction",
                "SCN.object_longitudinal_direction", "ANALYTICAL.object_speed_kph",
                "ANALYTICAL.collision_type",
            ],
            "input_values": {
                "ego_speed_kph": ego["value"],
                "ego_longitudinal_direction": ego_direction,
                "object_speed_kph": object_speed_kph,
                "object_longitudinal_direction": object_direction,
                "collision_type": collision.canonical_value,
            },
            "motion_relation": (
                "OPPOSING_LONGITUDINAL" if opposing else "SAME_OR_STATIONARY_LONGITUDINAL"
            ),
            "output_unit": "km/h",
            "scope": {"parent_scenario_id": parent_scenario_id},
            "ego": ego,
        }

    @staticmethod
    def _evidence_dependencies(assessment: dict[str, Any]) -> set[str]:
        causal = assessment.get("causal_assessment", {})
        causal = causal if isinstance(causal, dict) else {}
        bindings = causal.get("evidence_bindings", [])
        dependencies: set[str] = set()
        if not isinstance(bindings, list):
            return dependencies
        for binding in bindings:
            if not isinstance(binding, dict):
                continue
            refs = binding.get("evidence_refs", [])
            if isinstance(refs, str):
                refs = [refs]
            if not isinstance(refs, list):
                continue
            for ref in refs:
                text = str(ref)
                if text.startswith("SCN."):
                    dependencies.add(text.removeprefix("SCN."))
        return dependencies

    @staticmethod
    def _physical_dependencies(assessment: dict[str, Any]) -> set[str]:
        dependencies: set[str] = set()
        for key in ("physical_preconditions", "physical_feasibility_dependencies"):
            value = assessment.get(key, [])
            if isinstance(value, dict):
                dependencies.update(str(item) for item in value)
            elif isinstance(value, list):
                dependencies.update(str(item) for item in value)
        return dependencies

    def classify_delta(
        self, *, assessment: dict[str, Any], parent_facts: dict[str, Any],
        child_values: dict[str, Any], parent_scenario_id: str,
        malfunction_id: str,
    ) -> dict[str, Any]:
        """Prove noninterference before causal reuse; otherwise remain conservative."""
        del parent_scenario_id, malfunction_id
        changed = {
            field: value for field, value in child_values.items()
            if field in self._RISK_ONLY_FIELDS or field == "ego_speed_kph"
        }
        conflicts = [
            field for field, value in changed.items()
            if field in parent_facts and parent_facts[field] not in (None, "")
            and parent_facts[field] != value
        ]
        if conflicts:
            return {
                "classification": "SOURCE_CONFLICT",
                "causal_revalidation_required": False,
                "causal_reuse_basis": "",
                "changed_fields": sorted(changed),
                "conflicting_fields": sorted(conflicts),
                "dependency_check": "PARENT_FACT_CONFLICT",
            }
        causal_dependencies = self._evidence_dependencies(assessment)
        physical_dependencies = self._physical_dependencies(assessment)
        dependent = sorted(
            field for field in changed
            if field in causal_dependencies or field in physical_dependencies
        )
        if dependent:
            return {
                "classification": "CAUSAL_RELEVANT_CHANGE",
                "causal_revalidation_required": True,
                "causal_reuse_basis": "",
                "changed_fields": sorted(changed),
                "dependent_fields": dependent,
                "dependency_check": "PARENT_CAUSAL_OR_PHYSICAL_DEPENDENCY",
            }
        if "ego_speed_kph" in changed:
            envelope = parent_facts.get("ego_speed_constraint", {})
            lower = envelope.get("min_kph") if isinstance(envelope, dict) else None
            upper = envelope.get("max_kph") if isinstance(envelope, dict) else None
            value = changed["ego_speed_kph"]
            if (
                self._point(value)
                and (lower is None or value >= lower)
                and (upper is None or value <= upper)
            ):
                return {
                    "classification": "SUBSET_REFINEMENT",
                    "causal_revalidation_required": False,
                    "causal_reuse_basis": "VERIFIED_NONINTERFERENCE",
                    "changed_fields": sorted(changed),
                    "dependency_check": "POINT_WITHIN_PARENT_ENVELOPE_AND_NO_DEPENDENCY",
                }
            return {
                "classification": "CAUSAL_RELEVANT_CHANGE",
                "causal_revalidation_required": True,
                "causal_reuse_basis": "",
                "changed_fields": sorted(changed),
                "dependency_check": "SPEED_NOT_PROVEN_SUBSET",
            }
        if changed and set(changed).issubset(self._RISK_ONLY_FIELDS):
            return {
                "classification": "RISK_ONLY_REFINEMENT",
                "causal_revalidation_required": False,
                "causal_reuse_basis": "VERIFIED_NONINTERFERENCE",
                "changed_fields": sorted(changed),
                "dependency_check": "NO_PARENT_CAUSAL_OR_PHYSICAL_DEPENDENCY",
            }
        return {
            "classification": "UNCLASSIFIED",
            "causal_revalidation_required": True,
            "causal_reuse_basis": "",
            "changed_fields": sorted(changed),
            "dependency_check": "NO_CONSERVATIVE_REUSE_PROOF",
        }

    def _control_readiness(
        self, *, facts: dict[str, Any], parent_scenario_id: str,
        malfunction_id: str, values: dict[str, Any], motion: dict[str, Any],
    ) -> dict[str, Any]:
        accepted_controls = {
            field: self._accepted_fact(
                facts=facts, field=field, malfunction_id=malfunction_id,
                parent_scenario_id=parent_scenario_id,
            )
            for field in self._CONTROL_FIELDS
        }
        scenario = dict(facts)
        scenario_provenance = dict(
            facts.get("_fact_provenance", facts.get("fact_provenance", {}))
        )
        for field, item in accepted_controls.items():
            if item["status"] in {"EXISTING_PROJECT_FACT", "SCENARIO_DEFINED"}:
                scenario[field] = item["value"]
        scenario["_fact_provenance"] = scenario_provenance
        context = self.contexts.build(
            malfunction_id=malfunction_id,
            scenario_id=parent_scenario_id,
            hazard_node_id="SCOREABILITY_PROJECTION",
            scenario=scenario,
        )
        readiness = self.contexts.controllability_readiness(context)
        override_finalized = (
            readiness["status"] == "READY" and readiness["branch"] == "OVERRIDE"
        )
        override_state = (
            "MATCH" if override_finalized else
            "NO_MATCH" if readiness["branch"] == "TTC" else "UNKNOWN"
        )
        blockers = [
            f"{field.upper()}_ENGINEERING_ASSUMPTION_REQUIRED"
            for field, item in accepted_controls.items()
            if item["status"] == "MISSING"
        ]
        blockers.extend(
            f"{field.upper()}_SOURCE_CONFLICT"
            for field, item in accepted_controls.items()
            if item["status"] == "CONFLICT"
        )
        if not override_finalized and readiness["branch"] != "TTC":
            blockers.append("OVERRIDE_BRANCH_UNRESOLVED")
        if readiness["branch"] == "TTC" and readiness["status"] != "READY":
            blockers.extend(readiness["missing_reasons"])
        return {
            "controls": accepted_controls,
            "override_state": override_state,
            "override_finalized": override_finalized,
            "ttc_ready": readiness["status"] == "READY" and readiness["branch"] == "TTC",
            "blockers": sorted(set(blockers)),
            "method_readiness": readiness,
            "motion_status": motion["status"],
            "distance_available": self._point(values.get("obj_distance_m")),
        }

    def _record(
        self, *, option: dict[str, Any], assessment: dict[str, Any],
        scenario: dict[str, Any], malfunction: dict[str, Any],
    ) -> dict[str, Any]:
        instance = self._instance(option)
        values = self._option_values(option)
        malfunction_id = str(option.get("malfunction_id", ""))
        parent_id = str(option.get("parent_scenario_id", ""))
        facts = dict(scenario.get("facts", {}))
        facts["_fact_provenance"] = dict(scenario.get("fact_provenance", {}))
        road = self.vocabulary.resolve(
            field="road_user_type", raw_value=values.get("obj_type", ""),
        )
        collision = self.vocabulary.resolve(
            field="collision_type", raw_value=values.get("collision_type", ""),
        )
        ego = self._accepted_fact(
            facts=facts, field="ego_speed_kph", malfunction_id=malfunction_id,
            parent_scenario_id=parent_id,
        )
        motion = self._motion_readiness(
            facts=facts, parent_scenario_id=parent_id, malfunction_id=malfunction_id,
            object_speed_kph=values.get("obj_v_kph"), collision=collision,
        )
        s_blockers = []
        if not road.mapped:
            s_blockers.append("ROAD_USER_TYPE_UNMAPPED")
        if not collision.mapped:
            s_blockers.append("COLLISION_TYPE_UNMAPPED")
        if ego["status"] == "MISSING":
            s_blockers.append("EGO_POINT_SPEED_ENGINEERING_ASSUMPTION_REQUIRED")
        elif ego["status"] == "CONFLICT":
            s_blockers.append("EGO_POINT_SPEED_SOURCE_CONFLICT")
        if motion["status"] != "DERIVED_PHYSICS":
            s_blockers.append(str(motion["reason"]))
        controls = self._control_readiness(
            facts=facts, parent_scenario_id=parent_id, malfunction_id=malfunction_id,
            values=values, motion=motion,
        )
        child_values = {
            "object_type": values.get("obj_type"),
            "object_position": values.get("obj_position"),
            "relative_distance_m": values.get("obj_distance_m"),
            "object_speed_kph": values.get("obj_v_kph"),
        }
        if road.mapped:
            child_values["road_user_type"] = road.canonical_value
        if collision.mapped:
            child_values["collision_type"] = collision.canonical_value
        delta = self.classify_delta(
            assessment=assessment, parent_facts=facts, child_values=child_values,
            parent_scenario_id=parent_id, malfunction_id=malfunction_id,
        )
        s_ready = not s_blockers
        c_ready = controls["override_finalized"] or controls["ttc_ready"]
        return {
            "malfunction_id": malfunction_id,
            "function_id": str(malfunction.get("function_id", "")),
            "parent_scenario_id": parent_id,
            "template_id": str(instance.get("source_template_id", "")),
            "option_id": str(instance.get("source_option_id", "")),
            "child_identity": {
                "source_template_id": str(instance.get("source_template_id", "")),
                "source_option_id": str(instance.get("source_option_id", "")),
                "method_contract_hash": self.vocabulary.method_contract_hash,
            },
            "mappings": {
                "road_user_type": road.to_dict(),
                "collision_type": collision.to_dict(),
            },
            "project_fact_inventory": {
                field: self._accepted_fact(
                    facts=facts, field=field, malfunction_id=malfunction_id,
                    parent_scenario_id=parent_id,
                )
                for field in self._PROJECT_FACT_FIELDS
            },
            "motion": motion,
            "S": {
                "ready": s_ready,
                "road_user_type_ready": road.mapped,
                "collision_type_ready": collision.mapped,
                "ego_speed": ego,
                "relative_speed": motion,
                "blockers": sorted(set(s_blockers)),
            },
            "C": {"ready": c_ready, **controls},
            "delta": delta,
            "semantic_fingerprint": [
                malfunction_id,
                str(assessment.get("hazardous_event", malfunction.get("functional_effect", ""))),
                road.canonical_value or f"UNMAPPED:{values.get('obj_type', '')}",
                collision.canonical_value or f"UNMAPPED:{values.get('collision_type', '')}",
                str(values.get("obj_position", "")),
                str(values.get("obj_v_kph", "")),
                str(values.get("obj_distance_m", "")),
            ],
        }

    @staticmethod
    def _record_queue_status(record: dict[str, Any]) -> str:
        delta = record["delta"]
        mapping_blocked = not all(
            item["canonical_value"] for item in record["mappings"].values()
        )
        engineering_blocked = any(
            "ENGINEERING_ASSUMPTION_REQUIRED" in code
            for code in record["S"]["blockers"] + record["C"]["blockers"]
        )
        if delta["classification"] == "SOURCE_CONFLICT":
            return "SOURCE_CONFLICT"
        if mapping_blocked:
            return "UNRESOLVED_MAPPING"
        if engineering_blocked:
            return "BLOCKED_ENGINEERING_ASSUMPTION"
        if not delta["causal_revalidation_required"]:
            return "DETERMINISTIC_CAUSAL_REUSE"
        if record["S"]["ready"] and record["C"]["ready"]:
            return "READY_FOR_DIFFERENTIAL_PROVIDER"
        return "DETERMINISTIC_SCORING_ONLY"

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
        records = []
        for option in supplement.get("analytical_options_pending_validation", []):
            if not isinstance(option, dict):
                continue
            key = (str(option.get("malfunction_id", "")), str(option.get("parent_scenario_id", "")))
            assessment = assessments.get(key)
            scenario = scenarios.get(key[1])
            if assessment is None or scenario is None:
                continue
            records.append(self._record(
                option=option, assessment=assessment, scenario=scenario,
                malfunction=malfunctions.get(key[0], {}),
            ))

        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for record in records:
            grouped[self._canonical_json(record["semantic_fingerprint"])].append(record)
        groups = []
        classifications: Counter[str] = Counter()
        delta_classes: Counter[str] = Counter()
        for fingerprint, items in sorted(grouped.items()):
            statuses = {self._record_queue_status(item) for item in items}
            status = next(iter(statuses)) if len(statuses) == 1 else "SOURCE_CONFLICT"
            classifications[status] += 1
            delta_classes.update(item["delta"]["classification"] for item in items)
            groups.append({
                "semantic_fingerprint": json.loads(fingerprint),
                "classification": status,
                "option_count": len(items),
                "options": [item["option_id"] for item in items],
                "causal": {
                    "classes": sorted({item["delta"]["classification"] for item in items}),
                    "causal_revalidation_required": any(
                        item["delta"]["causal_revalidation_required"] for item in items
                    ),
                    "causal_reuse_basis": sorted({
                        item["delta"]["causal_reuse_basis"]
                        for item in items if item["delta"]["causal_reuse_basis"]
                    }),
                },
                "blockers": sorted({
                    code for item in items for code in item["S"]["blockers"] + item["C"]["blockers"]
                }),
            })

        project_inventory: dict[str, Counter[str]] = {
            field: Counter(record["project_fact_inventory"][field]["status"] for record in records)
            for field in self._PROJECT_FACT_FIELDS
        }
        summary = {
            "historical_eligible_relations": len(assessments),
            "template_options": len(records),
            "road_user_mapped": sum(record["mappings"]["road_user_type"]["canonical_value"] != "" for record in records),
            "road_user_unresolved": sum(record["mappings"]["road_user_type"]["canonical_value"] == "" for record in records),
            "collision_mapped": sum(record["mappings"]["collision_type"]["canonical_value"] != "" for record in records),
            "collision_unresolved": sum(record["mappings"]["collision_type"]["canonical_value"] == "" for record in records),
            "ego_point_speed_existing": sum(record["S"]["ego_speed"]["status"] == "EXISTING_PROJECT_FACT" for record in records),
            "ego_point_speed_needs_engineering_decision": sum(record["S"]["ego_speed"]["status"] == "MISSING" for record in records),
            "motion_relation_deterministic": sum(record["motion"]["status"] == "DERIVED_PHYSICS" for record in records),
            "motion_relation_unresolved": sum(record["motion"]["status"] != "DERIVED_PHYSICS" for record in records),
            "s_ready": sum(record["S"]["ready"] for record in records),
            "s_mapping_blocked": sum(any("UNMAPPED" in item for item in record["S"]["blockers"]) for record in records),
            "s_engineering_blocked": sum(any("ENGINEERING_ASSUMPTION_REQUIRED" in item for item in record["S"]["blockers"]) for record in records),
            "s_causal_validation_blocked": sum(record["S"]["ready"] and record["delta"]["causal_revalidation_required"] for record in records),
            "c_override_resolved": sum(record["C"]["override_state"] != "UNKNOWN" for record in records),
            "c_override_finalized": sum(record["C"]["override_finalized"] for record in records),
            "c_ttc_ready": sum(record["C"]["ttc_ready"] for record in records),
            "c_engineering_blocked": sum(any("ENGINEERING_ASSUMPTION_REQUIRED" in item for item in record["C"]["blockers"]) for record in records),
            "project_fact_inventory": {field: dict(sorted(values.items())) for field, values in project_inventory.items()},
        }
        payload = {
            "artifact_version": "risk-scoreability-v2",
            "method_contract_hash": self.vocabulary.method_contract_hash,
            "provider_calls": 0,
            "records": records,
            "summary": summary,
        }
        queue = {
            "artifact_version": "differential-validation-queue-v2",
            "method_contract_hash": self.vocabulary.method_contract_hash,
            "provider_calls": 0,
            "total_options": len(records),
            "deduplicated_semantic_groups": len(groups),
            "deterministic_reuse_groups": sum(not item["causal"]["causal_revalidation_required"] for item in groups),
            "provider_ready_groups": classifications["READY_FOR_DIFFERENTIAL_PROVIDER"],
            "engineering_blocked_groups": classifications["BLOCKED_ENGINEERING_ASSUMPTION"],
            "mapping_blocked_groups": classifications["UNRESOLVED_MAPPING"],
            "source_conflict_groups": classifications["SOURCE_CONFLICT"],
            "classification_counts": dict(sorted(classifications.items())),
            "delta_classification_counts": dict(sorted(delta_classes.items())),
            "groups": groups,
        }
        return payload, queue

    @staticmethod
    def minimal_assumption_pack(
        *, checkpoint_sha256: str, supplement: dict[str, Any], records: list[dict[str, Any]],
    ) -> dict[str, Any]:
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for record in records:
            grouped[record["parent_scenario_id"]].append(record)
        groups = []
        for parent_id, items in sorted(grouped.items()):
            required = []
            if any(item["S"]["ego_speed"]["status"] == "MISSING" for item in items):
                required.append({
                    "field": "ego_speed_kph", "current_status": "MISSING",
                    "allowed_source": "ENGINEERING_ANALYSIS_SETTING",
                    "reason": "No accepted parent-scenario ego point speed exists; an ODD envelope is not a point speed.",
                    "affected_option_count": len(items),
                })
            for field in RiskScoreabilityService._CONTROL_FIELDS:
                if any(item["C"]["controls"][field]["status"] == "MISSING" for item in items):
                    required.append({
                        "field": field, "current_status": "MISSING",
                        "allowed_source": "ENGINEERING_ANALYSIS_SETTING",
                        "reason": "Selected controllability override branch requires an exact accepted value before TTC.",
                        "affected_option_count": len(items),
                    })
            if required:
                groups.append({
                    "group_id": f"PARENT_SCENARIO:{parent_id}",
                    "scope_type": "PARENT_SCENARIO",
                    "scope": {"parent_scenario_id": parent_id},
                    "required_fields": required,
                })
        return {
            "artifact_version": "risk-minimal-assumption-pack-v1",
            "source_run_id": str(supplement.get("source_run_id", "")),
            "checkpoint_sha256": checkpoint_sha256,
            "groups": groups,
            "summary": {
                "groups": len(groups),
                "ego_speed_decision_groups": sum(any(item["field"] == "ego_speed_kph" for item in group["required_fields"]) for group in groups),
                "control_context_decision_groups": sum(any(item["field"] in RiskScoreabilityService._CONTROL_FIELDS for item in group["required_fields"]) for group in groups),
                "affected_options": len(records),
            },
        }
