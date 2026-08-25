from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any


class ValidatedArtifactCache:
    """Content-addressed validated cache with a separate candidate quarantine."""

    MODES = {"off", "readonly", "readwrite", "refresh"}

    def __init__(self, directory: str | Path, mode: str = "off"):
        self.directory = Path(directory)
        self.mode = mode.strip().lower()
        if self.mode not in self.MODES:
            raise ValueError("Artifact cache mode必须为off、readonly、readwrite或refresh")

    def key(self, material: dict[str, Any]) -> str:
        encoded = json.dumps(
            material, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def load(self, key: str) -> dict[str, Any] | None:
        payload, _ = self.load_with_status(key)
        return payload

    def load_with_status(self, key: str) -> tuple[dict[str, Any] | None, str]:
        if self.mode not in {"readonly", "readwrite"}:
            return None, f"cache_mode_{self.mode}"
        path = self.directory / f"{key}.json"
        if not path.is_file():
            return None, "cache_file_missing"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                return None, "cache_payload_not_object"
            return payload, "hit"
        except (OSError, ValueError, json.JSONDecodeError):
            return None, "cache_file_unreadable_or_invalid_json"

    def save(self, key: str, payload: dict[str, Any]) -> None:
        self._save_path(self.directory / f"{key}.json", payload)

    def load_candidate_with_status(
        self, key: str,
    ) -> tuple[dict[str, Any] | None, str]:
        """Load an unvalidated provider response from the isolated quarantine.

        Candidate payloads are never returned by ``load`` and therefore can
        never masquerade as validated artifacts.
        """

        if self.mode not in {"readonly", "readwrite"}:
            return None, f"cache_mode_{self.mode}"
        path = self.directory / "candidates" / f"{key}.json"
        if not path.is_file():
            return None, "candidate_file_missing"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                return None, "candidate_payload_not_object"
            return payload, "hit"
        except (OSError, ValueError, json.JSONDecodeError):
            return None, "candidate_file_unreadable_or_invalid_json"

    def save_candidate(self, key: str, payload: dict[str, Any]) -> None:
        """Persist raw output only in quarantine for deterministic revalidation."""

        self._save_path(
            self.directory / "candidates" / f"{key}.json", payload,
        )

    def _save_path(self, path: Path, payload: dict[str, Any]) -> None:
        if self.mode not in {"readwrite", "refresh"}:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            handle, temporary = tempfile.mkstemp(
                prefix=f".{path.stem}.", suffix=".tmp", dir=str(path.parent), text=True,
            )
            try:
                with os.fdopen(handle, "w", encoding="utf-8") as stream:
                    json.dump(payload, stream, ensure_ascii=False, sort_keys=True)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        except OSError:
            return
