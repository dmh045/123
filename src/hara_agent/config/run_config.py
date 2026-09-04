from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class RunConfig:
    item_path: Path
    template_path: Optional[Path]
    output_path: Path
    run_dir: Path
    method_baseline_path: Path = Path("method_assets/fusa_baseline_v1/manifest.yaml")
    report_template_path: Path = Path("references/HARA_Template_AI_20260327.xlsx")
    allow_draft: bool = False
    resume: bool = False
    run_id: str = "hara-run"
    ego_speed_kph: Optional[float] = None
    ego_speed_source: str = ""
    operating_mode: Optional[str] = None
    allow_aggregate_speed_fallback: bool = False
    max_workers: int = 4

    def validate(self) -> None:
        if not self.item_path.is_file():
            raise FileNotFoundError(f"Item Definition不存在: {self.item_path}")
        if self.template_path is not None:
            if not self.template_path.is_file():
                raise FileNotFoundError(f"HARA模板不存在: {self.template_path}")
            if self.template_path.suffix.lower() != ".xlsx":
                raise ValueError("HARA模板必须为.xlsx文件")
        else:
            if not self.method_baseline_path.is_file():
                raise FileNotFoundError(
                    f"HARA YAML baseline manifest不存在: {self.method_baseline_path}"
                )
            if not self.report_template_path.is_file():
                raise FileNotFoundError(
                    f"HARA报告模板不存在: {self.report_template_path}"
                )
            if self.report_template_path.suffix.lower() != ".xlsx":
                raise ValueError("HARA报告模板必须为.xlsx文件")
        if self.output_path.suffix.lower() != ".xlsx":
            raise ValueError("输出报告必须为.xlsx文件")
        if not self.run_id or any(not (char.isalnum() or char in "-_") for char in self.run_id):
            raise ValueError("run_id只能包含字母、数字、连字符和下划线")
        if self.ego_speed_kph is not None and self.ego_speed_kph < 0:
            raise ValueError("ego_speed_kph不得为负数")
        if self.operating_mode is not None and not self.operating_mode.strip():
            raise ValueError("operating_mode不得为空白字符串")
        if not 1 <= self.max_workers <= 32:
            raise ValueError("max_workers必须在1到32之间")
