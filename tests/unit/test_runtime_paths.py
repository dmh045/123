import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from hara_controller import HARAController  # noqa: E402
from main_executor import MainExecutor  # noqa: E402


def _controller() -> HARAController:
    return HARAController(
        argparse.Namespace(
            item=str(ROOT / "input" / "ItemDef.docx"),
            template=str(ROOT / "references" / "HARA_Template_AI_20260327.xlsx"),
            output=str(ROOT / "output" / "HARA_Report_Output.xlsx"),
            malfunction_json=None,
            resume=False,
            allow_draft=True,
        )
    )


def test_controller_runtime_artifacts_are_outside_source_tree():
    controller = _controller()

    assert controller.run_dir == ROOT / "runtime" / "current"
    assert controller.progress_file.parent == controller.run_dir
    assert controller.default_malfunction_json.parent == controller.run_dir
    assert controller.default_scen_hazevent_json.parent == controller.run_dir
    assert controller.default_scoring_json.parent == controller.run_dir
    assert controller.SCRIPTS_DIR not in controller.progress_file.parents


def test_main_executor_progress_is_outside_source_tree():
    assert MainExecutor.PROGRESS_FILE == ROOT / "runtime" / "current" / "main_executor_progress.json"

