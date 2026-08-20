from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class RunConfig:
    item_path: Path
    template_path: Path
    output_path: Path
    run_dir: Path
    domain: Optional[str] = None
    domain_profile_path: Optional[Path] = None
    allow_draft: bool = False
    resume: bool = False
    run_id: str = "hara-run"
    ego_speed_kph: Optional[float] = None
    ego_speed_source: str = ""
    operating_mode: Optional[str] = None
    allow_aggregate_speed_fallback: bool = False
    allow_legacy_speed_fallback: bool = False
    max_workers: int = 4

    def validate(self) -> None:
        if not self.item_path.is_file():
            raise FileNotFoundError(f"Item Definition不存在: {self.item_path}")
        if not self.template_path.is_file():
            raise FileNotFoundError(f"HARA模板不存在: {self.template_path}")
        if self.template_path.suffix.lower() != ".xlsx":
            raise ValueError("HARA模板必须为.xlsx文件")
        if self.output_path.suffix.lower() != ".xlsx":
            raise ValueError("输出报告必须为.xlsx文件")
        if self.domain_profile_path and not self.domain_profile_path.is_file():
            raise FileNotFoundError(f"Domain Profile不存在: {self.domain_profile_path}")
        if not self.run_id or any(not (char.isalnum() or char in "-_") for char in self.run_id):
            raise ValueError("run_id只能包含字母、数字、连字符和下划线")
        if self.ego_speed_kph is not None and self.ego_speed_kph < 0:
            raise ValueError("ego_speed_kph不得为负数")
        if self.operating_mode is not None and not self.operating_mode.strip():
            raise ValueError("operating_mode不得为空白字符串")
        if not 1 <= self.max_workers <= 32:
            raise ValueError("max_workers必须在1到32之间")
