from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING
from xml.etree import ElementTree as ET
from zipfile import ZIP_DEFLATED, ZipFile

from openpyxl import load_workbook

from .template_contract import HARATemplateContract

if TYPE_CHECKING:
    from hara_agent.workflow.state import HARAState


MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
NS = {"m": MAIN_NS, "r": REL_NS, "pr": PKG_REL_NS}
ET.register_namespace("", MAIN_NS)
ET.register_namespace("r", REL_NS)
ET.register_namespace("x14", "http://schemas.microsoft.com/office/spreadsheetml/2009/9/main")
ET.register_namespace("xm", "http://schemas.microsoft.com/office/excel/2006/main")
ET.register_namespace("mc", "http://schemas.openxmlformats.org/markup-compatibility/2006")
ET.register_namespace("x14ac", "http://schemas.microsoft.com/office/spreadsheetml/2009/9/ac")


class HARAExcelRenderer:
    """Edit only target worksheet XML while preserving the complete template package."""

    def __init__(self, contract: HARATemplateContract | None = None):
        self.contract = contract or HARATemplateContract()

    def render(self, state: HARAState, template_path: str | Path,
               output_path: str | Path, draft: bool = False, smoke: bool = False) -> Path:
        if not draft and not smoke and not state.can_publish:
            raise ValueError("HARAState仍有待评审项或错误，禁止生成正式报告")
        template = self.contract.validate(template_path)
        output = Path(output_path).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        hara_rows = self._hara_rows(state)
        sg_rows = self._safety_goal_rows(state)
        watermark = (
            "SMOKE TEST — NOT FOR RELEASE / 测试子集，禁止正式发布"
            if smoke else "DRAFT — NOT FOR RELEASE / 待工程评审，禁止正式发布"
        )
        replacements = {
            self.contract.HARA_SHEET: (
                self.contract.HARA_START_ROW, 21, hara_rows, self._merge_ranges(hara_rows),
                {"A3": watermark} if draft or smoke else {},
            ),
            self.contract.SG_SHEET: (
                self.contract.SG_START_ROW, 6, sg_rows, [],
                {"A2": watermark} if draft or smoke else {},
            ),
        }
        self._rewrite_package(template, output, replacements)
        self._verify_reopen(output, len(hara_rows), len(sg_rows))
        return output

    def _hara_rows(self, state: HARAState) -> list[list[object]]:
        scenarios = self._unique_index(state.scenarios, lambda item: item.scenario_id, "Scenario")
        malfunctions = self._unique_index(
            state.malfunctions, lambda item: str(item.get("malfunction_id", "")), "Malfunction",
        )
        functions = self._unique_index(
            state.functions, lambda item: str(item.get("function_id", "")), "Function",
        )
        goals = self._unique_index(state.safety_goals, lambda item: item.sg_id, "Safety Goal")
        rows = []
        for offset, risk in enumerate(state.risk_results):
            if risk.scenario_id not in scenarios or risk.malfunction_id not in malfunctions:
                raise ValueError(
                    f"Renderer外键缺失: risk={risk.assessment_id!r}, "
                    f"scenario={risk.scenario_id!r}, malfunction={risk.malfunction_id!r}"
                )
            scenario = scenarios[risk.scenario_id]
            malfunction = malfunctions[risk.malfunction_id]
            function_id = str(malfunction.get("function_id", ""))
            if function_id not in functions:
                raise ValueError(f"Renderer Function外键缺失: {function_id!r}")
            function = functions[function_id]
            goal = goals.get(risk.safety_goal_id)
            if risk.asil.value in {"A", "B", "C", "D"} and (
                not risk.safety_goal_id or goal is None
            ):
                raise ValueError(
                    f"Renderer非QM Risk缺少Safety Goal外键: risk={risk.assessment_id!r}, "
                    f"safety_goal_id={risk.safety_goal_id!r}"
                )
            rows.append([
                f"HARA_{offset + 1:03d}", function.get("name", ""), function.get("output", ""),
                malfunction.get("guideword", ""), malfunction.get("description", ""),
                malfunction.get("vehicle_level_hazard", ""), scenario.situational_description,
                scenario.situational_detailing,
                "；".join(value for value in (risk.hazardous_event, risk.potential_harm) if value),
                risk.severity.value, self._basis(risk.severity), risk.exposure.value,
                risk.exposure_tf, self._basis(risk.exposure),
                risk.controllability.value, self._basis(risk.controllability), risk.asil.value,
                goal.sg_id if goal else "", goal.text if goal else "", goal.safe_state if goal else "", "",
            ])
        return rows

    @staticmethod
    def _unique_index(values, key, label: str):
        result = {}
        for value in values:
            identity = key(value)
            if not identity:
                raise ValueError(f"Renderer {label} ID不能为空")
            if identity in result:
                raise ValueError(f"Renderer {label} ID不唯一，禁止静默覆盖: {identity!r}")
            result[identity] = value
        return result

    @staticmethod
    def _safety_goal_rows(state: HARAState) -> list[list[object]]:
        malfunctions = {
            str(item.get("malfunction_id", "")): item for item in state.malfunctions
        }
        risks_by_goal = {}
        for risk in state.risk_results:
            if risk.safety_goal_id:
                risks_by_goal.setdefault(risk.safety_goal_id, []).append(risk)
        rows = []
        for offset, goal in enumerate(state.safety_goals):
            hazards = list(dict.fromkeys(
                str(malfunctions.get(risk.malfunction_id, {}).get("vehicle_level_hazard", ""))
                for risk in risks_by_goal.get(goal.sg_id, [])
                if malfunctions.get(risk.malfunction_id, {}).get("vehicle_level_hazard")
            ))
            rows.append([
                f"HZ_{offset + 1:02d}", "；".join(hazards), goal.sg_id,
                goal.text, goal.safe_state, goal.max_asil,
            ])
        return rows

    @staticmethod
    def _basis(evidence) -> str:
        if evidence.sources and evidence.sources[0].excerpt:
            return evidence.sources[0].excerpt
        return evidence.review_reason

    def _rewrite_package(self, template: Path, output: Path, replacements: dict) -> None:
        with ZipFile(template, "r") as source:
            sheet_paths = self._sheet_paths(source)
            unknown = sorted(set(replacements) - set(sheet_paths))
            if unknown:
                raise ValueError(f"模板Sheet关系缺失: {unknown}")
            changed = {
                sheet_paths[name]: self._replace_sheet_data(
                    source.read(sheet_paths[name]), *config
                )
                for name, config in replacements.items()
            }
            handle, temp_name = tempfile.mkstemp(
                prefix=f".{output.stem}.", suffix=".xlsx", dir=str(output.parent)
            )
            os.close(handle)
            try:
                with ZipFile(temp_name, "w", ZIP_DEFLATED) as target:
                    for info in source.infolist():
                        target.writestr(info, changed.get(info.filename, source.read(info.filename)))
                os.replace(temp_name, output)
            finally:
                if os.path.exists(temp_name):
                    os.unlink(temp_name)

    @staticmethod
    def _sheet_paths(package: ZipFile) -> dict[str, str]:
        workbook = ET.fromstring(package.read("xl/workbook.xml"))
        relationships = ET.fromstring(package.read("xl/_rels/workbook.xml.rels"))
        targets = {
            item.attrib["Id"]: item.attrib["Target"].lstrip("/")
            for item in relationships.findall(f"{{{PKG_REL_NS}}}Relationship")
        }
        result = {}
        for sheet in workbook.find(f"{{{MAIN_NS}}}sheets"):
            relationship_id = sheet.attrib[f"{{{REL_NS}}}id"]
            target = targets[relationship_id]
            result[sheet.attrib["name"]] = target if target.startswith("xl/") else f"xl/{target}"
        return result

    def _replace_sheet_data(self, xml_bytes: bytes, start_row: int, max_column: int,
                            rows: list[list[object]], merge_ranges: list[str],
                            header_updates: dict[str, str]) -> bytes:
        root = ET.fromstring(xml_bytes)
        sheet_data = root.find("m:sheetData", NS)
        existing_rows = list(sheet_data.findall("m:row", NS))
        template_row = next((row for row in existing_rows if int(row.attrib["r"]) == start_row), None)
        if template_row is None:
            raise ValueError(f"模板缺少样式基准行: {start_row}")
        style_by_column = {
            self._column_number(cell.attrib["r"]): cell.attrib.get("s")
            for cell in template_row.findall("m:c", NS)
        }
        row_attributes = {key: value for key, value in template_row.attrib.items() if key != "r"}
        for row in existing_rows:
            if int(row.attrib["r"]) >= start_row:
                sheet_data.remove(row)
        for offset, values in enumerate(rows):
            row_number = start_row + offset
            row = ET.Element(f"{{{MAIN_NS}}}row", {"r": str(row_number), **row_attributes})
            for column in range(1, max_column + 1):
                value = values[column - 1] if column <= len(values) else ""
                if value is None or value == "":
                    continue
                attrs = {"r": f"{self._column_letter(column)}{row_number}", "t": "inlineStr"}
                if style_by_column.get(column) is not None:
                    attrs["s"] = style_by_column[column]
                cell = ET.SubElement(row, f"{{{MAIN_NS}}}c", attrs)
                inline = ET.SubElement(cell, f"{{{MAIN_NS}}}is")
                text = ET.SubElement(inline, f"{{{MAIN_NS}}}t")
                text.text = str(value)
            sheet_data.append(row)
        for reference, value in header_updates.items():
            self._set_inline_string(sheet_data, reference, value)
        self._replace_data_merges(root, start_row, merge_ranges)
        dimension = root.find("m:dimension", NS)
        if dimension is not None:
            last_row = max(start_row - 1, start_row + len(rows) - 1)
            dimension.attrib["ref"] = f"A1:{self._column_letter(max_column)}{last_row}"
        return ET.tostring(root, encoding="utf-8", xml_declaration=True)

    @classmethod
    def _set_inline_string(cls, sheet_data, reference: str, value: str) -> None:
        row_number = cls._range_max_row(reference)
        row = next(
            (item for item in sheet_data.findall("m:row", NS) if int(item.attrib["r"]) == row_number),
            None,
        )
        if row is None:
            row = ET.Element(f"{{{MAIN_NS}}}row", {"r": str(row_number)})
            inserted = False
            for index, existing in enumerate(list(sheet_data)):
                if int(existing.attrib["r"]) > row_number:
                    sheet_data.insert(index, row)
                    inserted = True
                    break
            if not inserted:
                sheet_data.append(row)
        cell = next(
            (item for item in row.findall("m:c", NS) if item.attrib.get("r") == reference),
            None,
        )
        if cell is None:
            cell = ET.Element(f"{{{MAIN_NS}}}c", {"r": reference})
            row.append(cell)
        for child in list(cell):
            cell.remove(child)
        cell.attrib["t"] = "inlineStr"
        inline = ET.SubElement(cell, f"{{{MAIN_NS}}}is")
        text = ET.SubElement(inline, f"{{{MAIN_NS}}}t")
        text.text = value

    @staticmethod
    def _replace_data_merges(root, start_row: int, new_ranges: list[str]) -> None:
        merge_cells = root.find("m:mergeCells", NS)
        if merge_cells is None:
            merge_cells = ET.Element(f"{{{MAIN_NS}}}mergeCells")
            sheet_data = root.find("m:sheetData", NS)
            root.insert(list(root).index(sheet_data) + 1, merge_cells)
        for merge in list(merge_cells):
            if HARAExcelRenderer._range_max_row(merge.attrib["ref"]) >= start_row:
                merge_cells.remove(merge)
        for reference in new_ranges:
            ET.SubElement(merge_cells, f"{{{MAIN_NS}}}mergeCell", {"ref": reference})
        merge_cells.attrib["count"] = str(len(list(merge_cells)))

    @staticmethod
    def _merge_ranges(rows: list[list[object]]) -> list[str]:
        ranges = []
        start_row = HARATemplateContract.HARA_START_ROW
        for column in range(2, 8):
            group_start = 0
            for index in range(1, len(rows) + 1):
                boundary = index == len(rows)
                if not boundary:
                    boundary = not (
                        rows[index][column - 1] == rows[index - 1][column - 1]
                        and rows[index][1:column - 1] == rows[index - 1][1:column - 1]
                    )
                if boundary:
                    if index - group_start > 1 and rows[group_start][column - 1] not in (None, ""):
                        letter = HARAExcelRenderer._column_letter(column)
                        ranges.append(f"{letter}{start_row + group_start}:{letter}{start_row + index - 1}")
                    group_start = index
        return ranges

    @staticmethod
    def _range_max_row(reference: str) -> int:
        end = reference.split(":")[-1]
        return int("".join(character for character in end if character.isdigit()))

    @staticmethod
    def _column_number(reference: str) -> int:
        letters = "".join(character for character in reference if character.isalpha())
        result = 0
        for character in letters:
            result = result * 26 + ord(character.upper()) - 64
        return result

    @staticmethod
    def _column_letter(number: int) -> str:
        result = ""
        while number:
            number, remainder = divmod(number - 1, 26)
            result = chr(65 + remainder) + result
        return result

    def _verify_reopen(self, output: Path, hara_count: int, sg_count: int) -> None:
        with ZipFile(output, "r") as package:
            if package.testzip() is not None:
                raise ValueError("报告ZIP包完整性校验失败")
        workbook = load_workbook(output, read_only=False, data_only=False)
        try:
            hara = workbook[self.contract.HARA_SHEET]
            for offset in range(hara_count):
                row = self.contract.HARA_START_ROW + offset
                if not hara.cell(row, 1).value:
                    raise ValueError(f"报告复核失败: 05_HARA!A{row}为空")
                if str(hara.cell(row, 17).value or "").startswith("="):
                    raise ValueError(f"报告复核失败: 05_HARA!Q{row}仍为旧ASIL公式")
            sg = workbook[self.contract.SG_SHEET]
            for offset in range(sg_count):
                row = self.contract.SG_START_ROW + offset
                if not sg.cell(row, 3).value:
                    raise ValueError(f"报告复核失败: 06_Safety Goal!C{row}为空")
        finally:
            workbook.close()
