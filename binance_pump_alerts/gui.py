"""Tkinter UI: a compact main window (Start / Stop / Settings, recent alerts, log), a settings dialog and a mini chart.

Recent alerts: every row has "Chart" (mini chart that scrolls with the mouse wheel) and "Copy" (copies the ticker)
cells; double-click a row to open the coin in Vataga.terminal.
"""

from __future__ import annotations

import queue
import re
import sys
import threading
import time
import tkinter as tk
import webbrowser
from tkinter import messagebox, scrolledtext, ttk

from . import __version__, notifier, popups, vataga
from .binance_api import CHART_INTERVALS, BinanceApi, Candle
from .chart import MAX_VIEW, MIN_VIEW, build_scene, clamp_offset, clamp_view, visible
from .monitor import Monitor
from .platform_utils import SleepGuard, open_file
from .settings import (
    APP_NAME, Settings, alerts_log_path, clear_alerts_log, load_settings, save_settings,
)

MAX_LOG_LINES = 300
BOLD = ("Helvetica", 12, "bold")

# label shown in the window -> value stored in settings
DIRECTIONS = {"Growth (+)": "up", "Drop (-), mirror mode": "down", "Growth and drop": "both"}

# Settings dialog: one tab per section. (settings key, label, python type, minimum allowed value); label None = tab title
FIELDS = [
    ("Filters", None, None, None),
    ("min_volume_million_usdt", "Minimum 24h volume (million USDT)", float, 0.0),
    ("volume_refresh_sec", "Volume list refresh interval (seconds)", int, 1),
    ("price_change_percent", "Minimum price move (%)", float, 0.01),
    ("window_minutes", "Time window (minutes)", int, 1),
    ("volume_spike_ratio", "Min volume spike (x usual volume, 0 = off)", float, 0.0),
    ("volume_spike_baseline_min", "History used as 'usual' volume (minutes)", int, 5),
    ("General", None, None, None),
    ("stale_warning_sec", "Warn if no market data for (seconds)", int, 10),
    ("check_interval_sec", "Price check interval (seconds)", int, 1),
    ("cooldown_minutes", "Alert cooldown per coin (minutes)", int, 0),
    ("popup_auto_close_sec", "Close alert popups after (seconds, 0 = never)", int, 0),
]

# (settings key, label); check boxes on the "General" tab
BOOL_FIELDS = [
    ("sound_enabled", "Play a sound on every alert"),
    ("popup_enabled", "Show a popup window with buttons (always on top)"),
    ("toast_enabled", "Show a macOS notification banner"),
    ("prevent_sleep", "Keep the Mac awake while monitoring"),
    ("clear_alerts_log_on_exit", "Empty the alerts log when the app is closed"),
    ("autostart_monitoring", "Start monitoring automatically when the app opens"),
]


