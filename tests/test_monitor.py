import unittest

from binance_pump_alerts.monitor import Monitor
from binance_pump_alerts.price_tracker import PriceTracker
from binance_pump_alerts.settings import Settings


class FakeApi:
    def __init__(self):
        self.price_map = {"BTCUSDT": 100.0, "ETHUSDT": 100.0, "XRPUSDT": 100.0}

    def usdt_perpetual_symbols(self):
        return {"BTCUSDT", "ETHUSDT", "XRPUSDT"}

    def quote_volumes(self):
        # BTC and ETH pass the 30M filter, XRP does not
        return {"BTCUSDT": 40e6, "ETHUSDT": 31e6, "XRPUSDT": 5e6}

    def prices(self):
        return dict(self.price_map)


class PriceTrackerTest(unittest.TestCase):
    def test_change_percent_and_window(self):
        tracker = PriceTracker()
        tracker.add("A", 100.0, now=0, window_sec=300)
        self.assertIsNone(tracker.change_percent("A"))  # one sample is not enough
        tracker.add("A", 105.0, now=100, window_sec=300)
        self.assertAlmostEqual(tracker.change_percent("A"), 5.0)
        tracker.add("A", 105.0, now=500, window_sec=300)  # first two samples fall out of the window
        self.assertIsNone(tracker.change_percent("A"))    # only one sample left in the window
        tracker.add("A", 105.0, now=510, window_sec=300)
        self.assertAlmostEqual(tracker.change_percent("A"), 0.0)

    def test_retain_only(self):
        tracker = PriceTracker()
        tracker.add("A", 1.0, 0, 300)
        tracker.add("B", 1.0, 0, 300)
        tracker.retain_only({"A"})
        tracker.add("B", 2.0, 1, 300)
        self.assertIsNone(tracker.change_percent("B"))  # B history was dropped


class MonitorTest(unittest.TestCase):
    def make_monitor(self, api, alerts, settings):
        return Monitor(
            api=api,
            load_settings=lambda: settings,
            notify=lambda symbol, change, price, window: alerts.append((symbol, round(change, 2), price)),
            log=lambda text: None,
            set_status=lambda text: None,
        )

    def test_alert_only_for_high_volume_pumping_coin(self):
        api, alerts, settings = FakeApi(), [], Settings()
        monitor = self.make_monitor(api, alerts, settings)

        monitor.tick(settings, now=1000)            # baseline prices
        api.price_map["BTCUSDT"] = 104.5            # +4.5% -> alert
        api.price_map["ETHUSDT"] = 103.0            # +3% -> no alert
        api.price_map["XRPUSDT"] = 150.0            # +50% but volume too low -> no alert
        monitor.tick(settings, now=1060)

        self.assertEqual(alerts, [("BTCUSDT", 4.5, 104.5)])

    def test_cooldown_prevents_repeated_alerts(self):
        api, alerts, settings = FakeApi(), [], Settings(cooldown_minutes=15)
        monitor = self.make_monitor(api, alerts, settings)

        monitor.tick(settings, now=1000)
        api.price_map["BTCUSDT"] = 105.0
        monitor.tick(settings, now=1015)            # alert
        api.price_map["BTCUSDT"] = 106.0
        monitor.tick(settings, now=1030)            # still pumping, but in cooldown
        self.assertEqual(len(alerts), 1)

        api.price_map["BTCUSDT"] = 110.0
        monitor.tick(settings, now=1900)            # new baseline, cooldown (until 1915) not over yet
        api.price_map["BTCUSDT"] = 120.0
        monitor.tick(settings, now=1916)            # +9% and cooldown is over -> second alert
        self.assertEqual(len(alerts), 2)

    def test_volume_setting_is_respected(self):
        api, alerts = FakeApi(), []
        settings = Settings(min_volume_million_usdt=1.0)  # now XRP passes too
        monitor = self.make_monitor(api, alerts, settings)

        monitor.tick(settings, now=1000)
        api.price_map["XRPUSDT"] = 150.0
        monitor.tick(settings, now=1015)
        self.assertEqual([a[0] for a in alerts], ["XRPUSDT"])


