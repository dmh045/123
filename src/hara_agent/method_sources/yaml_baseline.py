from __future__ import annotations

from pathlib import Path
from typing import Any

from hara_agent.contracts import (
    ASILMapping, ASILMatrix, CompileStatus, CompilerDiagnostic,
    CompilerDiagnosticCode, CompilerDiagnosticSeverity, ControllabilityBand,
    ControllabilityCondition, ControllabilityContract, ControllabilityOverride,
    ControllabilityProfile, DerivationMethod, ExposureAggregationPolicy,
    ExposureAtom, ExposureContract, ExposureDomainRule, ExposureMethod,
    ExposureMethodDomain, Guideword,
    GuidewordContract,
    MethodContract, NormativeStrength, ReportContract, RequiredFactSpec,
    RoleBinding, ScaleLevel, ScenarioDimension, ScenarioModel, SeverityContract,
    SeverityMethod, SeverityMethodBand, SeverityScale, SourceRef,
    SpeedSemantic, StructuredRiskMethod, TemplateRole, WorkflowContract,
)
from hara_agent.contracts.method_contract import FactOrigin, FactType
from hara_agent.models import ReviewStatus

from .yaml_loader import bundle_hash, canonical_node_text
from .yaml_validation import YamlBaselineValidationError, validate_manifest


class YamlBaselineCompileError(ValueError):
    pass


