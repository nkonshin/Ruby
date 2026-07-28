"""
Quick screen: каждая стратегия × каждая монета с дефолтными параметрами.

Цель — отсеять «никак не работает» пары до полного grid'а.
Параметры trade-management:
  • Стартовый баланс $200
  • Notional на сделку: $2000 (10× leverage)
  • SL/TP по ATR: 1× / 2× ATR_14 (R:R 1:2)
  • Комиссия + slippage = 0.1% round-trip
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from research.backtest_engine import run_backtest
from research.data_loader import WATCHLIST, load_klines
from research.strategies import STRATEGIES


def main() -> pd.DataFrame:
    rows = []
    total = len(WATCHLIST) * len(STRATEGIES)
    cnt = 0
    print(f"Quick screen: {len(WATCHLIST)} монет × {len(STRATEGIES)} стратегий = {total} прогонов\n")
    print(f"{'COIN':<8} {'STRATEGY':<16} {'ROI%':>8} {'DD%':>7} {'N':>4} {'WR%':>6} {'AvgT%':>7} {'Sharpe':>7}")
    print("─" * 75)

    for coin_name, symbol in WATCHLIST.items():
        for strat_name, meta in STRATEGIES.items():
            cnt += 1
            tf = meta["tf"]
            try:
                df = load_klines(symbol, tf, days=90)
                if df.empty or len(df) < 500:
                    continue
                signals = meta["fn"](df, **meta["default_params"])
                result = run_backtest(
                    df, signals,
                    initial_equity=200.0,
                    notional_per_trade=2000.0,
                    sl_atr_mult=1.0,
                    tp_atr_mult=2.0,
                    fee_round_trip=0.001,
                    slippage=0.0005,
                )
            except Exception as e:
                print(f"{coin_name:<8} {strat_name:<16} ОШИБКА: {type(e).__name__}: {e}")
                continue

            m = result.metrics
            rows.append({
                "coin": coin_name, "symbol": symbol, "strategy": strat_name, "tf": tf,
                "roi_pct": m["total_return_pct"],
                "max_dd_pct": m["max_drawdown_pct"],
                "n_trades": m["n_trades"],
                "win_rate": m["win_rate"],
                "avg_trade_pct": m["avg_trade_pct"],
                "sharpe": m["sharpe"],
                "final_equity": m["final_equity"],
            })
            print(f"{coin_name:<8} {strat_name:<16} "
                  f"{m['total_return_pct']:>+8.1f} {m['max_drawdown_pct']:>+7.1f} "
                  f"{m['n_trades']:>4} {m['win_rate']:>6.1f} "
                  f"{m['avg_trade_pct']:>+7.2f} {m['sharpe']:>+7.2f}")

    df_res = pd.DataFrame(rows)
    out = Path(__file__).parent / "data" / "quick_screen.csv"
    df_res.to_csv(out, index=False)
    print(f"\n💾 Сохранено: {out}\n")

    # Топ-10 по ROI
    print("🏆 Топ-10 по ROI:")
    top = df_res.sort_values("roi_pct", ascending=False).head(10)
    print(top[["coin", "strategy", "roi_pct", "max_dd_pct", "n_trades", "win_rate", "sharpe"]].to_string(index=False))

    print("\n⚡ Топ-10 по Sharpe (с фильтром n_trades >= 5):")
    top_sharpe = df_res[df_res["n_trades"] >= 5].sort_values("sharpe", ascending=False).head(10)
    print(top_sharpe[["coin", "strategy", "roi_pct", "max_dd_pct", "n_trades", "win_rate", "sharpe"]].to_string(index=False))

    return df_res


if __name__ == "__main__":
    main()
