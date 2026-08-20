#!/usr/bin/env python3
"""
HARA主执行器 V12
功能：智能路由HARA分析流程
1. 检查经验库 → 快速导出
2. 经验库无匹配 → 拆分引擎分析
3. 拆分引擎卡住 → Fallback到原始引擎
"""

import os
import sys
import json
import time
import subprocess
import re
import shutil
import difflib
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Any
from datetime import datetime
import argparse
import logging

script_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(script_dir))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


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


@dataclass
class ExecutorProgress:
    current_phase: str = "init"
    start_time: float = field(default_factory=time.time)
    experience_matched: bool = False
    experience_json_path: Optional[str] = None
    malfunction_completed: bool = False
    scen_hazevent_completed: bool = False
    scoring_completed: bool = False
    malfunction_json: Optional[str] = None
    scen_hazevent_json: Optional[str] = None
    scoring_json: Optional[str] = None
    fallback_triggered: bool = False
    fallback_reason: Optional[str] = None
    warnings: List[Dict] = field(default_factory=list)
    errors: List[Dict] = field(default_factory=list)
    user_input: Optional[str] = None
    subsystem: Optional[str] = None
    function: Optional[str] = None
    item_path: Optional[str] = None
    template_path: Optional[str] = None
    output_path: Optional[str] = None


class FallbackMonitor:
    def __init__(self, max_check_attempts: int = 50, max_time_seconds: int = 900):
        self.check_attempts: Dict[str, int] = {}
        self.phase_start_time: Dict[str, float] = {}
        self.current_problem: Optional[str] = None
        self.max_check_attempts = max_check_attempts
        self.max_time_seconds = max_time_seconds

    def record_check(self, phase: str, problem: str):
        if self.current_problem != problem:
            self.check_attempts[phase] = 0
            self.phase_start_time[phase] = time.time()
            self.current_problem = problem
        else:
            self.check_attempts[phase] = self.check_attempts.get(phase, 0) + 1

    def should_fallback(self, phase: str, problem: str) -> bool:
        if self.check_attempts.get(phase, 0) > self.max_check_attempts:
            ColoredOutput.warning(
                f"Fallback触发: {phase} 检查次数超过{self.max_check_attempts}次"
            )
            return True
        if phase in self.phase_start_time:
            elapsed = time.time() - self.phase_start_time[phase]
            if elapsed > self.max_time_seconds:
                ColoredOutput.warning(
                    f"Fallback触发: {phase} 耗时超过{self.max_time_seconds}秒 ({elapsed:.0f}s)"
                )
                return True
        return False

    def get_status(self) -> Dict[str, Any]:
        status = {
            "check_attempts": dict(self.check_attempts),
            "phase_durations": {}
        }
        for phase, start_time in self.phase_start_time.items():
            status["phase_durations"][phase] = time.time() - start_time
        return status


class HARAExperienceExporter:
    def __init__(self, library_dir: str = None):
        if library_dir is None:
            from experience_library import _get_default_library_dir
            library_dir = _get_default_library_dir()
        self.library_dir = library_dir
        self.library = None

    def initialize(self):
        from experience_library import HARAExperienceLibrary

        self.library = HARAExperienceLibrary(self.library_dir)
        if not self.library.initialize():
            raise RuntimeError(f"经验库初始化失败: {self.library_dir}")
        return True

    def match_and_export(self, user_input: str, output_dir: str = None) -> Dict[str, Any]:
        if not self.library:
            self.initialize()

        match_result = self.library.match_input(user_input)
        if not match_result.get("success"):
            return {
                "success": False,
                "matched": False,
                "error": match_result.get("error"),
                "hint": match_result.get("hint")
            }

        subsystem = None
        results = match_result.get("results", {})
        if results:
            first_key = list(results.keys())[0]
            subsystem = first_key

        # Keep only the latest exported experience data.  A stable filename
        # makes each run atomically replace the previous result instead of
        # accumulating timestamped artifacts in output/.
        filename = "experience_library_latest.json"

        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
            json_path = os.path.join(output_dir, filename)
        else:
            json_path = os.path.join(script_dir, filename)

        export_data = self._build_export_data(match_result, subsystem)

        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(export_data, f, ensure_ascii=False, indent=2)

        return {
            "success": True,
            "matched": True,
            "subsystem": subsystem,
            "json_path": json_path,
            "record_count": export_data.get("record_count", 0),
            "hara_data": export_data.get("hara_data", [])
        }

    def _build_export_data(self, match_result: Dict, subsystem: str) -> Dict[str, Any]:
        results = match_result.get("results", {})
        all_hara_data = []

        for key, info in results.items():
            if "full_hara_data" in info:
                all_hara_data.extend(info["full_hara_data"])
            elif "hara_data" in info:
                all_hara_data.extend(info["hara_data"])

        unique_sgs = {}
        for rec in all_hara_data:
            sg = rec.get("Safety Goal", "")
            sg_id = rec.get("SG-ID", "")
            asil = rec.get("ASIL", "")
            if sg_id and sg and sg not in [v.get("Safety Goal") for v in unique_sgs.values()]:
                unique_sgs[sg_id] = {"SG-ID": sg_id, "Safety Goal": sg, "Max.ASIL": asil}

        return {
            "subsystem": subsystem,
            "timestamp": datetime.now().isoformat(),
            "record_count": len(all_hara_data),
            "05_HARA": all_hara_data,
            "06_Safety Goal": list(unique_sgs.values()),
            "source": "HARA经验库",
            "exported_by": "main_executor.py V12"
        }


