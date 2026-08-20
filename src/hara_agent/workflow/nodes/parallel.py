from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, TypeVar


InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")


def ordered_parallel_map(
    items: list[InputT],
    worker: Callable[[InputT], OutputT],
    *,
    max_workers: int,
    on_progress: Callable[[int, int], None] | None = None,
) -> list[OutputT]:
    """Run independent LLM tasks concurrently while preserving input order."""
    if max_workers < 1:
        raise ValueError("max_workers必须大于0")
    total = len(items)
    if not items:
        return []
    if max_workers == 1 or total == 1:
        results = []
        for completed, item in enumerate(items, start=1):
            results.append(worker(item))
            if on_progress:
                on_progress(completed, total)
        return results

    missing = object()
    results: list[OutputT | object] = [missing] * total
    pool_size = min(max_workers, total)
    with ThreadPoolExecutor(max_workers=pool_size, thread_name_prefix="hara-llm") as executor:
        futures = {executor.submit(worker, item): index for index, item in enumerate(items)}
        completed = 0
        try:
            for future in as_completed(futures):
                results[futures[future]] = future.result()
                completed += 1
                if on_progress:
                    on_progress(completed, total)
        except Exception:
            for future in futures:
                future.cancel()
            raise
    if any(value is missing for value in results):
        raise RuntimeError("并发任务结果不完整")
    return list(results)  # type: ignore[return-value]
