#!/usr/bin/env python3
"""
HARA Schema Definitions
Unified field name definitions for all JSON interfaces between:
  - Phase1: malfunction_engine → malfunction_output.json
  - Phase2: scen_hazevent_engine → scen_hazevent_output.json
  - Phase3: scoring_sg_engine → scoring_sg_output.json
  - Checkers: malfunction_checker, scenario_checker, scoring_checker
  - Consumer: report_generator

Authoritative source of truth — all field names below must match what
each component produces AND consumes.

Usage:
    from scripts.hara_schema import *

    # Access field definitions
    MALFUNCTION_FIELDS  # {"guideword": str, "malfunction": str, "is_valid": bool}
    SEVERITY_RESULT_FIELDS  # {"hazard_event": str, "severity_score": str, "reasoning": str}

    # Get JSON root key names
    PHASE1_OUTPUT_KEYS  # ("functions", "function_outputs", ...)
"""

from typing import Any

# ============================================================
# Phase1: malfunction_output.json
# ============================================================

PHASE1_OUTPUT_KEYS = (
    "functions",
    "functions_raw",
    "item_semantics",
    "function_outputs",
    "function_guidewords",
    "function_malfunctions",
    "function_hazards",
    "metadata",
)

# functions_raw keeps source traceability and semantic decomposition.  Missing
# source evidence is represented explicitly (for example an empty consequences
# list plus consequence_status), rather than being fabricated by the parser.
PHASE1_FUNCTION_FIELDS: dict[str, type | str] = {
    "id": str,
    "name": str,
    "description": str,
    "output": str,
    "preconditions": list,
    "triggers": list,
    "expected_effects": list,
    "consequences": list,
    "odd_constraints": list,
    "fallback_behaviors": list,
    "source_table": int,
    "source_row": int,
}

# {func_name: output_description}
PHASE1_FUNCTION_OUTPUTS = dict  # str -> str

# {func_name: [guideword_item]}
PHASE1_FUNCTION_GUIDEWORDS = dict  # str -> list[dict]

# guideword item
GUIDEWORD_FIELDS: dict[str, type | str] = {
    "guideword": str,
    "description": str,
}

# {func_name: [malfunction_item]}
PHASE1_FUNCTION_MALFUNCTIONS = dict  # str -> list[dict]

# malfunction item — produced by malfunction_engine Step4
MALFUNCTION_FIELDS: dict[str, type | str] = {
    "guideword": str,
    "malfunction": str,
    "is_valid": bool,          # True/False after malfunction_checker
    "invalid_reason": str,     # reason if is_valid=False
    "suggestion": str,         # correction suggestion
}

# {func_name: [hazard_item]}
PHASE1_FUNCTION_HAZARDS = dict  # str -> list[dict]

# hazard item — produced by malfunction_engine Step5
HAZARD_FIELDS: dict[str, type | str] = {
    "guideword": str,
    # hazard description (from HazardRef or fallback)
    "description": str,
    # hazard category (e.g. "pinch", "collision")
    "category": str,
}

# ============================================================
# Phase2: scen_hazevent_output.json
# ============================================================

PHASE2_OUTPUT_KEYS = (
    "function_scenarios",
    "refined_function_scenarios",
    "hazard_events",
    "scenario_catalog",
    "metadata",
)

# {func_name: {malf_key: [scenario_item]}}
# Key: {malfunction_desc}||{guideword} or {malfunction_desc} (composite key)
PHASE2_FUNCTION_SCENARIOS = dict  # str -> dict[str, list[dict]]

