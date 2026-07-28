"""
Фильтры для сигналов: session filter (отсекаем тонкую азиатскую ночь),
volatility filter (не входим когда ATR слишком низкий).
"""
from __future__ import annotations

import pandas as pd
import ta


def apply_session_filter(signals: pd.Series, df: pd.DataFrame, *,
                          start_hour: int = 6, end_hour: int = 22) -> pd.Series:
    """
    Обнуляет сигналы вне торговой сессии. По UTC.
    06:00–22:00 UTC = Европа открытие → Америка закрытие. Самая ликвидная часть суток.
    """
    hours = df["open_time"].dt.hour
    in_session = (hours >= start_hour) & (hours < end_hour)
    return signals.where(in_session, 0)


def apply_volatility_filter(signals: pd.Series, df: pd.DataFrame, *,
                             min_atr_pct: float = 0.15) -> pd.Series:
    """
    Не входим когда ATR ниже min_atr_pct % от цены (мертвый рынок).
    """
    atr = ta.volatility.AverageTrueRange(df["high"], df["low"], df["close"], window=14).average_true_range()
    atr_pct = atr / df["close"] * 100
    return signals.where(atr_pct >= min_atr_pct, 0)
