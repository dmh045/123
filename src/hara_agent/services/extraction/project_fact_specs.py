from __future__ import annotations

from dataclasses import dataclass

from hara_agent.models import ProjectFactOutputType

from .evidence_retrieval import FactRetrievalSpec


@dataclass(frozen=True)
class RequiredProjectFactSpec:
    """Extraction schema only: terminology and required shape, never expected values."""

    fact_type: str
    output_type: ProjectFactOutputType
    aliases: tuple[str, ...]
    unit_hints: tuple[str, ...]
    context_hints: tuple[str, ...]
    required_fields: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.fact_type or not self.aliases or not self.required_fields:
            raise ValueError("RequiredProjectFactSpec requires identity, aliases and fields")
        forbidden = {"expected_value", "gold", "source_block_ids", "source_location"}
        if forbidden & set(self.required_fields):
            raise ValueError("RequiredProjectFactSpec cannot encode Gold answers or locators")

    def retrieval_spec(self) -> FactRetrievalSpec:
        return FactRetrievalSpec(
            fact_type=self.fact_type,
            aliases=self.aliases,
            unit_hints=self.unit_hints,
            context_hints=self.context_hints,
        )


SPEED_PROJECT_FACT_SPECS = (
    RequiredProjectFactSpec("speed.search", ProjectFactOutputType.SPEED_ENVELOPE,
        ("搜索车位", "search parking space", "search speed"), ("km/h", "kph"),
        ("search", "搜索"), ("status", "operator", "value", "value_max", "unit", "operating_mode", "source_block_id")),
    RequiredProjectFactSpec("speed.control", ProjectFactOutputType.SPEED_ENVELOPE,
        ("控车范围", "vehicle control range", "control speed"), ("km/h", "kph"),
        ("control", "控车"), ("status", "operator", "value", "value_max", "unit", "operating_mode", "source_block_id")),
    RequiredProjectFactSpec("speed.parking", ProjectFactOutputType.SPEED_ENVELOPE,
        ("泊车时的最大车速", "parking maximum speed", "parking speed"), ("km/h", "kph"),
        ("parking", "泊车"), ("status", "operator", "value", "unit", "operating_mode", "source_block_id")),
)

PERFORMANCE_PROJECT_FACT_SPECS = (
    RequiredProjectFactSpec("performance.normal_braking", ProjectFactOutputType.NUMERIC_CONSTRAINT,
        ("normal_braking_deceleration", "正常制动", "正常巡航", "normal braking"), ("m/s²", "m/s2"),
        ("normal_cruise", "纵向减速度"), ("status", "parameter", "operator", "value", "unit", "condition", "source_block_id")),
    RequiredProjectFactSpec("performance.emergency_braking", ProjectFactOutputType.NUMERIC_CONSTRAINT,
        ("emergency_braking_deceleration", "紧急制动", "emergency braking"), ("m/s²", "m/s2"),
        ("emergency_braking", "最大减速度"), ("status", "parameter", "operator", "value", "unit", "condition", "source_block_id")),
    RequiredProjectFactSpec("performance.brake_response", ProjectFactOutputType.NUMERIC_CONSTRAINT,
        ("brake_response_time", "泊车制动", "制动响应"), ("ms", "s"),
        ("parking_brake_control", "响应时间"), ("status", "parameter", "operator", "value", "unit", "condition", "source_block_id")),
    RequiredProjectFactSpec("performance.steering_error", ProjectFactOutputType.NUMERIC_CONSTRAINT,
        ("steering_steady_state_error", "角度稳态误差", "转向误差"), ("deg", "°"),
        ("steering_control", "转向控制"), ("status", "parameter", "operator", "value", "unit", "condition", "source_block_id")),
    RequiredProjectFactSpec("performance.steering_response", ProjectFactOutputType.NUMERIC_CONSTRAINT,
        ("steering_response_time", "角度响应时间", "转向响应"), ("ms", "s"),
        ("steering_control", "转向控制", "TBD"), ("status", "parameter", "operator", "value", "unit", "condition", "qualification", "source_block_id")),
)

DRIVER_PROJECT_FACT_SPECS = (
    RequiredProjectFactSpec("driver.inside", ProjectFactOutputType.DRIVER_CONTEXT,
        ("在驾驶位", "驾驶员在车内", "driver inside"), (), ("驾驶员位置",),
        ("status", "driver_location", "control_mode", "condition", "source_block_id")),
    RequiredProjectFactSpec("driver.outside", ProjectFactOutputType.DRIVER_CONTEXT,
        ("不在驾驶位", "驾驶员在车外", "driver outside"), (), ("驾驶员位置",),
        ("status", "driver_location", "control_mode", "condition", "source_block_id")),
)

PROJECT_FACT_SPEC_BATCHES = {
    "speed": SPEED_PROJECT_FACT_SPECS,
    "performance": PERFORMANCE_PROJECT_FACT_SPECS,
    "driver": DRIVER_PROJECT_FACT_SPECS,
}
