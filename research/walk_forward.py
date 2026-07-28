"""
Walk-forward проверка топ-комбинаций.

Берёт топ-N конфигов из focused_tuning, прогоняет каждую через скользящее окно:
train 60 дней — test 15 дней, шаг 15 дней. Считает out-of-sample результаты.
Если стратегия overfit — на out-of-sample она развалится.
"""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pandas as pd

from research.backtest_engine import run_backtest
from research.data_loader import WATCHLIST, load_klines
from research.filters import apply_session_filter
from research.strategies.bb_squeeze_breakout import generate_signals as bb_signals
from research.strategies.donchian_breakout import generate_signals as don_signals
from research.strategies.ema_cross_volume import generate_signals as ema_signals
from research.strategies.orb_scalper import generate_signals as orb_signals
from research.strategies.rsi_mean_reversion import generate_signals as rsi_signals
from research.strategies.heikin_ashi_trend import generate_signals as ha_signals
from research.strategies.mtf_momentum import generate_signals as mtf_signals

# Mapping имени стратегии (как в focused_tuning) → функция + tf + params
STRAT_MAP = {
    "BBSqueeze_15m":     (bb_signals,  "15m", {"bb_window": 20, "kc_window": 20, "kc_mult": 1.5}),
    "Donchian_15m_filt": (don_signals, "15m", {"lookback": 20, "adx_min": 25}),
    "ORB_5m_strict":     (orb_signals, "5m",  {"range_bars": 12, "vol_mult": 2.5}),
    "EMA+Vol_15m":       (ema_signals, "15m", {"fast": 9, "slow": 21, "vol_mult": 1.5}),
    "RSI_MR_15m":        (rsi_signals, "15m", {"oversold": 25, "overbought": 75}),
    "HeikinTrend_15m":   (ha_signals,  "15m", {"confirm_bars": 3}),
    "MTF_Momentum_5m":   (mtf_signals, "5m",  {"vol_mult": 1.4}),
}


def walk_forward_one(symbol: str, fn, tf: str, params: dict,
                      sl_mult: float, tp_mult: float, notional: float,
                      *, train_days: int = 30, test_days: int = 15, step_days: int = 10) -> list[dict]:
    """Скользящее окно: train_days истории → test_days форвард-теста, шаг step_days."""
    df = load_klines(symbol, tf, days=90)
    if df.empty:
        return []
    df = df.copy()
    df["__t"] = df["open_time"]

    earliest = df["__t"].iloc[0]
    latest = df["__t"].iloc[-1]

    results = []
    cur_test_start = earliest + timedelta(days=train_days)
    while cur_test_start + timedelta(days=test_days) <= latest:
        test_end = cur_test_start + timedelta(days=test_days)

        # Окно: берём сигналы на всём df, потом нарезаем нужное окно
        # (стратегии нужны исторические данные для расчёта индикаторов, поэтому полное окно сюда же)
        train_test = df[df["__t"] <= test_end].copy()
        signals = fn(train_test, **params)
        signals = apply_session_filter(signals, train_test)

        # Берём только TEST window
        test_mask = (train_test["__t"] >= cur_test_start) & (train_test["__t"] < test_end)
        test_df = train_test[test_mask].reset_index(drop=True)
        test_sig = signals[test_mask].reset_index(drop=True)
        if len(test_df) < 50:
            cur_test_start = cur_test_start + timedelta(days=step_days)
            continue

        try:
            res = run_backtest(
                test_df, test_sig,
                initial_equity=200.0, notional_per_trade=notional,
                sl_atr_mult=sl_mult, tp_atr_mult=tp_mult,
                fee_round_trip=0.001, slippage=0.0005,
            )
        except Exception:
            cur_test_start = cur_test_start + timedelta(days=step_days)
            continue

        m = res.metrics
        results.append({
            "window_start": cur_test_start.date(),
            "window_end": test_end.date(),
            "roi_pct": m["total_return_pct"],
            "max_dd_pct": m["max_drawdown_pct"],
            "n_trades": m["n_trades"],
            "win_rate": m["win_rate"],
            "sharpe": m["sharpe"],
        })
        cur_test_start = cur_test_start + timedelta(days=step_days)

    return results


def main() -> None:
    csv = Path(__file__).parent / "data" / "focused_tuning.csv"
    df = pd.read_csv(csv)
    df = df[(df["roi_pct"] > 50) & (df["n_trades"] >= 10)].copy()
    df = df.sort_values("sharpe", ascending=False)

    # Берём топ-25 (уникальные по coin × strategy)
    top = []
    seen = set()
    for _, row in df.iterrows():
        key = (row["coin"], row["strategy"])
        if key in seen:
            continue
        seen.add(key)
        top.append(row)
        if len(top) >= 25:
            break

    print(f"🔍 Walk-forward на {len(top)} топ-конфигах (train 30d / test 15d / step 10d, ≈5 окон):\n")
    print(f"{'COIN':<8} {'STRATEGY':<18} {'SL':>4} {'TP':>4} {'NTL':>5}  "
          f"{'IS_ROI%':>8}  {'OOS_avg':>8} {'OOS_std':>8} {'win/n':>7}  {'verdict':<10}")
    print("─" * 100)

    summary_rows = []
    for cfg in top:
        coin = cfg["coin"]; sym = cfg["symbol"]; strat = cfg["strategy"]
        sl = float(cfg["sl_mult"]); tp = float(cfg["tp_mult"]); ntl = float(cfg["notional"])
        is_roi = float(cfg["roi_pct"])

        fn, tf, params = STRAT_MAP[strat]
        wf = walk_forward_one(sym, fn, tf, params, sl, tp, ntl)
        if not wf:
            print(f"{coin:<8} {strat:<18} — нет окон")
            continue
        rois = [w["roi_pct"] for w in wf]
        oos_avg = sum(rois) / len(rois)
        oos_std = (sum((r - oos_avg) ** 2 for r in rois) / len(rois)) ** 0.5
        positive = sum(1 for r in rois if r > 0)

        # Грубый вердикт
        if oos_avg > 10 and positive >= len(rois) * 0.6:
            verdict = "✅ ROBUST"
        elif oos_avg > 0:
            verdict = "🟡 MIXED"
        else:
            verdict = "❌ FAIL"

        print(f"{coin:<8} {strat:<18} {sl:>4.1f} {tp:>4.1f} {ntl:>5.0f}  "
              f"{is_roi:>+8.1f}  {oos_avg:>+8.1f} {oos_std:>8.1f} "
              f"{positive}/{len(rois):>2}    {verdict:<10}")

        summary_rows.append({
            "coin": coin, "strategy": strat, "sl_mult": sl, "tp_mult": tp, "notional": ntl,
            "is_roi_pct": is_roi, "oos_avg_roi_pct": oos_avg, "oos_std": oos_std,
            "oos_positive_windows": positive, "oos_total_windows": len(rois),
            "verdict": verdict,
        })

    out = Path(__file__).parent / "data" / "walk_forward.csv"
    pd.DataFrame(summary_rows).to_csv(out, index=False)
    print(f"\n💾 Сохранено: {out}")


if __name__ == "__main__":
    main()
