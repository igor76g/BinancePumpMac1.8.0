"""Background monitoring loop.

Every `check_interval_sec` seconds:
  1. (periodically) refresh the list of coins whose 24h quote volume >= minimum volume,
  2. fetch current prices (REST) and track them over a rolling window,
  3. when the move within the window reaches the threshold (growth, drop or both, see `direction`),
     optionally check the trade-volume spike (1-minute candles),
  4. fire a notification.

If no market data arrives for `stale_warning_sec` seconds, a "Connection lost" warning is raised once,
and "Connection restored" when data is back.

Settings are re-read on every iteration, so changes saved in the window apply without restarting.
"""

from __future__ import annotations

import threading
import time
from typing import Callable

from .price_tracker import PriceTracker
from .settings import Settings
from .volume_spike import volume_ratio

SYMBOLS_REFRESH_SEC = 600  # exchangeInfo is large, so the symbol list is refreshed rarely
SPIKE_RECHECK_SEC = 20     # don't refetch candles for the same coin more often than this


class Monitor(threading.Thread):
    def __init__(
        self,
        api,
        load_settings: Callable[[], Settings],
        notify: Callable[..., None],
        log: Callable[[str], None],
        set_status: Callable[[str], None],
        warn: Callable[[str, str], None] | None = None,
    ) -> None:
        super().__init__(name="monitor", daemon=True)
        self.api = api
        self.load_settings = load_settings
        self.notify = notify
        self.log = log
        self.set_status = set_status
        self.warn = warn

        self.tracker = PriceTracker()
        self.qualifying: set[str] = set()
        self._last_volume_refresh: float | None = None
        self._last_alert: dict[tuple[str, str], float] = {}
        self._stopping = threading.Event()

        self._symbols: set[str] = set()
        self._symbols_at: float | None = None
        self._last_ok = time.time()
        self._connection_lost = False
        self._spike_cache: dict[str, tuple[float, float | None]] = {}
        self._skip_logged: dict[str, float] = {}

    def stop(self) -> None:
        self._stopping.set()

    def run(self) -> None:
        self.log("Monitoring started")
        self._last_ok = time.time()
        while not self._stopping.is_set():
            settings = self.load_settings()
            try:
                self.tick(settings, time.time())
            except Exception as exc:  # network errors, bad responses, etc.
                self.log(f"Error: {exc} (retrying)")
                self.set_status(f"Error: {exc} (retrying)")
            self.check_connection(settings, time.time())
            self._stopping.wait(max(1, settings.check_interval_sec))
        self.set_status("Stopped")
        self.log("Monitoring stopped")

    def check_connection(self, settings: Settings, now: float) -> None:
        """Warn once when no market data has arrived for a while, and once when it is back."""
        silent = now - self._last_ok
        limit = max(10, settings.stale_warning_sec)
        if not self._connection_lost and silent >= limit:
            self._connection_lost = True
            text = (
                f"No market data from Binance for {int(silent)} s. "
                "Alerts are NOT being generated. Check the internet / VPN."
            )
            self.log("WARNING: " + text)
            self._warn("Connection lost", text)
        elif self._connection_lost and silent < limit:
            self._connection_lost = False
            self.log("Connection restored")
            self._warn("Connection restored", "Market data from Binance is flowing again.")

    def _warn(self, title: str, text: str) -> None:
        if self.warn is None:
            return
        try:
            self.warn(title, text)
        except Exception as exc:
            self.log(f"Could not show warning: {exc}")

    def _spike_ratio(self, symbol: str, settings: Settings, now: float):
        """Trade-volume ratio for the coin, or None if unknown. Cached briefly."""
        cached = self._spike_cache.get(symbol)
        if cached is not None and now - cached[0] < SPIKE_RECHECK_SEC:
            return cached[1]
        fetch = getattr(self.api, "recent_quote_volumes", None)
        ratio = None
        if fetch is not None:
            window = max(1, settings.window_minutes)
            try:
                candles = fetch(symbol, window + max(window, settings.volume_spike_baseline_min))
                ratio = volume_ratio(candles, window)
            except Exception as exc:  # never block an alert because candles could not be loaded
                self.log(f"Could not load candles for {symbol}: {exc}")
        self._spike_cache[symbol] = (now, ratio)
        return ratio

    @staticmethod
    def _direction_of(change: float, settings: Settings) -> str | None:
        """'up' / 'down' if the move reaches the threshold and the chosen mode allows it, else None."""
        mode = settings.alert_direction
        if change >= settings.price_change_percent and mode in ("up", "both"):
            return "up"
        if change <= -settings.price_change_percent and mode in ("down", "both"):
            return "down"
        return None

    def tick(self, settings: Settings, now: float) -> None:
        """One monitoring iteration. `now` is a Unix timestamp in seconds."""
        # 1) Periodically refresh which coins pass the 24h volume filter
        refresh_sec = max(1, settings.volume_refresh_sec)
        if self._last_volume_refresh is None or now - self._last_volume_refresh >= refresh_sec:
            if self._symbols_at is None or now - self._symbols_at >= SYMBOLS_REFRESH_SEC:
                self._symbols = self.api.usdt_perpetual_symbols()
                self._symbols_at = now
            volumes = self.api.quote_volumes()
            qualifying = {s for s in self._symbols if volumes.get(s, 0.0) >= settings.min_volume_usdt}
            if qualifying != self.qualifying:
                self.log(
                    f"Coins with 24h volume >= {settings.min_volume_million_usdt:g}M USDT: {len(qualifying)}"
                )
            self.qualifying = qualifying
            self.tracker.retain_only(qualifying)
            self._last_volume_refresh = now

        # 2) Poll prices and detect moves
        prices = self.api.prices()
        if not prices:
            raise RuntimeError("empty price data")
        self._last_ok = now
        window_sec = max(1, settings.window_minutes) * 60
        cooldown_sec = max(0, settings.cooldown_minutes) * 60

        for symbol in sorted(self.qualifying):
            price = prices.get(symbol)
            if price is None:
                continue
            self.tracker.add(symbol, price, now, window_sec)
            change = self.tracker.change_percent(symbol)
            if change is None:
                continue
            direction = self._direction_of(change, settings)
            if direction is None:
                continue

            last = self._last_alert.get((symbol, direction))
            if last is not None and now - last < cooldown_sec:
                continue

            # 3) Trade-volume spike (also shown in the alert when known)
            ratio = None
            if settings.volume_spike_ratio > 0 or getattr(self.api, "recent_quote_volumes", None) is not None:
                ratio = self._spike_ratio(symbol, settings, now)
            if settings.volume_spike_ratio > 0 and ratio is not None and ratio < settings.volume_spike_ratio:
                if now - self._skip_logged.get(symbol, -1e9) >= 120:
                    self._skip_logged[symbol] = now
                    self.log(
                        f"Skipped {symbol}: {change:+.2f}% but trade volume only x{ratio:.1f} "
                        f"(needs x{settings.volume_spike_ratio:g})"
                    )
                continue  # no cooldown: it may still qualify on a later check

            self._last_alert[(symbol, direction)] = now
            extra = f", volume x{ratio:.1f}" if ratio is not None else ""
            self.log(f"ALERT {symbol}: {change:+.2f}% in {settings.window_minutes} min (price {price:g}{extra})")
            try:
                if ratio is None:
                    self.notify(symbol, change, price, settings.window_minutes)
                else:
                    self.notify(symbol, change, price, settings.window_minutes, ratio)
            except Exception as exc:  # a notification problem must not stop monitoring
                self.log(f"Could not show notification: {exc}")

        self.set_status(
            f"Watching {len(self.qualifying)} coins. Last check: {time.strftime('%H:%M:%S')}"
        )
