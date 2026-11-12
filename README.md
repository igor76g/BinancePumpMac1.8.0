# Binance Pump Alerts 1.8.0 for macOS

Port of the Windows app. Monitors all Binance USDT-M perpetual futures (public endpoints only, no API keys).

- Alert when the price moves >= 4 % within 5 min (Growth / Drop / Both), 24h volume > 30M USDT (all editable).
- Optional trade-volume spike filter (volume x N of the usual; 0 = off), cooldown per coin, connection-lost warning,
  REST polling only (low traffic).
- Alerts: sound (Glass), always-on-top popup with buttons, macOS banner, alerts.log.

## Window
Compact: **Start monitoring / Stop / Settings / Test notification**, the list of recent alerts and a small log.
All settings are behind the **Settings** button (tabs Filters and General).
Every alert row (and every popup) has **Chart** and **Copy**:
- **Copy** puts the ticker (e.g. INITUSDT) on the clipboard.
- **Chart** opens a mini chart (candles 1m / 5m / 15m / 1h, 1000 candles loaded, the newest refreshed every 10 s).
  **Mouse / trackpad scroll moves through history**, Ctrl + scroll zooms, Home / End jump to the oldest / newest candles,
  "Newest" returns to the latest candles.
- Double-click an alert: open the coin in Vataga.terminal (Mac version runs on the same Mac; partner id
  `BinancePumpAlerts`, port 5005; Vataga: Settings > General > "Application integration" on, window linked to "Screener").

The alerts log is **emptied when the app is closed** (Settings > General, on by default), so the recent-alerts list starts
empty after a restart.

## Build (GitHub Actions)
Upload the folder (with hidden `.github`) to GitHub -> Actions -> "Build macOS app" -> download
`BinancePumpAlerts-mac-apple-silicon` (M1-M4) or `BinancePumpAlerts-mac-intel`. Open the DMG, drag the app to Applications.
Local build: `./build.sh`. From source: `pip install -r requirements.txt && python3 run.py`.

## First launch
Not notarized: right-click -> Open, or `xattr -dr com.apple.quarantine /Applications/BinancePumpAlerts.app`.

## Notifications
Banners are sent through `osascript` and appear as "Script Editor": allow them in System Settings -> Notifications.
Popups and sound work regardless. Cmd+Q quits the app.

## Other
- "Keep the Mac awake while monitoring" uses `caffeinate` (closing the lid still sleeps the Mac).
- Data: `~/Library/Application Support/BinancePumpAlerts/` (settings.json, alerts.log).
- Not built or tested on a real Mac by the author - please report any issue.
