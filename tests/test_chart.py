import os
import tempfile
import unittest
from pathlib import Path

from binance_pump_alerts import settings as st
from binance_pump_alerts.binance_api import Candle, parse_candle
from binance_pump_alerts.chart import MAX_VIEW, MIN_VIEW, build_scene, clamp_offset, clamp_view, visible


def candles(n, start=100.0):
    return [Candle(i * 60_000, start + i, start + i + 2, start + i - 1, start + i + 1) for i in range(n)]


class ChartTest(unittest.TestCase):
    def test_parse_candle(self):
        raw = [1700000000000, "1.0", "2.0", "0.5", "1.5", "10", 1700000059999, "15.0", 7]
        self.assertEqual(parse_candle(raw), Candle(1700000000000, 1.0, 2.0, 0.5, 1.5))

    def test_view_is_clamped(self):
        self.assertEqual(clamp_view(5, 1000), MIN_VIEW)
        self.assertEqual(clamp_view(5000, 1000), MAX_VIEW)
        self.assertEqual(clamp_view(120, 50), 50)          # never more than there are candles
        self.assertEqual(clamp_view(120, 10), 10)

    def test_offset_is_clamped(self):
        self.assertEqual(clamp_offset(-5, 1000, 100), 0)
        self.assertEqual(clamp_offset(5000, 1000, 100), 900)
        self.assertEqual(clamp_offset(50, 1000, 100), 50)

    def test_visible_slice_follows_the_offset(self):
        data = candles(300)
        newest = visible(data, 100, 0)
        self.assertEqual(len(newest), 100)
        self.assertEqual(newest[-1], data[-1])
        older = visible(data, 100, 40)                      # scrolled back by 40 candles
        self.assertEqual(older[-1], data[-41])
        self.assertEqual(visible(data, 100, 10 ** 9)[0], data[0])   # scrolled to the oldest

    def test_scene_fits_the_shown_candles(self):
        data = candles(200)
        scene_new = build_scene(visible(data, 50, 0), data[-1].close, 800, 400)
        scene_old = build_scene(visible(data, 50, 100), data[-1].close, 800, 400)
        self.assertEqual(len(scene_new.candles), 50)
        self.assertGreater(scene_new.low, scene_old.high)    # the vertical range follows what is shown
        self.assertIsNone(scene_old.price_y)                 # the last price is outside the old range
        self.assertIsNotNone(scene_new.price_y)
        xs = [c[0] for c in scene_new.candles]
        self.assertEqual(xs, sorted(xs))
        self.assertEqual(len(scene_new.ticks), 5)

    def test_empty_scene(self):
        self.assertEqual(build_scene([], None, 800, 400).candles, [])


class AlertsLogTest(unittest.TestCase):
    def test_clear_alerts_log(self):
        with tempfile.TemporaryDirectory() as d:
            original = st.settings_path
            st.settings_path = lambda: Path(d) / "settings.json"
            try:
                path = st.alerts_log_path()
                path.write_text("ALERT something\n", encoding="utf-8")
                st.clear_alerts_log()
                self.assertEqual(path.read_text(encoding="utf-8"), "")
                st.clear_alerts_log()      # no error when it is already empty
                path.unlink()
                st.clear_alerts_log()      # ... or missing
            finally:
                st.settings_path = original

    def test_defaults(self):
        self.assertTrue(st.Settings().clear_alerts_log_on_exit)


if __name__ == "__main__":
    unittest.main()
