"""Rolling price history per symbol."""

from __future__ import annotations

from collections import deque
from typing import Optional


class PriceTracker:
    """Keeps (timestamp, price) samples per symbol and computes the price change
    in percent within a rolling time window."""

    def __init__(self) -> None:
        self._history: dict[str, deque] = {}

    def add(self, symbol: str, price: float, now: float, window_sec: float) -> None:
        samples = self._history.setdefault(symbol, deque())
        samples.append((now, price))
        cutoff = now - window_sec
        while samples and samples[0][0] < cutoff:
            samples.popleft()

    def change_percent(self, symbol: str) -> Optional[float]:
        """Growth in % between the oldest sample in the window and the newest one."""
        samples = self._history.get(symbol)
        if not samples or len(samples) < 2:
            return None
        oldest = samples[0][1]
        newest = samples[-1][1]
        if oldest <= 0:
            return None
        return (newest - oldest) / oldest * 100.0

    def retain_only(self, symbols: set[str]) -> None:
        """Drop history for symbols that no longer pass the volume filter."""
        for symbol in list(self._history):
            if symbol not in symbols:
                del self._history[symbol]
