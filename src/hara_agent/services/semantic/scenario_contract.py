from __future__ import annotations


SCENARIO_CONTRACT_VERSION = "atomic-scenario-v2"


# Authoritative machine values for ScenarioFeasibilityAssessment.risk_dimensions_changed.
# Ordering is representation-only and carries no business priority.
RISK_DIMENSION_VALUES: tuple[str, ...] = (
    "collision_object",
    "relative_speed",
    "distance",
    "collision_geometry",
    "operating_mode",
    "driver_intervention",
    "severity",
    "exposure",
    "controllability",
    "ftti",
    "safe_state",
)
