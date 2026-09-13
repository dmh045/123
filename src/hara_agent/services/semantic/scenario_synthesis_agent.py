from __future__ import annotations

import json
import time
from typing import Any

from hara_agent.contracts import ScenarioSynthesisAssessment, ScenarioSynthesisInput
from hara_agent.infrastructure.llm.protocol import LLMClient, LLMRequest
from hara_agent.services.analysis.scenario_synthesis_service import (
    ConstrainedScenarioSynthesisService, ScenarioSynthesisValidationError,
)


SCENARIO_SYNTHESIS_PROMPT_VERSION = "p5-d2-scenario-synthesis-v2"


class BoundedScenarioSynthesisAgent:
    """Select only from deterministically narrowed Method atom candidates."""

    system_prompt = """You perform bounded semantic selection for a HARA analytical Scenario.
You are not an engineering-rule authority. Select only atom IDs supplied in each dimension's candidate set.
Never invent IDs, context references, project facts, numeric physics values, source status, S/E/C, ASIL, or Exposure ratings.
Preserve only bindings marked non-refinable; refinable parent bindings may be replaced only by supplied compatible candidates. A compound atom must be returned in every dimension it explicitly fills and must not conflict with another atom in those dimensions.
Obey each supplied dimension applicability decision: REQUIRED means exactly one supplied atom, NOT_APPLICABLE means [], and OPTIONAL means zero or one supplied atom.
Every selected_atoms value must be a JSON array: use ["ATOM_ID"] for one atom and [] for an allowed empty dimension. Never return a bare atom-ID string.
Implement the supplied Scenario Coverage Plan exactly. Typical is the representative mechanism case; boundary is near a relevant project/Method/interaction boundary; extreme is more demanding but still project-valid. Never optimize risk, Exposure, S, E, C, or ASIL.
Sibling variants must differ on a primary variation dimension when more than one is requested; environment-only variation is invalid unless the Coverage Plan marks that environment dimension primary.
Return raw JSON matching the schema exactly, without Markdown."""

    def __init__(
        self, client: LLMClient, validator: ConstrainedScenarioSynthesisService,
    ):
        self.client = client
        self.validator = validator

    @staticmethod
    def _schema(synthesis_input: ScenarioSynthesisInput) -> dict[str, Any]:
        properties = {}
        required = []
        for candidate_set in synthesis_input.dimension_candidate_sets:
            ids = [item.atom_id for item in candidate_set.candidates]
            item_schema: dict[str, Any] = {"type": "string"}
            if ids:
                item_schema["enum"] = ids
            applicability = candidate_set.applicability.status.value
            minimum = 1 if applicability == "REQUIRED" else 0
            maximum = 0 if applicability == "NOT_APPLICABLE" else 1
            properties[candidate_set.dimension] = {
                "type": "array", "items": item_schema,
                "minItems": minimum, "maxItems": maximum, "uniqueItems": True,
            }
            required.append(candidate_set.dimension)
        return {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object", "additionalProperties": False,
            "required": ["variants"],
            "properties": {
                "variants": {
                    "type": "array",
                    "minItems": synthesis_input.coverage_plan.desired_variant_count,
                    "maxItems": synthesis_input.coverage_plan.desired_variant_count,
                    "items": {
                        "type": "object", "additionalProperties": False,
                        "required": [
                            "coverage_label", "selected_atoms",
                            "semantic_rationale", "context_refs",
                        ],
                        "properties": {
                            "coverage_label": {
                                "type": "string",
                                "enum": [
                                    str(item["coverage_label"])
                                    for item in synthesis_input.coverage_plan.variant_intents
                                ],
                            },
                            "selected_atoms": {
                                "type": "object", "additionalProperties": False,
                                "required": required, "properties": properties,
                            },
                            "semantic_rationale": {"type": "string", "minLength": 1},
                            "context_refs": {
                                "type": "array", "minItems": 1, "uniqueItems": True,
                                "items": {
                                    "type": "string", "enum": [
                                        "PROJECT.ODD", "MF.description",
                                        "MF.functional_effect", "MF.vehicle_level_hazard",
                                        "HE.hazardous_event", "CAUSAL.summary",
                                        "PARENT.scenario", "METHOD.scenario_atom_catalog",
                                        "METHOD.fm_scenario_template",
                                    ],
                                },
                            },
                        },
                    },
                },
            },
        }

    @staticmethod
    def _user_payload(synthesis_input: ScenarioSynthesisInput) -> dict[str, Any]:
        malfunction = synthesis_input.malfunction
        parent = synthesis_input.parent_scenario
        parent_facts = parent.get("facts", {}) if isinstance(parent, dict) else {}
        causal = synthesis_input.causal_assessment
        candidate_sets = {}
        for item in synthesis_input.dimension_candidate_sets:
            candidate_sets[item.dimension] = {
                "locked_atom_ids": list(item.locked_atom_ids),
                "generation_status": item.generation_status,
                "applicability": item.applicability.to_dict(),
                "binding_decision": item.binding_decision.to_dict(),
                "catalog_size": item.catalog_size,
                "hard_filtered_pool_size": item.hard_filtered_pool_size,
                "shortlist_truncated": item.shortlist_truncated,
                "shortlist_budget": item.shortlist_budget,
                "shortlist_policy": item.shortlist_policy,
                "shortlist_diagnostics": item.shortlist_diagnostics,
                "hard_filter_diagnostics": item.hard_filter_diagnostics,
                "candidates": [{
                    "atom_id": candidate.atom_id,
                    "canonical_atom_id": candidate.canonical_atom_id,
                    "dimensions": list(candidate.dimensions),
                    "label": candidate.label,
                    "speed_range_kph": list(candidate.speed_range_kph)
                    if candidate.speed_range_kph is not None else None,
                    "source_asset": candidate.source_asset,
                    "source_rule": candidate.source_rule,
                    "supporting_context_refs": list(candidate.supporting_context_refs),
                    "candidate_origin": candidate.candidate_origin.value,
                    "selection_reason": candidate.selection_reason,
                    "binding_authority": candidate.binding_authority,
                    "compact_physical_semantics": candidate.method_semantics,
                    "ranking_scores": candidate.ranking_scores,
                    "template_relationship": candidate.template_relationship,
                    "semantic_compatibility": candidate.semantic_compatibility.value,
                    "semantic_family": candidate.semantic_family,
                } for candidate in item.candidates],
            }
        return {
            "semantic_group_id": synthesis_input.semantic_group_id,
            "identities": {
                "malfunction_id": synthesis_input.malfunction_id,
                "parent_scenario_id": synthesis_input.parent_scenario_id,
                "hazardous_event_id": synthesis_input.hazardous_event_id,
                "function_id": synthesis_input.function_id,
            },
            "malfunction": {
                key: malfunction.get(key, "") for key in (
                    "guideword", "description", "functional_effect",
                    "vehicle_level_hazard", "component_category", "failure_type",
                )
            },
            "hazard_and_causal_summary": {
                "hazardous_event": causal.get("hazardous_event", ""),
                "causal_chain": causal.get("causal_chain", []),
                "risk_dimension_changes": causal.get("risk_dimension_changes", []),
            },
            "parent_scenario": {
                "operating_scenario": parent.get("operating_scenario", ""),
                "operating_mode": parent.get("operating_mode", ""),
                "situational_description": parent.get("situational_description", ""),
                "facts": {
                    key: parent_facts.get(key) for key in (
                        "ego_speed_constraint", "object_type", "object_position",
                        "road_user_type", "collision_type", "vehicle_state",
                        "operating_scenario", "road_surface_conditions",
                    ) if key in parent_facts
                },
            },
            "project_odd": synthesis_input.project_context,
            "structured_semantic_query": synthesis_input.structured_semantic_query,
            "dimension_applicability": {
                item.dimension: item.applicability.to_dict()
                for item in synthesis_input.dimension_candidate_sets
            },
            "scenario_coverage_plan": synthesis_input.coverage_plan.to_dict(),
            "fm_scenario_template": synthesis_input.fm_scenario_template,
            "candidate_sets": candidate_sets,
            "required_output_contract": {
                "top_level_keys_exactly": ["variants"],
                "variant_keys_exactly": [
                    "coverage_label", "selected_atoms",
                    "semantic_rationale", "context_refs",
                ],
                "coverage_label_enum": [
                    str(item["coverage_label"])
                    for item in synthesis_input.coverage_plan.variant_intents
                ],
                "variant_count": synthesis_input.coverage_plan.desired_variant_count,
                "selected_atoms_keys_exactly": [
                    item.dimension for item in synthesis_input.dimension_candidate_sets
                ],
                "selected_atoms_value_type": (
                    "JSON array of zero or one supplied atom-ID strings; "
                    "never a bare string"
                ),
                "context_ref_enum": [
                    "PROJECT.ODD", "MF.description", "MF.functional_effect",
                    "MF.vehicle_level_hazard", "HE.hazardous_event",
                    "CAUSAL.summary", "PARENT.scenario",
                    "METHOD.scenario_atom_catalog", "METHOD.fm_scenario_template",
                ],
                "additional_properties": False,
            },
            "explicit_constraints": [
                "IDs must be present in the corresponding candidate set",
                "non-refinable locked atoms must remain unchanged",
                "dimension applicability is deterministic and cannot be changed",
                "variants must satisfy the supplied primary variation dimensions",
                "compound atoms must be repeated across every filled dimension",
                "do not choose by S/E/C/ASIL or Exposure rating",
                "do not output numeric physics assumptions",
            ],
        }

    def _request(
        self, synthesis_input: ScenarioSynthesisInput, *, repair_error: str = "",
    ) -> LLMRequest:
        system = self.system_prompt
        if repair_error:
            system += (
                "\nREPAIR: The previous response was rejected by the deterministic validator: "
                + repair_error
                + ". Re-select from the exact same candidates and return a corrected JSON object."
            )
        return LLMRequest(
            task="select_scenario_synthesis",
            system_prompt=system,
            user_prompt=json.dumps(
                self._user_payload(synthesis_input), ensure_ascii=False, sort_keys=True,
            ),
            schema_name="ScenarioSynthesisSelection",
            prompt_version=SCENARIO_SYNTHESIS_PROMPT_VERSION,
            metadata={
                "malfunction_id": synthesis_input.malfunction_id,
                "scenario_count": 1,
                "semantic_group_id": synthesis_input.semantic_group_id,
                "repair": bool(repair_error),
            },
            max_tokens=4096,
            response_schema=self._schema(synthesis_input),
        )

    def select(
        self, synthesis_input: ScenarioSynthesisInput,
    ) -> tuple[tuple[ScenarioSynthesisAssessment, ...], dict[str, Any]]:
        calls = []
        error = ""
        for attempt in range(2):
            started = time.monotonic()
            response = None
            try:
                response = self.client.complete_json(
                    self._request(synthesis_input, repair_error=error)
                )
                assessments = self.validator.validate_provider_payload(
                    synthesis_input, response.data,
                )
                usage = dict(response.usage)
                calls.append({
                    "attempt": attempt + 1,
                    "request_id": response.request_id,
                    "configured_model": str(getattr(getattr(self.client, "config", None), "model", "")),
                    "resolved_model": response.model,
                    "thinking": str(
                        getattr(getattr(self.client, "config", None), "scenario_thinking", "default")
                    ),
                    "finish_reason": str(usage.get("finish_reason", "")),
                    "reasoning_characters": int(usage.get("reasoning_characters", 0) or 0),
                    "latency_seconds": float(usage.get("latency_seconds", time.monotonic() - started)),
                    "usage": usage,
                    "schema_status": "PASS",
                    "deterministic_validation": "PASS",
                })
                return assessments, {
                    "semantic_group_id": synthesis_input.semantic_group_id,
                    "status": "PASS", "calls": calls,
                    "repairs": attempt, "failure_code": "",
                }
            except Exception as exc:
                code = (
                    exc.code if isinstance(exc, ScenarioSynthesisValidationError)
                    else type(exc).__name__
                )
                error = f"{code}: {exc}"
                call = {
                    "attempt": attempt + 1,
                    "configured_model": str(getattr(getattr(self.client, "config", None), "model", "")),
                    "resolved_model": str(response.model) if response is not None else "",
                    "thinking": str(
                        getattr(getattr(self.client, "config", None), "scenario_thinking", "default")
                    ),
                    "finish_reason": str(response.usage.get("finish_reason", "")) if response is not None else "",
                    "latency_seconds": round(time.monotonic() - started, 3),
                    "schema_status": "PASS" if response is not None else "FAIL",
                    "deterministic_validation": "FAIL",
                    "failure_code": code,
                    "failure_reason": str(exc),
                }
                if response is not None:
                    call.update({
                        "request_id": response.request_id,
                        "reasoning_characters": int(
                            response.usage.get("reasoning_characters", 0) or 0
                        ),
                        "usage": dict(response.usage),
                        "response_shape": {
                            "top_level_type": type(response.data).__name__,
                            "top_level_keys": sorted(response.data)
                            if isinstance(response.data, dict) else [],
                            "variant_keys": sorted(response.data["variants"][0])
                            if (
                                isinstance(response.data, dict)
                                and isinstance(response.data.get("variants"), list)
                                and response.data["variants"]
                                and isinstance(response.data["variants"][0], dict)
                            ) else [],
                        },
                    })
                calls.append(call)
                if attempt == 1:
                    return (), {
                        "semantic_group_id": synthesis_input.semantic_group_id,
                        "status": "PENDING_SCENARIO_SYNTHESIS", "calls": calls,
                        "repairs": 1, "failure_code": code,
                        "failure_reason": str(exc),
                    }
        raise AssertionError("unreachable")


__all__ = [
    "BoundedScenarioSynthesisAgent", "SCENARIO_SYNTHESIS_PROMPT_VERSION",
]
