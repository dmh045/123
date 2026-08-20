from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hara_agent.contracts import MethodContract


@dataclass(frozen=True)
class TemplateScoreLevel:
    dimension: str
    level: str
    description: str
    criterion: str
    sheet: str
    location: str


@dataclass(frozen=True)
class TemplateScoringStandards:
    """S/E/C definitions read from the template used for the current run."""

    source_path: Path
    severity: dict[str, TemplateScoreLevel]
    exposure_duration: dict[str, TemplateScoreLevel]
    exposure_frequency: dict[str, TemplateScoreLevel]
    controllability: dict[str, TemplateScoreLevel]
    method_contract_hash: str = ""

    EXPOSURE_METHODS = {"T": "duration", "F": "frequency"}

    def reference(
        self,
        dimension: str,
        level: str,
        exposure_method: str = "",
    ) -> TemplateScoreLevel:
        name = str(dimension).strip().lower()
        code = str(level).strip().upper()
        if name == "severity":
            table = self.severity
        elif name == "controllability":
            table = self.controllability
        elif name == "exposure":
            method = str(exposure_method).strip().upper()
            if code == "E0" and method not in self.EXPOSURE_METHODS:
                method = "T"
            if method not in self.EXPOSURE_METHODS:
                raise ValueError(f"Exposure必须声明T(时间占比)或F(发生频率)，实际={exposure_method!r}")
            table = self.exposure_duration if method == "T" else self.exposure_frequency
        else:
            raise ValueError(f"不支持的评分维度: {dimension!r}")
        if code not in table:
            raise ValueError(
                f"输入模板未定义{dimension}等级{code!r}; source={self.source_path}"
            )
        return table[code]

    @property
    def counts(self) -> dict[str, int]:
        return {
            "severity": len(self.severity),
            "exposure_duration": len(self.exposure_duration),
            "exposure_frequency": len(self.exposure_frequency),
            "controllability": len(self.controllability),
        }


def scoring_standards_from_method_contract(
    method: MethodContract, source_path: str | Path
) -> TemplateScoringStandards:
    """Adapt compiled scales for the migration-only Domain scoring service."""

    path = Path(source_path).expanduser().resolve()

    def scale_table(levels, dimension: str) -> dict[str, TemplateScoreLevel]:
        return {
            item.level: TemplateScoreLevel(
                dimension=dimension,
                level=item.level,
                description=item.description,
                criterion=item.criterion,
                sheet=item.source_ref.sheet,
                location=item.source_ref.range,
            )
            for item in levels
        }

    severity = scale_table(method.severity.scale.levels, "severity")
    controllability = scale_table(method.controllability.scale, "controllability")
    exposure_scale = scale_table(method.exposure.scale, "exposure")

    def exposure_table(rules) -> dict[str, TemplateScoreLevel]:
        result: dict[str, TemplateScoreLevel] = {}
        for level, base in exposure_scale.items():
            matching = [rule for rule in rules if rule.result == level]
            if not matching:
                result[level] = base
                continue
            sources = [source for rule in matching for source in rule.source_refs]
            result[level] = TemplateScoreLevel(
                dimension="exposure",
                level=level,
                description=base.description,
                criterion="\n".join(rule.raw_text for rule in matching),
                sheet=sources[0].sheet,
                location=",".join(source.range for source in sources),
            )
        return result

    return TemplateScoringStandards(
        source_path=path,
        severity=severity,
        exposure_duration=exposure_table(method.exposure.duration_rules),
        exposure_frequency=exposure_table(method.exposure.frequency_rules),
        controllability=controllability,
        method_contract_hash=str(method.metadata["template_hash"]),
    )


