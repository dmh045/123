from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, replace
from typing import Protocol

from openpyxl.utils import get_column_letter, range_boundaries

from hara_agent.contracts import (
    REQUIRED_TEMPLATE_ROLES, RoleBinding, TemplateDiagnostic, TemplateDiagnosticCode,
    TemplateDiagnosticSeverity, TemplateRole, TemplateRoleConfirmation,
    TemplateRoleContract,
)

from .errors import TemplateRoleAmbiguityError, TemplateRoleMissingError
from .scanner import CellSnapshot, SheetSnapshot, WorkbookSnapshot, normalize_template_text


@dataclass(frozen=True)
class RoleClassification:
    role: TemplateRole
    sheet: str
    region: str
    confidence: float
    reason: str


class TemplateRoleClassifier(Protocol):
    """Optional bounded classifier for selecting one existing region candidate."""

    def classify(
        self,
        snapshot: WorkbookSnapshot,
        role: TemplateRole,
        candidates: tuple[RoleBinding, ...],
    ) -> RoleClassification: ...


@dataclass(frozen=True)
class _Candidate:
    binding: RoleBinding


class TemplateRoleResolver:
    """Discover semantic method roles from workbook structure and headers.

    Sheet names are recorded only as low-weight ranking hints. Every accepted
    candidate must independently satisfy a structural or semantic signature.
    """

    _SHEET_HINTS = {
        TemplateRole.WORKFLOW: {"ai-process", "workflow"},
        TemplateRole.GUIDEWORD_TABLE: {"04_hazop", "hazop"},
        TemplateRole.SCENARIO_MODEL: {"scenarios_library", "scenario library"},
        TemplateRole.SEVERITY_LEVELS: {"severity"},
        TemplateRole.SEVERITY_RULES: {"severity"},
        TemplateRole.EXPOSURE_DURATION_RULES: {"exposure"},
        TemplateRole.EXPOSURE_FREQUENCY_RULES: {"exposure"},
        TemplateRole.EXPOSURE_EXAMPLES: {"exposure"},
        TemplateRole.VDA702_SUMMARY: {"vda702 summary"},
        TemplateRole.VDA702_FULL: {"vda702 full"},
        TemplateRole.SITUATION_CATALOG: {"10_sitkat_alt", "situation catalog"},
        TemplateRole.CONTROLLABILITY_LEVELS: {"controllability"},
        TemplateRole.CONTROLLABILITY_RULES: {"controllability"},
        TemplateRole.ASIL_MATRIX: {"asil_table", "asil matrix"},
        TemplateRole.HARA_OUTPUT_TABLE: {"05_hara", "hara"},
        TemplateRole.SAFETY_GOAL_OUTPUT_TABLE: {"06_safety goal"},
    }

    _HARA_FIELDS = {
        "hara_id": ("hara-id", "hara id"),
        "function": ("function", "功能"),
        "output": ("output", "输出"),
        "guideword": ("guide-word", "guide word", "guideword", "引导词"),
        "malfunction": ("malfunction", "失效"),
        "hazard": ("hazard", "危害"),
        "situational_description": ("situational description", "场景描述"),
        "situational_detailing": ("situational detailing", "场景细化", "详细场景"),
        "hazardous_event": ("hazardous event", "potential damage", "危害事件"),
        "severity": ("severity", "严重度"),
        "severity_rationale": ("rationale severity", "severity evaluation"),
        "exposure": ("exposure", "暴露度"),
        "exposure_method": ("t/f", "exposure method"),
        "exposure_rationale": ("rationale exposure", "exposure evaluation"),
        "controllability": ("controllability", "可控度"),
        "controllability_rationale": (
            "rationale controllability", "controllability evaluation"
        ),
        "asil": ("asil",),
        "sg_id": ("sg-id", "sg id", "sz-id"),
        "safety_goal": ("safety goal", "安全目标"),
        "safe_state": ("safe state", "safety state", "安全状态"),
        "remark": ("remark", "备注"),
    }

    _SG_FIELDS = {
        "hazard_id": ("hz-id", "hazard id"),
        "hazard": ("hazard", "危害"),
        "sg_id": ("sg-id", "sg id", "sz-id"),
        "safety_goal": ("safety goal", "安全目标"),
        "safe_state": ("safe state", "safety state", "安全状态"),
        "asil": ("asil",),
    }

    def __init__(self, classifier: TemplateRoleClassifier | None = None):
        self.classifier = classifier

    def resolve(
        self,
        snapshot: WorkbookSnapshot,
        *,
        confirmation: TemplateRoleConfirmation | None = None,
    ) -> TemplateRoleContract:
        if confirmation and confirmation.template_hash != snapshot.source_hash:
            raise ValueError(
                "Template role confirmation hash mismatch: "
                f"confirmation={confirmation.template_hash}, template={snapshot.source_hash}"
            )
        candidates = self._discover(snapshot)
        selected: list[RoleBinding] = []
        missing: list[TemplateRole] = []
        evidence: list[str] = []
        for role in REQUIRED_TEMPLATE_ROLES:
            role_candidates = self._deduplicate(candidates.get(role, []))
            if not role_candidates:
                missing.append(role)
                continue
            chosen = self._select(snapshot, role, role_candidates, confirmation)
            selected.append(chosen)
            evidence.append(
                f"{role.value}={chosen.sheet}!{chosen.region} via {chosen.detection_method}"
            )
        if missing:
            raise TemplateRoleMissingError(missing)

        diagnostics = self._diagnostics(snapshot, tuple(selected))
        return TemplateRoleContract(
            template_id=snapshot.source_path.stem,
            template_hash=snapshot.source_hash,
            role_bindings=tuple(selected),
            discovery_evidence=tuple(evidence),
            diagnostics=tuple(diagnostics),
            confirmation=confirmation,
        )

    def _discover(self, snapshot: WorkbookSnapshot) -> dict[TemplateRole, list[RoleBinding]]:
        found: dict[TemplateRole, list[RoleBinding]] = {
            role: [] for role in REQUIRED_TEMPLATE_ROLES
        }
        self._discover_explicit_metadata(snapshot, found)
        for sheet in snapshot.sheets:
            self._discover_workflow(sheet, found)
            self._discover_guidewords(sheet, found)
            self._discover_scenario_model(sheet, found)
            self._discover_level_tables(sheet, found)
            self._discover_severity_rules(sheet, found)
            self._discover_exposure(sheet, found)
            self._discover_catalogs(sheet, found)
            self._discover_controllability_rules(sheet, found)
            self._discover_asil(sheet, found)
            self._discover_report_tables(sheet, found)
        self._method_candidates(snapshot, found)
        return found

    def _discover_explicit_metadata(
        self,
        snapshot: WorkbookSnapshot,
        found: dict[TemplateRole, list[RoleBinding]],
    ) -> None:
        """Accept explicit metadata only when its region also fits the role shape."""
        named_aliases = {
            TemplateRole.WORKFLOW: ("workflow", "ai_process"),
            TemplateRole.GUIDEWORD_TABLE: ("guideword_table", "guide_word_table"),
            TemplateRole.SCENARIO_MODEL: ("scenario_model", "scenario_dimensions"),
            TemplateRole.SEVERITY_LEVELS: ("severity_levels",),
            TemplateRole.SEVERITY_RULES: ("severity_rules",),
            TemplateRole.EXPOSURE_DURATION_RULES: ("exposure_duration",),
            TemplateRole.EXPOSURE_FREQUENCY_RULES: ("exposure_frequency",),
            TemplateRole.CONTROLLABILITY_LEVELS: ("controllability_levels",),
            TemplateRole.CONTROLLABILITY_RULES: ("controllability_rules",),
            TemplateRole.ASIL_MATRIX: ("asil_matrix", "asil_table"),
            TemplateRole.HARA_OUTPUT_TABLE: ("hara_output", "hara_table"),
            TemplateRole.SAFETY_GOAL_OUTPUT_TABLE: ("safety_goal_output", "sg_table"),
            TemplateRole.SAFETY_GOAL_METHOD: ("safety_goal_method",),
            TemplateRole.SAFE_STATE_METHOD: ("safe_state_method",),
        }
        for named_range in snapshot.named_ranges:
            normalized_name = normalize_template_text(named_range.name).replace(" ", "_")
            for role, aliases in named_aliases.items():
                if not any(alias == normalized_name for alias in aliases):
                    continue
                for sheet_name, raw_region in named_range.destinations:
                    region = raw_region.replace("$", "")
                    sheet = snapshot.sheet(sheet_name)
                    min_column, min_row, max_column, max_row = range_boundaries(region)
                    width = max_column - min_column + 1
                    height = max_row - min_row + 1
                    if role is TemplateRole.GUIDEWORD_TABLE and width < 2:
                        continue
                    if role is TemplateRole.ASIL_MATRIX and (width < 4 or height < 10):
                        continue
                    found[role].append(
                        self._binding(
                            role, sheet, region, "explicit_named_range",
                            (f"named_range={named_range.name}", f"shape={height}x{width}"),
                            (normalized_name,), 0.995,
                        )
                    )

        for sheet in snapshot.sheets:
            for table in sheet.tables:
                header_text = " | ".join(table.headers)
                table_name = normalize_template_text(table.name)
                role: TemplateRole | None = None
                signature: tuple[str, ...] = ()
                hara_count = sum(
                    any(alias in header_text for alias in aliases)
                    for aliases in self._HARA_FIELDS.values()
                )
                sg_count = sum(
                    any(alias in header_text for alias in aliases)
                    for aliases in self._SG_FIELDS.values()
                )
                if hara_count >= 16:
                    role, signature = TemplateRole.HARA_OUTPUT_TABLE, (f"fields={hara_count}",)
                elif sg_count >= 5:
                    role, signature = TemplateRole.SAFETY_GOAL_OUTPUT_TABLE, (f"fields={sg_count}",)
                elif (
                    any(token in header_text for token in ("guide word", "guideword", "引导词"))
                    and any(token in header_text for token in ("description", "描述"))
                ):
                    role, signature = TemplateRole.GUIDEWORD_TABLE, ("guideword", "description")
                elif all(f"s{i}".casefold() in header_text for i in range(4)):
                    role, signature = TemplateRole.SEVERITY_LEVELS, ("S0-S3",)
                elif all(f"c{i}".casefold() in header_text for i in range(4)):
                    role, signature = TemplateRole.CONTROLLABILITY_LEVELS, ("C0-C3",)
                elif all(f"e{i}".casefold() in header_text for i in range(1, 5)):
                    if "duration" in header_text or "duration" in table_name:
                        role = TemplateRole.EXPOSURE_DURATION_RULES
                    elif "frequency" in header_text or "frequency" in table_name:
                        role = TemplateRole.EXPOSURE_FREQUENCY_RULES
                    signature = ("E1-E4",)
                if role is not None:
                    found[role].append(
                        self._binding(
                            role, sheet, table.region, "explicit_structured_table",
                            (f"table={table.name}",) + signature,
                            table.headers, 1.0,
                        )
                    )

    def _binding(
        self,
        role: TemplateRole,
        sheet: SheetSnapshot,
        region: str,
        method: str,
        structural: tuple[str, ...],
        semantic: tuple[str, ...],
        confidence: float,
    ) -> RoleBinding:
        hint = normalize_template_text(sheet.name) in self._SHEET_HINTS.get(role, set())
        diagnostics = ("sheet_name_hint",) if hint else ()
        return RoleBinding(
            role=role,
            sheet=sheet.name,
            region=region,
            detection_method=method,
            structural_signature=structural,
            semantic_signature=semantic,
            confidence=min(1.0, confidence + (0.005 if hint else 0.0)),
            source_hash=self._region_hash(sheet, region),
            diagnostics=diagnostics,
        )

    def _discover_workflow(
        self, sheet: SheetSnapshot, found: dict[TemplateRole, list[RoleBinding]]
    ) -> None:
        required_groups = (
            ("序号", "sequence", "step"),
            ("输入", "input"),
            ("活动", "activity"),
            ("过程描述", "process description"),
            ("输出", "output"),
        )
        for header in sheet.header_candidates:
            text = " | ".join(header.normalized_values)
            matched = tuple(group[0] for group in required_groups if any(x in text for x in group))
            if len(matched) < 4:
                continue
            region = sheet.used_region
            found[TemplateRole.WORKFLOW].append(
                self._binding(
                    TemplateRole.WORKFLOW, sheet, region, "header_semantic_signature",
                    (f"header_row={header.row}", f"matched_groups={len(matched)}"),
                    matched, 0.94,
                )
            )

    def _discover_guidewords(
        self, sheet: SheetSnapshot, found: dict[TemplateRole, list[RoleBinding]]
    ) -> None:
        for header in sheet.header_candidates:
            cells = sheet.cells_in_row(header.row)
            guide = next(
                (
                    cell for cell in cells
                    if any(token in cell.normalized_value for token in ("guide word", "guide-word", "引导词"))
                ),
                None,
            )
            description = next(
                (
                    cell for cell in cells
                    if any(token in cell.normalized_value for token in ("description", "描述"))
                ),
                None,
            )
            if not guide or not description:
                continue
            value_rows = []
            for row in range(header.row + 1, sheet.max_row + 1):
                value = sheet.cell(row, guide.column)
                desc = sheet.cell(row, description.column)
                if value is None and value_rows:
                    break
                if value is not None and value.normalized_value:
                    value_rows.append(row)
                    if desc is None:
                        break
            if len(value_rows) < 2:
                continue
            region = self._region(
                header.row, min(guide.column, description.column),
                max(value_rows), max(guide.column, description.column),
            )
            found[TemplateRole.GUIDEWORD_TABLE].append(
                self._binding(
                    TemplateRole.GUIDEWORD_TABLE, sheet, region,
                    "structural_and_header_signature",
                    ("guideword_column", "description_column", f"data_rows={len(value_rows)}"),
                    (guide.normalized_value, description.normalized_value), 0.96,
                )
            )

    def _discover_scenario_model(
        self, sheet: SheetSnapshot, found: dict[TemplateRole, list[RoleBinding]]
    ) -> None:
        groups = {
            "operating_scenario": ("operating scenario", "运行场景"),
            "vehicle_state": ("vehicle state", "车辆状态"),
            "vehicle_speed": ("vehicle speed", "车辆速度"),
            "weather": ("weather", "天气"),
            "road_surface": ("road surface", "路面"),
        }
        for header in sheet.header_candidates:
            row_cells = sheet.cells_in_row(header.row)
            matched = {
                name: cell
                for name, aliases in groups.items()
                for cell in row_cells
                if any(alias in cell.normalized_value for alias in aliases)
            }
            if len(matched) < 4 or len({cell.column for cell in matched.values()}) < 4:
                continue
            columns = [cell.column for cell in matched.values()]
            header_columns = sorted(cell.column for cell in row_cells)
            lower = min(columns)
            upper = max(columns)
            while lower - 1 in header_columns:
                lower -= 1
            while upper + 1 in header_columns:
                upper += 1
            data_cells = [
                cell for cell in sheet.cells
                if cell.row >= header.row and lower <= cell.column <= upper
            ]
            region = self._cells_region(data_cells)
            found[TemplateRole.SCENARIO_MODEL].append(
                self._binding(
                    TemplateRole.SCENARIO_MODEL, sheet, region,
                    "structural_dimension_signature",
                    (f"dimension_count={upper - lower + 1}", f"header_row={header.row}"),
                    tuple(sorted(matched)), 0.96,
                )
            )

    def _discover_level_tables(
        self, sheet: SheetSnapshot, found: dict[TemplateRole, list[RoleBinding]]
    ) -> None:
        for prefix, role, semantic_tokens in (
            ("S", TemplateRole.SEVERITY_LEVELS, ("class of severity", "severity")),
            ("C", TemplateRole.CONTROLLABILITY_LEVELS, ("class of controllability", "controllability")),
        ):
            expected = {f"{prefix}{index}" for index in range(4)}
            for row in sorted({cell.row for cell in sheet.cells}):
                row_cells = sheet.cells_in_row(row)
                codes = {
                    cell.normalized_value.upper(): cell
                    for cell in row_cells
                    if cell.normalized_value.upper() in expected
                }
                if set(codes) != expected:
                    continue
                nearby = " | ".join(
                    cell.normalized_value
                    for cell in sheet.cells
                    if max(1, row - 3) <= cell.row <= min(sheet.max_row, row + 3)
                )
                if not any(token in nearby for token in semantic_tokens):
                    continue
                columns = [cell.column for cell in codes.values()]
                region = self._region(
                    row, max(1, min(columns) - 1), min(sheet.max_row, row + 3), max(columns)
                )
                found[role].append(
                    self._binding(
                        role, sheet, region, "structural_level_signature",
                        (f"complete_{prefix}0_{prefix}3", f"code_row={row}"),
                        semantic_tokens, 0.96,
                    )
                )

    def _discover_severity_rules(
        self, sheet: SheetSnapshot, found: dict[TemplateRole, list[RoleBinding]]
    ) -> None:
        collision = re.compile(
            r"rear|front|side|pedestrian|cyclist|collision|碰撞|行人|骑行"
        )
        speed = re.compile(r"(?:\bv\b|v\s*[:<>=]|km/?h)")
        score = re.compile(r"\bS[0-3]\b", re.IGNORECASE)
        cells = [
            cell for cell in sheet.cells
            if collision.search(cell.normalized_value)
            and speed.search(cell.normalized_value)
            and score.search(cell.normalized_value)
        ]
        if len(cells) < 4:
            return
        by_column: dict[int, list[CellSnapshot]] = {}
        for cell in cells:
            by_column.setdefault(cell.column, []).append(cell)
        best = max(by_column.values(), key=len)
        if len(best) < 4:
            return
        region = self._cells_region(best)
        found[TemplateRole.SEVERITY_RULES].append(
            self._binding(
                TemplateRole.SEVERITY_RULES, sheet, region,
                "structural_rule_signature",
                ("collision_category", "speed_condition", "severity_result", f"rules={len(best)}"),
                ("collision", "speed", "S0-S3"), 0.97,
            )
        )

    def _discover_exposure(
        self, sheet: SheetSnapshot, found: dict[TemplateRole, list[RoleBinding]]
    ) -> None:
        expected = {f"E{index}" for index in range(1, 5)}
        rows_by_method: dict[str, list[int]] = {"duration": [], "frequency": []}
        for row in sorted({cell.row for cell in sheet.cells}):
            codes = {
                cell.normalized_value.upper()
                for cell in sheet.cells_in_row(row)
                if cell.normalized_value.upper() in expected
            }
            if codes != expected:
                continue
            nearby = " | ".join(
                cell.normalized_value
                for cell in sheet.cells
                if row <= cell.row <= min(sheet.max_row, row + 3)
            )
            if "duration" in nearby or "时间" in nearby:
                rows_by_method["duration"].append(row)
            if "frequency" in nearby or "频率" in nearby:
                rows_by_method["frequency"].append(row)
        for method, role in (
            ("duration", TemplateRole.EXPOSURE_DURATION_RULES),
            ("frequency", TemplateRole.EXPOSURE_FREQUENCY_RULES),
        ):
            rows = rows_by_method[method]
            if not rows:
                continue
            cells = [
                cell for cell in sheet.cells
                if min(rows) - 2 <= cell.row <= min(sheet.max_row, max(rows) + 3)
            ]
            region = self._cells_region(cells)
            found[role].append(
                self._binding(
                    role, sheet, region, "structural_exposure_signature",
                    ("complete_E1_E4", f"method={method}", f"tables={len(rows)}"),
                    (method, "exposure"), 0.95,
                )
            )
        text = sheet.normalized_text
        if "examples" in text and "probability of exposure" in text and all(
            code.casefold() in text for code in expected
        ):
            found[TemplateRole.EXPOSURE_EXAMPLES].append(
                self._binding(
                    TemplateRole.EXPOSURE_EXAMPLES, sheet, sheet.used_region,
                    "semantic_example_signature", ("E1-E4", "example_rows"),
                    ("examples", "probability of exposure"), 0.88,
                )
            )

    def _discover_catalogs(
        self, sheet: SheetSnapshot, found: dict[TemplateRole, list[RoleBinding]]
    ) -> None:
        text = sheet.normalized_text
        if (
            "basic situation catalog" in text
            and "category" in text
            and "description of the situation" in text
            and "e-rating" in text
        ):
            found[TemplateRole.VDA702_SUMMARY].append(
                self._binding(
                    TemplateRole.VDA702_SUMMARY, sheet, sheet.used_region,
                    "catalog_header_signature", ("category", "description", "dual_e_rating"),
                    ("basic situation catalog", "summary"), 0.94,
                )
            )
        if (
            "basic situation catalog" in text
            and "valuation (time range)" in text
            and "rating (frequency range)" in text
            and "situation catalog" in text
        ):
            found[TemplateRole.VDA702_FULL].append(
                self._binding(
                    TemplateRole.VDA702_FULL, sheet, sheet.used_region,
                    "catalog_header_signature", ("situation_id", "time_rating", "frequency_rating"),
                    ("basic situation catalog", "full"), 0.95,
                )
            )
        if (
            ("situationskatalog" in text or "verkehrsort" in text)
            and "zeitbereich" in text
            and ("frequ" in text or "frequenz" in text)
        ):
            found[TemplateRole.SITUATION_CATALOG].append(
                self._binding(
                    TemplateRole.SITUATION_CATALOG, sheet, sheet.used_region,
                    "catalog_header_signature", ("location", "time_domain", "frequency_domain"),
                    ("situation catalog", "traffic location"), 0.94,
                )
            )

    def _discover_controllability_rules(
        self, sheet: SheetSnapshot, found: dict[TemplateRole, list[RoleBinding]]
    ) -> None:
        text = sheet.normalized_text
        if not (
            "class of controllability" in text
            and "%" in text
            and ("avoid harm" in text or "controllable" in text)
        ):
            return
        found[TemplateRole.CONTROLLABILITY_RULES].append(
            self._binding(
                TemplateRole.CONTROLLABILITY_RULES, sheet, sheet.used_region,
                "structural_rule_signature", ("C0-C3", "quantitative_avoidability", "examples"),
                ("controllability", "avoid harm"), 0.94,
            )
        )

    def _discover_asil(
        self, sheet: SheetSnapshot, found: dict[TemplateRole, list[RoleBinding]]
    ) -> None:
        c_expected = {f"C{index}" for index in range(4)}
        for header_row in sorted({cell.row for cell in sheet.cells}):
            row_cells = sheet.cells_in_row(header_row)
            c_cells = {
                cell.normalized_value.upper(): cell
                for cell in row_cells
                if cell.normalized_value.upper() in c_expected
            }
            if set(c_cells) != c_expected:
                continue
            min_c = min(cell.column for cell in c_cells.values())
            s_values = set()
            e_values = set()
            result_count = 0
            relevant: list[CellSnapshot] = list(c_cells.values())
            for cell in sheet.cells:
                if cell.row <= header_row:
                    continue
                upper = cell.normalized_value.upper().replace("ASIL ", "")
                if cell.column < min_c and re.fullmatch(r"S[0-3]", upper):
                    s_values.add(upper)
                    relevant.append(cell)
                elif cell.column < min_c and re.fullmatch(r"E[0-4]", upper):
                    e_values.add(upper)
                    relevant.append(cell)
                elif min_c <= cell.column <= max(x.column for x in c_cells.values()) and upper in {
                    "QM", "NA", "N/A", "A", "B", "C", "D"
                }:
                    result_count += 1
                    relevant.append(cell)
            if s_values != {f"S{i}" for i in range(4)} or e_values != {
                f"E{i}" for i in range(5)
            } or result_count < 80:
                continue
            region = self._cells_region(relevant)
            found[TemplateRole.ASIL_MATRIX].append(
                self._binding(
                    TemplateRole.ASIL_MATRIX, sheet, region, "structural_matrix_signature",
                    ("S0-S3", "E0-E4", "C0-C3", f"result_cells={result_count}"),
                    ("asil", "matrix"), 0.99,
                )
            )

    def _discover_report_tables(
        self, sheet: SheetSnapshot, found: dict[TemplateRole, list[RoleBinding]]
    ) -> None:
        for start_row in sorted({cell.row for cell in sheet.cells}):
            mapping = self._field_mapping(sheet, (start_row, start_row + 1), self._HARA_FIELDS)
            if len(mapping) >= 16:
                region = self._region(
                    start_row, min(mapping.values()), start_row + 1, max(mapping.values())
                )
                found[TemplateRole.HARA_OUTPUT_TABLE].append(
                    self._binding(
                        TemplateRole.HARA_OUTPUT_TABLE, sheet, region,
                        "canonical_header_signature",
                        (f"canonical_fields={len(mapping)}", "two_row_header"),
                        tuple(sorted(mapping)), 0.98,
                    )
                )
                break
        for row in sorted({cell.row for cell in sheet.cells}):
            mapping = self._field_mapping(sheet, (row,), self._SG_FIELDS)
            if len(mapping) >= 5:
                region = self._region(row, min(mapping.values()), row, max(mapping.values()))
                found[TemplateRole.SAFETY_GOAL_OUTPUT_TABLE].append(
                    self._binding(
                        TemplateRole.SAFETY_GOAL_OUTPUT_TABLE, sheet, region,
                        "canonical_header_signature",
                        (f"canonical_fields={len(mapping)}", "safety_goal_header"),
                        tuple(sorted(mapping)), 0.97,
                    )
                )
                break

    def _method_candidates(
        self, snapshot: WorkbookSnapshot, found: dict[TemplateRole, list[RoleBinding]]
    ) -> None:
        workflow_sheets = {item.sheet for item in found[TemplateRole.WORKFLOW]}
        for sheet_name in workflow_sheets:
            sheet = snapshot.sheet(sheet_name)
            for row in sorted({cell.row for cell in sheet.cells}):
                row_cells = sheet.cells_in_row(row)
                text = " | ".join(cell.normalized_value for cell in row_cells)
                if ("填写安全目标" in text or "safety goal" in text) and "避免" in text:
                    region = self._cells_region(list(row_cells))
                    found[TemplateRole.SAFETY_GOAL_METHOD].append(
                        self._binding(
                            TemplateRole.SAFETY_GOAL_METHOD, sheet, region,
                            "workflow_method_signature", (f"workflow_row={row}",),
                            ("safety goal", "avoidance intent"), 0.94,
                        )
                    )
                if ("安全状态" in text or "safe state" in text) and (
                    "推论" in text or "推理" in text or "derive" in text
                ):
                    region = self._cells_region(list(row_cells))
                    found[TemplateRole.SAFE_STATE_METHOD].append(
                        self._binding(
                            TemplateRole.SAFE_STATE_METHOD, sheet, region,
                            "workflow_method_signature", (f"workflow_row={row}",),
                            ("safe state", "derivation"), 0.94,
                        )
                    )

    def _select(
        self,
        snapshot: WorkbookSnapshot,
        role: TemplateRole,
        candidates: list[RoleBinding],
        confirmation: TemplateRoleConfirmation | None,
    ) -> RoleBinding:
        if confirmation:
            selected = confirmation.selection_for(role)
            if selected:
                sheet, region = selected
                match = next(
                    (item for item in candidates if item.sheet == sheet and item.region == region),
                    None,
                )
                if match is None:
                    raise ValueError(
                        f"Confirmed region is not a valid {role.value} candidate: {sheet}!{region}"
                    )
                return replace(
                    match,
                    detection_method="human_confirmation",
                    diagnostics=match.diagnostics + (
                        f"confirmed_by={confirmation.confirmed_by}",
                        f"confirmed_at={confirmation.confirmed_at}",
                    ),
                )
        ordered = sorted(candidates, key=lambda item: item.confidence, reverse=True)
        if len(ordered) == 1 or ordered[0].confidence - ordered[1].confidence > 0.015:
            return ordered[0]
        contenders = tuple(
            item for item in ordered if ordered[0].confidence - item.confidence <= 0.015
        )
        if self.classifier is not None:
            classification = self.classifier.classify(snapshot, role, contenders)
            match = next(
                (
                    item for item in contenders
                    if item.sheet == classification.sheet and item.region == classification.region
                ),
                None,
            )
            if match is not None and classification.confidence >= 0.8:
                return replace(
                    match,
                    detection_method="llm_assisted_role_classification",
                    confidence=min(match.confidence, classification.confidence),
                    diagnostics=match.diagnostics + (classification.reason,),
                )
        raise TemplateRoleAmbiguityError(
            role,
            [(item.sheet, item.region, item.confidence) for item in contenders],
        )

    def _diagnostics(
        self, snapshot: WorkbookSnapshot, bindings: tuple[RoleBinding, ...]
    ) -> list[TemplateDiagnostic]:
        diagnostics: list[TemplateDiagnostic] = []
        if not any(sheet.tables for sheet in snapshot.sheets):
            diagnostics.append(
                TemplateDiagnostic(
                    TemplateDiagnosticSeverity.INFO,
                    TemplateDiagnosticCode.STRUCTURE_METADATA_MISSING,
                    "Workbook has no structured tables; roles were compiled from structural and semantic signatures.",
                )
            )
        workflow = next(item for item in bindings if item.role is TemplateRole.WORKFLOW)
        output = next(item for item in bindings if item.role is TemplateRole.HARA_OUTPUT_TABLE)
        workflow_sheet = snapshot.sheet(workflow.sheet)
        output_sheet = snapshot.sheet(output.sheet)
        min_col, min_row, max_col, max_row = range_boundaries(output.region)
        field_mapping = self._field_mapping(
            output_sheet, tuple(range(min_row, max_row + 1)), self._HARA_FIELDS
        )
        expectations = {
            "situational_detailing": ("细化组合场景", "situational detail"),
            "hazardous_event": ("危害事件", "hazardous event"),
            "safety_goal": ("填写安全目标", "safety goal"),
            "safe_state": ("安全状态", "safe state"),
        }
        activity_column = None
        for cell in workflow_sheet.cells:
            if cell.normalized_value in {"活动", "activity"}:
                activity_column = cell.column
                break
        for row in sorted({cell.row for cell in workflow_sheet.cells}):
            text = " | ".join(
                cell.normalized_value for cell in workflow_sheet.cells_in_row(row)
            )
            activity_cell = (
                workflow_sheet.cell(row, activity_column) if activity_column is not None else None
            )
            semantic_text = activity_cell.normalized_value if activity_cell is not None else text
            for field, aliases in expectations.items():
                if field not in field_mapping or not any(
                    alias in semantic_text for alias in aliases
                ):
                    continue
                declared = re.findall(r"([a-z]{1,2})列", text)
                if not declared:
                    continue
                actual = get_column_letter(field_mapping[field]).casefold()
                if declared[-1] != actual:
                    diagnostics.append(
                        TemplateDiagnostic(
                            TemplateDiagnosticSeverity.WARNING,
                            TemplateDiagnosticCode.WORKFLOW_COORDINATE_MISMATCH,
                            f"Workflow row {row} declares column {declared[-1].upper()} for {field}, "
                            f"but the report header binds it to {actual.upper()}.",
                            role=TemplateRole.WORKFLOW,
                            sheet=workflow.sheet,
                            region=self._cells_region(list(workflow_sheet.cells_in_row(row))),
                            details={
                                "field": field,
                                "declared_column": declared[-1].upper(),
                                "report_column": actual.upper(),
                                "workflow_row": row,
                            },
                        )
                    )
        malformed = next(
            (
                cell for cell in workflow_sheet.cells
                if re.search(r"c0\s*[,，/]\s*11\s*[,，/]\s*c2", cell.normalized_value)
            ),
            None,
        )
        if malformed:
            diagnostics.append(
                TemplateDiagnostic(
                    TemplateDiagnosticSeverity.WARNING,
                    TemplateDiagnosticCode.STRUCTURE_INCOMPLETE,
                    "Workflow contains malformed controllability level text 'C0,11,C2'.",
                    role=TemplateRole.WORKFLOW,
                    sheet=workflow.sheet,
                    region=malformed.coordinate,
                    details={"raw_text": str(malformed.raw_value)},
                )
            )
        return diagnostics

    def _field_mapping(
        self,
        sheet: SheetSnapshot,
        rows: tuple[int, ...],
        aliases: dict[str, tuple[str, ...]],
    ) -> dict[str, int]:
        by_column: dict[int, str] = {}
        for column in range(1, sheet.max_column + 1):
            text = " ".join(
                cell.normalized_value
                for row in rows
                for cell in (sheet.cell(row, column),)
                if cell is not None
            )
            by_column[column] = text
        result: dict[str, int] = {}
        for field, field_aliases in aliases.items():
            exact = [column for column, text in by_column.items() if text in field_aliases]
            if exact:
                result[field] = exact[0]
                continue
            matches = [
                column for column, text in by_column.items()
                if any(alias in text for alias in field_aliases)
            ]
            if matches:
                result[field] = matches[0]
        return result

    @staticmethod
    def _deduplicate(candidates: list[RoleBinding]) -> list[RoleBinding]:
        unique: dict[tuple[str, str], RoleBinding] = {}
        for item in candidates:
            key = (item.sheet, item.region)
            if key not in unique or item.confidence > unique[key].confidence:
                unique[key] = item
        return list(unique.values())

    @staticmethod
    def _region(min_row: int, min_column: int, max_row: int, max_column: int) -> str:
        return (
            f"{get_column_letter(min_column)}{min_row}:"
            f"{get_column_letter(max_column)}{max_row}"
        )

    @classmethod
    def _cells_region(cls, cells: list[CellSnapshot]) -> str:
        if not cells:
            return "A1:A1"
        return cls._region(
            min(cell.row for cell in cells), min(cell.column for cell in cells),
            max(cell.row for cell in cells), max(cell.column for cell in cells),
        )

    @staticmethod
    def _region_hash(sheet: SheetSnapshot, region: str) -> str:
        min_column, min_row, max_column, max_row = range_boundaries(region)
        payload = "\n".join(
            f"{cell.coordinate}={cell.normalized_value}"
            for cell in sheet.cells
            if min_row <= cell.row <= max_row and min_column <= cell.column <= max_column
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
