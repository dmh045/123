from __future__ import annotations

import sys
import time

from hara_agent.services.extraction import DocumentReader
from hara_agent.workflow.state import HARAState, WorkflowStage


def read_item_document(state: HARAState, path: str,
                       reader: DocumentReader | None = None) -> HARAState:
    started = time.monotonic()
    artifact = (reader or DocumentReader()).read(path)
    elapsed_seconds = time.monotonic() - started
    state.item_definition = {
        "source_id": artifact.source_id,
        "source_path": str(artifact.source_path),
        "blocks": [
            {
                "block_id": block.block_id,
                "kind": block.kind,
                "location": block.location,
                "text": block.text,
            }
            for block in artifact.blocks
        ],
        "text": artifact.text,
    }
    state.stage = WorkflowStage.EXTRACT
    state.record(
        "item_document_read",
        source_id=artifact.source_id,
        block_count=len(artifact.blocks),
        character_count=len(artifact.text),
        elapsed_seconds=round(elapsed_seconds, 3),
    )
    print(
        "[HARA] document parse completed "
        f"blocks={len(artifact.blocks)} input_chars={len(artifact.text)} "
        f"elapsed={elapsed_seconds:.1f}s",
        file=sys.stderr,
        flush=True,
    )
    return state