class MainExecutor:
    SCRIPTS_DIR = script_dir
    PROJECT_ROOT = SCRIPTS_DIR.parent
    RUN_DIR = PROJECT_ROOT / "runtime" / "current"
    PROGRESS_FILE = RUN_DIR / "main_executor_progress.json"
    HARA_CONTROLLER = SCRIPTS_DIR / "hara_controller.py"
    HARA_ENGINE = SCRIPTS_DIR / "hara_engine.py"
    REPORT_GENERATOR = SCRIPTS_DIR / "report_generator.py"

    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.RUN_DIR.mkdir(parents=True, exist_ok=True)
        self.progress = self._load_progress()
        self.fallback_monitor = FallbackMonitor()
        self.python_exe = sys.executable

    def _load_progress(self) -> ExecutorProgress:
        if getattr(self.args, 'resume', False) and self.PROGRESS_FILE.exists():
            try:
                data = json.loads(self.PROGRESS_FILE.read_text(encoding="utf-8"))
                p = ExecutorProgress(**data)
                p.start_time = time.time()
                ColoredOutput.info(f"已从断点恢复: {p.current_phase}")
                return p
            except Exception as e:
                ColoredOutput.warning(f"无法加载进度文件: {e}，从头开始")
        return ExecutorProgress()

    def _save_progress(self):
        data = {
            "current_phase": self.progress.current_phase,
            "experience_matched": self.progress.experience_matched,
            "experience_json_path": self.progress.experience_json_path,
            "malfunction_completed": self.progress.malfunction_completed,
            "scen_hazevent_completed": self.progress.scen_hazevent_completed,
            "scoring_completed": self.progress.scoring_completed,
            "malfunction_json": self.progress.malfunction_json,
            "scen_hazevent_json": self.progress.scen_hazevent_json,
            "scoring_json": self.progress.scoring_json,
            "fallback_triggered": self.progress.fallback_triggered,
            "fallback_reason": self.progress.fallback_reason,
            "warnings": self.progress.warnings,
            "errors": self.progress.errors,
            "user_input": self.progress.user_input,
            "subsystem": self.progress.subsystem,
            "function": self.progress.function,
            "item_path": self.progress.item_path,
            "template_path": self.progress.template_path,
            "output_path": self.progress.output_path,
        }
        self.PROGRESS_FILE.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )

    def _clear_progress(self):
        if self.PROGRESS_FILE.exists():
            self.PROGRESS_FILE.unlink()

    def _update_ref_from_experience(self, json_path: str) -> bool:
        """从经验库JSON更新ScenarioRef和HazardRef参考文件

        当经验库分析成功时，提取场景组合和危害并追加到参考文件。

        Args:
            json_path: 经验库JSON文件路径

        Returns:
            是否更新成功
        """
        if not json_path or not os.path.exists(json_path):
            return False

        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                exp_data = json.load(f)

            subsystem = self.progress.subsystem or exp_data.get("subsystem", "unknown")
            hara_rows = exp_data.get("05_HARA", [])

            if not hara_rows:
                return False

            subsystem_slug = subsystem.lower()

            scenario_ref_path = os.path.join(script_dir, "ScenarioRef.json")
            try:
                if os.path.exists(scenario_ref_path):
                    with open(scenario_ref_path, 'r', encoding='utf-8') as f:
                        scenario_ref = json.load(f)
                else:
                    scenario_ref = {"version": "1.0", "description": "HARA场景组合参考库", "entries": [], "main_scenarios": []}
            except Exception:
                scenario_ref = {"version": "1.0", "description": "HARA场景组合参考库", "entries": [], "main_scenarios": []}

            collected_scenarios = []
            for row in hara_rows:
                sit_desc = str(row.get("Situational description", ""))
                sit_det = str(row.get("Situational detailing", ""))
                func = str(row.get("Function", ""))
                hazard = str(row.get("Hazard", ""))
                malfunction = str(row.get("Malfunction", ""))

                func_keywords = self._extract_keywords(func)
                hazard_keywords = self._extract_keywords(hazard)
                malf_keywords = self._extract_keywords(malfunction)

                all_scenarios_text = f"{sit_desc} {sit_det}".strip()

                if all_scenarios_text and len(all_scenarios_text) > 10:
                    existing_ids = [e.get("id", "") for e in scenario_ref.get("entries", [])]
                    entry_id = f"SCN_{subsystem_slug}_{len(existing_ids) + 1:03d}"

                    scene_list = []
                    for line in all_scenarios_text.split('\n'):
                        line = line.strip()
                        if line:
                            scene_list.append({"scenario": line, "e_value": ""})

                    new_entry = {
                        "id": entry_id,
                        "subsystem_keywords": [subsystem] + func_keywords[:3],
                        "function_keywords": func_keywords[:5],
                        "hazard_keywords": hazard_keywords[:5],
                        "malfunction_keywords": malf_keywords[:3],
                        "required_scenarios": scene_list[:5],
                        "notes": f"From experience library: {subsystem}",
                        "_source": "experience",
                        "_timestamp": datetime.now().isoformat()
                    }

                    is_duplicate = False
                    for existing in scenario_ref.get("entries", []):
                        existing_keywords = " ".join(existing.get("function_keywords", []) + existing.get("hazard_keywords", []))
                        new_keywords = " ".join(new_entry.get("function_keywords", []) + new_entry.get("hazard_keywords", []))
                        if difflib.SequenceMatcher(None, existing_keywords, new_keywords).ratio() > 0.7:
                            is_duplicate = True
                            break

                    if not is_duplicate:
                        scenario_ref["entries"].append(new_entry)

            scenario_ref["updated"] = datetime.now().strftime("%Y-%m-%d")

            with open(scenario_ref_path, 'w', encoding='utf-8') as f:
                json.dump(scenario_ref, f, indent=2, ensure_ascii=False)

            ColoredOutput.info(f"已更新 ScenarioRef: {len(scenario_ref.get('entries', []))} 条场景")

            hazard_ref_path = os.path.join(script_dir, "HazardRef.json")
            try:
                if os.path.exists(hazard_ref_path):
                    with open(hazard_ref_path, 'r', encoding='utf-8') as f:
                        hazard_ref = json.load(f)
                else:
                    hazard_ref = {"version": "1.0", "description": "HARA危害参考库", "entries": [], "severity_mapping": {}}
            except Exception:
                hazard_ref = {"version": "1.0", "description": "HARA危害参考库", "entries": [], "severity_mapping": {}}

            existing_hz_ids = [e.get("id", "") for e in hazard_ref.get("entries", [])]

            for row in hara_rows:
                func = str(row.get("Function", ""))
                malfunction = str(row.get("Malfunction", ""))
                hazard = str(row.get("Hazard", ""))
                asil = str(row.get("ASIL", "QM"))

                if not hazard or len(hazard) < 5:
                    continue

                func_keywords = self._extract_keywords(func)
                malf_keywords = self._extract_keywords(malfunction)
                hazard_keywords = self._extract_keywords(hazard)

                is_duplicate = False
                for existing in hazard_ref.get("entries", []):
                    existing_hazards = existing.get("hazards", [])
                    for hz in existing_hazards:
                        existing_desc = hz.get("description", "")
                        if difflib.SequenceMatcher(None, existing_desc, hazard).ratio() > 0.6:
                            is_duplicate = True
                            break
                    if is_duplicate:
                        break

                if is_duplicate:
                    continue

                existing_count = len([i for i in existing_hz_ids if subsystem_slug in i])
                entry_id = f"HZ_{subsystem_slug.upper()}_{existing_count + 1:03d}"

                severity = "S1"
                if asil == "ASIL A" or asil == "ASIL B":
                    severity = "S2"
                elif asil == "ASIL C" or asil == "ASIL D":
                    severity = "S3"

                new_hz_entry = {
                    "id": entry_id,
                    "subsystem_keywords": [subsystem] + func_keywords[:2],
                    "function_keywords": func_keywords[:5],
                    "malfunction_keywords": malf_keywords[:3],
                    "context_keywords": hazard_keywords[:5],
                    "hazards": [{
                        "type": hazard_keywords[0] if hazard_keywords else "injury",
                        "severity": severity,
                        "description": hazard[:200]
                    }],
                    "reference_hara": row.get("HARA-ID", ""),
                    "_source": "experience",
                    "_timestamp": datetime.now().isoformat()
                }

                hazard_ref["entries"].append(new_hz_entry)
                existing_hz_ids.append(entry_id)

            hazard_ref["updated"] = datetime.now().strftime("%Y-%m-%d")

            with open(hazard_ref_path, 'w', encoding='utf-8') as f:
                json.dump(hazard_ref, f, indent=2, ensure_ascii=False)

            ColoredOutput.info(f"已更新 HazardRef: {len(hazard_ref.get('entries', []))} 条危害")
            return True

        except Exception as e:
            ColoredOutput.warning(f"更新参考文件失败: {e}")
            return False

    def _extract_keywords(self, text: str) -> List[str]:
        """从文本中提取关键词（英文+中文）

        Args:
            text: 输入文本

        Returns:
            关键词列表
        """
        if not text:
            return []

        cn_keywords = re.findall(r'[\u4e00-\u9fff]{2,4}', text)

        en_keywords = re.findall(r'[a-zA-Z]{3,}', text)

        stop_words = {"the", "and", "for", "are", "but", "not", "you", "all", "can", "has", "her", "was", "one", "our", "out", "this", "that", "with", "from", "they", "been", "have", "were", "will", "would", "could", "should", "when", "what", "where", "which", "their", "there", "into", "than", "then", "some", "more", "other", "such", "only", "also", "very", "just", "about", "over", "both", "each", "most", "same", "any", "after", "before"}
        en_keywords = [w for w in en_keywords if w.lower() not in stop_words]

        all_kw = list(dict.fromkeys(cn_keywords + en_keywords))
        return all_kw[:10]

    def parse_user_input(self) -> Dict[str, Any]:
        self.progress.current_phase = "parse_input"

        user_input = self.args.user_input if hasattr(self.args, 'user_input') else ""

        subsystem = self.args.system if hasattr(self.args, 'system') and self.args.system else None
        function = self.args.function if hasattr(self.args, 'function') and self.args.function else None
        item_path = self.args.item if hasattr(self.args, 'item') else None
        template_path = self.args.template if hasattr(self.args, 'template') else None

        if not user_input and subsystem:
            user_input = subsystem
        elif not user_input and function:
            user_input = function

        subsystem_keywords = {
            "light": ["灯光", "照明", "车灯", "light", "lamp", "headlamp", "尾灯"],
            "door": ["车门", "门", "door", "liftgate"],
            "window": ["车窗", "窗", "window", "玻璃"],
            "seat": ["座椅", "座位", "seat"],
            "wiper": ["雨刷", "刮水器", "wiper", "雨刮"],
            "hud": ["HUD", "抬头显示", "head-up display"]
        }

        if not subsystem and user_input:
            user_lower = user_input.lower()
            for subsys_key, keywords in subsystem_keywords.items():
                for kw in keywords:
                    if kw in user_lower:
                        subsystem = subsys_key
                        break
                if subsystem:
                    break

        self.progress.user_input = user_input
        self.progress.subsystem = subsystem
        self.progress.function = function
        self.progress.item_path = item_path
        self.progress.template_path = template_path

        return {
            "success": True,
            "user_input": user_input,
            "subsystem": subsystem,
            "function": function,
            "item_path": item_path,
            "template_path": template_path
        }

    def check_experience_library(self) -> Dict[str, Any]:
        self.progress.current_phase = "check_experience"

        user_input = self.progress.user_input
        if not user_input and not self.progress.subsystem:
            return {
                "success": False,
                "matched": False,
                "error": "无用户输入且未指定子系统"
            }

        output_dir = os.path.dirname(self.args.output) if hasattr(self.args, 'output') and self.args.output else None
        if output_dir is None:
            output_dir = str(script_dir / "output")

        try:
            exporter = HARAExperienceExporter()
            result = exporter.match_and_export(user_input, output_dir)

            if result.get("matched"):
                self.progress.experience_matched = True
                self.progress.experience_json_path = result.get("json_path")
                self._save_progress()
                ColoredOutput.success(
                    f"经验库匹配成功: {result.get('subsystem')}, "
                    f"记录数: {result.get('record_count')}"
                )
                return result
            else:
                ColoredOutput.info("经验库未匹配，将使用HARA引擎分析")
                return result

        except Exception as e:
            ColoredOutput.warning(f"经验库检查异常: {e}")
            self.progress.errors.append({
                "phase": "check_experience",
                "error": str(e),
                "timestamp": datetime.now().isoformat()
            })
            return {"success": False, "matched": False, "error": str(e)}

    def export_report_from_experience(self) -> bool:
        self.progress.current_phase = "export_report"

        if not self.progress.experience_json_path:
            return False

        json_path = self.progress.experience_json_path
        template_path = self.progress.template_path
        output_path = self.args.output if hasattr(self.args, 'output') and self.args.output else None

        if not output_path:
            output_path = str(self.PROJECT_ROOT / "output" / "HARA_Report_Output.xlsx")

        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        ColoredOutput.phase(f"从经验库导出报告: {output_path}")

        try:
            report_args = [
                self.python_exe,
                str(self.REPORT_GENERATOR),
                "--experience-json", json_path,
                "--template", template_path,
                "--output", output_path,
            ]
            if getattr(self.args, "allow_draft", False):
                report_args.append("--allow-draft")
            result = subprocess.run(
                report_args,
                capture_output=True,
                text=True,
                timeout=300,
                encoding="utf-8"
            )

            if result.stdout:
                print(result.stdout)
            if result.stderr:
                print(result.stderr, file=sys.stderr)

            if result.returncode == 0:
                self.progress.output_path = output_path
                self._update_ref_from_experience(json_path)
                self._clear_progress()
                ColoredOutput.success(f"报告导出成功: {output_path}")
                return True
            else:
                ColoredOutput.error(f"报告导出失败: returncode={result.returncode}")
                return False

        except subprocess.TimeoutExpired:
            ColoredOutput.error("报告生成超时 (300s)")
            return False
        except Exception as e:
            ColoredOutput.error(f"报告生成异常: {e}")
            return False

    def run_hara_controller(self) -> bool:
        self.progress.current_phase = "run_controller"

        item_path = self.progress.item_path
        template_path = self.progress.template_path
        output_path = self.args.output if hasattr(self.args, 'output') and self.args.output else None

        if not output_path:
            output_path = str(self.PROJECT_ROOT / "output" / "HARA_Report_Output.xlsx")

        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        ColoredOutput.phase(f"启动 hara_controller.py")

        max_retries = 5
        attempt = 0

        while attempt < max_retries:
            attempt += 1
            if attempt > 1:
                ColoredOutput.phase(f"重试 hara_controller.py #{attempt}")

            start_time = time.time()
            problem_desc = f"controller_attempt_{attempt}"

            self.fallback_monitor.record_check("controller", problem_desc)

            if self.fallback_monitor.should_fallback("controller", problem_desc):
                self.progress.fallback_triggered = True
                self.progress.fallback_reason = "controller超过重试限制"
                self._save_progress()
                ColoredOutput.warning("触发Fallback机制，切换到 hara_engine.py")
                return self.run_fallback_engine()

            try:
                controller_args = [
                    self.python_exe,
                    str(self.HARA_CONTROLLER),
                    "--item", item_path,
                    "--template", template_path,
                    "--output", output_path,
                ]
                if getattr(self.args, "resume", False):
                    controller_args.append("--resume")
                if getattr(self.args, "allow_draft", False):
                    controller_args.append("--allow-draft")
                result = subprocess.run(
                    controller_args,
                    capture_output=True,
                    text=True,
                    timeout=600,
                    encoding="utf-8"
                )

                elapsed = time.time() - start_time

                if result.stdout:
                    print(result.stdout)
                if result.stderr:
                    print(result.stderr, file=sys.stderr)

                if result.returncode == 0:
                    self.progress.output_path = output_path
                    self.progress.malfunction_completed = True
                    self.progress.scen_hazevent_completed = True
                    self.progress.scoring_completed = True
                    self._clear_progress()
                    ColoredOutput.success(f"hara_controller执行成功 (耗时 {elapsed:.1f}s)")
                    return True
                else:
                    ColoredOutput.warning(f"hara_controller执行失败 (耗时 {elapsed:.1f}s)")
                    if attempt >= max_retries:
                        self.progress.fallback_triggered = True
                        self.progress.fallback_reason = f"controller重试{attempt}次失败"
                        self._save_progress()
                        return self.run_fallback_engine()

            except subprocess.TimeoutExpired:
                ColoredOutput.error(f"hara_controller执行超时 (600s)")
                if attempt >= max_retries:
                    self.progress.fallback_triggered = True
                    self.progress.fallback_reason = "controller执行超时"
                    self._save_progress()
                    return self.run_fallback_engine()
            except Exception as e:
                ColoredOutput.error(f"hara_controller执行异常: {e}")
                if attempt >= max_retries:
                    self.progress.fallback_triggered = True
                    self.progress.fallback_reason = f"controller异常: {e}"
                    self._save_progress()
                    return self.run_fallback_engine()

        return False

    def run_fallback_engine(self) -> bool:
        self.progress.current_phase = "fallback_engine"
        ColoredOutput.phase("执行 Fallback: hara_engine.py (完整17步)")

        item_path = self.progress.item_path
        template_path = self.progress.template_path
        output_path = self.args.output if hasattr(self.args, 'output') and self.args.output else None

        if not output_path:
            output_path = str(script_dir / "output" / "HARA_Report_Output.xlsx")

        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        try:
            from hara_engine import HARAEngine

            engine = HARAEngine()
            engine.initialize(
                item_definition_path=item_path,
                excel_template_path=template_path,
                user_input=self.progress.user_input
            )

            result = engine.execute_all_steps(1, 17)
            engine.export_results(str(self.RUN_DIR))

            report_file = output_path
            try:
                from report_generator import HARAReportGenerator
                generator = HARAReportGenerator(
                    template_path=template_path,
                    allow_draft=getattr(self.args, "allow_draft", False),
                )
                report_result = generator.generate_hara_report(
                    engine.context,
                    template_excel_path=template_path,
                    output_excel_path=report_file
                )
                if report_result.get("success"):
                    ColoredOutput.success(f"Fallback报告生成成功: {report_file}")
                    self.progress.output_path = report_file
                    self._clear_progress()
                    return True
                ColoredOutput.error(
                    f"Fallback质量门阻断报告: {report_result.get('error', 'unknown error')}"
                )
                self.progress.errors.append({
                    "phase": "fallback_report_quality_gate",
                    "error": report_result.get("error", "quality_gate_blocked"),
                    "quality_gate": report_result.get("quality_gate", {}),
                    "timestamp": datetime.now().isoformat(),
                })
                self._save_progress()
                return False
            except Exception as e:
                ColoredOutput.error(f"Fallback报告生成失败: {e}")
                self.progress.errors.append({
                    "phase": "fallback_report",
                    "error": str(e),
                    "timestamp": datetime.now().isoformat(),
                })
                self._save_progress()
                return False

        except Exception as e:
            ColoredOutput.error(f"Fallback引擎执行失败: {e}")
            self.progress.errors.append({
                "phase": "fallback_engine",
                "error": str(e),
                "timestamp": datetime.now().isoformat()
            })
            self._save_progress()
            return False

    def run_info(self):
        ColoredOutput.phase("查看经验库索引信息")

        try:
            from experience_library import HARAExperienceLibrary

            library = HARAExperienceLibrary()
            if not library.initialize():
                ColoredOutput.error("经验库初始化失败")
                return False

            summary = library.get_index_summary()

            print()
            print(f"{ColoredOutput.BOLD}{'='*60}{ColoredOutput.RESET}")
            print(f"{ColoredOutput.BOLD}  HARA经验库{ColoredOutput.RESET}")
            print(f"{ColoredOutput.BOLD}{'='*60}{ColoredOutput.RESET}")
            print(f"  目录: {summary.get('library_dir')}")
            print(f"  文件: {summary.get('total_files')}")
            print(f"  子系统: {summary.get('total_subsystems')}")
            print(f"  功能: {summary.get('total_functions')}")
            print()
            print(f"  子系统列表:")
            for subsys in summary.get("subsystems", []):
                func_count = summary.get("function_count_by_subsystem", {}).get(subsys, 0)
                print(f"    - {subsys}: {func_count} 个功能")
            print(f"{ColoredOutput.BOLD}{'='*60}{ColoredOutput.RESET}")

            return True

        except Exception as e:
            ColoredOutput.error(f"获取经验库信息失败: {e}")
            return False

    def run_analyze(self) -> bool:
        overall_start = time.time()

        self.parse_user_input()
        if not self.progress.item_path or not os.path.isfile(self.progress.item_path):
            ColoredOutput.error(f"Item Definition文档不存在: {self.progress.item_path}")
            return False
        if not self.progress.template_path or not os.path.isfile(self.progress.template_path):
            ColoredOutput.error(f"HARA模板不存在: {self.progress.template_path}")
            return False

        print()
        print(f"{ColoredOutput.BOLD}{'#'*60}{ColoredOutput.RESET}")
        print(f"{ColoredOutput.BOLD}#  HARA主执行器 V12{ColoredOutput.RESET}")
        print(f"{ColoredOutput.BOLD}#  Item: {self.progress.item_path}{ColoredOutput.RESET}")
        print(f"{ColoredOutput.BOLD}#  Template: {self.progress.template_path}{ColoredOutput.RESET}")
        if self.progress.subsystem:
            print(f"{ColoredOutput.BOLD}#  Subsystem: {self.progress.subsystem}{ColoredOutput.RESET}")
        if self.progress.function:
            print(f"{ColoredOutput.BOLD}#  Function: {self.progress.function}{ColoredOutput.RESET}")
        print(f"{ColoredOutput.BOLD}{'#'*60}{ColoredOutput.RESET}")
        print()

        self._save_progress()

        exp_result = self.check_experience_library()

        if exp_result.get("matched"):
            success = self.export_report_from_experience()
        else:
            success = self.run_hara_controller()

        total_time = time.time() - overall_start
        print()
        if success:
            ColoredOutput.success(f"全部流程执行完成! 总耗时: {total_time:.1f}s")
            if self.progress.output_path:
                print(f"  输出路径: {self.progress.output_path}")
        else:
            ColoredOutput.error(f"执行失败 (耗时: {total_time:.1f}s)")

        return success

    def run_fallback(self) -> bool:
        overall_start = time.time()

        # The fallback subcommand bypasses run_analyze(), so it must populate
        # and validate the execution context itself before starting the engine.
        self.parse_user_input()
        if not self.progress.item_path or not os.path.isfile(self.progress.item_path):
            ColoredOutput.error(f"Item Definition文档不存在: {self.progress.item_path}")
            return False
        if not self.progress.template_path or not os.path.isfile(self.progress.template_path):
            ColoredOutput.error(f"HARA模板不存在: {self.progress.template_path}")
            return False

        print()
        print(f"{ColoredOutput.BOLD}{'#'*60}{ColoredOutput.RESET}")
        print(f"{ColoredOutput.BOLD}#  HARA Fallback模式{ColoredOutput.RESET}")
        print(f"{ColoredOutput.BOLD}#  Item: {self.progress.item_path}{ColoredOutput.RESET}")
        print(f"{ColoredOutput.BOLD}#  Template: {self.progress.template_path}{ColoredOutput.RESET}")
        print(f"{ColoredOutput.BOLD}{'#'*60}{ColoredOutput.RESET}")
        print()

        self.progress.fallback_triggered = True
        self.progress.fallback_reason = "manual_fallback"

        success = self.run_fallback_engine()

        total_time = time.time() - overall_start
        print()
        if success:
            ColoredOutput.success(f"Fallback执行完成! 总耗时: {total_time:.1f}s")
        else:
            ColoredOutput.error(f"Fallback执行失败 (耗时: {total_time:.1f}s)")

        return success


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="HARA主执行器 V12 - 智能路由HARA分析流程",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python main_executor.py analyze --system "Light" --item "ItemDef.docx" --template "HARA_Template.xlsx"
  python main_executor.py analyze --function "Turn indication" --item "ItemDef.docx" --template "HARA_Template.xlsx"
  python main_executor.py analyze --system "Light" --item "ItemDef.docx" --template "HARA_Template.xlsx" --output "output.xlsx"
  python main_executor.py info
  python main_executor.py fallback --item "ItemDef.docx" --template "HARA_Template.xlsx"
        """
    )
    subparsers = parser.add_subparsers(dest="command", help="可用命令")

    parser_analyze = subparsers.add_parser("analyze", help="执行HARA分析")
    parser_analyze.add_argument("--system", help="子系统名称 (如 Light, Door, Seat, Wiper, Window, HUD)")
    parser_analyze.add_argument("--function", help="功能名称 (如 Turn indication)")
    parser_analyze.add_argument("--item", required=True, help="Item Definition文档路径")
    parser_analyze.add_argument("--template", required=True, help="HARA模板Excel路径")
    parser_analyze.add_argument("--output", help="输出报告路径 (可选)")
    parser_analyze.add_argument("--user-input", help="用户输入信息 (可选，用于匹配经验库)")
    parser_analyze.add_argument(
        "--resume",
        action="store_true",
        help="从断点恢复执行"
    )
    parser_analyze.add_argument(
        "--allow-draft", action="store_true",
        help="允许生成带水印的Draft/Diagnostic报告；正式Fail-Closed为默认值",
    )

    parser_info = subparsers.add_parser("info", help="查看经验库索引信息")

    parser_fallback = subparsers.add_parser("fallback", help="手动触发Fallback引擎")
    parser_fallback.add_argument("--item", required=True, help="Item Definition文档路径")
    parser_fallback.add_argument("--template", required=True, help="HARA模板Excel路径")
    parser_fallback.add_argument("--output", help="输出报告路径 (可选)")
    parser_fallback.add_argument(
        "--resume",
        action="store_true",
        help="从断点恢复执行"
    )

    parser_agent = subparsers.add_parser(
        "agent", help="运行src/hara_agent新Agent主链（迁移期显式入口）"
    )
    parser_agent.add_argument("--item", required=True, help="Item Definition文档路径")
    parser_agent.add_argument("--template", required=True, help="HARA模板Excel路径")
    parser_agent.add_argument("--output", required=True, help="输出报告路径")
    parser_agent.add_argument("--domain", required=True, help="显式Domain名称，如avp")
    parser_agent.add_argument("--run-dir", default="runtime/agent", help="Checkpoint目录")
    parser_agent.add_argument("--run-id", default="hara-run", help="运行ID")
    parser_agent.add_argument("--resume", action="store_true", help="从新Agent checkpoint恢复")
    parser_agent.add_argument("--allow-draft", action="store_true", help="允许输出带水印Draft")
    parser_agent.add_argument("--ego-speed-kph", type=float, help="项目自车速度km/h")
    parser_agent.add_argument("--ego-speed-source", default="", help="项目车速证据来源ID")
    parser_agent.add_argument(
        "--operating-mode",
        help="结构化运行模式，如 search、parking、control",
    )
    parser_agent.add_argument(
        "--allow-aggregate-speed-fallback",
        action="store_true",
        help="显式允许无上下文旧ProjectFacts使用全局速度包络",
    )
    parser_agent.add_argument(
        "--allow-legacy-speed-fallback",
        action="store_true",
        help="显式允许Draft使用未批准Domain Profile迁移速度",
    )
    parser_agent.add_argument(
        "--max-workers", type=int, default=4,
        help="LLM受控并发数（1-32，默认4）",
    )

    parser_agent_doctor = subparsers.add_parser(
        "agent-doctor", help="检查新Agent生产运行条件，不执行分析"
    )
    parser_agent_doctor.add_argument("--template", required=True, help="HARA模板Excel路径")
    parser_agent_doctor.add_argument("--domain", required=True, help="显式Domain名称，如avp")
    parser_fallback.add_argument(
        "--allow-draft", action="store_true",
        help="允许生成带水印的Draft/Diagnostic报告；正式Fail-Closed为默认值",
    )

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    try:
        if args.command == "analyze":
            executor = MainExecutor(args)
            success = executor.run_analyze()
        elif args.command == "info":
            executor = MainExecutor(args)
            success = executor.run_info()
        elif args.command == "fallback":
            executor = MainExecutor(args)
            success = executor.run_fallback()
        elif args.command == "agent":
            repository_root = script_dir.parent
            source_root = repository_root / "src"
            if str(source_root) not in sys.path:
                sys.path.insert(0, str(source_root))
            from hara_agent.cli import main as agent_main

            forwarded = [
                "analyze", "--item", args.item, "--template", args.template,
                "--output", args.output, "--domain", args.domain,
                "--run-dir", args.run_dir, "--run-id", args.run_id,
            ]
            if args.resume:
                forwarded.append("--resume")
            if args.allow_draft:
                forwarded.append("--allow-draft")
            if args.ego_speed_kph is not None:
                forwarded.extend(["--ego-speed-kph", str(args.ego_speed_kph)])
            if args.ego_speed_source:
                forwarded.extend(["--ego-speed-source", args.ego_speed_source])
            if args.operating_mode:
                forwarded.extend(["--operating-mode", args.operating_mode])
            if args.allow_aggregate_speed_fallback:
                forwarded.append("--allow-aggregate-speed-fallback")
            if args.allow_legacy_speed_fallback:
                forwarded.append("--allow-legacy-speed-fallback")
            forwarded.extend(["--max-workers", str(args.max_workers)])
            success = agent_main(forwarded) == 0
        elif args.command == "agent-doctor":
            repository_root = script_dir.parent
            source_root = repository_root / "src"
            if str(source_root) not in sys.path:
                sys.path.insert(0, str(source_root))
            from hara_agent.cli import main as agent_main

            success = agent_main([
                "doctor", "--template", args.template, "--domain", args.domain,
            ]) == 0
        else:
            parser.print_help()
            sys.exit(1)

    except KeyboardInterrupt:
        ColoredOutput.warning("用户中断执行")
        if 'executor' in locals():
            executor._save_progress()
        sys.exit(130)
    except Exception as e:
        ColoredOutput.error(f"执行异常: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
