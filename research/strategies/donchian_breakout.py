"""
Donchian Channel breakout (15m).

Логика:
  • Считаем high/low за последние N свечей (например 20 = 5 часов на 15m)
  • Пробой high → long, пробой low → short
  • Фильтр: ADX > порог, чтобы не торговать в флэте

Самая «дубовая» turtle-стратегия. Хорошо ловит выходы из консолидации.
"""
from __future__ import annotations
import pandas as pd
import ta


def generate_signals(df: pd.DataFrame, *, lookback: int = 20, adx_min: float = 22) -> pd.Series:
    signals = pd.Series(0, index=df.index, dtype=int)

    rolling_high = df["high"].shift(1).rolling(lookback).max()
    rolling_low = df["low"].shift(1).rolling(lookback).min()

    adx = ta.trend.ADXIndicator(df["high"], df["low"], df["close"], window=14).adx()

    long_cond = (df["close"] > rolling_high) & (adx >= adx_min)
    short_cond = (df["close"] < rolling_low) & (adx >= adx_min)

    signals[long_cond] = 1
    signals[short_cond] = -1
    return signals
