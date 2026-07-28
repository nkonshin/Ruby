"""
RSI Mean Reversion с подтверждением (15m).

Логика:
  • RSI(14) < oversold (например 25) — кандидат на long
  • Подтверждение: следующая свеча закрывается зелёной (bullish reversal candle)
  • Зеркально для short

Mean-reversion стратегия, но с фильтром «не ловить падающий нож».
"""
from __future__ import annotations
import pandas as pd
import ta


def generate_signals(df: pd.DataFrame, *, oversold: int = 25, overbought: int = 75) -> pd.Series:
    signals = pd.Series(0, index=df.index, dtype=int)
    rsi = ta.momentum.RSIIndicator(df["close"], window=14).rsi()

    is_bull = df["close"] > df["open"]
    is_bear = df["close"] < df["open"]

    rsi_prev = rsi.shift(1)
    long_cond = (rsi_prev < oversold) & is_bull
    short_cond = (rsi_prev > overbought) & is_bear

    signals[long_cond] = 1
    signals[short_cond] = -1
    return signals
