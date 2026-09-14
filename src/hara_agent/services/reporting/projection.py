from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
import re
from typing import Any, Mapping

from hara_agent.contracts import MethodContract
from hara_agent.workflow.state import HARAState

from .engineering_text_mapper import EngineeringReportTextMapper
from .report_schema import ReportSchema
from .view_model import (
    AuditReferenceView, HARAReportRowView, HARAReportViewModel,
    MethodBasisView, SafetyGoalView, ScenarioDetailView, SummaryView,
)


def _value(evidence: Any) -> str:
    value = getattr(evidence, "value", evidence)
    return str(getattr(value, "value", value) or "")


def _status(evidence: Any) -> str:
    value = getattr(evidence, "status", "")
    return str(getattr(value, "value", value))


class HARAReportProjectionService:
    """Project committed runtime facts into reviewer-facing report values."""

    def __init__(self, schema: ReportSchema):
        self.schema = schema
        self.text = EngineeringReportTextMapper()

    @staticmethod
    def _clarifications(risk: Any) -> str:
        ids: list[str] = []
        if _status(risk.severity) != "FINALIZED":
            ids.append("EC-03")
        if _status(risk.exposure) != "FINALIZED":
            ids.append("EC-01")
        if _status(risk.controllability) != "FINALIZED":
            ids.append("EC-02")
        return "; ".join(ids)

    @staticmethod
    def _causal_status_by_pair(
        causal_trace: Mapping[str, Any] | None,
    ) -> dict[tuple[str, str], str]:
        statuses: dict[tuple[str, str], str] = {}
        for audit in (causal_trace or {}).get("audits", []):
            if not isinstance(audit, Mapping):
                continue
            for item in audit.get("item_salvage_audit", []):
                if not isinstance(item, Mapping):
                    continue
                parsed = item.get("parsed_assessment", {})
                if not isinstance(parsed, Mapping):
                    continue
                pair = (
                    str(parsed.get("malfunction_id", "")),
                    str(parsed.get("scenario_id", "")),
                )
                if not all(pair):
                    continue
                statuses[pair] = (
                    "METHOD_VALID — CAUSAL_REVALIDATED"
                    if bool(parsed.get("final_retain"))
                    else "CAUSAL_GAP — EXCLUDED_FROM_RISK"
                )
        return statuses

    @classmethod
    def _assessment_status(
        cls, *, risk: Any, scenario: Any, trace: Mapping[str, Any],
        causal_status: str,
    ) -> str:
        if bool(trace.get("risk_scoring_invoked")):
            return "ELIGIBLE — RISK SCORING INVOKED"
        if causal_status:
            return causal_status
        instance = getattr(scenario, "analysis_instance", {}) or {}
        if str(instance.get("validation_status", "")).upper() == "VALIDATED":
            return "METHOD_VALID — CAUSAL_REVALIDATION_REQUIRED"
        finalized = all(
            _status(getattr(risk, field)) == "FINALIZED"
            for field in ("severity", "exposure", "controllability", "asil")
        )
        return (
            "ELIGIBLE — RISK SCORING INVOKED"
            if finalized else "PENDING — RISK SCORING NOT EXECUTED"
        )

    @staticmethod
    def _normalized_hazardous_event(value: Any) -> str:
        return re.sub(r"[\W_]+", "", str(value or "").casefold())

    @staticmethod
    def _semantic_group_id(scenario: Any) -> str:
        instance = getattr(scenario, "analysis_instance", {}) or {}
        if isinstance(instance, Mapping) and instance.get("semantic_group_id"):
            return str(instance["semantic_group_id"])
        context = getattr(scenario, "context_resolution", {}) or {}
        synthesis = context.get("scenario_synthesis", {}) if isinstance(context, Mapping) else {}
        return str(synthesis.get("semantic_group_id", "")) if isinstance(synthesis, Mapping) else ""

    @staticmethod
    def _parent_scenario_id(scenario: Any) -> str:
        instance = getattr(scenario, "analysis_instance", {}) or {}
        return str(
            getattr(scenario, "source_scenario_id", "")
            or (instance.get("parent_scenario_id", "") if isinstance(instance, Mapping) else "")
            or getattr(scenario, "scenario_id", "")
        )

    @classmethod
    def _base_group_key(
        cls, *, risk: Any, scenario: Any, hazardous_event_id: str,
    ) -> tuple[str, str, str]:
        malfunction_id = str(risk.malfunction_id)
        semantic_group_id = cls._semantic_group_id(scenario)
        if semantic_group_id:
            return "semantic_group", malfunction_id, semantic_group_id
        if hazardous_event_id:
            return "hazardous_event", malfunction_id, hazardous_event_id
        parent_id = cls._parent_scenario_id(scenario)
        if parent_id and parent_id != str(scenario.scenario_id):
            return "parent_scenario", malfunction_id, parent_id
        return "scenario", malfunction_id, str(scenario.scenario_id)

    @classmethod
    def _representative_rank(cls, entry: Mapping[str, Any]) -> tuple[int, str]:
        scenario = entry["scenario"]
        context = getattr(scenario, "context_resolution", {}) or {}
        synthesis = context.get("scenario_synthesis", {}) if isinstance(context, Mapping) else {}
        label = str(synthesis.get("coverage_label", "") if isinstance(synthesis, Mapping) else "")
        if not label:
            label = str(getattr(scenario, "atomic_variant", "") or "").rsplit(":", 1)[-1]
        order = {"typical": 0, "representative": 0, "boundary": 1, "extreme": 2, "demanding": 2}
        return order.get(label.casefold(), 3), str(scenario.scenario_id)

    @staticmethod
    def _selected_atom_ids(scenario: Any) -> str:
        instance = getattr(scenario, "analysis_instance", {}) or {}
        selected = instance.get("selected_atoms", []) if isinstance(instance, Mapping) else []
        if isinstance(selected, Mapping):
            selected = [atom for atoms in selected.values() for atom in atoms]
        if not isinstance(selected, (list, tuple)):
            return ""
        return "; ".join(dict.fromkeys(str(item) for item in selected if str(item)))

    @staticmethod
    def _source_references(scenario: Any) -> str:
        references = []
        for source in getattr(scenario, "sources", ()) or ():
            source_type = str(getattr(source, "source_type", "") or "")
            source_id = str(getattr(source, "source_id", "") or "")
            location = str(getattr(source, "location", "") or "")
            identity = ":".join(item for item in (source_type, source_id) if item)
            references.append(f"{identity}@{location}" if location else identity)
        instance = getattr(scenario, "analysis_instance", {}) or {}
        if isinstance(instance, Mapping):
            references.extend(
                str(item) for item in instance.get("method_facts_used", [])
                if str(item)
            )
        facts = getattr(scenario, "facts", {}) or {}
        speed_context = facts.get("speed_context_resolution", {})
        if isinstance(speed_context, Mapping):
            for source in speed_context.get("source_refs", []):
                if not isinstance(source, Mapping):
                    continue
                identity = ":".join(
                    str(source.get(key, ""))
                    for key in ("source_type", "source_id")
                    if str(source.get(key, ""))
                )
                location = str(source.get("location", ""))
                references.append(f"{identity}@{location}" if location else identity)
        return "; ".join(dict.fromkeys(item for item in references if item))

    @staticmethod
    def _report_scenario(scenario: Any, context: Mapping[str, Any]) -> Any:
        projected = deepcopy(scenario)
        speed = dict(context.get("contextual_speed", {}))
        query = dict(context.get("structured_semantic_query", {}))
        coverage = dict(context.get("coverage_plan", {}))
        semantic_group_id = str(context.get("semantic_group_id", ""))

        facts = dict(getattr(projected, "facts", {}) or {})
        facts.pop("ego_speed_kph", None)
        facts["ego_speed_constraint"] = {
            "min_kph": speed.get("min_kph"),
            "max_kph": speed.get("max_kph"),
        }
        facts["speed_context_resolution"] = speed
        projected.facts = facts

        provenance = dict(getattr(projected, "fact_provenance", {}) or {})
        provenance.pop("ego_speed_kph", None)
        projected.fact_provenance = provenance

        instance = dict(getattr(projected, "analysis_instance", {}) or {})
        instance.update({
            "semantic_group_id": semantic_group_id,
            "parent_scenario_id": str(getattr(scenario, "scenario_id", "")),
            "structured_semantic_query": query,
            "coverage_plan": coverage,
        })
        projected.analysis_instance = instance

        resolution = dict(getattr(projected, "context_resolution", {}) or {})
        synthesis = dict(resolution.get("scenario_synthesis", {}) or {})
        synthesis.pop("coverage_label", None)
        synthesis.update({
            "semantic_group_id": semantic_group_id,
            "coverage_intents": [
                str(item.get("coverage_label", ""))
                for item in coverage.get("variant_intents", [])
                if isinstance(item, Mapping) and str(item.get("coverage_label", ""))
            ],
            "desired_variant_count": int(coverage.get("desired_variant_count", 1)),
            "coverage_status": "PLANNED_NOT_INSTANTIATED",
        })
        resolution["scenario_synthesis"] = synthesis
        projected.context_resolution = resolution
        return projected

    def project(
        self,
        state: HARAState,
        method: MethodContract,
        *,
        risk_trace: Mapping[str, Any] | None = None,
        causal_trace: Mapping[str, Any] | None = None,
        run_summary: Mapping[str, Any] | None = None,
        style_template_hash: str = "",
        risk_trace_reference: str = "",
        scenario_projection_contexts: Mapping[
            tuple[str, str, str], Mapping[str, Any]
        ] | None = None,
    ) -> HARAReportViewModel:
        trace_rows = (risk_trace or {}).get("assessments", [])
        trace_by_pair = {
            (str(item.get("malfunction_id", "")), str(item.get("scenario_id", ""))): item
            for item in trace_rows if isinstance(item, Mapping)
        }
        causal_status_by_pair = self._causal_status_by_pair(causal_trace)
        malfunctions = {str(item.get("malfunction_id", "")): item for item in state.malfunctions}
        scenarios = {str(item.scenario_id): item for item in state.scenarios}
        functions = {str(item.get("function_id", "")): item for item in state.functions}

        entries: list[dict[str, Any]] = []
        base_events: dict[tuple[str, str, str], set[str]] = defaultdict(set)
        group_source_counts: Counter[str] = Counter()
        for risk in state.risk_results:
            scenario = scenarios.get(risk.scenario_id)
            if scenario is None:
                raise ValueError(f"Report projection scenario foreign key missing: {risk.scenario_id}")
            trace = trace_by_pair.get((risk.malfunction_id, risk.scenario_id), {})
            instance = getattr(scenario, "analysis_instance", {}) or {}
            hazardous_event_id = str(
                trace.get("hazardous_event_id", "")
                or (instance.get("hazardous_event_id", "") if isinstance(instance, Mapping) else "")
            )
            if scenario_projection_contexts is not None:
                context_key = (
                    str(risk.malfunction_id),
                    str(risk.scenario_id),
                    hazardous_event_id,
                )
                if context_key not in scenario_projection_contexts:
                    raise ValueError(
                        "Fresh report projection context missing for risk pair: "
                        f"{context_key}"
                    )
                scenario = self._report_scenario(
                    scenario, scenario_projection_contexts[context_key]
                )
            base_key = self._base_group_key(
                risk=risk, scenario=scenario, hazardous_event_id=hazardous_event_id,
            )
            normalized_event = self._normalized_hazardous_event(risk.hazardous_event)
            causal_identity = hazardous_event_id or normalized_event
            event_identity = f"{causal_identity}\0{normalized_event}"
            base_events[base_key].add(event_identity)
            group_key = (
                *base_key,
                hashlib.sha256(event_identity.encode("utf-8")).hexdigest()[:12],
            )
            entries.append({
                "risk": risk, "scenario": scenario, "trace": trace,
                "hazardous_event_id": hazardous_event_id, "base_key": base_key,
                "group_key": group_key, "causal_identity": causal_identity,
            })

        grouped: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
        for entry in entries:
            grouped[entry["group_key"]].append(entry)
        ordered_groups = sorted(
            grouped.values(),
            key=lambda items: min(str(item["scenario"].scenario_id) for item in items),
        )
        group_consistency_failures = []
        for siblings in ordered_groups:
            identities = set()
            for entry in siblings:
                child_risk = entry["risk"]
                child_malfunction = malfunctions.get(child_risk.malfunction_id, {})
                identities.add((
                    str(child_malfunction.get("function_id", "")),
                    str(child_malfunction.get("guideword", "")),
                    str(child_malfunction.get("description", "")),
                    str(child_malfunction.get("vehicle_level_hazard", "")),
                    str(entry["causal_identity"]),
                ))
            if len(identities) != 1:
                group_consistency_failures.append({
                    "group_key": list(siblings[0]["group_key"]),
                    "identity_count": len(identities),
                })
        if group_consistency_failures:
            raise ValueError(
                "Reviewer grouping invariant failed: "
                f"{len(group_consistency_failures)} inconsistent groups"
            )

        rows: list[HARAReportRowView] = []
        details: list[ScenarioDetailView] = []
        audit: list[AuditReferenceView] = []
        variant_counts: Counter[str] = Counter()
        for offset, siblings in enumerate(ordered_groups, start=1):
            siblings.sort(key=self._representative_rank)
            representative = siblings[0]
            risk = representative["risk"]
            scenario = representative["scenario"]
            trace = representative["trace"]
            malfunction = malfunctions.get(risk.malfunction_id, {})
            function = functions.get(str(malfunction.get("function_id", "")), {})
            operational, detail = self.text.scenario(
                scenario,
                variant_count=max(
                    len(siblings), self.text.coverage_variant_count(scenario),
                ),
            )
            severity_trace = trace.get("severity", {}) if isinstance(trace, Mapping) else {}
            exposure_trace = trace.get("exposure", {}) if isinstance(trace, Mapping) else {}
            control_trace = trace.get("controllability", {}) if isinstance(trace, Mapping) else {}
            values = {
                field: _value(getattr(risk, field)) or self.text.pending_value(getattr(risk, field))
                for field in ("severity", "exposure", "controllability", "asil")
            }
            causal_status = causal_status_by_pair.get((risk.malfunction_id, risk.scenario_id), "")
            assessment_status = self._assessment_status(
                risk=risk, scenario=scenario, trace=trace, causal_status=causal_status,
            )
            hara_id = f"HARA_{offset:03d}"
            row = HARAReportRowView(
                hara_id=hara_id,
                malfunction_id=risk.malfunction_id,
                scenario_id=risk.scenario_id,
                hazardous_event_id=representative["hazardous_event_id"],
                function_id=str(malfunction.get("function_id", "")),
                function_name=str(function.get("name", "")),
                function_output=str(function.get("output", "")),
                guideword=str(malfunction.get("guideword", "")),
                malfunction=str(malfunction.get("description", "")),
                hazard=str(malfunction.get("vehicle_level_hazard", "")),
                operational_scenario=operational,
                scenario_detail=detail,
                hazardous_event=self.text.hazardous_event(risk.hazardous_event),
                potential_harm=self.text.potential_harm(risk.potential_harm, severity=risk.severity),
                severity=values["severity"],
                severity_rationale=self.text.severity_rationale(risk.severity, severity_trace),
                exposure=values["exposure"],
                exposure_rationale=self.text.exposure_rationale(risk.exposure, exposure_trace),
                controllability=values["controllability"],
                controllability_rationale=self.text.controllability_rationale(risk.controllability, control_trace),
                asil=values["asil"],
                asil_rationale=self.text.asil_rationale(
                    risk.asil, trace.get("asil", {}) if isinstance(trace, Mapping) else {},
                ),
                ftti="Pending",
                ftti_rationale=self.text.ftti_rationale(),
                sg_id="",
                safety_goal="",
                safe_state="",
                assessment_status=assessment_status,
                clarification_ids=self._clarifications(risk),
                remark="",
            )
            rows.append(row)
            group_source_counts[str(representative["base_key"][0])] += 1

            for entry in siblings:
                child_risk = entry["risk"]
                child = entry["scenario"]
                child_trace = entry["trace"]
                child_causal = causal_status_by_pair.get(
                    (child_risk.malfunction_id, child_risk.scenario_id), "",
                )
                child_status = self._assessment_status(
                    risk=child_risk, scenario=child, trace=child_trace,
                    causal_status=child_causal,
                )
                child_operational, child_detail = self.text.scenario(child)
                variant = self.text.variant_text(child)
                variant_counts[variant] += 1
                details.append(ScenarioDetailView(
                    hara_id=hara_id,
                    variant=variant,
                    scenario_id=child.scenario_id,
                    operational_scenario=child_operational,
                    scenario_detail=child_detail,
                    speed_constraint=self.text.speed_text(child),
                    causal_status=child_status,
                    hazardous_event=self.text.hazardous_event(child_risk.hazardous_event),
                    semantic_group_id=self._semantic_group_id(child),
                    object_interaction_summary=self.text.object_interaction_summary(child),
                ))
                audit.append(AuditReferenceView(
                    run_id=state.run_id,
                    method_hash=str(method.metadata.get("method_source_hash", method.metadata.get("template_hash", ""))),
                    report_schema_hash=self.schema.schema_hash,
                    style_template_hash=style_template_hash,
                    hara_id=hara_id,
                    hazardous_event_id=entry["hazardous_event_id"],
                    scenario_id=child.scenario_id,
                    risk_trace_reference=risk_trace_reference or "risk_execution_trace.json",
                    clarification_ids=self._clarifications(child_risk),
                    assessment_status=child_status,
                    semantic_group_id=self._semantic_group_id(child),
                    parent_scenario_id=self._parent_scenario_id(child),
                    variant=variant,
                    selected_atom_ids=self._selected_atom_ids(child),
                    source_references=self._source_references(child),
                ))

        summary = run_summary or {}

        def count(field: str, status: str = "FINALIZED") -> int:
            return sum(_status(getattr(item, field)) == status for item in state.risk_results)

        method_hash = str(method.metadata.get("method_source_hash", method.metadata.get("template_hash", "")))
        clarification_ids = "; ".join(sorted({
            item for row in rows for item in row.clarification_ids.split("; ") if item
        }))
        summary_view = SummaryView(
            run_id=state.run_id,
            method_source=str(state.method_contract.get("source_kind", "YAML_BASELINE")),
            method_hash=method_hash,
            report_schema=self.schema.report_id,
            report_schema_version=self.schema.schema_version,
            report_schema_hash=self.schema.schema_hash,
            style_template_hash=style_template_hash,
            report_status=(
                "SCENARIO SYNTHESIS COMPLETE — CAUSAL REVALIDATION IN PROGRESS — "
                "RISK SCORING NOT YET EXECUTED"
                if any(row.assessment_status != "ELIGIBLE — RISK SCORING INVOKED" for row in rows)
                else "DRAFT_READY"
            ),
            release_status="NOT FOR RELEASE",
            function_count=len(state.functions),
            guideword_assessment_count=len(state.guideword_assessments),
            malfunction_count=len(state.malfunctions),
            scenario_count=len(state.scenarios),
            eligible_hazardous_event_count=len(rows),
            severity_finalized=count("severity"),
            severity_pending=len(state.risk_results) - count("severity"),
            exposure_finalized=count("exposure"),
            exposure_pending=len(state.risk_results) - count("exposure"),
            controllability_finalized=count("controllability"),
            controllability_pending=len(state.risk_results) - count("controllability"),
            asil_finalized=count("asil"),
            asil_pending=len(state.risk_results) - count("asil"),
            clarification_ids=clarification_ids,
        )
        basis = MethodBasisView(
            method_source=summary_view.method_source,
            guidewords=f"{len(method.guidewords.guidewords)} 个启用的引导词",
            severity="基于相对速度；依赖交通参与者与碰撞配置",
            exposure="基于 MethodContract 场景维度的确定性评定",
            controllability="基于 MethodContract 的可控性模型；TTC 证据保持可追溯",
            asil="当前 MethodContract 矩阵",
            ftti="方法源已提供；运行时未启用",
        )
        goals = tuple(
            SafetyGoalView(goal.sg_id, goal.text, goal.safe_state, goal.max_asil, "")
            for goal in state.safety_goals
        )
        divergence_groups = sum(max(0, len(events) - 1) for events in base_events.values())
        speed_values = [item.speed_constraint for item in details if item.speed_constraint]
        projection_metrics = {
            "child_scenario_rows": len(details),
            "main_hara_groups": len(rows),
            "grouping_reduction": len(details) - len(rows),
            "groups_not_merged_due_hazardous_event_divergence": divergence_groups,
            "group_source_counts": dict(sorted(group_source_counts.items())),
            "variant_distribution": dict(sorted(variant_counts.items())),
            "distinct_visible_speed_expressions": len(set(speed_values)),
            "broad_0_20_speed_rows": sum("0–20 km/h" in value for value in speed_values),
            "source_conflict_speed_rows": sum("来源存在冲突" in value for value in speed_values),
            "contextual_speed_rows": sum(
                isinstance(
                    (getattr(item["scenario"], "facts", {}) or {}).get(
                        "speed_context_resolution"
                    ), Mapping,
                )
                and str(
                    (getattr(item["scenario"], "facts", {}) or {}).get(
                        "speed_context_resolution", {}
                    ).get("classification", "")
                ) == "CONTEXTUAL_SPEED_CONSUMED"
                for item in entries
            ),
            "group_consistency_failures": len(group_consistency_failures),
        }
        return HARAReportViewModel(
            tuple(rows), summary_view, basis, goals, tuple(audit), self.schema.schema_hash,
            method_hash, style_template_hash, tuple(details), projection_metrics,
        )


__all__ = ["HARAReportProjectionService"]
