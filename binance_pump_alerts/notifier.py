"""Alerts on macOS: notification-center banner (osascript) + sound (afplay) + popup window + alerts.log.

* The banner is shown with `osascript` (`display notification`). macOS attributes it to "Script Editor", so allow
  notifications for Script Editor in System Settings > Notifications (it appears there after the first notification).
  Banners cannot have buttons, so the popup window (shown by the GUI through `alert_listener`) offers
  "Open in Vataga" / "Open on Binance".
* The sound is played by the app itself with `afplay`, so it works even if the banner is muted.
* Banners run in a background thread, so a slow call never delays market checks.

On non-macOS systems (e.g. when testing) everything falls back to console output.
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time

from decimal import Decimal

from .settings import alerts_log_path, load_settings

IS_MAC = sys.platform == "darwin"
SOUND_FILE = "/System/Library/Sounds/Glass.aiff"

# Set by the GUI: called as listener(symbol, title, message, direction) from any thread; shows the popup window.
alert_listener = None


def _format_price(price: float) -> str:
    return format(Decimal(str(price)).normalize(), "f")


def _append_log(line: str) -> None:
    """Record an alert / problem in alerts.log (never raises)."""
    try:
        path = alerts_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {line}\n")
    except OSError:
        pass


def play_sound() -> None:
    """Play the alert sound without blocking the caller."""
    if not IS_MAC:
        return
    try:
        subprocess.Popen(["afplay", SOUND_FILE], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass


def _show_banner(title: str, message: str) -> None:
    try:
        if not IS_MAC:
            print(f"[notification] {title}: {message}")
            return
        # Text is passed as arguments (not pasted into the script), so quotes etc. are safe.
        cmd = [
            "osascript",
            "-e", "on run argv",
            "-e", "display notification (item 1 of argv) with title (item 2 of argv)",
            "-e", "end run",
            message.replace("\n", " | "),
            title,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if result.returncode != 0:
            _append_log(f"BANNER FAILED for '{title}': {result.stderr.strip()}")
    except Exception as exc:
        _append_log(f"BANNER FAILED for '{title}': {exc}")


def _notify(symbol: str, title: str, message: str, sound: bool, direction: int = 0) -> None:
    settings = load_settings()
    if sound:
        play_sound()
    if settings.toast_enabled:
        threading.Thread(target=_show_banner, args=(title, message), daemon=True).start()
    if settings.popup_enabled and alert_listener is not None:
        try:
            alert_listener(symbol, title, message, direction)
        except Exception as exc:
            _append_log(f"POPUP FAILED for '{title}': {exc}")


def show_alert(
    symbol: str,
    change_percent: float,
    price: float,
    window_minutes: int,
    sound: bool = True,
    volume_ratio: float | None = None,
) -> None:
    """Pump (change > 0) or dump (change < 0) alert with the coin ticker in the title."""
    price_text = _format_price(price)
    verb = "rose" if change_percent >= 0 else "fell"
    message = f"Price {verb} {abs(change_percent):.2f}% in {window_minutes} min. Current price: {price_text}"
    log_line = f"ALERT {symbol} {change_percent:+.2f}% in {window_minutes} min, price {price_text}"
    if volume_ratio is not None:
        message += f"\nTrade volume: x{volume_ratio:.1f} of the usual"
        log_line += f", volume x{volume_ratio:.1f}"
    _append_log(log_line)
    _notify(
        symbol=symbol,
        title=f"{symbol}  {change_percent:+.2f}%",
        message=message,
        sound=sound,
        direction=1 if change_percent >= 0 else -1,
    )


def show_warning(title: str, message: str, sound: bool = True) -> None:
    """Problem notice (e.g. connection lost). Shown without coin buttons."""
    _append_log(f"WARNING {title}: {message}")
    _notify(symbol="", title=title, message=message, sound=sound)


def show_test(sound: bool = True) -> None:
    _notify("BTCUSDT", "Test notification", "Notifications are working.", sound)
