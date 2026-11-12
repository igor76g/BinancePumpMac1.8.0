"""User-editable settings, stored as JSON in ~/Library/Application Support/BinancePumpAlerts/settings.json."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, fields
from pathlib import Path

APP_NAME = "Binance Pump Alerts"


@dataclass
class Settings:
    """Defaults match the requested conditions:
    24h volume > 30M USDT and price growth > 4% within 5 minutes."""

    # --- Volume filter ---
    min_volume_million_usdt: float = 30.0   # minimum 24h quote volume, in millions of USDT
    volume_refresh_sec: int = 60            # how often to refresh the list of qualifying coins

    # --- Price pump filter ---
    alert_direction: str = "up"             # "up" = growth, "down" = drop (mirror mode), "both"
    price_change_percent: float = 4.0       # minimum price move within the window, in %
    window_minutes: int = 5                 # time window used to measure price growth

    # --- Trade volume spike filter (0 = off) ---
    volume_spike_ratio: float = 0.0         # volume of the last window must be >= this x the usual volume
    volume_spike_baseline_min: int = 60     # how many minutes of history define the "usual" volume

    # --- General ---
    stale_warning_sec: int = 60             # warn if no market data arrives for this long
    check_interval_sec: int = 15            # how often to poll current prices
    cooldown_minutes: int = 15              # don't re-alert the same coin within this time
    autostart_monitoring: bool = False      # start monitoring as soon as the app opens
    sound_enabled: bool = True              # play a sound on every alert

    # --- How alerts are shown ---
    popup_enabled: bool = True              # always-on-top popup window with buttons
    toast_enabled: bool = True              # macOS notification banner
    popup_auto_close_sec: int = 0           # 0 = popups stay until you close them
    clear_alerts_log_on_exit: bool = True   # empty alerts.log when the app is closed
    prevent_sleep: bool = True              # keep the Mac awake while monitoring

    # --- Open the coin in Vataga.terminal (popup / toast / list buttons) ---
    vataga_type: str = "F"                  # F = futures, S = spot
    vataga_port: int = 5005                 # local HTTP port of Vataga.terminal
    vataga_partner_id: str = "BinancePumpAlerts"

    @property
    def min_volume_usdt(self) -> float:
        return self.min_volume_million_usdt * 1_000_000.0


def settings_path() -> Path:
    if sys.platform == "darwin":
        base = str(Path.home() / "Library" / "Application Support")
    elif sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home())
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "BinancePumpAlerts" / "settings.json"


def alerts_log_path() -> Path:
    """Text file where every alert (and notification problem) is recorded."""
    return settings_path().parent / "alerts.log"


def clear_alerts_log() -> None:
    """Empty alerts.log (never raises)."""
    try:
        path = alerts_log_path()
        if path.exists():
            path.write_text("", encoding="utf-8")
    except OSError:
        pass


def load_settings() -> Settings:
    """Load settings from disk. Missing or broken values fall back to the defaults."""
    defaults = Settings()
    try:
        data = json.loads(settings_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return defaults
    if not isinstance(data, dict):
        return defaults

    values = {}
    for field in fields(Settings):
        if field.name not in data:
            continue
        expected_type = type(getattr(defaults, field.name))
        try:
            values[field.name] = expected_type(data[field.name])
        except (TypeError, ValueError):
            pass
    return Settings(**values)


def save_settings(settings: Settings) -> None:
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(settings), indent=2), encoding="utf-8")
