from __future__ import annotations

from collections import Counter
from typing import Any

from hara_agent.contracts import MethodContract

from .method_atom_resolver import MethodAtomResolver


_PARKING_SLOT_GEOMETRY_TERMS = frozenset({
    "平面车位",
    "空间车位",
    "水平车位",
    "垂直车位",
    "斜向车位",
})


class ExposureBindingAuditService:
    """Read-only audit of governed Scenario atom inputs to Exposure.

    The service consumes an already-compiled MethodContract and persisted
    Scenario projections.  It never reads a YAML asset and never changes the
    exposure executor or Scenario truth.
    """

    def __init__(self, method: MethodContract):
        self.method = method
        self.resolver = MethodAtomResolver(method)
        structured = method.structured_risk_method
        self.exposure_atoms = {
            atom.atom_id: atom for atom in (structured.exposure.atoms if structured else ())
        }
        self.domain_rules = tuple(structured.exposure.domain_rules if structured else ())

    @staticmethod
    def _bindings(candidate: dict[str, Any]) -> dict[str, dict[str, Any]]:
        context = candidate.get("context_resolution", {})
        facts = candidate.get("facts", {})
        value = (
            context.get("dimension_bindings")
            if isinstance(context, dict) else None
        )
        if not isinstance(value, dict) and isinstance(facts, dict):
            value = facts.get("method_scenario_dimensions")
        return {
            str(name): dict(binding)
            for name, binding in (value or {}).items()
            if isinstance(binding, dict)
        }

    @staticmethod
    def _source_location(candidate: dict[str, Any], binding: dict[str, Any]) -> str:
        sources = candidate.get("source_refs", [])
        if isinstance(sources, list):
            for source in sources:
                if isinstance(source, dict) and source.get("source_type") == "item_definition":
                    return str(source.get("location", ""))
        return str(binding.get("dimension_source", ""))

    @staticmethod
    def _where_audit_classification(term: str) -> str:
        """Classify review terms without introducing a runtime Method binding."""
        if term.strip() in _PARKING_SLOT_GEOMETRY_TERMS:
            return "PROJECT_CONTEXT_NO_METHOD_DIMENSION"
        return "PENDING_PROJECT_BINDING"

    def _binding_row(
        self, candidate: dict[str, Any], dimension: str, binding: dict[str, Any],
    ) -> dict[str, Any]:
        atom_id = str(binding.get("atom_id", "")).strip()
        atom = self.resolver.by_id.get(atom_id, {})
        exposure_atom = self.exposure_atoms.get(atom_id)
        source_term = str(binding.get("project_value", ""))
        classification = (
            self._where_audit_classification(source_term)
            if dimension == "WHERE" and source_term
            else ""
        )
        has_method_dimension = classification != "PROJECT_CONTEXT_NO_METHOD_DIMENSION"
        return {
            "scenario_id": str(candidate.get("scenario_id", "")),
            "source_term": source_term,
            "source_location": self._source_location(candidate, binding),
            "current_dimension": dimension,
            "current_binding_status": str(binding.get("resolution_status", "PENDING")),
            "binding_reason": str(binding.get("unresolved_reason", "")),
            "audit_classification": classification,
            "candidate_method_dimensions": (
                (list(binding.get("filled_dimensions", [])) or [dimension])
                if has_method_dimension else []
            ),
            "candidate_atoms": list(binding.get("candidate_atom_ids", [])) if has_method_dimension else [],
            "atom_id": atom_id,
            "atom_label": str(atom.get("label", "")),
            "atom_e_class": {
                "E_Z": getattr(exposure_atom, "duration_level", ""),
                "E_F": getattr(exposure_atom, "frequency_level", ""),
            },
            "resolution_basis": (
                str(binding.get("speed_constraint", {}).get("resolved_by", ""))
                if isinstance(binding.get("speed_constraint"), dict)
                else ""
            ) or str(binding.get("unresolved_reason", "")),
            "method_hash": str(candidate.get("method_source_hash", "")),
            "would_be_E_input": bool(atom_id and exposure_atom is not None),
        }

    def _range_preview(self, binding: dict[str, Any]) -> dict[str, Any] | None:
        constraint = binding.get("speed_constraint")
        if not isinstance(constraint, dict):
            return None
        result = self.resolver.resolve_speed_range(
            minimum_kph=constraint.get("min_kph"),
            maximum_kph=constraint.get("max_kph"),
        )
        atom = self.resolver.by_id.get(result.atom_id, {})
        return {
            "status": result.status,
            "reason": result.reason,
            "atom_id": result.atom_id,
            "atom_label": str(atom.get("label", "")),
            "candidate_atom_ids": list(result.candidate_atom_ids),
            "range_kph": list(result.compatible_range_kph or ()),
        }

    def _component_domains(self, malfunctions: list[dict[str, Any]]) -> dict[str, Any]:
        rows = []
        for malfunction in malfunctions:
            category = str(malfunction.get("component_category", "")).strip()
            matches = [rule for rule in self.domain_rules if category in rule.component_categories]
            rows.append({
                "malfunction_id": str(malfunction.get("malfunction_id", "")),
                "component_category": category,
                "status": "RESOLVED" if len(matches) == 1 else "PENDING_COMPONENT_DOMAIN",
                "domain": matches[0].domain.value if len(matches) == 1 else "",
                "rule_id": matches[0].rule_id if len(matches) == 1 else "",
            })
        return {
            "resolved_malfunctions": sum(row["status"] == "RESOLVED" for row in rows),
            "pending_malfunctions": sum(row["status"] != "RESOLVED" for row in rows),
            "by_component_category": dict(Counter(
                f"{row['component_category']}:{row['domain'] or row['status']}" for row in rows
            )),
            "records": rows,
        }

    def generate(
        self,
        records: dict[str, list[dict[str, Any]]],
        gap_payload: dict[str, Any],
    ) -> dict[str, Any]:
        candidates = [item for item in records.get("scenario_candidate", []) if isinstance(item, dict)]
        malfunctions = [item for item in records.get("malfunction", []) if isinstance(item, dict)]
        component = self._component_domains(malfunctions)
        domain_by_malfunction = {
            row["malfunction_id"]: row for row in component["records"]
        }
        inventory: list[dict[str, Any]] = []
        readiness: list[dict[str, Any]] = []
        routing = Counter()
        project_binding = Counter()
        range_binding = Counter()
        coverage_dimensions = gap_payload.get("scenario_binding_coverage", {}).get(
            "dimensions", {}
        )
        coverage_governance = gap_payload.get("coverage_governance", {})
        if not isinstance(coverage_governance, dict):
            coverage_governance = {}
        coverage_status = str(coverage_governance.get("status", "PENDING")).upper()
        if coverage_status not in {"PENDING", "RESOLVED"}:
            coverage_status = "PENDING"
        required_dimensions = sorted({
            str(item) for item in coverage_governance.get("required_dimensions", [])
            if str(item).strip()
        })
        if isinstance(coverage_dimensions, dict):
            for dimension, coverage in coverage_dimensions.items():
                if not isinstance(coverage, dict):
                    continue
                for value in coverage.get("context_values", []):
                    if not isinstance(value, dict):
                        continue
                    term = str(value.get("source_value", "")).strip()
                    if not term:
                        continue
                    classification = ""
                    if dimension == "ROAD":
                        classification = "PENDING_NO_METHOD_ATOM"
                        routing["ROUTED_TO_ROAD"] += 1
                    elif dimension == "EGO_ACTION":
                        classification = "SEMANTIC_LEVEL_MISMATCH"
                        routing[classification] += 1
                    elif dimension == "WHERE":
                        classification = self._where_audit_classification(term)
                        project_binding[classification] += 1
                    has_method_dimension = classification != "PROJECT_CONTEXT_NO_METHOD_DIMENSION"
                    inventory.append({
                        "scenario_id": "",
                        "source_term": term,
                        "source_location": "ProjectFacts (persisted review projection)",
                        "current_dimension": str(dimension),
                        "current_binding_status": str(value.get("status", "PENDING")),
                        "binding_reason": str(value.get("reason", "")),
                        "audit_classification": classification,
                        "candidate_method_dimensions": [str(dimension)] if has_method_dimension else [],
                        "candidate_atoms": (
                            list(value.get("candidate_method_atoms", []))
                            if has_method_dimension else []
                        ),
                        "atom_id": "",
                        "atom_label": "",
                        "atom_e_class": {"E_Z": "", "E_F": ""},
                        "resolution_basis": str(value.get("reason", "")),
                        "method_hash": str(self.method.metadata.get("template_hash", "")),
                        "would_be_E_input": False,
                    })
        for candidate in candidates:
            bindings = self._bindings(candidate)
            before_atoms = {
                str(atom_id) for atom_id in candidate.get("facts", {}).get("scenario_atom_ids", [])
            } if isinstance(candidate.get("facts"), dict) else set()
            preview_atoms = set()
            pending_dimensions = []
            for dimension, binding in bindings.items():
                row = self._binding_row(candidate, dimension, binding)
                preview = self._range_preview(binding) if dimension == "EGO_DYNAMICS" else None
                if preview is not None:
                    row["range_resolution_preview"] = preview
                    range_binding[preview["reason"]] += 1
                    if preview["status"] == "RESOLVED" and preview["atom_id"]:
                        preview_atoms.add(preview["atom_id"])
                if row["source_term"] or dimension == "EGO_DYNAMICS":
                    inventory.append(row)
                status = row["current_binding_status"]
                if status != "RESOLVED":
                    pending_dimensions.append(dimension)

            related = [
                domain_by_malfunction.get(str(malfunction_id), {})
                for malfunction_id in candidate.get("generated_for_malfunction_ids", [])
            ]
            domain_pending = any(row.get("status") != "RESOLVED" for row in related)
            missing_required = [
                dimension for dimension in required_dimensions
                if bindings.get(dimension, {}).get("resolution_status") != "RESOLVED"
            ]
            if coverage_status == "PENDING":
                input_status = "NOT_EVALUABLE"
                blocking_reasons = ["PENDING_COVERAGE_GOVERNANCE"]
            elif domain_pending or not before_atoms:
                input_status = "BLOCKED"
                blocking_reasons = (
                    (["PENDING_COMPONENT_DOMAIN"] if domain_pending else [])
                    + (["NO_SCENARIO_ATOM"] if not before_atoms else [])
                )
            elif missing_required:
                input_status = "PARTIAL"
                blocking_reasons = ["REQUIRED_DIMENSION_MISSING_OR_UNRESOLVED"]
            else:
                input_status = "READY"
                blocking_reasons = []
            readiness.append({
                "scenario_id": str(candidate.get("scenario_id", "")),
                "coverage_status": coverage_status,
                "required_dimensions": required_dimensions,
                "resolved_dimensions": sorted(
                    name for name, binding in bindings.items()
                    if binding.get("resolution_status") == "RESOLVED"
                ),
                "pending_dimensions": sorted(pending_dimensions),
                "not_applicable_dimensions": [],
                "scenario_atom_ids": sorted(before_atoms),
                "resolved_bindings_preview": {
                    "EGO_DYNAMICS": sorted(preview_atoms),
                } if preview_atoms else {},
                "after_range_preview_atom_ids": sorted(preview_atoms),
                "component_domain_readiness": "PENDING_COMPONENT_DOMAIN" if domain_pending else "READY",
                "exposure_input_status": input_status,
                "blocking_reasons": blocking_reasons,
            })
        domain_knowledge = self.method.scenario_model.scenario_method.domain_knowledge
        fallback_dimensions = [
            {
                "source_dimension": item.source_dimension,
                "term_count": len(item.terms),
                "target_dimension": item.target_dimension,
                "target_status": item.target_status,
            }
            for item in (domain_knowledge.fallback_dimensions if domain_knowledge else ())
        ]
        return {
            "artifact_version": "exposure-binding-audit-v1",
            "method_contract_hash": str(self.method.metadata.get("template_hash", "")),
            "runtime_yaml_read": 0,
            "dimension_routing_summary": dict(routing),
            "project_binding_summary": dict(project_binding),
            "range_binding_summary": dict(range_binding),
            "component_domain_summary": component,
            "scenario_term_inventory": inventory,
            "scenario_readiness": readiness,
            "before": {
                "atom_binding_available": sum(bool(item["scenario_atom_ids"]) for item in readiness),
                "binding_dimensions": sorted(coverage_dimensions) if isinstance(coverage_dimensions, dict) else [],
            },
            "after": {
                "atom_binding_preview_available": sum(
                    bool(item["after_range_preview_atom_ids"]) for item in readiness
                ),
                "coverage_resolved": sum(
                    item["coverage_status"] == "RESOLVED" for item in readiness
                ),
                "exposure_input_ready": sum(
                    item["exposure_input_status"] == "READY" for item in readiness
                ),
                "exposure_input_not_evaluable": sum(
                    item["exposure_input_status"] == "NOT_EVALUABLE" for item in readiness
                ),
            },
            "fallback_dimension_adapter": fallback_dimensions,
        }
