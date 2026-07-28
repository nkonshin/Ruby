"""
VWAP-reversion (5m).

Логика:
  • Каждый день строим VWAP с UTC-00:00
  • Цена ушла > deviation_pct % от VWAP вниз + RSI < 30 → long (отбой)
  • Цена ушла > deviation_pct % выше VWAP + RSI > 70 → short (откат)
  • Mean-reversion — для боковых монет (LINK, AAVE, ATOM)

Когда рынок чисто трендовый — стратегия будет терять; это и проверим.
"""
from __future__ import annotations
import pandas as pd
import ta


def _vwap_daily(df: pd.DataFrame) -> pd.Series:
    """VWAP, ресетящийся в начале каждого UTC-дня."""
    typical = (df["high"] + df["low"] + df["close"]) / 3
    pv = typical * df["volume"]
    date = df["open_time"].dt.date
    cum_pv = pv.groupby(date).cumsum()
    cum_v = df["volume"].groupby(date).cumsum()
    return cum_pv / cum_v


def generate_signals(df: pd.DataFrame, *, deviation_pct: float = 0.5, rsi_filter: bool = True) -> pd.Series:
    signals = pd.Series(0, index=df.index, dtype=int)
    vwap = _vwap_daily(df)
    dev_pct = (df["close"] - vwap) / vwap * 100

    rsi = ta.momentum.RSIIndicator(df["close"], window=14).rsi()

    long_cond = dev_pct < -deviation_pct
    short_cond = dev_pct > deviation_pct
    if rsi_filter:
        long_cond &= rsi < 30
        short_cond &= rsi > 70

    signals[long_cond] = 1
    signals[short_cond] = -1
    return signals
