from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import yaml


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_yaml_mapping(path: Path) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as error:
        raise ValueError(f"Cannot load YAML asset {path}: {error}") from error
    if not isinstance(value, dict):
        raise ValueError(f"YAML asset must contain a mapping: {path}")
    return value


def canonical_node_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def bundle_hash(manifest: dict[str, Any], actual_hashes: dict[str, str]) -> str:
    payload = {
        "manifest": {key: value for key, value in manifest.items() if key != "asset_hashes"},
        "asset_hashes": dict(sorted(actual_hashes.items())),
    }
    return hashlib.sha256(canonical_node_text(payload).encode("utf-8")).hexdigest()
