import os
from pathlib import Path
from uuid import uuid4

import pytest

from hara_agent.config import load_local_env


def _workspace_env_file() -> Path:
    root = Path("tmp") / f"env-file-test-{uuid4().hex}"
    root.mkdir(parents=True)
    return root / ".env"


def test_local_env_loads_values_without_overriding_process(monkeypatch):
    env_file = _workspace_env_file()
    try:
        env_file.write_text(
            "HARA_TEST_EXISTING=file-value\n"
            "export HARA_TEST_QUOTED='quoted value'\n"
            "# ignored\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("HARA_TEST_EXISTING", "process-value")
        monkeypatch.delenv("HARA_TEST_QUOTED", raising=False)

        assert load_local_env(env_file) is True
        assert os.environ["HARA_TEST_EXISTING"] == "process-value"
        assert os.environ["HARA_TEST_QUOTED"] == "quoted value"
    finally:
        env_file.unlink(missing_ok=True)
        env_file.parent.rmdir()


def test_local_env_is_optional_and_rejects_invalid_assignments():
    invalid = _workspace_env_file()
    try:
        assert load_local_env(invalid.parent / "missing.env") is False
        invalid.write_text("not an assignment", encoding="utf-8")
        with pytest.raises(ValueError, match="line 1"):
            load_local_env(invalid)
    finally:
        invalid.unlink(missing_ok=True)
        invalid.parent.rmdir()
