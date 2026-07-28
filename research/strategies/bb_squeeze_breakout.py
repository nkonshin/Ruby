"""
Bollinger Squeeze → Breakout (15m).

Логика:
  • Squeeze: Bollinger Bands внутри Keltner Channel → консолидация
  • Выход из squeeze: BB выходят за пределы KC + закрытие свечи > верхний BB → long
  • Зеркально вниз → short
  • Идея: перед мощным движением рынок сжимается. Ловим момент выхода.

Хорошо работает на трендовых альтах (SUI, APT, TON, RENDER).
"""
from __future__ import annotations
import pandas as pd
import ta


def generate_signals(
    df: pd.DataFrame, *, bb_window: int = 20, bb_std: float = 2.0,
    kc_window: int = 20, kc_mult: float = 1.5,
) -> pd.Series:
    signals = pd.Series(0, index=df.index, dtype=int)

    bb = ta.volatility.BollingerBands(df["close"], window=bb_window, window_dev=bb_std)
    bb_high = bb.bollinger_hband()
    bb_low = bb.bollinger_lband()

    kc = ta.volatility.KeltnerChannel(df["high"], df["low"], df["close"],
                                       window=kc_window, window_atr=kc_window, multiplier=kc_mult)
    kc_high = kc.keltner_channel_hband()
    kc_low = kc.keltner_channel_lband()

    # Squeeze on: BB внутри KC
    squeeze_on = (bb_high < kc_high) & (bb_low > kc_low)
    squeeze_was_on = squeeze_on.shift(1).fillna(False)
    # Выход из squeeze: предыдущий бар был в squeeze, текущий — нет
    just_released = squeeze_was_on & ~squeeze_on

    # Направление пробоя
    long_break = just_released & (df["close"] > bb_high)
    short_break = just_released & (df["close"] < bb_low)

    signals[long_break] = 1
    signals[short_break] = -1
    return signals
