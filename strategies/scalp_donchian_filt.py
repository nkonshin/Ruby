"""
Scalp Donchian Channel Breakout с ADX-фильтром (15m) — production-версия.

Логика:
  • Пробой high за последние lookback свечей + ADX > порог → BUY
  • Зеркально пробой low → SELL
  • SL/TP по ATR: 2.5×ATR / 3×ATR (для TON оптимально)
  • Session-фильтр: 06–22 UTC

Из исследования: walk-forward ROBUST на TON (+10% OOS/15д).
"""
from __future__ import annotations

import pandas as pd
import ta

from strategies.base import BaseStrategy, Signal, SignalType


class ScalpDonchianFiltStrategy(BaseStrategy):
    name = "scalp_donchian_filt"
    description = "Donchian Breakout + ADX 15m"
    timeframe = "15m"
    min_candles = 50
    risk_category = "aggressive"

    def __init__(self,
                 lookback: int = 20,
                 adx_min: float = 25,
                 sl_atr_mult: float = 2.5,
                 tp_atr_mult: float = 3.0,
                 session_start_utc: int = 6,
                 session_end_utc: int = 22):
        self.lookback = lookback
        self.adx_min = adx_min
        self.sl_atr_mult = sl_atr_mult
        self.tp_atr_mult = tp_atr_mult
        self.session_start = session_start_utc
        self.session_end = session_end_utc

    def analyze(self, df: pd.DataFrame, symbol: str) -> Signal:
        if len(df) < self.min_candles:
            return Signal(type=SignalType.HOLD, symbol=symbol, strategy=self.name,
                          reason="недостаточно данных")

        # Donchian (shift(1) — exclude current bar)
        rolling_high = df["high"].shift(1).rolling(self.lookback).max()
        rolling_low = df["low"].shift(1).rolling(self.lookback).min()

        adx = ta.trend.ADXIndicator(df["high"], df["low"], df["close"], window=14).adx()
        atr = ta.volatility.AverageTrueRange(df["high"], df["low"], df["close"],
                                              window=14).average_true_range()

        last = df.iloc[-1]
        rh = float(rolling_high.iloc[-1]) if not pd.isna(rolling_high.iloc[-1]) else None
        rl = float(rolling_low.iloc[-1]) if not pd.isna(rolling_low.iloc[-1]) else None
        adx_now = float(adx.iloc[-1]) if not pd.isna(adx.iloc[-1]) else 0
        cur_atr = float(atr.iloc[-1])
        price = float(last["close"])

        ts = last.get("timestamp")
        if ts is not None and not pd.isna(ts):
            hour = pd.Timestamp(ts).hour
            if not (self.session_start <= hour < self.session_end):
                return Signal(type=SignalType.HOLD, symbol=symbol, strategy=self.name,
                              reason=f"вне сессии (UTC {hour})")

        if rh is None or rl is None or cur_atr <= 0:
            return Signal(type=SignalType.HOLD, symbol=symbol, strategy=self.name,
                          reason="индикаторы не готовы")

        sl_pct = (self.sl_atr_mult * cur_atr / price) * 100
        tp_pct = (self.tp_atr_mult * cur_atr / price) * 100

        indicators = {
            "donchian_high": round(rh, 4),
            "donchian_low": round(rl, 4),
            "adx": round(adx_now, 1),
            "atr_pct": round(cur_atr / price * 100, 2),
        }

        if price > rh and adx_now >= self.adx_min:
            return Signal(
                type=SignalType.BUY, price=price, symbol=symbol, strategy=self.name,
                reason=f"Donchian breakout↑ (high={rh:.4f}), ADX={adx_now:.1f}",
                custom_sl_pct=sl_pct, custom_tp_pct=tp_pct, indicators=indicators,
            )
        if price < rl and adx_now >= self.adx_min:
            return Signal(
                type=SignalType.SELL, price=price, symbol=symbol, strategy=self.name,
                reason=f"Donchian breakout↓ (low={rl:.4f}), ADX={adx_now:.1f}",
                custom_sl_pct=sl_pct, custom_tp_pct=tp_pct, indicators=indicators,
            )
        return Signal(type=SignalType.HOLD, symbol=symbol, strategy=self.name,
                      reason="нет пробоя", indicators=indicators)
