"""
Opening Range Breakout (5m).

Логика:
  • В начале UTC-дня (00:00) ждём первые N свечей — это initial range.
  • Запоминаем high и low этого диапазона.
  • Дальше: пробой high со spike-объёмом → long; пробой low → short.
  • После 1 сделки за день — пас, ждём следующий день.

Под мемкоины и горячие альты — момент когда вход в азиатскую/европейскую сессию даёт чистый сигнал.
"""
from __future__ import annotations
import pandas as pd


def generate_signals(df: pd.DataFrame, *, range_bars: int = 12, vol_mult: float = 1.5) -> pd.Series:
    """
    range_bars: сколько 5m-свечей формируют initial range (12 = первый час UTC)
    vol_mult: объём пробойной свечи должен быть >= vol_mult × средний объём диапазона
    """
    signals = pd.Series(0, index=df.index, dtype=int)
    df = df.copy()
    df["date"] = df["open_time"].dt.date

    for day, group in df.groupby("date"):
        if len(group) < range_bars + 5:
            continue
        range_slice = group.iloc[:range_bars]
        rh = range_slice["high"].max()
        rl = range_slice["low"].min()
        avg_vol = range_slice["volume"].mean()
        traded = False
        for i in range(range_bars, len(group)):
            row = group.iloc[i]
            idx = row.name
            if traded:
                break
            if row["close"] > rh and row["volume"] > vol_mult * avg_vol:
                signals.loc[idx] = 1
                traded = True
            elif row["close"] < rl and row["volume"] > vol_mult * avg_vol:
                signals.loc[idx] = -1
                traded = True
    return signals
