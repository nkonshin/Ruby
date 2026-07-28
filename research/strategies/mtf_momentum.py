"""
Multi-Timeframe Momentum (вход на 5m, фильтр направления на 15m).

Логика:
  • На 15m считаем EMA(50): если цена выше — bias LONG, ниже — bias SHORT
  • На 5m: ловим pullback к EMA(20), отбой со spike-объёмом
  • Long: 15m bias bullish + 5m цена коснулась EMA20 снизу + закрылась выше + volume spike
  • Short: зеркально

Это «trend-pullback» подход — даёт лучший R:R чем breakout, но реже сигналов.
"""
from __future__ import annotations
import pandas as pd
import ta


def generate_signals(df_5m: pd.DataFrame, df_15m: pd.DataFrame = None, *,
                      vol_mult: float = 1.4) -> pd.Series:
    """
    Работает на 5m данных, но внутри ресемплит до 15m для bias-фильтра.
    df_15m игнорируется (для совместимости с интерфейсом).
    """
    signals = pd.Series(0, index=df_5m.index, dtype=int)

    # 15m bias через resample
    df_15m_res = df_5m.set_index("open_time")[["close"]].resample("15min").last().dropna()
    ema50_15m = ta.trend.EMAIndicator(df_15m_res["close"], window=50).ema_indicator()
    bias = pd.Series(0, index=df_15m_res.index, dtype=int)
    bias[df_15m_res["close"] > ema50_15m] = 1
    bias[df_15m_res["close"] < ema50_15m] = -1
    bias_5m = bias.reindex(df_5m["open_time"], method="ffill").reset_index(drop=True)

    # 5m: EMA20 + объём
    ema20 = ta.trend.EMAIndicator(df_5m["close"], window=20).ema_indicator()
    vol_sma = df_5m["volume"].rolling(20).mean()
    vol_ok = df_5m["volume"] > vol_mult * vol_sma

    # Цена коснулась EMA20 и отскочила
    touched_below = (df_5m["low"] <= ema20) & (df_5m["close"] > ema20)
    touched_above = (df_5m["high"] >= ema20) & (df_5m["close"] < ema20)

    long_cond = (bias_5m == 1) & touched_below & vol_ok
    short_cond = (bias_5m == -1) & touched_above & vol_ok

    signals[long_cond.values] = 1
    signals[short_cond.values] = -1
    return signals