# Top-level scenario item produced by Step6 + Step7 merge
#
# Note: JSON serializes dict keys as strings. When Python dict objects
# were used as JSON keys, they become str(dict) repr which must be
# parsed with ast.literal_eval() then extract "malfunction" field.
SCENARIO_FIELDS: dict[str, type | str] = {
    # Stable foreign key into the top-level scenario_catalog.
    "scenario_id": str,
    "scenario_facts": dict,
    # Original scenario name from ScenarioRef
    "scenario": str,
    # Human-readable summary text — primary field for G column (Situational description)
    "scenario_summary": str,
    # Hazard event text — primary field for I column
    "hazard_event": str,
    # Risk level: 低/中/高/极高
    "risk_level": str,
    # Full situational description — from Step7 refinement (merged into Step6)
    # Used by ExposureReasoningEngine and report_generator
    "situational_description": str,
    # Situational detailing (vehicle state/speed/weather/road surface) — for H column
    "situational_detailing": str,
    # Step7 refinement — detailed narrative of the scenario
    "refined_scenario": str,

    # Step7 refinement fields (populated by _merge_refined_into_function_scenarios)
    "vehicle_state": str,
    "vehicle_speed": str,
    "ego_speed_kph": float,
    "relative_speed_kph": float,
    "longitudinal_acceleration_mps2": float,
    "steering_angle_deg": "float|null",
    "weather_conditions": str,
    "road_surface_conditions": str,
}

# {func_name: {malf_key: [refined_scenario_item]}}
PHASE2_REFINED_SCENARIOS = dict  # str -> dict[str, list[dict]]

REFINED_SCENARIO_FIELDS: dict[str, type | str] = {
    "scenario_id": str,
    "scenario_facts": dict,
    "refined_scenario": str,
    "original_scenario": str,
    "vehicle_state": str,
    "vehicle_speed": str,
    "weather_conditions": str,
    "road_surface_conditions": str,
}

# ============================================================
# Phase2 hazard_events (Step8 output)
# ============================================================

# {func_name: {malf_key: [hazard_event_item]}}
PHASE2_HAZARD_EVENTS = dict  # str -> dict[str, list[dict]]

HAZARD_EVENT_ITEM_FIELDS: dict[str, type | str] = {
    "scenario_id": str,
    "scenario_facts": dict,
    "hazard_event": str,
    # Refined scenario context (may be present after Step8)
    "refined_scenario": str,
}

# ============================================================
# Phase3: scoring_sg_output.json
# ============================================================

PHASE3_OUTPUT_KEYS = (
    "severity_results",
    "exposure_results",
    "controllability_results",
    "asil_results",
    "ftti_results",
    "safety_goals",
    "safe_states",
    "scenario_catalog",
    "metadata",
)

# Top-level: {func: {composite_key: {guideword: [result_items]}}}
# composite_key = f"{malfunction_desc}||{guideword}"

# Severity result item — produced by SeverityReasoningEngine
# Consumed by report_generator: sev_result.get("severity_score"), sev_result.get("reasoning")
SEVERITY_RESULT_FIELDS: dict[str, type | str] = {
    "hazard_event": str,
    "severity_score": str,       # S0/S1/S2/S3
    "reasoning": str,            # K column: Rationale Severity
    "affected_road_users": list,
}

# Exposure result item — produced by ExposureReasoningEngine
# Consumed by report_generator: exp_result.get("exposure_score"), exp_result.get("reasoning")
EXPOSURE_RESULT_FIELDS: dict[str, type | str] = {
    "scenario": str,
    "exposure_score": str,       # E0/E1/E2/E3/E4
    "exposure_method": str,      # T=average operating-time share; F=situation frequency
    "exposure_reason": str,      # N column: Rationale Exposure
    "reasoning": str,            # N column: Rationale Exposure (primary field)
    "_extracted": dict,          # Internal: extracted scenario keywords
}

# Controllability result item — produced by ControllabilityReasoningEngine
# Consumed by report_generator: ctrl_result.get("controllability_score"), ctrl_result.get("reasoning")
CONTROLLABILITY_RESULT_FIELDS: dict[str, type | str] = {
    "hazard_event": str,
    "controllability_score": str,  # C0/C1/C2/C3
    "controllability_reason": str, # P column: Rationale Controllability
    "reasoning": str,              # P column: Rationale Controllability (primary field)
    "logic_types_used": list,
}

