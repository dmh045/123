import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from hara_agent.config import RunConfig


@dataclass
class PhaseResult:
    success: bool
    output_json: Optional[str] = None
    error_message: Optional[str] = None
    warnings: list = field(default_factory=list)


@dataclass
class Progress:
    current_phase: str = "init"
    malfunction_completed: bool = False
    scen_hazevent_completed: bool = False
    scoring_completed: bool = False
    malfunction_json: Optional[str] = None
    scen_hazevent_json: Optional[str] = None
    scoring_json: Optional[str] = None
    skipped_phases: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


class ColoredOutput:
    RESET = "\033[0m"
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    MAGENTA = "\033[95m"
    CYAN = "\033[96m"
    WHITE = "\033[97m"
    BOLD = "\033[1m"

    @staticmethod
    def info(msg):
        print(f"{ColoredOutput.CYAN}[INFO]{ColoredOutput.RESET} {msg}")

    @staticmethod
    def success(msg):
        print(f"{ColoredOutput.GREEN}[SUCCESS]{ColoredOutput.RESET} {msg}")

    @staticmethod
    def warning(msg):
        print(f"{ColoredOutput.YELLOW}[WARNING]{ColoredOutput.RESET} {msg}")

    @staticmethod
    def error(msg):
        print(f"{ColoredOutput.RED}[ERROR]{ColoredOutput.RESET} {msg}")

    @staticmethod
    def phase(msg):
        print(f"{ColoredOutput.BOLD}{ColoredOutput.MAGENTA}[PHASE]{ColoredOutput.RESET} {msg}")

    @staticmethod
    def check(msg):
        print(f"{ColoredOutput.BLUE}[CHECK]{ColoredOutput.RESET} {msg}")

    @staticmethod
    def prompt(msg):
        print(f"{ColoredOutput.YELLOW}[PROMPT]{ColoredOutput.RESET} {msg}", end=" ")