class ChartWindow(tk.Toplevel):
    """Mini chart of a coin. Mouse wheel = scroll through history, Ctrl + wheel = zoom, Home / End = oldest / newest.

    1000 candles are loaded once (one small request); the newest candles are refreshed every few seconds."""

    REDRAW_MS = 1000
    REFRESH_SEC = 10

    def __init__(self, app: "App", symbol: str) -> None:
        super().__init__(app)
        self.app = app
        self.symbol = symbol
        self.title(f"{symbol} - chart")
        self.geometry("900x520+120+80")
        self.candles: list[Candle] = []
        self.view = 120          # candles on the screen
        self.offset = 0          # newest candles scrolled out of view
        self.error = ""
        self._fetching = False
        self._fetched_at = 0.0
        self._full_loaded_interval = ""
        self._job = None

        top = ttk.Frame(self, padding=(8, 6))
        top.pack(fill="x")
        ttk.Label(top, text=symbol, font=BOLD).pack(side="left")
        ttk.Label(top, text="   Candles:").pack(side="left")
        self.interval_var = tk.StringVar(value="1m")
        box = ttk.Combobox(top, textvariable=self.interval_var, values=CHART_INTERVALS, width=5, state="readonly")
        box.pack(side="left", padx=4)
        box.bind("<<ComboboxSelected>>", lambda _e: self._interval_changed())
        ttk.Button(top, text="Newest", command=self.go_newest).pack(side="left", padx=(12, 0))
        self.canvas = tk.Canvas(self, bg="#1e2329", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.info = tk.StringVar(value="")
        ttk.Label(self, textvariable=self.info).pack(anchor="w", padx=8, pady=4)

        # the wheel goes to the focused widget on Windows, so bind it on the window itself
        self.bind("<MouseWheel>", self._wheel)
        self.bind("<Control-MouseWheel>", self._ctrl_wheel)
        self.bind("<Button-4>", lambda e: self._scroll(+1))        # Linux
        self.bind("<Button-5>", lambda e: self._scroll(-1))
        self.bind("<Left>", lambda e: self._scroll(+1))
        self.bind("<Right>", lambda e: self._scroll(-1))
        self.bind("<Home>", lambda e: self._set_offset(10 ** 9))
        self.bind("<End>", lambda e: self.go_newest())
        self.canvas.bind("<Configure>", lambda _e: self._draw_safe())
        self.canvas.bind("<Enter>", lambda _e: self.canvas.focus_set())
        self.protocol("WM_DELETE_WINDOW", self._close)
        self._tick()

    # ---------------------------------------------------------------- data

    def _close(self) -> None:
        if self._job is not None:
            self.after_cancel(self._job)
        self.app.charts.pop(self.symbol, None)
        self.destroy()

    def _interval_changed(self) -> None:
        self.candles = []
        self.offset = 0
        self._fetched_at = 0.0
        self._tick(reschedule=False)

    def _tick(self, reschedule: bool = True) -> None:
        if time.time() - self._fetched_at >= self.REFRESH_SEC and not self._fetching:
            self._start_fetch()
        self._draw_safe()
        if reschedule:
            self._job = self.after(self.REDRAW_MS, self._tick)

    def _start_fetch(self) -> None:
        self._fetching = True
        interval = self.interval_var.get()
        full = not self.candles or self._full_loaded_interval != interval

        def work() -> None:
            try:
                fresh = self.app.api.klines(self.symbol, interval, 1000 if full else 5)
                if interval == self.interval_var.get():
                    if full:
                        self.candles = fresh
                        self._full_loaded_interval = interval
                    else:   # replace / append the newest candles
                        by_time = {c.open_time: c for c in self.candles}
                        by_time.update({c.open_time: c for c in fresh})
                        self.candles = [by_time[t] for t in sorted(by_time)][-2000:]
                self.error = ""
            except Exception as exc:
                self.error = str(exc)
            finally:
                self._fetched_at = time.time()
                self._fetching = False

        threading.Thread(target=work, daemon=True).start()

    # -------------------------------------------------------------- scrolling

    def _step(self) -> int:
        return max(1, self.view // 10)

    def _set_offset(self, offset: int) -> None:
        total = len(self.candles)
        self.view = clamp_view(self.view, total)
        self.offset = clamp_offset(offset, total, self.view)
        self._draw_safe()

    def go_newest(self) -> None:
        self._set_offset(0)

    def _scroll(self, direction: int) -> None:
        """direction +1 = back in time (older candles), -1 = forward."""
        self._set_offset(self.offset + direction * self._step())

    @staticmethod
    def _wheel_direction(event) -> int:
        delta = getattr(event, "delta", 0)
        return 1 if delta > 0 else (-1 if delta < 0 else 0)

    def _wheel(self, event) -> None:
        direction = self._wheel_direction(event)
        if direction:
            self._scroll(direction)

    def _ctrl_wheel(self, event) -> None:
        """Ctrl + wheel up = zoom in (fewer candles), down = zoom out."""
        direction = self._wheel_direction(event)
        if not direction:
            return
        factor = 0.8 if direction > 0 else 1.25
        self.view = max(MIN_VIEW, min(MAX_VIEW, int(round(self.view * factor))))
        self._set_offset(self.offset)

    # ------------------------------------------------------------------ draw

    def _draw_safe(self) -> None:
        try:
            self._draw()
        except Exception as exc:  # a drawing problem must never break the app
            self.info.set(f"Chart error: {exc}")

    def _draw(self) -> None:
        c = self.canvas
        c.delete("all")
        width, height = max(c.winfo_width(), 300), max(c.winfo_height(), 160)
        if not self.candles:
            c.create_text(width / 2, height / 2, fill="#9aa4af", text=self.error or "Loading candles...")
            return
        total = len(self.candles)
        self.view = clamp_view(self.view, total)
        self.offset = clamp_offset(self.offset, total, self.view)
        shown = visible(self.candles, self.view, self.offset)
        last_price = self.candles[-1].close
        scene = build_scene(shown, last_price, width, height)
        right = width - 76
        for y, value in scene.ticks:
            c.create_line(8, y, right, y, fill="#2b3139")
            c.create_text(right + 6, y, anchor="w", fill="#9aa4af", font=("Helvetica", 8), text=f"{value:.6g}")
        for x, y_high, y_low, y_open, y_close, is_up in scene.candles:
            color = "#0ecb81" if is_up else "#f6465d"
            c.create_line(x, y_high, x, y_low, fill=color)
            half = scene.body_width / 2
            c.create_rectangle(x - half, min(y_open, y_close), x + half, max(y_open, y_close) + 1, fill=color, outline=color)
        if scene.price_y is not None:
            c.create_line(8, scene.price_y, right, scene.price_y, fill="#f0b90b", dash=(4, 3))
            c.create_text(right + 6, scene.price_y - 10, anchor="w", fill="#f0b90b", font=("Helvetica", 9, "bold"),
                          text=f"{last_price:.6g}")
        first = time.strftime("%d.%m %H:%M", time.localtime(shown[0].open_time / 1000))
        last = time.strftime("%d.%m %H:%M", time.localtime(shown[-1].open_time / 1000))
        self.info.set(
            f"{self.interval_var.get()} candles: showing {len(shown)} of {total}  ({first} - {last}).  "
            "Mouse wheel = scroll through history, Ctrl + wheel = zoom, Home / End = oldest / newest."
        )


class SettingsDialog(tk.Toplevel):
    def __init__(self, app: "App") -> None:
        super().__init__(app)
        self.app = app
        self.title("Settings")
        self.resizable(False, False)
        self.transient(app)

        settings = load_settings()
        self.vars: dict[str, tk.StringVar] = {}
        self.labels: dict[str, str] = {}
        self.types: dict[str, tuple] = {}
        self.bool_vars: dict[str, tk.BooleanVar] = {}
        self.dir_var = tk.StringVar(
            value=next((k for k, v in DIRECTIONS.items() if v == settings.alert_direction), "Growth (+)")
        )

        root = ttk.Frame(self, padding=14)
        root.pack(fill="both", expand=True)
        notebook = ttk.Notebook(root)
        notebook.pack(fill="both", expand=True)

        form = None
        row = 0
        for key, label, py_type, minimum in FIELDS:
            if label is None:   # a new tab
                form = ttk.Frame(notebook, padding=10)
                form.columnconfigure(0, weight=1)
                notebook.add(form, text=key)
                row = 0
                if key == "Filters":
                    ttk.Label(form, text="Alert on").grid(row=row, column=0, sticky="w", pady=3)
                    ttk.Combobox(form, textvariable=self.dir_var, values=list(DIRECTIONS), width=22, state="readonly").grid(
                        row=row, column=1, sticky="e", pady=3)
                    row += 1
                continue
            var = tk.StringVar()
            value = getattr(settings, key)
            var.set(f"{value:g}" if isinstance(value, float) else str(value))
            self.vars[key] = var
            self.labels[key] = label
            self.types[key] = (py_type, minimum)
            ttk.Label(form, text=label).grid(row=row, column=0, sticky="w", pady=3, padx=(0, 16))
            ttk.Entry(form, textvariable=var, width=12).grid(row=row, column=1, sticky="e", pady=3)
            row += 1

        row += 1
        for key, label in BOOL_FIELDS:
            var = tk.BooleanVar(value=bool(getattr(settings, key)))
            self.bool_vars[key] = var
            ttk.Checkbutton(form, text=label, variable=var).grid(row=row, column=0, columnspan=2, sticky="w", pady=2)
            row += 1

        ttk.Label(form, text="Vataga.terminal", font=BOLD).grid(row=row, column=0, columnspan=2, sticky="w", pady=(12, 2))
        row += 1
        vrow = ttk.Frame(form)
        vrow.grid(row=row, column=0, columnspan=2, sticky="we")
        self.vtype_var = tk.StringVar(value=settings.vataga_type)
        self.vport_var = tk.StringVar(value=str(settings.vataga_port))
        ttk.Label(vrow, text="Market").pack(side="left")
        ttk.Combobox(vrow, textvariable=self.vtype_var, values=["F", "S"], width=3, state="readonly").pack(side="left", padx=4)
        ttk.Label(vrow, text="(F = futures, S = spot)   Port").pack(side="left")
        ttk.Entry(vrow, textvariable=self.vport_var, width=7).pack(side="left", padx=4)
        ttk.Button(vrow, text="Test Vataga", command=self.test_vataga).pack(side="right")

        buttons = ttk.Frame(root)
        buttons.pack(fill="x", pady=(14, 0))
        ttk.Button(buttons, text="Save", command=self.save).pack(side="right")
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side="right", padx=6)

    def read(self) -> Settings | None:
        """Validate the form. Shows an error dialog and returns None if something is invalid."""
        values: dict = {}
        for key, var in self.vars.items():
            py_type, minimum = self.types[key]
            raw = var.get().strip().replace(",", ".")
            try:
                value = py_type(raw)
            except ValueError:
                messagebox.showerror(APP_NAME, f"'{self.labels[key]}' must be a valid number.", parent=self)
                return None
            if value < minimum:
                messagebox.showerror(APP_NAME, f"'{self.labels[key]}' must be at least {minimum:g}.", parent=self)
                return None
            values[key] = value
        try:
            port = int(self.vport_var.get().strip())
            if not 1 <= port <= 65535:
                raise ValueError
        except ValueError:
            messagebox.showerror(APP_NAME, "Port must be a number from 1 to 65535.", parent=self)
            return None
        for key, var in self.bool_vars.items():
            values[key] = var.get()
        return Settings(
            alert_direction=DIRECTIONS.get(self.dir_var.get(), "up"),
            vataga_type=self.vtype_var.get() or "F",
            vataga_port=port,
            vataga_partner_id=load_settings().vataga_partner_id,
            **values,
        )

    def save(self) -> None:
        settings = self.read()
        if settings is None:
            return
        try:
            save_settings(settings)
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"Could not save settings: {exc}", parent=self)
            return
        self.app.add_log("Settings saved")
        self.destroy()

    def test_vataga(self) -> None:
        settings = self.read()
        if settings is None:
            return
        ok, message = vataga.open_symbol("BTCUSDT", settings)
        if ok:
            messagebox.showinfo(APP_NAME, "Request sent. BTCUSDT should open in the window linked to 'Screener'.", parent=self)
        else:
            messagebox.showerror(APP_NAME, message, parent=self)


