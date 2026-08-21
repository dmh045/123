from __future__ import annotations

import os
import re
from pathlib import Path


_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def load_local_env(path: str | Path = ".env") -> bool:
    """Load a local env file without logging values or overriding the process.

    The production CLI calls this before constructing configuration. Explicit
    shell/CI environment variables remain authoritative.
    """

    env_path = Path(path)
    if not env_path.is_file():
        return False
    for line_number, raw_line in enumerate(
        env_path.read_text(encoding="utf-8-sig").splitlines(), start=1,
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        name, separator, raw_value = line.partition("=")
        name = name.strip()
        if not separator or not _NAME.fullmatch(name):
            raise ValueError(f"Invalid .env assignment at line {line_number}")
        value = raw_value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(name, value)
    return True
