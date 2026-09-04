from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from hara_agent.contracts import CompileStatus, MethodContract
from hara_agent.template import TemplateRoleCompiler, TemplateRoleManifestStore

from .yaml_baseline import YamlBaselineCompiler


class MethodSourceKind(str, Enum):
    TEMPLATE = "TEMPLATE"
    YAML_BASELINE = "YAML_BASELINE"


@dataclass(frozen=True)
class MethodResolution:
    method: MethodContract
    source_kind: MethodSourceKind
    report_template_path: Path
    report_template_hash: str


class MethodSourceResolver:
    def __init__(self, manifest_root: str | Path | None = None):
        root = manifest_root or os.getenv(
            "HARA_TEMPLATE_ROLE_MANIFEST_DIR",
            "runtime/agent/template-role-manifests",
        )
        self.template_compiler = TemplateRoleCompiler(
            manifest_store=TemplateRoleManifestStore(root)
        )
        self.yaml_compiler = YamlBaselineCompiler()

    @staticmethod
    def _ready(method: MethodContract, label: str) -> None:
        if (
            method.compile_status is CompileStatus.NOT_READY
            or not method.engineering_rules_compiled
        ):
            blockers = [item.message for item in method.blocking_diagnostics]
            raise ValueError(f"{label} MethodContract is not ready: {blockers}")

    def resolve(
        self, *, template_path: Path | None, baseline_manifest_path: Path,
        report_template_path: Path,
    ) -> MethodResolution:
        if template_path is not None:
            method = self.template_compiler.compile_method(template_path)
            self._ready(method, "Active template")
            return MethodResolution(
                method=method, source_kind=MethodSourceKind.TEMPLATE,
                report_template_path=template_path,
                report_template_hash=str(method.metadata["template_hash"]),
            )

        layout_method = self.template_compiler.compile_method(report_template_path)
        self._ready(layout_method, "Report template")
        method = self.yaml_compiler.compile(
            baseline_manifest_path, report_contract=layout_method.report_contract,
        )
        self._ready(method, "YAML baseline")
        return MethodResolution(
            method=method, source_kind=MethodSourceKind.YAML_BASELINE,
            report_template_path=report_template_path,
            report_template_hash=str(layout_method.metadata["template_hash"]),
        )
