"""
Регим-снимок watchlist'а: ATR%, ADX, BB Width — за последние 30 дней.
Категоризирует каждую монету как trend / range / volatile.

Использование:
    python3 -m research.regime_snapshot

Кладёт результат в research/data/regime_snapshot.csv + красивая таблица в stdout.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import ta

from research.data_loader import WATCHLIST, load_klines

LOOKBACK_DAYS = 30
TF = "15m"  # 15m достаточно гладко чтобы дать стабильный regime-сигнал


def compute_features(df: pd.DataFrame) -> dict:
    """Считает ATR%, ADX, BB Width на последних LOOKBACK_DAYS днях."""
    if len(df) < 200:
        return {}
    last_close = df["close"].iloc[-1]
    atr = ta.volatility.AverageTrueRange(df["high"], df["low"], df["close"], window=14).average_true_range()
    atr_pct = (atr.iloc[-200:].mean() / last_close) * 100  # % от последней цены

    adx = ta.trend.ADXIndicator(df["high"], df["low"], df["close"], window=14).adx()
    adx_mean = adx.iloc[-200:].mean()

    bb = ta.volatility.BollingerBands(df["close"], window=20, window_dev=2)
    bbw = ((bb.bollinger_hband() - bb.bollinger_lband()) / bb.bollinger_mavg() * 100)
    bbw_mean = bbw.iloc[-200:].mean()

    # Сила тренда за период (отношение текущая/предыдущая через окно)
    period_return = (df["close"].iloc[-1] / df["close"].iloc[-int(LOOKBACK_DAYS * 24 * 60 / 15)] - 1) * 100

    return {
        "price": last_close,
        "atr_pct": atr_pct,
        "adx": adx_mean,
        "bb_width_pct": bbw_mean,
        "period_return_pct": period_return,
    }


def classify(features: dict) -> str:
    """Грубая категоризация: trend / range / volatile."""
    if not features:
        return "n/a"
    adx = features["adx"]
    atr = features["atr_pct"]
    if adx > 25:
        return "TREND"
    if atr > 1.5:
        return "VOLATILE"
    return "RANGE"


def main() -> None:
    rows = []
    print(f"Регим-снимок на {TF}, окно {LOOKBACK_DAYS} дней:\n")
    for name, sym in WATCHLIST.items():
        try:
            df = load_klines(sym, TF, days=LOOKBACK_DAYS + 10)
            feats = compute_features(df)
            if not feats:
                print(f"  {name:<8} — мало данных")
                continue
            cls = classify(feats)
            rows.append({"coin": name, "symbol": sym, "regime": cls, **feats})
        except Exception as e:
            print(f"  {name:<8} — ошибка: {e}")
            continue

    if not rows:
        print("Нет данных")
        return

    df = pd.DataFrame(rows).sort_values(["regime", "adx"], ascending=[True, False])
    out_path = Path(__file__).parent / "data" / "regime_snapshot.csv"
    df.to_csv(out_path, index=False)

    # Красивая таблица
    print(f"{'COIN':<8} {'REGIME':<9} {'PRICE':>12} {'ATR%':>7} {'ADX':>6} {'BBW%':>7} {'30d_ret%':>9}")
    print("─" * 70)
    for r in df.to_dict("records"):
        print(f"{r['coin']:<8} {r['regime']:<9} {r['price']:>12,.4f} "
              f"{r['atr_pct']:>7.2f} {r['adx']:>6.1f} {r['bb_width_pct']:>7.2f} {r['period_return_pct']:>+9.1f}")

    print(f"\n💾 Сохранено: {out_path}")
    print(f"\n📊 По регимам:")
    for cls, group in df.groupby("regime"):
        coins = ", ".join(group["coin"].tolist())
        print(f"   {cls:<9} ({len(group)}): {coins}")


if __name__ == "__main__":
    main()
