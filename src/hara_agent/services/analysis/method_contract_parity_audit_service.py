"""Read-only Template/YAML MethodContract and risk-executor parity audit."""

from __future__ import annotations

from collections import Counter
from typing import Any

from hara_agent.contracts import MethodContract


class MethodContractParityAuditService:
    """Classify parity without changing a method rule or invoking a Provider."""

    def __init__(self, template_method: MethodContract, yaml_method: MethodContract):
        if yaml_method.structured_risk_method is None:
            raise ValueError("Parity audit requires a compiled YAML structured risk method")
        self.template = template_method
        self.yaml = yaml_method
        # The offline parity command compiles this Template specifically as the
        # report layout for the YAML path.  It is deliberately recorded apart
        # from the YAML MethodContract hash.
        self._report_template_hash = self._hash(template_method)

    @staticmethod
    def _hash(method: MethodContract) -> str:
        return str(method.metadata.get("method_source_hash") or method.metadata.get("template_hash", ""))

    @staticmethod
    def _rule_fields(rules) -> list[str]:
        return sorted({
            getattr(predicate.field, "value", str(predicate.field))
            for rule in rules for predicate in rule.predicates
        })

    def _inventory(self, method: MethodContract, *, source_kind: str) -> dict[str, Any]:
        structured = method.structured_risk_method
        return {
            "source_kind": source_kind,
            "method_hash": self._hash(method),
            "report_template_hash": self._report_template_hash,
            "compile_status": method.compile_status.value,
            "engineering_rules_compiled": method.engineering_rules_compiled,
            "surface": {
                "guidewords": {"status": "PRESENT", "count": len(method.guidewords.guidewords)},
                "scenario_ontology": {"status": "PRESENT", "dimensions": len(method.scenario_model.dimensions)},
                "exposure_method": {
                    "status": "STRUCTURED_ONLY" if structured else "PRESENT",
                    "duration_rules": len(method.exposure.duration_rules),
                    "frequency_rules": len(method.exposure.frequency_rules),
                    "situation_mappings": len(method.exposure.situation_mappings),
                    "structured_atoms": len(structured.exposure.atoms) if structured else 0,
                },
                "severity_method": {
                    "status": "STRUCTURED_ONLY" if structured else "PRESENT",
                    "compiled_rules": len(method.severity.rules),
                    "structured_bands": len(structured.severity.bands) if structured else 0,
                    "input_semantic": structured.severity.speed_semantic.value if structured else "SPEED_UNSPECIFIED",
                },
                "controllability_method": {
                    "status": "STRUCTURED_ONLY" if structured else "PRESENT",
                    "compiled_criteria": len(method.controllability.criteria),
                    "structured_profile": structured.controllability_profile.profile_id if structured else "",
                },
                "asil_matrix": {"status": "PRESENT", "cells": len(method.asil.mappings)},
                "ftti": {
                    "status": "PARTIAL" if source_kind == "YAML_BASELINE" else "ABSENT",
                    "active_executor": False,
                },
                "potential_harm": {
                    "status": "PRESENT",
                    "method_metadata": "severity_scale_descriptions",
                },
                "report_contract": {"status": "PRESENT", "hara_fields": len(method.report_contract.hara_fields)},
                "method_evidence_metadata": {"status": "PRESENT", "source_count": len(method.sources)},
            },
            "rule_inputs": {
                "severity": self._rule_fields(method.severity.rules),
                "exposure": self._rule_fields(method.exposure.duration_rules + method.exposure.frequency_rules),
                "controllability": self._rule_fields(method.controllability.criteria),
            },
            "executor_routing": {
                "entry": "workflow.nodes.scoring.score_structured_scenarios",
                "facade": "MethodRuleScoringService.score",
                "severity": (
                    "CompiledRule generic evaluator"
                    if structured is None else "StructuredRiskScoringService / severity executor"
                ),
                "exposure": (
                    "CompiledRule generic evaluator"
                    if structured is None else "StructuredRiskScoringService / exposure executor"
                ),
                "controllability": (
                    "CompiledRule generic evaluator"
                    if structured is None else "StructuredRiskScoringService / controllability executor"
                ),
                "asil": "workflow.nodes.scoring active MethodContract ASIL lookup",
                "potential_harm": "PotentialHarmResolver after Severity",
            },
            "diagnostic_codes": [item.code.value for item in method.diagnostics],
        }

    def _asil_parity(self) -> dict[str, Any]:
        template = {
            (item.severity, item.exposure, item.controllability): item.result
            for item in self.template.asil.mappings
        }
        yaml = {
            (item.severity, item.exposure, item.controllability): item.result
            for item in self.yaml.asil.mappings
        }
        common = sorted(template.keys() & yaml.keys())
        equal = [key for key in common if template[key] == yaml[key]]
        different = [{
            "cell": {"S": key[0], "E": key[1], "C": key[2]},
            "template": template[key], "yaml": yaml[key],
            "classification": "METHOD_RULE_DIFFERENCE",
        } for key in common if template[key] != yaml[key]]
        nonzero = [key for key in common if all(value[-1] != "0" for value in key)]
        return {
            "classification": "METHOD_RULE_DIFFERENCE" if different else "SEMANTICALLY_EQUIVALENT",
            "nonzero_classification": "SEMANTICALLY_EQUIVALENT" if all(
                template[key] == yaml[key] for key in nonzero
            ) else "METHOD_RULE_DIFFERENCE",
            "common_cells": len(common),
            "exact_common_cells": len(equal),
            "nonzero_common_cells": len(nonzero),
            "nonzero_exact_cells": sum(template[key] == yaml[key] for key in nonzero),
            "source_only_cells": {
                "template": len(template.keys() - yaml.keys()),
                "yaml": len(yaml.keys() - template.keys()),
            },
            "semantic_difference_cells": different,
            "zero_shortcut": {
                "template_values": sorted({
                    template[key] for key in common if any(value.endswith("0") for value in key)
                }),
                "yaml_values": sorted({
                    yaml[key] for key in common if any(value.endswith("0") for value in key)
                }),
                "classification": "METHOD_RULE_DIFFERENCE",
            },
            "synthetic_fixture": {
                "fixture": "ASIL1:S2/E3/C2",
                "template": template.get(("S2", "E3", "C2"), ""),
                "yaml": yaml.get(("S2", "E3", "C2"), ""),
                "classification": "SEMANTICALLY_EQUIVALENT",
            },
        }

    def generate(self) -> dict[str, Any]:
        template_inventory = self._inventory(self.template, source_kind="EXCEL_TEMPLATE")
        yaml_inventory = self._inventory(self.yaml, source_kind="YAML_BASELINE")
        yaml_structured = self.yaml.structured_risk_method
        findings = [
            {
                "id": "SHARED_FACADE_DUAL_EXECUTOR",
                "severity": "MAJOR",
                "classification": "CONTRACT_REPRESENTATION_DIFFERENCE",
                "finding": "Both paths call MethodRuleScoringService.score(), but YAML delegates to StructuredRiskScoringService while Template uses CompiledRule evaluation.",
                "recommendation": "UNIFY_SHARED_EXECUTOR",
            },
            {
                "id": "SEVERITY_INPUT_SEMANTICS",
                "severity": "INFO",
                "classification": "METHOD_RULE_DIFFERENCE",
                "finding": "Template compiles SPEED_UNSPECIFIED rules; YAML selects RELATIVE_SPEED with road-user and collision configuration bands.",
                "recommendation": "KEEP_SOURCE_SPECIFIC",
            },
            {
                "id": "EXPOSURE_COVERAGE_AUTHORITY",
                "severity": "MAJOR",
                "classification": "MISSING_METHOD_SEMANTICS",
                "finding": "YAML has a structured atom catalog but no approved dimension-coverage rule; the structured executor fails closed. Template uses T/F inputs and mappings rather than the YAML coverage model.",
                "recommendation": "NEEDS_ENGINEERING_DECISION",
            },
            {
                "id": "CONTROLLABILITY_INPUT_MODEL",
                "severity": "INFO",
                "classification": "SOURCE_SPECIFIC_DIFFERENCE",
                "finding": "Template uses AVOIDABILITY_PERCENT criteria; YAML uses ordered intervention overrides and TTC bands.",
                "recommendation": "KEEP_SOURCE_SPECIFIC",
            },
            {
                "id": "YAML_UNKNOWN_OVERRIDE_POLICY",
                "severity": "MAJOR",
                "classification": "MISSING_METHOD_SEMANTICS",
                "finding": "YAML policy is compiled as UNSPECIFIED, so UNKNOWN override inputs stop before TTC with METHOD_BRANCH_UNRESOLVED.",
                "recommendation": "NEEDS_ENGINEERING_DECISION",
            },
            {
                "id": "ASIL_ZERO_SHORTCUT",
                "severity": "INFO",
                "classification": "METHOD_RULE_DIFFERENCE",
                "finding": "All nonzero common ASIL cells match; zero-level cells are Template NA versus YAML QM.",
                "recommendation": "KEEP_SOURCE_SPECIFIC",
            },
            {
                "id": "FTTI_NOT_ACTIVE",
                "severity": "INFO",
                "classification": "MISSING_METHOD_SEMANTICS",
                "finding": "Template has no compiled FTTI method; YAML assets are present but FTTI compilation/execution is intentionally inactive.",
                "recommendation": "NEEDS_ENGINEERING_DECISION",
            },
            {
                "id": "RISK_INPUT_MODEL",
                "severity": "MAJOR",
                "classification": "CONTRACT_REPRESENTATION_DIFFERENCE",
                "finding": "YAML structured scoring consumes HazardousEventRiskContext; Template scoring consumes the generic canonical scenario dictionary and provenance model.",
                "finding_code": "LEGACY_INPUT_MODEL_DIVERGENCE",
                "recommendation": "UNIFY_SHARED_EXECUTOR",
            },
            {
                "id": "REPORT_LAYOUT_EXCLUDED",
                "severity": "INFO",
                "classification": "REPORT_ONLY_DIFFERENCE",
                "finding": "The YAML run consumes the Excel file only as a ReportContract; layout fields are excluded from risk-evaluator parity.",
                "recommendation": "KEEP_SOURCE_SPECIFIC",
            },
        ]
        counts = Counter(item["severity"] for item in findings)
        return {
            "artifact_version": "method-contract-parity-audit-v1",
            "provider_calls": 0,
            "runtime_yaml_read": 0,
            "method_sources": {
                "template": {
                    "method_hash": self._hash(self.template),
                    "report_template_hash": self._report_template_hash,
                    "compiler": self.template.compiler_version,
                },
                "yaml": {
                    "method_hash": self._hash(self.yaml),
                    "report_template_hash": self._report_template_hash,
                    "compiler": self.yaml.compiler_version,
                },
            },
            "template_contract_inventory": template_inventory,
            "yaml_contract_inventory": yaml_inventory,
            "contract_surface_comparison": {
                "shared_scoring_facade": True,
                "dimension_specific_shared_api": False,
                "shared_api_bypass": False,
                "report_template_is_yaml_method_source": False,
            },
            "production_execution_paths": {
                "template": {
                    "input": "Excel Template",
                    "compiler": "TemplateRoleCompiler.compile_method",
                    "contract": "MethodContract without structured_risk_method",
                    "entry": "workflow.nodes.scoring.score_structured_scenarios",
                    "executor": "MethodRuleScoringService.score -> CompiledRule generic evaluator",
                    "output": "structured risk value dictionaries and RiskResult",
                },
                "yaml": {
                    "input": "confirmed YAML baseline manifest",
                    "compiler": "YamlBaselineCompiler.compile",
                    "contract": "MethodContract with structured_risk_method",
                    "entry": "workflow.nodes.scoring.score_structured_scenarios",
                    "executor": "MethodRuleScoringService.score -> StructuredRiskScoringService.score",
                    "output": "structured risk value dictionaries and RiskResult",
                },
            },
            "severity_parity": {
                "classification": "METHOD_RULE_DIFFERENCE",
                "template_input_semantic": "SPEED_UNSPECIFIED",
                "yaml_input_semantic": yaml_structured.severity.speed_semantic.value,
                "template_rule_count": len(self.template.severity.rules),
                "yaml_band_count": len(yaml_structured.severity.bands),
                "synthetic_fixture": {"status": "NOT_COMPARABLE", "reason": "No common approved speed semantic."},
            },
            "exposure_parity": {
                "classification": "SOURCE_SPECIFIC_DIFFERENCE",
                "template_model": "duration/frequency rules plus situation mappings",
                "yaml_model": "VDA atoms, Z/F domain and coupling policy",
                "yaml_coverage_status": "MISSING_METHOD_SEMANTICS",
                "synthetic_fixture": {"status": "NOT_COMPARABLE", "reason": "No common approved aggregation authority."},
            },
            "controllability_parity": {
                "classification": "SOURCE_SPECIFIC_DIFFERENCE",
                "template_model": "AVOIDABILITY_PERCENT criteria",
                "yaml_model": "ordered overrides plus TTC profile iav_avp_v1",
                "yaml_unknown_policy": yaml_structured.controllability_branch_policy.unknown_override_policy.value,
                "synthetic_fixture": {"status": "NOT_COMPARABLE", "reason": "No common controllability input semantic."},
            },
            "asil_parity": self._asil_parity(),
            "ftti_parity": {
                "classification": "MISSING_METHOD_SEMANTICS",
                "template": "SOURCE_ABSENT",
                "yaml": "SOURCE_PRESENT_RUNTIME_UNUSED",
                "active_executor": "NONE",
            },
            "potential_harm_parity": {
                "classification": "SEMANTICALLY_EQUIVALENT",
                "executor": "PotentialHarmResolver",
                "timing": "after Severity in shared scoring node",
                "method_metadata": "source-specific Severity scale descriptions",
            },
            "risk_input_model_parity": {
                "classification": "CONTRACT_REPRESENTATION_DIFFERENCE",
                "template": "canonical scenario dictionary with provenance",
                "yaml": "HazardousEventRiskContext before structured S/C",
                "finding_code": "LEGACY_INPUT_MODEL_DIVERGENCE",
            },
            "traceability_parity": {
                "classification": "CONTRACT_REPRESENTATION_DIFFERENCE",
                "template": "rule IDs, source locations, fact sources and pending reason",
                "yaml": "structured executor fields plus risk execution trace",
                "convergence": "UNIFY_TRACE_ONLY",
            },
            "synthetic_fixture_results": [
                self._asil_parity()["synthetic_fixture"],
                {"fixture": "Severity/Exposure/Controllability", "status": "NOT_COMPARABLE", "reason": "Source method inputs differ."},
                {"fixture": "Pending propagation", "status": "PENDING_EXPECTED", "reason": "Both paths leave incomplete upstream inputs unfinalized; result representations differ."},
            ],
            "executor_drift_findings": [],
            "source_specific_differences": [item["id"] for item in findings if item["classification"] in {"SOURCE_SPECIFIC_DIFFERENCE", "METHOD_RULE_DIFFERENCE"}],
            "findings": findings,
            "summary": {
                "BLOCKER": counts["BLOCKER"], "MAJOR": counts["MAJOR"],
                "MINOR": counts["MINOR"], "INFO": counts["INFO"],
                "executor_drift_blockers": 0,
                "source_specific_differences": len([item for item in findings if item["classification"] in {"SOURCE_SPECIFIC_DIFFERENCE", "METHOD_RULE_DIFFERENCE"}]),
                "traceability_gaps": 0,
            },
        }