class YamlBaselineCompiler:
    """Compile a governed YAML bundle into the production MethodContract."""

    compiler_version = "yaml-baseline-compiler-v1"

    @staticmethod
    def _binding(role: TemplateRole, source_hash: str, location: str) -> RoleBinding:
        return RoleBinding(
            role=role, sheet="yaml", region=location,
            detection_method="BASELINE_MANIFEST", structural_signature=(location,),
            semantic_signature=(role.value,), confidence=1.0,
            source_hash=source_hash,
        )

    @staticmethod
    def _source(bundle: str, asset: str, location: str, value: Any) -> SourceRef:
        return SourceRef.create(
            workbook=asset, template_hash=bundle, sheet="yaml", range=location,
            raw_text=canonical_node_text(value),
        )

    @staticmethod
    def _level_scales(
        bundle: str, asset: str, values: dict[str, Any], prefix: str,
    ) -> tuple[ScaleLevel, ...]:
        result = []
        for level, raw in values.items():
            description = raw.get("description", "") if isinstance(raw, dict) else str(raw)
            source = YamlBaselineCompiler._source(bundle, asset, f"{prefix}.{level}", raw)
            result.append(ScaleLevel(str(level), description, description, asset, source))
        return tuple(result)

    def compile(
        self, manifest_path: str | Path, *, report_contract: ReportContract,
    ) -> MethodContract:
        path = Path(manifest_path).expanduser().resolve()
        try:
            manifest, assets, hashes = validate_manifest(path)
            source_hash = bundle_hash(manifest, hashes)
            return self._compile_valid(manifest, assets, hashes, source_hash, report_contract)
        except (KeyError, TypeError, ValueError, YamlBaselineValidationError) as error:
            raise YamlBaselineCompileError(str(error)) from error

    def _compile_valid(
        self, manifest: dict[str, Any], assets: dict[str, dict[str, Any]],
        hashes: dict[str, str], source_hash: str, report_contract: ReportContract,
    ) -> MethodContract:
        source_paths = {**manifest["sources"], **manifest["normalized_sources"]}
        refs: list[SourceRef] = []

        guide_asset = assets["guidewords"]
        guide_ref = self._source(source_hash, source_paths["guidewords"], "guidewords", guide_asset["guidewords"])
        refs.append(guide_ref)
        guidewords = tuple(
            Guideword(
                guideword_id=str(item["id"]), name=str(item["name"]),
                description=str(item["description"]), order=index,
                source_ref=self._source(
                    source_hash, source_paths["guidewords"], f"guidewords[{index - 1}]", item,
                ),
            )
            for index, item in enumerate(guide_asset["guidewords"], start=1)
        )
        if len({item.guideword_id for item in guidewords}) != len(guidewords):
            raise YamlBaselineCompileError("Guideword IDs must be unique")

        atom_asset = assets["exposure_atoms"]
        raw_atoms = atom_asset.get("atoms", [])
        atom_ids = [str(item.get("id", "")) for item in raw_atoms]
        if not atom_ids or len(set(atom_ids)) != len(atom_ids):
            raise YamlBaselineCompileError("VDA atom IDs must be present and unique")
        allowed_dimensions = {
            "WHERE", "WHERE_MICRO", "ROAD", "EGO_ACTION", "EGO_X_ROAD",
            "TRAFFIC_PATTERN", "EGO_DYNAMICS", "OBJECT",
        }
        exposure_atoms = []
        grouped: dict[str, list[str]] = {key: [] for key in allowed_dimensions}
        for index, item in enumerate(raw_atoms):
            dimensions = tuple(str(item["dim"]).split("+"))
            if any(value not in allowed_dimensions for value in dimensions):
                raise YamlBaselineCompileError(
                    f"Atom {item['id']} has an unsupported dimension: {dimensions}"
                )
            source = self._source(
                source_hash, source_paths["exposure_atoms"], f"atoms[{index}]", item,
            )
            refs.append(source)
            duration = str(item.get("e_t") or "")
            frequency = str(item.get("e_f") or "")
            duration = duration if duration in {"E0", "E1", "E2", "E3", "E4"} else ""
            frequency = frequency if frequency in {"E0", "E1", "E2", "E3", "E4"} else ""
            exposure_atoms.append(ExposureAtom(
                atom_id=str(item["id"]), dimensions=dimensions,
                label=str(item["label"]), duration_level=duration,
                frequency_level=frequency, source_ref=source,
            ))
            display = f"{item['id']} | {item['label']}"
            for dimension in dimensions:
                grouped[dimension].append(display)

        execution_order = [
            "WHERE", "ROAD", "EGO_ACTION", "EGO_X_ROAD",
            "TRAFFIC_PATTERN", "EGO_DYNAMICS", "OBJECT",
        ]
        scenario_dimensions = []
        for dimension in execution_order:
            values = tuple(dict.fromkeys(grouped.get(str(dimension), [])))
            if not values:
                raise YamlBaselineCompileError(f"Scenario dimension has no atoms: {dimension}")
            source = self._source(
                source_hash, source_paths["scenario_structure"],
                f"dimensions.{dimension}", assets["scenario_structure"].get("dimensions", {}),
            )
            scenario_dimensions.append(ScenarioDimension(
                dimension_id=f"DIM-{dimension}", canonical_name=str(dimension),
                display_name=str(dimension), values=values, unit="", source_ref=source,
                semantics="VDA702_ATOM_ID_LABEL",
            ))

        severity_asset = assets["severity"]
        severity_ref = self._source(source_hash, source_paths["severity"], "s_by_relative_velocity", severity_asset["s_by_relative_velocity"])
        refs.append(severity_ref)
        severity_bands: list[SeverityMethodBand] = []
        relative_tables = severity_asset["s_by_relative_velocity"]
        table_items: list[tuple[str, str, list[dict[str, Any]]]] = []
        for collision_type, rows in relative_tables["vehicle_collision"].items():
            table_items.append(("vehicle", str(collision_type), rows))
        for group in ("pedestrian_collision", "cyclist_collision", "motorcycle_collision"):
            table_items.append((group.removesuffix("_collision"), "any", relative_tables[group]))
        for group, collision_type, rows in table_items:
            previous: float | None = None
            for index, row in enumerate(rows):
                lower = float(row["min"]) if "min" in row else previous
                upper = float(row["max"]) if "max" in row else None
                source = self._source(
                    source_hash, source_paths["severity"],
                    f"s_by_relative_velocity.{group}.{collision_type}[{index}]", row,
                )
                severity_bands.append(SeverityMethodBand(
                    rule_id=f"S-{group}-{collision_type}-{index + 1}",
                    collision_group=group, collision_type=collision_type,
                    result=str(row["s"]), lower_kph=lower, upper_kph=upper,
                    lower_inclusive=True, upper_inclusive=False, source_ref=source,
                ))
                previous = upper

        domain_rules = []
        for index, item in enumerate(assets["exposure_domain"]["rules"]):
            source = self._source(
                source_hash, source_paths["exposure_domain"], f"rules[{index}]", item,
            )
            domain_rules.append(ExposureDomainRule(
                rule_id=str(item["rule_id"]),
                component_categories=tuple(map(str, item["match_component_category"])),
                domain=ExposureMethodDomain(str(item["e_dimension"])),
                source_ref=source,
            ))

        c_asset = assets["controllability_profile"]
        c_ref = self._source(source_hash, source_paths["controllability_profile"], "ttc_thresholds", c_asset["ttc_thresholds"])
        refs.append(c_ref)
        bands = []
        lower: float | None = None
        for index, item in enumerate(c_asset["ttc_thresholds"]["bands"]):
            upper = item.get("upper_bound")
            upper_value = float(upper) if upper is not None else None
            bands.append(ControllabilityBand(
                rule_id=f"C-TTC-{index + 1}", result=str(item["result"]),
                source_ref=self._source(
                    source_hash, source_paths["controllability_profile"],
                    f"ttc_thresholds.bands[{index}]", item,
                ),
                lower_ttc_s=lower, upper_ttc_s=upper_value,
                lower_inclusive=False if lower is not None else True,
                upper_inclusive=bool(item.get("upper_inclusive", False)),
                review_status=ReviewStatus.FINALIZED,
            ))
            lower = upper_value
        self._validate_c_bands(bands)

        aliases = {
            str(key): str(value)
            for key, value in assets["controllability_aliases"]["aliases"].items()
        }
        overrides = []
        for priority, item in enumerate(c_asset.get("override_rules", []), start=1):
            expression = str(item["when"])
            separator = " OR " if " OR " in expression else " AND "
            conditions = []
            for clause in expression.split(separator):
                field, operator, raw = clause.strip().split()
                if operator != "==" or raw.casefold() not in {"true", "false"}:
                    raise YamlBaselineCompileError(f"Unsupported C override expression: {expression}")
                conditions.append(ControllabilityCondition(
                    field=aliases.get(field, field), expected=raw.casefold() == "true",
                ))
            source = self._source(
                source_hash, source_paths["controllability_profile"],
                f"override_rules[{priority - 1}]", item,
            )
            overrides.append(ControllabilityOverride(
                rule_id=str(item["id"]), any_of=tuple(conditions) if separator == " OR " else (),
                all_of=tuple(conditions) if separator == " AND " else (),
                result=str(item["result"]), priority=priority, source_ref=source,
            ))

        iso = assets["asil"]
        asil_mappings = []
        for severity in ("S0", "S1", "S2", "S3"):
            for exposure in ("E0", "E1", "E2", "E3", "E4"):
                for controllability in ("C0", "C1", "C2", "C3"):
                    if "0" in (severity[1:], exposure[1:], controllability[1:]):
                        value = str(manifest["policies"]["asil_zero_short_circuit"])
                        location = "policies.asil_zero_short_circuit"
                    else:
                        value = str(iso["asil_matrix"][severity][exposure][controllability])
                        location = f"asil_matrix.{severity}.{exposure}.{controllability}"
                    asil_mappings.append(ASILMapping(
                        severity, exposure, controllability, value,
                        self._source(source_hash, source_paths["asil"], location, value),
                    ))
        if len(asil_mappings) != 80:
            raise YamlBaselineCompileError("ASIL matrix expansion must contain 80 cells")

        scale_sources = self._level_scales(source_hash, source_paths["severity"], severity_asset["s_levels"], "s_levels")
        exposure_levels = tuple(
            ScaleLevel(level, str(text), str(text), source_paths["exposure_atoms"],
                       self._source(source_hash, source_paths["exposure_atoms"], f"e_value_levels.{level}", text))
            for level, text in atom_asset["e_value_levels"].items()
        )
        c_levels = tuple(
            ScaleLevel(value, value, value, source_paths["controllability_profile"], c_ref)
            for value in ("C0", "C1", "C2", "C3")
        )
        policy_asset = assets["exposure_policy"]
        policy_source = self._source(
            source_hash, source_paths["exposure_policy"], "policy", policy_asset,
        )
        aggregation_policy = ExposureAggregationPolicy(
            policy_id=str(policy_asset["policy_id"]),
            minimum_level=int(policy_asset["minimum_level"]),
            all_highest_operand=str(policy_asset["all_highest"]["operand"]),
            all_highest_result=str(policy_asset["all_highest"]["result"]),
            mixed_high_operands=tuple(map(str, policy_asset["mixed_high"]["operands"])),
            mixed_high_result=str(policy_asset["mixed_high"]["result"]),
            mixed_strategy=str(policy_asset["mixed_levels"]["strategy"]),
            independent_decrement=int(policy_asset["equal_levels"]["independent_decrement"]),
            dependent_decrement=int(policy_asset["equal_levels"]["dependent_decrement"]),
            source_ref=policy_source,
        )
        exposure_method = ExposureMethod(
            method_id="fusa_exposure_v1", atoms=tuple(exposure_atoms),
            domain_rules=tuple(domain_rules),
            strong_couplings=tuple(
                tuple(map(str, pair))
                for pair in assets["scenario_structure"]["dimension_dependency"]["strong_coupling"]
            ),
            aggregation_policy=aggregation_policy,
            dimension_fallback_policy=str(manifest["policies"]["exposure_dimension_fallback"]),
            source_refs=(domain_rules[0].source_ref, exposure_atoms[0].source_ref),
        )
        structured = StructuredRiskMethod(
            severity=SeverityMethod(
                method_id="relative_velocity_v2", speed_semantic=SpeedSemantic.DELTA_V,
                bands=tuple(severity_bands), source_ref=severity_ref,
                road_user_groups=tuple(
                    (str(key), str(value))
                    for key, value in assets["severity_semantics"]["road_user_groups"].items()
                ),
                collision_types=tuple(
                    (str(key), str(value))
                    for key, value in assets["severity_semantics"]["collision_types"].items()
                ),
                fallback_policy=str(manifest["policies"]["severity_fallback_method"]),
            ),
            exposure=exposure_method,
            controllability_profile=ControllabilityProfile(
                profile_id="iav_avp_v1", bands=tuple(bands), source_ref=c_ref,
                review_status=ReviewStatus.FINALIZED,
            ),
            controllability_overrides=tuple(overrides),
            asil_zero_short_circuit=str(manifest["policies"]["asil_zero_short_circuit"]),
            method_source_hash=source_hash,
        )

        workflow_binding = self._binding(TemplateRole.WORKFLOW, source_hash, "runtime_workflow")
        empty_derivation = lambda role, pattern: DerivationMethod(
            inputs_required=(), derivation_pattern=pattern,
            qm_handling="QM does not generate a Safety Goal",
            aggregation_instructions="Aggregate by canonical safety intent",
            semantic_derivation_required=True, assumptions=(), instructions=(),
            source_binding=self._binding(role, source_hash, "normalized/derivation_rules.yaml"),
        )
        required = self._required_facts(source_hash, source_paths, severity_ref, c_ref)
        diagnostics = (
            CompilerDiagnostic(
                severity=CompilerDiagnosticSeverity.WARNING,
                code=CompilerDiagnosticCode.BASELINE_SCENARIO_BINDING_INCOMPLETE,
                message=(
                    "The seven-dimension atom ontology is compiled, but project ODD-to-atom "
                    "semantic binding and bounded candidate selection are not yet complete."
                ),
                role=TemplateRole.SCENARIO_MODEL,
                source_refs=(exposure_atoms[0].source_ref,),
            ),
            CompilerDiagnostic(
                severity=CompilerDiagnosticSeverity.WARNING,
                code=CompilerDiagnosticCode.SEVERITY_FALLBACK_UNCOMPILED,
                message=(
                    "Relative-speed severity bands are executable; the traffic-domain "
                    "fallback remains pending contract compilation."
                ),
                role=TemplateRole.SEVERITY_RULES,
                source_refs=(severity_ref,),
            ),
            CompilerDiagnostic(
                severity=CompilerDiagnosticSeverity.WARNING,
                code=CompilerDiagnosticCode.FTTI_METHOD_UNCOMPILED,
                message=(
                    "FTTI YAML assets are hash-bound but formula routing and the audited "
                    "formula registry are not yet connected to runtime scoring."
                ),
                source_refs=(self._source(
                    source_hash, source_paths["ftti_formulas"], "formulas",
                    assets["ftti_formulas"].get("formulas", []),
                ),),
            ),
        )
        source_refs = tuple(dict.fromkeys([
            *refs,
            *(item.source_ref for item in guidewords),
            *(item.source_ref for item in severity_bands),
            *(item.source_ref for item in bands),
            *(item.source_ref for item in overrides),
            *(item.source_ref for item in asil_mappings),
        ]))
        return MethodContract(
            metadata={
                "template_hash": source_hash,
                "method_source_hash": source_hash,
                "source_kind": "YAML_BASELINE",
                "method_id": str(manifest["method_id"]),
                "method_version": str(manifest["method_version"]),
                "asset_hashes": dict(sorted(hashes.items())),
            },
            workflow=WorkflowContract(steps=(), source_binding=workflow_binding),
            guidewords=GuidewordContract(
                guidewords, self._binding(TemplateRole.GUIDEWORD_TABLE, source_hash, source_paths["guidewords"]),
            ),
            scenario_model=ScenarioModel(
                dimensions=tuple(scenario_dimensions),
                structural_constraints=("bounded_atom_selection_required",),
                source_binding=self._binding(TemplateRole.SCENARIO_MODEL, source_hash, source_paths["scenario_structure"]),
                source_type="YAML_BASELINE_SCENARIO_ONTOLOGY",
            ),
            severity=SeverityContract(
                SeverityScale(scale_sources, self._binding(TemplateRole.SEVERITY_LEVELS, source_hash, source_paths["severity"])),
                rules=(), diagnostics=(),
            ),
            exposure=ExposureContract(exposure_levels, (), (), (), (), ()),
            controllability=ControllabilityContract(c_levels, (), (), (), (), ()),
            asil=ASILMatrix(
                ("S0", "S1", "S2", "S3"), ("E0", "E1", "E2", "E3", "E4"),
                ("C0", "C1", "C2", "C3"), tuple(asil_mappings), "QM",
                self._binding(TemplateRole.ASIL_MATRIX, source_hash, source_paths["asil"]), (),
            ),
            safety_goal_method=empty_derivation(TemplateRole.SAFETY_GOAL_METHOD, "baseline_safety_intent_semantic_derivation"),
            safe_state_method=empty_derivation(TemplateRole.SAFE_STATE_METHOD, "baseline_project_capability_safe_state_derivation"),
            report_contract=report_contract,
            required_fact_specs=required,
            diagnostics=diagnostics, sources=source_refs,
            compile_status=CompileStatus.READY_WITH_WARNINGS,
            engineering_rules_compiled=True,
            structured_risk_method=structured,
            compiler_version=self.compiler_version,
        )

    @staticmethod
    def _validate_c_bands(bands: list[ControllabilityBand]) -> None:
        probes = (0.0, 3.0, 3.000001, 4.0, 4.000001, 5.0, 5.000001, 100.0)
        for value in probes:
            matches = []
            for band in bands:
                lower_ok = band.lower_ttc_s is None or value > band.lower_ttc_s or (
                    value == band.lower_ttc_s and band.lower_inclusive
                )
                upper_ok = band.upper_ttc_s is None or value < band.upper_ttc_s or (
                    value == band.upper_ttc_s and band.upper_inclusive
                )
                if lower_ok and upper_ok:
                    matches.append(band)
            if len(matches) != 1:
                raise YamlBaselineCompileError(f"C profile gap/overlap at TTC={value}")

    def _required_facts(
        self, source_hash: str, paths: dict[str, str], severity: SourceRef,
        controllability: SourceRef,
    ) -> tuple[RequiredFactSpec, ...]:
        def spec(
            fact: FactType, required_for: tuple[str, ...], origin: FactOrigin,
            source: SourceRef, unit: str = "", constraints: tuple[str, ...] = (),
        ):
            return RequiredFactSpec(
                fact, required_for, unit, constraints, "when applicable",
                origin, (), (source,),
            )
        return (
            spec(
                FactType.COLLISION_TYPE, ("SEVERITY", "FTTI"),
                FactOrigin.SCENARIO_FACT, severity,
                constraints=(
                    "IN ('FRONTAL','REAR_END','SIDE','VEHICLE_TO_ROAD_USER')",
                ),
            ),
            spec(
                FactType.ROAD_USER_TYPE, ("SEVERITY",),
                FactOrigin.SCENARIO_FACT, severity,
                constraints=(
                    "IN ('VEHICLE','PEDESTRIAN','CYCLIST','MOTORCYCLIST')",
                ),
            ),
            spec(FactType.DELTA_V, ("SEVERITY",), FactOrigin.DERIVED_FACT, severity, "km/h"),
            spec(FactType.TTC, ("CONTROLLABILITY", "FTTI"), FactOrigin.DERIVED_FACT, controllability, "s"),
            spec(FactType.DRIVER_IN_VEHICLE, ("CONTROLLABILITY",), FactOrigin.PROJECT_FACT, controllability),
            spec(FactType.REMOTE_INTERVENTION_AVAILABLE, ("CONTROLLABILITY",), FactOrigin.PROJECT_FACT, controllability),
            spec(FactType.INTERVENTION_AVAILABLE, ("CONTROLLABILITY",), FactOrigin.SCENARIO_FACT, controllability),
        )