if __name__ == "__main__":
    unittest.main()


class VatagaTest(unittest.TestCase):
    def test_api_url_and_link(self):
        from binance_pump_alerts import vataga
        from binance_pump_alerts.settings import Settings
        self.assertEqual(
            vataga.build_api_url(Settings(), "BTCUSDT"),
            "http://localhost:5005/api/open/BinancePumpAlerts/binance/F/BTCUSDT",
        )


class FiltersTest(unittest.TestCase):
    def test_volume_ratio(self):
        from binance_pump_alerts.volume_spike import volume_ratio
        # 60 quiet minutes at 100, then 5 minutes at 300 -> x3 of the usual 5-minute volume
        candles = [100.0] * 60 + [300.0] * 5
        self.assertAlmostEqual(volume_ratio(candles, 5), 3.0)
        self.assertIsNone(volume_ratio([100.0] * 6, 5))  # not enough history
        self.assertIsNone(volume_ratio([0.0] * 20, 5))   # zero baseline

    def test_spike_filter_skips_then_allows(self):
        class SpikeApi(FakeApi):
            def __init__(self):
                super().__init__()
                self.candles = [100.0] * 60 + [110.0] * 5  # no spike

            def recent_quote_volumes(self, symbol, minutes):
                return self.candles

        api, alerts = SpikeApi(), []
        settings = Settings(volume_spike_ratio=2.0)
        monitor = Monitor(api, lambda: settings, lambda *a: alerts.append(a), lambda t: None, lambda t: None)
        monitor.tick(settings, now=1000)
        api.price_map["BTCUSDT"] = 105.0
        monitor.tick(settings, now=1060)
        self.assertEqual(alerts, [])                      # +5% but volume only x1.1 -> skipped
        api.candles = [100.0] * 60 + [400.0] * 5          # spike x4
        monitor.tick(settings, now=1100)                  # cache expired -> re-check -> alert
        self.assertEqual(len(alerts), 1)
        self.assertAlmostEqual(alerts[0][4], 4.0)

    def test_drop_mode(self):
        api, alerts = FakeApi(), []
        settings = Settings(alert_direction="down")
        monitor = Monitor(api, lambda: settings, lambda *a: alerts.append(a), lambda t: None, lambda t: None)
        monitor.tick(settings, now=1000)
        api.price_map["BTCUSDT"] = 105.0                  # growth: ignored in "down" mode
        api.price_map["ETHUSDT"] = 95.0                   # -5%: alert
        monitor.tick(settings, now=1060)
        self.assertEqual([(a[0], round(a[1], 1)) for a in alerts], [("ETHUSDT", -5.0)])

    def test_both_mode(self):
        api, alerts = FakeApi(), []
        settings = Settings(alert_direction="both")
        monitor = Monitor(api, lambda: settings, lambda *a: alerts.append(a), lambda t: None, lambda t: None)
        monitor.tick(settings, now=1000)
        api.price_map["BTCUSDT"] = 105.0
        api.price_map["ETHUSDT"] = 95.0
        monitor.tick(settings, now=1060)
        self.assertEqual(sorted(a[0] for a in alerts), ["BTCUSDT", "ETHUSDT"])

    def test_connection_lost_and_restored(self):
        warnings = []
        api, settings = FakeApi(), Settings(stale_warning_sec=60)
        monitor = Monitor(api, lambda: settings, lambda *a: None, lambda t: None, lambda t: None,
                          warn=lambda title, text: warnings.append(title))
        monitor._last_ok = 1000
        monitor.check_connection(settings, 1030)
        self.assertEqual(warnings, [])
        monitor.check_connection(settings, 1065)
        monitor.check_connection(settings, 1100)          # only one warning
        self.assertEqual(warnings, ["Connection lost"])
        monitor._last_ok = 1100
        monitor.check_connection(settings, 1101)
        self.assertEqual(warnings, ["Connection lost", "Connection restored"])
