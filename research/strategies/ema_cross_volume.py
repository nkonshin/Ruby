"""
EMA fast/slow cross + объёмное подтверждение (5m).

Логика:
  • EMA(9) пересекает EMA(21) снизу-вверх + объём свечи > 1.3×SMA(volume, 20) → long
  • Зеркально вниз → short
  • Без фильтра тренда (на 5m тренд сам собой возникает)

Самая «классическая» скальп-стратегия. Хорошо ловит начало движений на альтах.
"""
from __future__ import annotations
import pandas as pd
import ta


def generate_signals(df: pd.DataFrame, *, fast: int = 9, slow: int = 21, vol_mult: float = 1.3) -> pd.Series:
    signals = pd.Series(0, index=df.index, dtype=int)
    ema_fast = ta.trend.EMAIndicator(df["close"], window=fast).ema_indicator()
    ema_slow = ta.trend.EMAIndicator(df["close"], window=slow).ema_indicator()
    vol_sma = df["volume"].rolling(20).mean()

    # Cross detection
    cross_up = (ema_fast.shift(1) <= ema_slow.shift(1)) & (ema_fast > ema_slow)
    cross_dn = (ema_fast.shift(1) >= ema_slow.shift(1)) & (ema_fast < ema_slow)
    vol_ok = df["volume"] > vol_mult * vol_sma

    signals[cross_up & vol_ok] = 1
    signals[cross_dn & vol_ok] = -1
    return signals