class TemplateScoringStandardReader:
    SEVERITY_SHEET = "Severity"
    EXPOSURE_SHEET = "Exposure"
    CONTROLLABILITY_SHEET = "Controllability"
    VALUES_SHEET = "Werte"

    def read(self, workbook: Any, source_path: Path) -> TemplateScoringStandards:
        required = {
            self.SEVERITY_SHEET,
            self.EXPOSURE_SHEET,
            self.CONTROLLABILITY_SHEET,
            self.VALUES_SHEET,
        }
        missing = sorted(required - set(workbook.sheetnames))
        if missing:
            raise ValueError(f"模板缺少S/E/C标准Sheet: {missing}")

        severity = self._single_table(
            workbook[self.SEVERITY_SHEET], tuple(f"S{i}" for i in range(4)), "severity"
        )
        exposure_duration = self._exposure_table(workbook[self.EXPOSURE_SHEET], "T")
        exposure_frequency = self._exposure_table(workbook[self.EXPOSURE_SHEET], "F")
        e0 = self._e0_reference(workbook[self.VALUES_SHEET])
        exposure_duration = {"E0": e0, **exposure_duration}
        exposure_frequency = {"E0": e0, **exposure_frequency}
        controllability = self._single_table(
            workbook[self.CONTROLLABILITY_SHEET], tuple(f"C{i}" for i in range(4)),
            "controllability",
        )
        return TemplateScoringStandards(
            source_path=source_path,
            severity=severity,
            exposure_duration=exposure_duration,
            exposure_frequency=exposure_frequency,
            controllability=controllability,
        )

    @staticmethod
    def _row_codes(sheet: Any, row: int, expected: tuple[str, ...]) -> dict[str, int]:
        found: dict[str, int] = {}
        for column in range(1, sheet.max_column + 1):
            value = str(sheet.cell(row, column).value or "").strip().upper()
            if value in expected:
                found[value] = column
        return found

    def _single_table(
        self,
        sheet: Any,
        expected: tuple[str, ...],
        dimension: str,
    ) -> dict[str, TemplateScoreLevel]:
        for row in range(1, min(sheet.max_row, 40) + 1):
            columns = self._row_codes(sheet, row, expected)
            if set(columns) != set(expected):
                continue
            description_row = row + 1
            criterion_row = row + 2
            return {
                code: TemplateScoreLevel(
                    dimension=dimension,
                    level=code,
                    description=str(sheet.cell(description_row, column).value or "").strip(),
                    criterion=str(sheet.cell(criterion_row, column).value or "").strip(),
                    sheet=sheet.title,
                    location=f"{sheet.cell(row, column).coordinate}:{sheet.cell(criterion_row, column).coordinate}",
                )
                for code, column in columns.items()
            }
        raise ValueError(f"{sheet.title}未包含完整等级表: {expected}")

    def _exposure_table(self, sheet: Any, method: str) -> dict[str, TemplateScoreLevel]:
        expected = tuple(f"E{i}" for i in range(1, 5))
        criterion_token = "duration" if method == "T" else "frequency"
        for row in range(1, min(sheet.max_row, 45) + 1):
            columns = self._row_codes(sheet, row, expected)
            if set(columns) != set(expected):
                continue
            criterion_row = row + 2
            label = str(sheet.cell(criterion_row, 2).value or "").strip().lower()
            if criterion_token not in label:
                continue
            return {
                code: TemplateScoreLevel(
                    dimension="exposure",
                    level=code,
                    description=str(sheet.cell(row + 1, column).value or "").strip(),
                    criterion=str(sheet.cell(criterion_row, column).value or "").strip(),
                    sheet=sheet.title,
                    location=f"{sheet.cell(row, column).coordinate}:{sheet.cell(criterion_row, column).coordinate}",
                )
                for code, column in columns.items()
            }
        label = "时间占比" if method == "T" else "发生频率"
        raise ValueError(f"Exposure未包含完整{label}等级表")

    @staticmethod
    def _e0_reference(sheet: Any) -> TemplateScoreLevel:
        for row in range(1, sheet.max_row + 1):
            for column in range(1, sheet.max_column + 1):
                if str(sheet.cell(row, column).value or "").strip().upper() == "E0":
                    return TemplateScoreLevel(
                        dimension="exposure",
                        level="E0",
                        description="No exposure / 无暴露",
                        criterion="模板Exposure参数零轴",
                        sheet=sheet.title,
                        location=sheet.cell(row, column).coordinate,
                    )
        raise ValueError("Werte未定义E0")
