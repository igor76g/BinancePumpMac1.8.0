"""Binance USDT-M Futures PUBLIC REST API client.

No API key is needed: all endpoints used here are public market data.

    GET /fapi/v1/exchangeInfo   - list of symbols
    GET /fapi/v1/ticker/24hr    - 24h statistics (quoteVolume)
    GET /fapi/v1/ticker/price   - latest prices
    GET /fapi/v1/klines         - candles (trade volume, mini chart)
"""

from __future__ import annotations

from typing import NamedTuple

import requests

BASE_URL = "https://fapi.binance.com"
REQUEST_TIMEOUT = (10, 20)  # (connect, read) seconds


CHART_INTERVALS = ["1m", "5m", "15m", "1h"]


class Candle(NamedTuple):
    open_time: int      # ms
    open: float
    high: float
    low: float
    close: float


def parse_candle(raw: list) -> Candle:
    """Binance kline array: [openTime, open, high, low, close, volume, closeTime, quoteVolume, ...]."""
    return Candle(int(raw[0]), float(raw[1]), float(raw[2]), float(raw[3]), float(raw[4]))


class BinanceApi:
    def __init__(self) -> None:
        self._session = requests.Session()

    def _get(self, path: str):
        response = self._session.get(BASE_URL + path, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        return response.json()

    def usdt_perpetual_symbols(self) -> set[str]:
        """Currently trading USDT-margined perpetual futures symbols (e.g. BTCUSDT)."""
        data = self._get("/fapi/v1/exchangeInfo")
        return {
            s["symbol"]
            for s in data.get("symbols", [])
            if s.get("quoteAsset") == "USDT"
            and s.get("contractType") == "PERPETUAL"
            and s.get("status") == "TRADING"
        }

    def quote_volumes(self) -> dict[str, float]:
        """Map of symbol -> 24h quote volume in USDT."""
        result: dict[str, float] = {}
        for item in self._get("/fapi/v1/ticker/24hr"):
            try:
                result[item["symbol"]] = float(item["quoteVolume"])
            except (KeyError, TypeError, ValueError):
                continue
        return result

    def prices(self) -> dict[str, float]:
        """Map of symbol -> last price."""
        result: dict[str, float] = {}
        for item in self._get("/fapi/v1/ticker/price"):
            try:
                result[item["symbol"]] = float(item["price"])
            except (KeyError, TypeError, ValueError):
                continue
        return result

    def recent_quote_volumes(self, symbol: str, minutes: int) -> list[float]:
        """Quote volume (USDT) of the last `minutes` one-minute candles, oldest first."""
        data = self._get(f"/fapi/v1/klines?symbol={symbol}&interval=1m&limit={min(1500, max(2, minutes))}")
        return [float(candle[7]) for candle in data]

    def klines(self, symbol: str, interval: str = "1m", limit: int = 500) -> list[Candle]:
        """Candles for the mini chart, oldest first. The last one is still forming (it moves with the price)."""
        limit = max(1, min(1000, int(limit)))   # up to 1000 candles cost the same small request weight
        data = self._get(f"/fapi/v1/klines?symbol={symbol}&interval={interval}&limit={limit}")
        return [parse_candle(raw) for raw in data]
