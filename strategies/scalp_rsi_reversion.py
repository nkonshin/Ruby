"""
Scalp RSI Mean Reversion (15m) — production-версия.

Логика:
  • RSI(14) на предыдущей свече < oversold (по умолчанию 25) и текущая свеча зелёная (close > open) → BUY
  • RSI на предыдущей < перевёрнуто: > overbought (75) + красная свеча → SELL
  • SL/TP по ATR_14: SL = 2.5×ATR, TP = 4×ATR (по бэктесту — лучшая комбинация на PEPE/ATOM/RENDER)
  • Session-фильтр: только 06–22 UTC

Из исследования: walk-forward ROBUST на PEPE (+35% OOS/15д), ATOM (+46%), RENDER (+19%).
"""
from __future__ import annotations

import pandas as pd
import ta

from strategies.base import BaseStrategy, Signal, SignalType


class ScalpRsiReversionStrategy(BaseStrategy):
    name = "scalp_rsi_mr"
    description = "RSI Mean Reversion 15m с подтверждением reversal-свечой"
    timeframe = "15m"
    min_candles = 30
    risk_category = "aggressive"

    def __init__(self,
                 oversold: int = 25,
                 overbought: int = 75,
                 sl_atr_mult: float = 2.5,
                 tp_atr_mult: float = 4.0,
                 session_start_utc: int = 6,
                 session_end_utc: int = 22):
        self.oversold = oversold
        self.overbought = overbought
        self.sl_atr_mult = sl_atr_mult
        self.tp_atr_mult = tp_atr_mult
        self.session_start = session_start_utc
        self.session_end = session_end_utc

    def analyze(self, df: pd.DataFrame, symbol: str) -> Signal:
        if len(df) < self.min_candles:
            return Signal(type=SignalType.HOLD, symbol=symbol, strategy=self.name,
                          reason="недостаточно данных")

        rsi = ta.momentum.RSIIndicator(df["close"], window=14).rsi()
        atr = ta.volatility.AverageTrueRange(df["high"], df["low"], df["close"],
                                              window=14).average_true_range()

        last = df.iloc[-1]
        prev_rsi = float(rsi.iloc[-2]) if not pd.isna(rsi.iloc[-2]) else 50.0
        cur_atr = float(atr.iloc[-1])
        price = float(last["close"])

        # Session-фильтр (берём время свечи если есть timestamp)
        ts = last.get("timestamp")
        if ts is not None and not pd.isna(ts):
            hour = pd.Timestamp(ts).hour
            if not (self.session_start <= hour < self.session_end):
                return Signal(type=SignalType.HOLD, symbol=symbol, strategy=self.name,
                              reason=f"вне торговой сессии (UTC {hour})",
                              indicators={"rsi_prev": prev_rsi, "hour_utc": hour})

        is_bull = last["close"] > last["open"]
        is_bear = last["close"] < last["open"]

        if cur_atr <= 0:
            return Signal(type=SignalType.HOLD, symbol=symbol, strategy=self.name,
                          reason="ATR=0")
        sl_pct = (self.sl_atr_mult * cur_atr / price) * 100
        tp_pct = (self.tp_atr_mult * cur_atr / price) * 100

        indicators = {
            "rsi_prev": round(prev_rsi, 1),
            "atr_pct": round(cur_atr / price * 100, 2),
            "sl_pct": round(sl_pct, 2),
            "tp_pct": round(tp_pct, 2),
        }

        if prev_rsi < self.oversold and is_bull:
            return Signal(
                type=SignalType.BUY, price=price, symbol=symbol, strategy=self.name,
                strength=min(1.0, (self.oversold - prev_rsi) / 10),
                reason=f"RSI prev={prev_rsi:.1f} < {self.oversold}, bullish reversal",
                custom_sl_pct=sl_pct, custom_tp_pct=tp_pct, indicators=indicators,
            )
        if prev_rsi > self.overbought and is_bear:
            return Signal(
                type=SignalType.SELL, price=price, symbol=symbol, strategy=self.name,
                strength=min(1.0, (prev_rsi - self.overbought) / 10),
                reason=f"RSI prev={prev_rsi:.1f} > {self.overbought}, bearish reversal",
                custom_sl_pct=sl_pct, custom_tp_pct=tp_pct, indicators=indicators,
            )

        return Signal(type=SignalType.HOLD, symbol=symbol, strategy=self.name,
                      reason="нет сигнала", indicators=indicators)