class HARAController:
    SCRIPTS_DIR = Path(__file__).parent
    PROJECT_ROOT = SCRIPTS_DIR.parent
    DEFAULT_RUN_DIR = PROJECT_ROOT / "runtime" / "current"
    ENGINE_MALFUNCTION = SCRIPTS_DIR / "malfunction_engine.py"
    CHECKER_MALFUNCTION = SCRIPTS_DIR / "logic_checkers" / "malfunction_checker.py"
    ENGINE_SCEN_HAZEVENT = SCRIPTS_DIR / "scen_hazevent_engine.py"
    CHECKER_SCEN = SCRIPTS_DIR / "logic_checkers" / "scenario_checker.py"
    ENGINE_SCORING = SCRIPTS_DIR / "scoring_sg_engine.py"
    CHECKER_SCORING = SCRIPTS_DIR / "logic_checkers" / "scoring_checker.py"
    ENGINE_REPORT = SCRIPTS_DIR / "report_generator.py"

    def __init__(self, args: argparse.Namespace):
        self.item_path = args.item
        self.template_path = args.template
        self.output_path = args.output
        self.malfunction_json_arg = args.malfunction_json
        self.resume = args.resume
        self.allow_draft = getattr(args, "allow_draft", False)
        self.domain = getattr(args, "domain", None)
        self.domain_profile_path = getattr(args, "domain_profile", None)
        self.run_dir = self.DEFAULT_RUN_DIR
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.progress_file = self.run_dir / "progress.json"
        self.default_malfunction_json = self.run_dir / "malfunction_output.json"
        self.default_scen_hazevent_json = self.run_dir / "scen_hazevent_output.json"
        self.default_scoring_json = self.run_dir / "scoring_sg_output.json"
        self.run_config = None
        if self.item_path and self.template_path and self.output_path:
            self.run_config = RunConfig(
                item_path=Path(self.item_path),
                template_path=Path(self.template_path),
                output_path=Path(self.output_path),
                run_dir=self.run_dir,
                domain=self.domain,
                domain_profile_path=Path(self.domain_profile_path) if self.domain_profile_path else None,
                allow_draft=self.allow_draft,
                resume=self.resume,
            )
        self.progress = self._load_progress()
        self.python_exe = sys.executable

    def _load_progress(self) -> Progress:
        if self.resume and self.progress_file.exists():
            try:
                data = json.loads(self.progress_file.read_text(encoding="utf-8"))
                p = Progress(**data)
                ColoredOutput.info(f"已从断点恢复: {p.current_phase}")
                return p
            except Exception as e:
                ColoredOutput.warning(f"无法加载进度文件: {e}，从头开始")
        return Progress()

    def _save_progress(self):
        data = {
            "current_phase": self.progress.current_phase,
            "malfunction_completed": self.progress.malfunction_completed,
            "scen_hazevent_completed": self.progress.scen_hazevent_completed,
            "scoring_completed": self.progress.scoring_completed,
            "malfunction_json": self.progress.malfunction_json,
            "scen_hazevent_json": self.progress.scen_hazevent_json,
            "scoring_json": self.progress.scoring_json,
            "skipped_phases": self.progress.skipped_phases,
            "warnings": self.progress.warnings,
        }
        self.progress_file.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def _run_engine(
        self, script: Path, extra_args: list[str] = None, timeout: int = 300
    ) -> tuple[bool, Optional[str]]:
        args = [self.python_exe, str(script)]
        if extra_args:
            args.extend(extra_args)
        ColoredOutput.info(f"执行: {' '.join(args)}")
        try:
            result = subprocess.run(
                args, capture_output=True, text=True, timeout=timeout, encoding="utf-8"
            )
            if result.stdout:
                print(result.stdout)
            if result.stderr:
                print(result.stderr, file=sys.stderr)
            return result.returncode == 0, result.stdout + result.stderr
        except subprocess.TimeoutExpired:
            ColoredOutput.error(f"执行超时 ({timeout}s): {script.name}")
            return False, "执行超时"
        except Exception as e:
            ColoredOutput.error(f"执行失败: {e}")
            return False, str(e)

    def _run_checker(
        self, script: Path, input_json: str, extra_args: list[str] = None, timeout: int = 120
    ) -> tuple[bool, dict]:
        args = [
            self.python_exe,
            str(script),
            "--input",
            input_json,
        ]
        if extra_args:
            args.extend(extra_args)
        ColoredOutput.check(f"检查: {' '.join(args)}")
        try:
            result = subprocess.run(
                args, capture_output=True, text=True, timeout=timeout, encoding="utf-8"
            )
            if result.stdout:
                print(result.stdout)
            if result.stderr:
                print(result.stderr, file=sys.stderr)
            if result.returncode == 0:
                lines = result.stdout.strip().splitlines()
                for line in reversed(lines):
                    line = line.strip()
                    if line.startswith("{") and line.endswith("}"):
                        try:
                            data = json.loads(line)
                            return True, data
                        except json.JSONDecodeError:
                            pass
                return True, {"status": "pass"}
            else:
                lines = result.stdout.strip().splitlines()
                for line in reversed(lines):
                    line = line.strip()
                    if line.startswith("{"):
                        try:
                            data = json.loads(line)
                            return False, data
                        except json.JSONDecodeError:
                            pass
                return False, {"status": "fail", "message": result.stderr or "检查失败"}
        except subprocess.TimeoutExpired:
            ColoredOutput.error(f"检查超时 ({timeout}s): {script.name}")
            return False, {"status": "fail", "message": "检查超时"}
        except Exception as e:
            ColoredOutput.error(f"检查异常: {e}")
            return False, {"status": "fail", "message": str(e)}

    def _ask_retry(self, phase_name: str) -> bool:
        try:
            ColoredOutput.prompt(f"{phase_name} 检查未通过，是否重新执行? (Y/N): ")
            choice = input().strip().upper()
            return choice == "Y"
        except EOFError:
            return False

    def _show_check_result(self, result: dict):
        status = result.get("status", "unknown")
        if status == "pass":
            ColoredOutput.success("检查通过")
        else:
            msg = result.get("message", result.get("error", "未知错误"))
            errors = result.get("errors", [])
            warnings = result.get("warnings", [])
            if msg:
                ColoredOutput.error(f"检查失败: {msg}")
            if errors:
                ColoredOutput.error("错误详情:")
                for err in errors:
                    print(f"  - {err}")
            if warnings:
                ColoredOutput.warning("警告信息:")
                for w in warnings:
                    print(f"  - {w}")

    def run_malfunction_phase(self) -> bool:
        self.progress.current_phase = "malfunction"
        if self.progress.malfunction_completed and self.progress.malfunction_json:
            ColoredOutput.info(
                f"malfunction 阶段已完成，跳过 (json: {self.progress.malfunction_json})"
            )
            return True

        if self.malfunction_json_arg:
            supplied_json = Path(self.malfunction_json_arg)
            if not supplied_json.is_file():
                ColoredOutput.error(f"指定的 malfunction JSON 不存在: {supplied_json}")
                return False
            ColoredOutput.info(f"校验外部 malfunction 阶段结果: {supplied_json}")
            check_success, check_result = self._run_checker(
                self.CHECKER_MALFUNCTION, str(supplied_json)
            )
            self._show_check_result(check_result)
            if not check_success:
                self.progress.warnings.append(
                    {"phase": "malfunction", "reason": "supplied_json_quality_gate_blocked"}
                )
                self._save_progress()
                return False
            self.progress.malfunction_completed = True
            self.progress.malfunction_json = str(supplied_json.resolve())
            self._save_progress()
            return True

        output_json = self.progress.malfunction_json or str(self.default_malfunction_json)
        max_retries = 5
        attempt = 0

        while attempt < max_retries:
            attempt += 1
            if attempt > 1:
                ColoredOutput.phase(
                    f"malfunction 阶段重试 #{attempt} ({self.ENGINE_MALFUNCTION.name})"
                )
            else:
                ColoredOutput.phase(f"开始 malfunction 阶段 ({self.ENGINE_MALFUNCTION.name})")

            start_time = time.time()
            args = [
                "--item", self.item_path,
                "--template", self.template_path,
                "--output", output_json,
            ]
            success, output = self._run_engine(self.ENGINE_MALFUNCTION, args)
            elapsed = time.time() - start_time

            if not success:
                ColoredOutput.error(f"malfunction_engine 执行失败 (耗时 {elapsed:.1f}s)")
                if not self._ask_retry("malfunction_engine"):
                    self.progress.warnings.append(
                        {"phase": "malfunction", "reason": "engine_failed_quality_gate_blocked"}
                    )
                    self._save_progress()
                    return False
                continue

            if not output_json or not Path(output_json).exists():
                ColoredOutput.error(f"未找到 malfunction_output.json")
                if not self._ask_retry("malfunction_engine"):
                    self.progress.warnings.append(
                        {"phase": "malfunction", "reason": "output_missing_quality_gate_blocked"}
                    )
                    self._save_progress()
                    return False
                continue

            ColoredOutput.info(f"检查输出: {output_json}")
            check_success, check_result = self._run_checker(
                self.CHECKER_MALFUNCTION, output_json
            )
            self._show_check_result(check_result)

            if check_success:
                self.progress.malfunction_completed = True
                self.progress.malfunction_json = output_json
                self._save_progress()
                ColoredOutput.success(f"malfunction 阶段完成 (耗时 {elapsed:.1f}s)")
                return True

            ColoredOutput.warning(f"检查未通过，重新尝试")
            if not self._ask_retry("malfunction_engine"):
                self.progress.warnings.append(
                    {"phase": "malfunction", "reason": "checker_failed_quality_gate_blocked"}
                )
                self._save_progress()
                ColoredOutput.error("质量门阻断：malfunction Checker未通过，禁止进入下一阶段")
                return False

        ColoredOutput.error("malfunction 阶段重试次数已用尽")
        return False

    def run_scen_hazevent_phase(self) -> bool:
        self.progress.current_phase = "scen_hazevent"
        if not self.progress.malfunction_completed:
            ColoredOutput.error("malfunction 阶段未完成，无法执行 scen_hazevent")
            return False
        if self.progress.scen_hazevent_completed and self.progress.scen_hazevent_json:
            ColoredOutput.info(
                f"scen_hazevent 阶段已完成，跳过 (json: {self.progress.scen_hazevent_json})"
            )
            return True

        output_json = self.progress.scen_hazevent_json or str(self.default_scen_hazevent_json)
        max_retries = 5
        attempt = 0

        while attempt < max_retries:
            attempt += 1
            if attempt > 1:
                ColoredOutput.phase(
                    f"scen_hazevent 阶段重试 #{attempt} ({self.ENGINE_SCEN_HAZEVENT.name})"
                )
            else:
                ColoredOutput.phase(
                    f"开始 scen_hazevent 阶段 ({self.ENGINE_SCEN_HAZEVENT.name})"
                )

            start_time = time.time()
            args = [
                "--input", self.progress.malfunction_json,
                "--template", self.template_path,
                "--output", output_json,
            ]
            if self.domain_profile_path:
                args.extend(["--domain-profile", self.domain_profile_path])
            success, output = self._run_engine(self.ENGINE_SCEN_HAZEVENT, args)
            elapsed = time.time() - start_time

            if not success:
                ColoredOutput.error(f"scen_hazevent_engine 执行失败 (耗时 {elapsed:.1f}s)")
                if not self._ask_retry("scen_hazevent_engine"):
                    self.progress.warnings.append(
                        {"phase": "scen_hazevent", "reason": "engine_failed_quality_gate_blocked"}
                    )
                    self._save_progress()
                    return False
                continue

            if not output_json or not Path(output_json).exists():
                ColoredOutput.error("未找到 scen_hazevent_output.json")
                if not self._ask_retry("scen_hazevent_engine"):
                    self.progress.warnings.append(
                        {"phase": "scen_hazevent", "reason": "output_missing_quality_gate_blocked"}
                    )
                    self._save_progress()
                    return False
                continue

            ColoredOutput.info(f"检查输出: {output_json}")
            check_extra = ["--template", self.template_path] if self.template_path else []
            if self.allow_draft:
                check_extra.append("--allow-draft")
            check_success, check_result = self._run_checker(
                self.CHECKER_SCEN, output_json, extra_args=check_extra
            )
            self._show_check_result(check_result)

            if check_success:
                self.progress.scen_hazevent_completed = True
                self.progress.scen_hazevent_json = output_json
                self._save_progress()
                ColoredOutput.success(f"scen_hazevent 阶段完成 (耗时 {elapsed:.1f}s)")
                return True

            ColoredOutput.error("质量门阻断：scen_hazevent Checker未通过，禁止进入下一阶段")
            self.progress.warnings.append(
                {"phase": "scen_hazevent", "reason": "checker_failed_quality_gate_blocked"}
            )
            self._save_progress()
            return False

        ColoredOutput.error("scen_hazevent 阶段重试次数已用尽")
        return False

    def run_scoring_phase(self) -> bool:
        self.progress.current_phase = "scoring"
        if not self.progress.scen_hazevent_completed:
            ColoredOutput.error("scen_hazevent 阶段未完成，无法执行 scoring")
            return False
        if self.progress.scoring_completed and self.progress.scoring_json:
            ColoredOutput.info(
                f"scoring 阶段已完成，跳过 (json: {self.progress.scoring_json})"
            )
            return True

        output_json = self.progress.scoring_json or str(self.default_scoring_json)
        max_retries = 5
        attempt = 0

        while attempt < max_retries:
            attempt += 1
            if attempt > 1:
                ColoredOutput.phase(
                    f"scoring 阶段重试 #{attempt} ({self.ENGINE_SCORING.name})"
                )
            else:
                ColoredOutput.phase(f"开始 scoring 阶段 ({self.ENGINE_SCORING.name})")

            start_time = time.time()
            args = [
                "--malfunction",
                self.progress.malfunction_json,
                "--scen-hazevent",
                self.progress.scen_hazevent_json,
                "--template",
                self.template_path,
                "--output",
                output_json,
            ]
            if self.domain_profile_path:
                args.extend(["--domain-profile", self.domain_profile_path])
            success, output = self._run_engine(self.ENGINE_SCORING, args)
            elapsed = time.time() - start_time

            if not success:
                ColoredOutput.error(f"scoring_sg_engine 执行失败 (耗时 {elapsed:.1f}s)")
                if not self._ask_retry("scoring_sg_engine"):
                    self.progress.warnings.append(
                        {"phase": "scoring", "reason": "engine_failed_quality_gate_blocked"}
                    )
                    self._save_progress()
                    return False
                continue

            if not output_json or not Path(output_json).exists():
                ColoredOutput.error("未找到 scoring_sg_output.json")
                if not self._ask_retry("scoring_sg_engine"):
                    self.progress.warnings.append(
                        {"phase": "scoring", "reason": "output_missing_quality_gate_blocked"}
                    )
                    self._save_progress()
                    return False
                continue

            ColoredOutput.info(f"检查输出: {output_json}")
            check_extra = ["--template", self.template_path] if self.template_path else []
            if self.allow_draft:
                check_extra.append("--allow-draft")
            check_success, check_result = self._run_checker(
                self.CHECKER_SCORING, output_json, extra_args=check_extra
            )
            self._show_check_result(check_result)

            if check_success:
                self.progress.scoring_completed = True
                self.progress.scoring_json = output_json
                self._save_progress()
                ColoredOutput.success(f"scoring 阶段完成 (耗时 {elapsed:.1f}s)")
                return True

            ColoredOutput.error("质量门阻断：scoring Checker未通过，禁止生成报告")
            self.progress.warnings.append(
                {"phase": "scoring", "reason": "checker_failed_quality_gate_blocked"}
            )
            self._save_progress()
            return False

        ColoredOutput.error("scoring 阶段重试次数已用尽")
        return False

    def generate_report(self) -> bool:
        self.progress.current_phase = "report"
        ColoredOutput.phase(f"生成报告阶段 ({self.ENGINE_REPORT.name})")

        start_time = time.time()
        args = [
            "--output",
            self.output_path,
            "--malfunction-json",
            self.progress.malfunction_json or "",
            "--scen-hazevent-json",
            self.progress.scen_hazevent_json or "",
            "--scoring-json",
            self.progress.scoring_json or "",
        ]
        if self.template_path:
            args.extend(["--template", self.template_path])
        if self.allow_draft:
            args.append("--allow-draft")

        success, output = self._run_engine(self.ENGINE_REPORT, args)
        elapsed = time.time() - start_time

        if success:
            ColoredOutput.success(f"报告生成成功 (耗时 {elapsed:.1f}s): {self.output_path}")
            self._print_summary()
            if self.progress_file.exists():
                self.progress_file.unlink()
            return True
        else:
            ColoredOutput.error(f"报告生成失败 (耗时 {elapsed:.1f}s)")
            return False

    def _print_summary(self):
        print()
        print(f"{ColoredOutput.BOLD}{'='*60}{ColoredOutput.RESET}")
        print(f"{ColoredOutput.BOLD}  执行摘要{ColoredOutput.RESET}")
        print(f"{ColoredOutput.BOLD}{'='*60}{ColoredOutput.RESET}")
        print(f"  malfunction:     {'✓' if self.progress.malfunction_completed else '✗'} "
              f"({self.progress.malfunction_json or 'N/A'})")
        print(f"  scen_hazevent:   {'✓' if self.progress.scen_hazevent_completed else '✗'} "
              f"({self.progress.scen_hazevent_json or 'N/A'})")
        print(f"  scoring:         {'✓' if self.progress.scoring_completed else '✗'} "
              f"({self.progress.scoring_json or 'N/A'})")
        if self.progress.skipped_phases:
            print(f"  跳过阶段:        {', '.join(self.progress.skipped_phases)}")
        if self.progress.warnings:
            print(f"  警告数量:        {len(self.progress.warnings)}")
        print(f"  输出报告:        {self.output_path}")
        print(f"{ColoredOutput.BOLD}{'='*60}{ColoredOutput.RESET}")

    def run(self) -> bool:
        if self.run_config:
            try:
                self.run_config.validate()
            except (FileNotFoundError, ValueError) as exc:
                ColoredOutput.error(str(exc))
                return False
        print()
        print(f"{ColoredOutput.BOLD}{'#'*60}{ColoredOutput.RESET}")
        print(f"{ColoredOutput.BOLD}#  HARA 自动填充控制器{ColoredOutput.RESET}")
        print(f"{ColoredOutput.BOLD}#  Item: {self.item_path}{ColoredOutput.RESET}")
        print(f"{ColoredOutput.BOLD}#  Template: {self.template_path}{ColoredOutput.RESET}")
        print(f"{ColoredOutput.BOLD}#  Output: {self.output_path}{ColoredOutput.RESET}")
        print(f"{ColoredOutput.BOLD}{'#'*60}{ColoredOutput.RESET}")
        print()

        overall_start = time.time()

        if not self.run_malfunction_phase():
            return False

        if not self.run_scen_hazevent_phase():
            return False

        if not self.run_scoring_phase():
            return False

        if not self.generate_report():
            return False

        total_time = time.time() - overall_start
        print()
        ColoredOutput.success(f"全部流程执行完成! 总耗时: {total_time:.1f}s")
        return True

    @staticmethod
    def parse_args() -> "HARAController":
        parser = argparse.ArgumentParser(
            description="HARA 自动填充主控制器",
            formatter_class=argparse.RawDescriptionHelpFormatter,
        )
        parser.add_argument(
            "--item", required=True, help="Item定义文件路径 (xlsx/docx)"
        )
        parser.add_argument(
            "--template", required=True, help="报告模板文件路径 (xlsx)"
        )
        parser.add_argument(
            "--output", required=True, help="输出报告文件路径 (xlsx)"
        )
        parser.add_argument(
            "--malfunction-json",
            help="malfunction阶段输出JSON路径 (可选)",
        )
        parser.add_argument(
            "--resume",
            action="store_true",
            help="从断点恢复执行",
        )
        parser.add_argument(
            "--allow-draft", action="store_true",
            help="允许输出带水印的Draft/Diagnostic报告；正式模式仍为默认值",
        )
        parser.add_argument("--domain", help="显式Domain名称，例如avp")
        parser.add_argument("--domain-profile", help="显式Domain Profile JSON路径")
        return HARAController(parser.parse_args())


def main():
    controller = HARAController.parse_args()
    try:
        success = controller.run()
    except KeyboardInterrupt:
        ColoredOutput.warning("用户中断执行")
        controller._save_progress()
        sys.exit(130)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