class App(tk.Tk):
    HISTORY_COLUMNS = [
        ("time", "Time", 62, "w"),
        ("ticker", "Ticker", 105, "w"),
        ("chart", "", 52, "center"),
        ("copy", "", 52, "center"),
        ("change", "Change", 70, "e"),
        ("details", "Details", 240, "w"),
    ]
    CHART_TEXT, COPY_TEXT = "Chart", "Copy"

    def __init__(self) -> None:
        super().__init__()
        self.title(f"{APP_NAME} {__version__}")
        height = min(580, max(self.winfo_screenheight() - 100, 420))
        self.geometry(f"700x{height}+60+40")
        self.minsize(560, 420)

        self.api = BinanceApi()
        self.monitor: Monitor | None = None
        self.messages: queue.Queue = queue.Queue()
        self.charts: dict[str, ChartWindow] = {}
        self.sleep_guard = SleepGuard()

        self._build_widgets()
        notifier.alert_listener = lambda symbol, title, message, direction=0: self.messages.put(
            ("popup", (symbol, title, message, direction))
        )

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        if sys.platform == "darwin":
            self.createcommand("::tk::mac::Quit", self._on_close)  # Cmd+Q
        self.after(200, self._drain_messages)
        if load_settings().autostart_monitoring:
            self.after(500, self.start_monitoring)

    # ------------------------------------------------------------------ UI

    def _build_widgets(self) -> None:
        root = ttk.Frame(self, padding=10)
        root.pack(fill="both", expand=True)

        ttk.Label(root, text="Binance Futures Pump Alerts", font=("Helvetica", 13, "bold")).pack(anchor="w")
        self.status_var = tk.StringVar(value="Stopped")
        ttk.Label(root, textvariable=self.status_var, wraplength=600).pack(anchor="w", pady=(2, 6))

        buttons = ttk.Frame(root)
        buttons.pack(fill="x")
        self.start_button = ttk.Button(buttons, text="Start monitoring", command=self.start_monitoring)
        self.start_button.pack(side="left")
        self.stop_button = ttk.Button(buttons, text="Stop", command=self.stop_monitoring, state="disabled")
        self.stop_button.pack(side="left", padx=6)
        ttk.Button(buttons, text="Settings", command=self.open_settings).pack(side="right")
        ttk.Button(buttons, text="Test notification", command=self.test_notification).pack(side="right", padx=6)

        ttk.Label(root, text="Recent alerts", font=BOLD).pack(anchor="w", pady=(10, 2))
        hist = ttk.Frame(root)
        hist.pack(fill="both", expand=True)
        self.history = ttk.Treeview(
            hist, columns=[c[0] for c in self.HISTORY_COLUMNS], show="headings", height=8, selectmode="browse"
        )
        for col, text, width, anchor in self.HISTORY_COLUMNS:
            self.history.heading(col, text=text)
            self.history.column(col, width=width, anchor=anchor)
        bar = ttk.Scrollbar(hist, orient="vertical", command=self.history.yview)
        self.history.configure(yscrollcommand=bar.set)
        self.history.pack(side="left", fill="both", expand=True)
        bar.pack(side="right", fill="y")
        self.history.bind("<Button-1>", self._history_click)
        self.history.bind("<Double-1>", self._history_double_click)
        hbtn = ttk.Frame(root)
        hbtn.pack(fill="x", pady=(4, 0))
        ttk.Button(hbtn, text="Open in Vataga", command=self.history_open_vataga).pack(side="left")
        ttk.Button(hbtn, text="Open on Binance", command=self.history_open_binance).pack(side="left", padx=6)
        ttk.Button(hbtn, text="Close all popups", command=popups.close_all).pack(side="right")
        ttk.Button(hbtn, text="Alerts log", command=self.open_alerts_log).pack(side="right", padx=6)
        self.history_status = tk.StringVar(
            value="Double-click an alert: open in Vataga.  \"Chart\": mini chart (mouse wheel scrolls).  \"Copy\": copy the ticker."
        )
        ttk.Label(root, textvariable=self.history_status, wraplength=600).pack(anchor="w", pady=(4, 4))
        self._load_history()

        ttk.Label(root, text="Log", font=BOLD).pack(anchor="w")
        self.log_widget = scrolledtext.ScrolledText(root, height=5, state="disabled", wrap="word")
        self.log_widget.pack(fill="x")

    # ------------------------------------------------------------- history

    _ALERT_RE = re.compile(r"^(\S+ \S+)\s+ALERT (\S+) ([+-][\d.]+%) in (\d+) min, price ([^,\s]+)")

    def _load_history(self) -> None:
        """Show alerts from previous runs (read from alerts.log)."""
        try:
            lines = alerts_log_path().read_text(encoding="utf-8").splitlines()[-300:]
        except OSError:
            return
        for line in lines:
            m = self._ALERT_RE.match(line)
            if m:
                stamp, symbol, change, minutes, price = m.groups()
                self._add_history(stamp[11:], symbol, change, f"in {minutes} min, price {price}")

    def _add_history(self, stamp: str, symbol: str, change: str, details: str) -> None:
        self.history.insert("", 0, values=(stamp, symbol, self.CHART_TEXT, self.COPY_TEXT, change, details))
        for old in self.history.get_children()[500:]:
            self.history.delete(old)

    def _column(self, event) -> str:
        names = [c[0] for c in self.HISTORY_COLUMNS]
        try:
            return names[int(self.history.identify_column(event.x)[1:]) - 1]
        except (ValueError, IndexError):
            return ""

    def _history_click(self, event) -> str | None:
        """A click on the 'Chart' / 'Copy' cell acts on the coin of that row."""
        if self.history.identify_region(event.x, event.y) != "cell":
            return None
        item = self.history.identify_row(event.y)
        column = self._column(event)
        if not item or column not in ("chart", "copy"):
            return None
        symbol = str(self.history.set(item, "ticker"))
        self.history.selection_set(item)
        (self.open_chart if column == "chart" else self.copy_symbol)(symbol)
        return "break"

    def _history_double_click(self, event) -> None:
        if self._column(event) in ("chart", "copy"):
            return
        self.history_open_vataga()

    def _selected_symbol(self) -> str | None:
        selection = self.history.selection()
        if not selection:
            self.history_status.set("Select an alert in the list first.")
            return None
        return str(self.history.set(selection[0], "ticker"))

    def history_open_vataga(self) -> None:
        symbol = self._selected_symbol()
        if symbol is None:
            return
        self.open_symbol(symbol)

    def history_open_binance(self) -> None:
        symbol = self._selected_symbol()
        if symbol is not None:
            webbrowser.open(f"https://www.binance.com/en/futures/{symbol}")

    # ------------------------------------------------------------- actions

    def open_settings(self) -> None:
        SettingsDialog(self)

    def open_symbol(self, symbol: str) -> None:
        self.history_status.set(f"Sending {symbol} to Vataga.terminal...")
        settings = load_settings()

        def work() -> None:
            ok, message = vataga.open_symbol(symbol, settings)
            self.messages.put(("hstatus", f"{symbol} opened in Vataga.terminal" if ok else message))

        threading.Thread(target=work, daemon=True).start()

    def open_chart(self, symbol: str) -> None:
        window = self.charts.get(symbol)
        if window is not None:
            try:
                window.lift()
                return
            except tk.TclError:
                self.charts.pop(symbol, None)
        self.charts[symbol] = ChartWindow(self, symbol)

    def copy_symbol(self, symbol: str) -> None:
        """Put the ticker (e.g. INITUSDT) on the clipboard."""
        try:
            self.clipboard_clear()
            self.clipboard_append(symbol)
            self.update_idletasks()
            self.history_status.set(f"{symbol} copied to the clipboard")
        except tk.TclError as exc:
            self.history_status.set(f"Could not copy {symbol}: {exc}")

    def start_monitoring(self) -> None:
        if self.monitor is not None and self.monitor.is_alive():
            return
        self.monitor = Monitor(
            api=self.api,
            load_settings=load_settings,
            notify=lambda symbol, change, price, window, ratio=None: notifier.show_alert(
                symbol, change, price, window, sound=load_settings().sound_enabled, volume_ratio=ratio
            ),
            log=lambda text: self.messages.put(("log", text)),
            set_status=lambda text: self.messages.put(("status", text)),
            warn=lambda title, text: notifier.show_warning(title, text, sound=load_settings().sound_enabled),
        )
        if load_settings().prevent_sleep:
            self.sleep_guard.start()
        self.monitor.start()
        self.start_button.config(state="disabled")
        self.stop_button.config(state="normal")
        self.status_var.set("Starting...")

    def stop_monitoring(self) -> None:
        if self.monitor is not None:
            self.monitor.stop()
        self.sleep_guard.stop()
        self.stop_button.config(state="disabled")

    def test_notification(self) -> None:
        try:
            notifier.show_test(sound=load_settings().sound_enabled)
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Could not show notification: {exc}")

    def open_alerts_log(self) -> None:
        """Open alerts.log (every alert with time, coin, growth and price) in the default text editor."""
        path = alerts_log_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch(exist_ok=True)
            open_file(path)
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"Could not open {path}: {exc}")

    # ---------------------------------------------------------------- misc

    def add_log(self, text: str) -> None:
        self.log_widget.config(state="normal")
        self.log_widget.insert("end", f"{time.strftime('%H:%M:%S')}  {text}\n")
        lines = int(self.log_widget.index("end-1c").split(".")[0])
        if lines > MAX_LOG_LINES:
            self.log_widget.delete("1.0", f"{lines - MAX_LOG_LINES + 1}.0")
        self.log_widget.see("end")
        self.log_widget.config(state="disabled")

    def _drain_messages(self) -> None:
        """Move messages from the monitor thread into the UI (runs on the UI thread).
        One bad message must never stop the loop, otherwise the whole UI stops updating."""
        while True:
            try:
                kind, text = self.messages.get_nowait()
            except queue.Empty:
                break
            try:
                if kind == "log":
                    self.add_log(text)
                elif kind == "popup":
                    symbol, title, message, direction = text
                    try:
                        if symbol:  # warnings (no coin) are shown as a popup only
                            change = title.split()[-1] if title.split() else ""
                            self._add_history(time.strftime("%H:%M:%S"), symbol, change, message.replace("\n", " | "))
                    finally:
                        popups.show_popup(self, symbol, title, message, direction)
                elif kind == "hstatus":
                    self.history_status.set(text)
                else:
                    self.status_var.set(text)
            except Exception as exc:  # keep going; the problem is recorded in the log
                try:
                    self.add_log(f"UI error ({kind}): {exc}")
                except Exception:
                    pass

        if self.monitor is not None and not self.monitor.is_alive():
            self.monitor = None
            self.sleep_guard.stop()
            self.start_button.config(state="normal")
            self.stop_button.config(state="disabled")
        self.after(200, self._drain_messages)

    def _on_close(self) -> None:
        monitor = self.monitor
        if monitor is not None:
            monitor.stop()
            monitor.join(timeout=2.0)   # let it finish, so that nothing is written to the log after it is emptied
        self.sleep_guard.stop()
        if load_settings().clear_alerts_log_on_exit:
            clear_alerts_log()
        self.destroy()


def main() -> None:
    App().mainloop()
