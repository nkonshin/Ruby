"""
Scalp EMA Cross + Volume (15m) — production-версия.

Логика:
  • EMA(9) пересекает EMA(21) снизу-вверх + объём > 1.5 × SMA(volume, 20) → BUY
  • Зеркально вниз → SELL
  • SL/TP по ATR: SL = 2.5×ATR, TP = 4×ATR (для LINK), или 2.0/4.0 (для ATOM)
  • Session-фильтр: 06–22 UTC

Из исследования: walk-forward ROBUST на ATOM (+21% OOS/15д), LINK (+10%).
"""
from __future__ import annotations

import pandas as pd
import ta

from strategies.base import BaseStrategy, Signal, SignalType


class ScalpEmaVolumeStrategy(BaseStrategy):
    name = "scalp_ema_vol"
    description = "EMA Cross + Volume 15m"
    timeframe = "15m"
    min_candles = 50
    risk_category = "aggressive"

    def __init__(self,
                 fast: int = 9,
                 slow: int = 21,
                 vol_mult: float = 1.5,
                 sl_atr_mult: float = 2.5,
                 tp_atr_mult: float = 4.0,
                 session_start_utc: int = 6,
                 session_end_utc: int = 22):
        self.fast = fast
        self.slow = slow
        self.vol_mult = vol_mult
        self.sl_atr_mult = sl_atr_mult
        self.tp_atr_mult = tp_atr_mult
        self.session_start = session_start_utc
        self.session_end = session_end_utc

    def analyze(self, df: pd.DataFrame, symbol: str) -> Signal:
        if len(df) < self.min_candles:
            return Signal(type=SignalType.HOLD, symbol=symbol, strategy=self.name,
                          reason="недостаточно данных")

        ema_fast = ta.trend.EMAIndicator(df["close"], window=self.fast).ema_indicator()
        ema_slow = ta.trend.EMAIndicator(df["close"], window=self.slow).ema_indicator()
        vol_sma = df["volume"].rolling(20).mean()
        atr = ta.volatility.AverageTrueRange(df["high"], df["low"], df["close"],
                                              window=14).average_true_range()

        last = df.iloc[-1]
        ef_now, ef_prev = float(ema_fast.iloc[-1]), float(ema_fast.iloc[-2])
        es_now, es_prev = float(ema_slow.iloc[-1]), float(ema_slow.iloc[-2])
        vol_now = float(last["volume"])
        vol_avg = float(vol_sma.iloc[-1]) if not pd.isna(vol_sma.iloc[-1]) else 0
        cur_atr = float(atr.iloc[-1])
        price = float(last["close"])

        # Session фильтр
        ts = last.get("timestamp")
        if ts is not None and not pd.isna(ts):
            hour = pd.Timestamp(ts).hour
            if not (self.session_start <= hour < self.session_end):
                return Signal(type=SignalType.HOLD, symbol=symbol, strategy=self.name,
                              reason=f"вне сессии (UTC {hour})")

        if cur_atr <= 0 or vol_avg == 0:
            return Signal(type=SignalType.HOLD, symbol=symbol, strategy=self.name,
                          reason="индикаторы не готовы")

        cross_up = ef_prev <= es_prev and ef_now > es_now
        cross_dn = ef_prev >= es_prev and ef_now < es_now
        vol_ok = vol_now > self.vol_mult * vol_avg

        sl_pct = (self.sl_atr_mult * cur_atr / price) * 100
        tp_pct = (self.tp_atr_mult * cur_atr / price) * 100

        indicators = {
            "ema_fast": round(ef_now, 4), "ema_slow": round(es_now, 4),
            "vol_ratio": round(vol_now / vol_avg, 2),
            "atr_pct": round(cur_atr / price * 100, 2),
        }

        if cross_up and vol_ok:
            return Signal(
                type=SignalType.BUY, price=price, symbol=symbol, strategy=self.name,
                reason=f"EMA{self.fast}/{self.slow} cross↑ + vol×{vol_now/vol_avg:.1f}",
                custom_sl_pct=sl_pct, custom_tp_pct=tp_pct, indicators=indicators,
            )
        if cross_dn and vol_ok:
            return Signal(
                type=SignalType.SELL, price=price, symbol=symbol, strategy=self.name,
                reason=f"EMA{self.fast}/{self.slow} cross↓ + vol×{vol_now/vol_avg:.1f}",
                custom_sl_pct=sl_pct, custom_tp_pct=tp_pct, indicators=indicators,
            )
        return Signal(type=SignalType.HOLD, symbol=symbol, strategy=self.name,
                      reason="нет cross или объём слабый", indicators=indicators)
