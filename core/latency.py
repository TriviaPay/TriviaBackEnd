"""In-process latency stats for identifying hot endpoints without full tracing."""

from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass
from threading import Lock
from typing import Deque, Dict, List, Tuple


@dataclass
class LatencySummary:
    key: str
    count: int
    p50_ms: float
    p95_ms: float
    max_ms: float
    last_seen_epoch: float


class LatencyTracker:
    def __init__(self, *, window: int = 200):
        self._window = max(10, int(window))
        self._lock = Lock()
        self._data: Dict[str, Deque[float]] = defaultdict(lambda: deque(maxlen=self._window))
        self._last_seen: Dict[str, float] = {}

    def record(self, *, key: str, elapsed_ms: float) -> None:
        now = time.time()
        with self._lock:
            self._data[key].append(float(elapsed_ms))
            self._last_seen[key] = now

    def top(self, *, n: int = 5, metric: str = "p95") -> List[LatencySummary]:
        with self._lock:
            items: List[Tuple[str, List[float], float]] = []
            for key, dq in self._data.items():
                if not dq:
                    continue
                items.append((key, list(dq), self._last_seen.get(key, 0.0)))

        summaries: List[LatencySummary] = []
        for key, samples, last_seen in items:
            samples.sort()
            count = len(samples)
            p50 = samples[int(0.50 * (count - 1))]
            p95 = samples[int(0.95 * (count - 1))]
            summaries.append(
                LatencySummary(
                    key=key,
                    count=count,
                    p50_ms=float(p50),
                    p95_ms=float(p95),
                    max_ms=float(samples[-1]),
                    last_seen_epoch=float(last_seen),
                )
            )

        if metric == "max":
            summaries.sort(key=lambda s: s.max_ms, reverse=True)
        else:
            summaries.sort(key=lambda s: s.p95_ms, reverse=True)
        return summaries[: max(1, int(n))]

