"""
Heikin Ashi Trend Follower (15m).

Логика:
  • Считаем Heikin Ashi свечи (сглаженные)
  • Сигнал long: 3 HA-свечи подряд зелёные + closes растут
  • Сигнал short: 3 HA-свечи подряд красные + closes падают
  • Идея: HA фильтрует шум обычных свечей, ловит «чистый» тренд

Простая трендовая, без множества параметров. Хорошо ловит длинные движения.
"""
from __future__ import annotations
import pandas as pd


def _heikin_ashi(df: pd.DataFrame) -> pd.DataFrame:
    ha_close = (df["open"] + df["high"] + df["low"] + df["close"]) / 4
    ha_open = pd.Series(index=df.index, dtype=float)
    ha_open.iloc[0] = (df["open"].iloc[0] + df["close"].iloc[0]) / 2
    for i in range(1, len(df)):
        ha_open.iloc[i] = (ha_open.iloc[i - 1] + ha_close.iloc[i - 1]) / 2
    ha_high = pd.concat([df["high"], ha_open, ha_close], axis=1).max(axis=1)
    ha_low = pd.concat([df["low"], ha_open, ha_close], axis=1).min(axis=1)
    return pd.DataFrame({"open": ha_open, "high": ha_high, "low": ha_low, "close": ha_close})


def generate_signals(df: pd.DataFrame, *, confirm_bars: int = 3) -> pd.Series:
    signals = pd.Series(0, index=df.index, dtype=int)
    ha = _heikin_ashi(df)

    is_green = ha["close"] > ha["open"]
    is_red = ha["close"] < ha["open"]

    # N подряд зелёных + closes растут
    streak_green = is_green.rolling(confirm_bars).sum() == confirm_bars
    streak_red = is_red.rolling(confirm_bars).sum() == confirm_bars
    rising = ha["close"] > ha["close"].shift(confirm_bars - 1)
    falling = ha["close"] < ha["close"].shift(confirm_bars - 1)

    # Сигнал только на самом первом баре после streak (не повторяющийся каждый бар)
    long_cond = streak_green & rising & ~streak_green.shift(1).fillna(False)
    short_cond = streak_red & falling & ~streak_red.shift(1).fillna(False)

    signals[long_cond] = 1
    signals[short_cond] = -1
    return signals