# ASIL result item — produced by ASILDeterminer
# Consumed by report_generator: asil_list[si].get("ASIL")
ASIL_RESULT_FIELDS: dict[str, type | str] = {
    "severity": str,       # S0-S3
    "exposure": str,       # E0-E4
    "controllability": str,  # C0-C3
    "ASIL": str,           # QM/A/B/C/D
}

# FTTI result item. TTC is an input only and is never copied into FTTI.
FTTI_RESULT_FIELDS: dict[str, type | str] = {
    "scenario_id": str,
    "ASIL": str,
    "ftti_value_s": "float|null",
    "ftti_requirement": str,
    "ftti_status": str,
    "formula_id": str,
    "formula_status": str,
    "ttc_screening_s": "float|null",
    "calculation_inputs": dict,
    "assumptions": list,
    "basis": str,
    "review_reason": str,
    "source": str,
}

# Safety Goal item — produced by SafetyGoalGenerator
# Consumed by report_generator: sg_item.get("safety_goal")
SAFETY_GOAL_FIELDS: dict[str, type | str] = {
    "ASIL": str,            # A/B/C/D (QM items also present)
    "sg_id": str,           # Stable semantic safety-goal identifier
    "safety_goal": str,     # S column
}

# Safe State item — produced by SafetyStateGenerator
# Consumed by report_generator: ss_item.get("safe_state")
SAFE_STATE_FIELDS: dict[str, type | str] = {
    "sg_id": str,
    "safety_goal": str,
    "safety_state": str,    # T column
}

# ============================================================
# Checker interfaces
# ============================================================

# malfunction_checker.py reads from malfunction_output.json:
#   - function_malfunctions[func] → [{"guideword": str, "malfunction": str, "is_valid": bool}]
#   - function_guidewords[func]  → [{"guideword": str, "description": str}]
# Produces:
#   - check_result: {"is_valid": bool, "invalid_reason": str, "suggestion": str, "guideword": str}

# scenario_checker.py reads from Phase1 + scenario library:
#   - malf_desc, guideword, scenario
# Produces check_result:
SCENARIO_CHECK_RESULT_FIELDS: dict[str, type | str] = {
    "is_valid": bool,
    "malfunction": str,
    "scenario": str,
    "scenario_summary": str,
    "risk_level": str,            # 低/中/高/极高
    "hazard_event": str,
    "should_include": bool,
}

# scoring_checker.py reads from scoring_sg_output.json:
#   - asil_results[func][composite_key][guideword][idx]
#     → {"severity": str, "exposure": str, "controllability": str, "ASIL": str}
#   - safety_goals[func][composite_key][guideword][idx]
#     → {"ASIL": str, "safety_goal": str}
#   - safe_states[func][composite_key][guideword][idx]
#     → {"safety_goal": str, "safety_state": str}

# ============================================================
# report_generator.py consumption map
# ============================================================

# Context fields consumed by _fill_hara_sheet:
REPORT_GENERATOR_CONTEXT_FIELDS = {
    # From malfunction_output.json
    "functions": list,                    # [func_name, ...]
    "function_outputs": dict,             # {func: output_desc}
    "function_guidewords": dict,          # {func: [{"guideword": str}]}
    "function_malfunctions": dict,        # {func: [{"guideword": str, "malfunction": str}]}
    "function_hazards": dict,             # {func: [{"guideword": str, "description": str}]}

    # From scen_hazevent_output.json
    "function_scenarios": dict,           # {func: {malf_key: [scenario_items]}}
    "hazard_events": dict,                # {func: {malf_key: [hazard_event_items]}}

    # From scoring_sg_output.json
    "severity_results": dict,             # {func: {composite_key: {gw: [severity_items]}}}
    "exposure_results": dict,             # {func: {composite_key: {gw: [exposure_items]}}}
    "controllability_results": dict,      # {func: {composite_key: {gw: [ctrl_items]}}}
    "asil_results": dict,                 # {func: {composite_key: {gw: [asil_items]}}}
    "ftti_results": dict,                 # {func: {composite_key: {gw: [ftti_items]}}}
    "safety_goals": dict,                 # {func: {composite_key: {gw: [sg_items]}}}
    "safe_states": dict,                  # {func: {composite_key: {gw: [ss_items]}}}
}

