"""Trade-volume spike: how much was traded in the last N minutes compared with the usual amount."""

from __future__ import annotations

from typing import Optional, Sequence


def volume_ratio(candle_volumes: Sequence[float], window_minutes: int) -> Optional[float]:
    """`candle_volumes` are 1-minute quote volumes, oldest first; the last candle is the current one.

    Returns (volume of the last `window_minutes` candles) / (average volume of the same-size windows
    before them). None if there is not enough history or the baseline is zero."""
    window = max(1, int(window_minutes))
    if len(candle_volumes) < window * 2:
        return None
    recent = sum(candle_volumes[-window:])
    baseline = candle_volumes[:-window]
    windows = len(baseline) / window
    average = sum(baseline) / windows
    if average <= 0:
        return None
    return recent / average
