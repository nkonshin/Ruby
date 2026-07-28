"""
Focused tuning: BB Squeeze 15m + Donchian 15m + ORB 5m с session фильтром.

Grid по SL/TP мультипликаторам ATR и notional. Цель — найти рабочие комбинации.
"""
from __future__ import annotations

from itertools import product
from pathlib import Path

import pandas as pd

from research.backtest_engine import run_backtest
from research.data_loader import WATCHLIST, load_klines
from research.filters import apply_session_filter
from research.strategies.bb_squeeze_breakout import generate_signals as bb_signals
from research.strategies.donchian_breakout import generate_signals as don_signals
from research.strategies.orb_scalper import generate_signals as orb_signals
from research.strategies.ema_cross_volume import generate_signals as ema_signals
from research.strategies.rsi_mean_reversion import generate_signals as rsi_signals
from research.strategies.heikin_ashi_trend import generate_signals as ha_signals
from research.strategies.mtf_momentum import generate_signals as mtf_signals


STRATEGIES_TO_TUNE = [
    ("BBSqueeze_15m", bb_signals, "15m", {"bb_window": 20, "kc_window": 20, "kc_mult": 1.5}),
    ("Donchian_15m_filt", don_signals, "15m", {"lookback": 20, "adx_min": 25}),
    ("ORB_5m_strict", orb_signals, "5m", {"range_bars": 12, "vol_mult": 2.5}),
    ("EMA+Vol_15m", ema_signals, "15m", {"fast": 9, "slow": 21, "vol_mult": 1.5}),
    ("RSI_MR_15m", rsi_signals, "15m", {"oversold": 25, "overbought": 75}),
    ("HeikinTrend_15m", ha_signals, "15m", {"confirm_bars": 3}),
    ("MTF_Momentum_5m", mtf_signals, "5m", {"vol_mult": 1.4}),
]

# Grid параметров
SL_MULTS = [1.0, 1.5, 2.0, 2.5]
TP_MULTS = [1.5, 2.0, 3.0, 4.0]
NOTIONALS = [1250, 2000, 3000]


def main() -> pd.DataFrame:
    rows = []
    total = len(WATCHLIST) * len(STRATEGIES_TO_TUNE) * len(SL_MULTS) * len(TP_MULTS) * len(NOTIONALS)
    print(f"Focused tuning: {len(WATCHLIST)} монет × {len(STRATEGIES_TO_TUNE)} стратегий × "
          f"{len(SL_MULTS)}×{len(TP_MULTS)}×{len(NOTIONALS)} = {total} прогонов")
    print("Session-фильтр: 06–22 UTC. Все exits intrabar. fee+slip = 0.1% round-trip.\n")

    cnt = 0
    for coin_name, symbol in WATCHLIST.items():
        # Кэшируем сигналы для каждой стратегии на этой монете (грид меняет только trade-mgmt)
        per_strategy_cache = {}
        for strat_name, fn, tf, params in STRATEGIES_TO_TUNE:
            try:
                df = load_klines(symbol, tf, days=90)
                if df.empty or len(df) < 500:
                    continue
                signals = fn(df, **params)
                signals = apply_session_filter(signals, df)
                per_strategy_cache[strat_name] = (df, signals)
            except Exception as e:
                print(f"{coin_name} {strat_name}: prep error {type(e).__name__}: {e}")
                continue

        for strat_name, (df, signals) in per_strategy_cache.items():
            for sl_m, tp_m, notional in product(SL_MULTS, TP_MULTS, NOTIONALS):
                if tp_m <= sl_m:  # R:R < 1 — пропускаем
                    continue
                cnt += 1
                try:
                    res = run_backtest(
                        df, signals,
                        initial_equity=200.0,
                        notional_per_trade=notional,
                        sl_atr_mult=sl_m,
                        tp_atr_mult=tp_m,
                        fee_round_trip=0.001,
                        slippage=0.0005,
                    )
                except Exception as e:
                    continue
                m = res.metrics
                rows.append({
                    "coin": coin_name, "symbol": symbol, "strategy": strat_name,
                    "sl_mult": sl_m, "tp_mult": tp_m, "notional": notional,
                    "rr": tp_m / sl_m,
                    "roi_pct": m["total_return_pct"],
                    "max_dd_pct": m["max_drawdown_pct"],
                    "n_trades": m["n_trades"],
                    "win_rate": m["win_rate"],
                    "avg_trade_pct": m["avg_trade_pct"],
                    "sharpe": m["sharpe"],
                    "final_equity": m["final_equity"],
                })

    df_res = pd.DataFrame(rows)
    out = Path(__file__).parent / "data" / "focused_tuning.csv"
    df_res.to_csv(out, index=False)
    print(f"\n💾 Сохранено: {out}  ({len(df_res)} комбинаций)")

    # Топ положительных
    profit = df_res[df_res["roi_pct"] > 0].sort_values("roi_pct", ascending=False)
    print(f"\n🎯 Найдено {len(profit)} прибыльных комбинаций из {len(df_res)} ({100*len(profit)/max(1,len(df_res)):.1f}%)\n")

    print("🏆 Топ-15 по ROI (только n_trades >= 10, чтоб не случайный сэмпл):")
    significant = df_res[df_res["n_trades"] >= 10].sort_values("roi_pct", ascending=False).head(15)
    print(significant[["coin", "strategy", "sl_mult", "tp_mult", "notional", "roi_pct",
                       "max_dd_pct", "n_trades", "win_rate", "sharpe"]].to_string(index=False))

    print("\n📊 Топ-10 по Sharpe (n_trades >= 10):")
    by_sharpe = df_res[df_res["n_trades"] >= 10].sort_values("sharpe", ascending=False).head(10)
    print(by_sharpe[["coin", "strategy", "sl_mult", "tp_mult", "notional", "roi_pct",
                     "max_dd_pct", "n_trades", "win_rate", "sharpe"]].to_string(index=False))

    return df_res


if __name__ == "__main__":
    main()