# Per-row field consumption in _fill_hara_sheet (inner loop):
#
# From function_malfunctions[func] (list of dicts):
#   .get("guideword")                          → D column
#   .get("malfunction_description") or  .get("malfunction") → E column
#
# From function_hazards[func] (list of dicts):
#   .get("hazard_description") or .get("description")        → F column
#
# From function_scenarios[func] → malf_key lookup → [scenario_items]:
#   .get("situational_description") or .get("scenario_summary") → G column
#   .get("situational_detailing")                              → H column
#   .get("hazard_event")                                       → I column (with generic-fallback)
#   .get("risk_level")                                         → Remark
#
# From severity_results[func][composite_key][guideword][si]:
#   .get("severity_score")  → J column
#   .get("reasoning")       → K column
#
# From exposure_results[func][composite_key][guideword][si]:
#   .get("exposure_score")  → L column
#   .get("exposure_method") → M column (T=time share, F=frequency; never derived from E level)
#   .get("reasoning")       → N column
#
# From controllability_results[func][composite_key][guideword][si]:
#   .get("controllability_score") → O column
#   .get("reasoning")             → P column
#
# From asil_results[func][composite_key][guideword][si]:
#   .get("ASIL")         → Q column
#
# From safety_goals[func][composite_key][guideword][si]:
#   .get("safety_goal")  → S column
#
# From safe_states[func][composite_key][guideword][si]:
#   .get("safe_state")   → T column

# ============================================================
# Excel column mapping (HARA_Template standard 21-column)
# ============================================================

EXCEL_COLUMNS = {
    1: "HARA-ID",
    2: "Function",
    3: "Output",
    4: "Guide-Word",
    5: "Malfunction",
    6: "Hazard",
    7: "Situational description",
    8: "Situational detailing",
    9: "Hazardous event",
    10: "Severity",            # J column, row5="S"
    11: "Severity_Rationale",  # K column, row5="Rationale Severity Evaluation"
    12: "Exposure",            # L column, row5="E"
    13: "Exposure_TF",         # M column, row5="T/F"
    14: "Exposure_Rationale",  # N column, row5="Rationale Exposure Evaluation"
    15: "Controllability",     # O column, row5="C"
    16: "Controllability_Rationale",  # P column, row5="Rationale Controllability Evaluation"
    17: "ASIL",                # Q column
    18: "SG-ID",               # R column
    19: "Safety Goal",         # S column
    20: "Safe state",          # T column
    21: "Remark",              # U column
    22: "FTTI",                # V column, generated extension
    23: "FTTI_Basis",          # W column, generated extension
}

# ============================================================
# Composite key format
# ============================================================

# Format: f"{malfunction_desc}||{guideword}"
# Used as key in:
#   - scoring_sg_output.json inner dicts
#   - report_generator.py composite_key lookup
# Fallback: bare malfunction_desc when guideword is empty

COMPOSITE_KEY_SEPARATOR = "||"

def to_composite_key(malfunction_desc: str, guideword: str = "") -> str:
    """Build composite key from malfunction description and guideword"""
    if guideword:
        return f"{malfunction_desc}{COMPOSITE_KEY_SEPARATOR}{guideword}"
    return malfunction_desc

def from_composite_key(composite_key: str) -> tuple[str, str]:
    """Parse composite key into (malfunction_desc, guideword)"""
    if COMPOSITE_KEY_SEPARATOR in composite_key:
        parts = composite_key.rsplit(COMPOSITE_KEY_SEPARATOR, 1)
        return parts[0], parts[1]
    return composite_key, ""
