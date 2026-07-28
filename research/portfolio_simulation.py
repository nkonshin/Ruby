"""
Симуляция портфолио из топ-ROBUST конфигов на полных 90 днях.

Каждая стратегия торгует отдельный баланс. Метрики собираем общие:
  • суммарный equity curve
  • совокупный ROI и max DD
  • декомпозиция по конфигам
"""
from __future__ import annotations

from pathlib import Path
from dataclasses import dataclass

import numpy as np
import pandas as pd

from research.backtest_engine import run_backtest
from research.data_loader import WATCHLIST, load_klines
from research.filters import apply_session_filter
from research.walk_forward import STRAT_MAP


@dataclass
class PortfolioConfig:
    coin: str
    strategy: str
    sl_mult: float
    tp_mult: float
    notional: float
    initial_alloc: float


# Финальный список конфигов: все ROBUST из walk-forward
ROBUST_CONFIGS = [
    PortfolioConfig("PEPE",   "RSI_MR_15m",        2.5, 4.0, 3000.0, 0.0),
    PortfolioConfig("ATOM",   "RSI_MR_15m",        2.5, 4.0, 3000.0, 0.0),
    PortfolioConfig("RENDER", "RSI_MR_15m",        2.5, 3.0, 3000.0, 0.0),
    PortfolioConfig("ATOM",   "EMA+Vol_15m",       2.0, 4.0, 3000.0, 0.0),
    PortfolioConfig("TON",    "Donchian_15m_filt", 2.5, 3.0, 1250.0, 0.0),
    PortfolioConfig("TON",    "ORB_5m_strict",     2.5, 4.0, 3000.0, 0.0),
    PortfolioConfig("LINK",   "EMA+Vol_15m",       2.5, 4.0, 1250.0, 0.0),
]


def main() -> None:
    total_equity = 200.0
    # ИСПРАВЛЕНО: каждая стратегия работает с полным $200 баланса (независимая симуляция).
    # Реалистично — на MEXC одна позиция $1250-$3000 c $200 баланса = 6-15x плечо, не 100x.
    # Если запускать ВСЕ одновременно — нужно делить notional пропорционально, но это уже
    # этап production-tuning.
    alloc_per_strat = total_equity

    print(f"💼 Independent runs ({len(ROBUST_CONFIGS)} ROBUST-конфигов)")
    print(f"   Каждая стратегия: ${total_equity:.0f} стартовый, плечо как в конфиге\n")

    print(f"{'COIN':<8} {'STRATEGY':<20} {'SL':>4} {'TP':>4} {'NTL':>5}  "
          f"{'ROI%':>8} {'DD%':>7} {'N':>4} {'WR%':>6} {'FinalEq':>9}")
    print("─" * 90)

    aggregate_trades = []
    final_equity_total = 0.0
    cum_results = []

    for cfg in ROBUST_CONFIGS:
        symbol = WATCHLIST[cfg.coin]
        fn, tf, params = STRAT_MAP[cfg.strategy]
        df = load_klines(symbol, tf, days=90)
        signals = fn(df, **params)
        signals = apply_session_filter(signals, df)
        res = run_backtest(
            df, signals,
            initial_equity=alloc_per_strat,
            notional_per_trade=cfg.notional,
            sl_atr_mult=cfg.sl_mult, tp_atr_mult=cfg.tp_mult,
            fee_round_trip=0.001, slippage=0.0005,
        )
        m = res.metrics
        cum_results.append((cfg, res))
        final_equity_total += m["final_equity"]
        for t in res.trades:
            aggregate_trades.append({
                "exit_time": t.exit_time,
                "pnl_dollars": t.pnl_dollars,
                "coin": cfg.coin,
                "strategy": cfg.strategy,
            })

        print(f"{cfg.coin:<8} {cfg.strategy:<20} {cfg.sl_mult:>4.1f} {cfg.tp_mult:>4.1f} {cfg.notional:>5.0f}  "
              f"{m['total_return_pct']:>+8.1f} {m['max_drawdown_pct']:>+7.1f} "
              f"{m['n_trades']:>4} {m['win_rate']:>6.1f} {m['final_equity']:>9.2f}")

    print("─" * 90)
    avg_final = final_equity_total / len(ROBUST_CONFIGS)
    avg_roi = (avg_final / total_equity - 1) * 100
    print(f"\n📈 Средний по конфигам: ${total_equity:.0f} → ${avg_final:.2f}  ({avg_roi:+.1f}%)")
    print(f"   (это «выбери одну ROBUST стратегию» — средний результат)")

    # Реконструируем portfolio equity curve по времени exit'ов
    if aggregate_trades:
        agg_df = pd.DataFrame(aggregate_trades).sort_values("exit_time")
        agg_df["cum_pnl"] = agg_df["pnl_dollars"].cumsum()
        agg_df["equity"] = total_equity + agg_df["cum_pnl"]
        agg_df["running_max"] = agg_df["equity"].cummax()
        agg_df["dd_pct"] = (agg_df["equity"] / agg_df["running_max"] - 1) * 100
        max_dd = agg_df["dd_pct"].min()
        print(f"📉 Portfolio Max DD (по сделкам): {max_dd:+.1f}%")
        print(f"📊 Всего сделок: {len(agg_df)}")
        out = Path(__file__).parent / "data" / "portfolio_equity_curve.csv"
        agg_df.to_csv(out, index=False)
        print(f"\n💾 Сохранена equity curve: {out}")

        # Помесячная разбивка
        agg_df["month"] = agg_df["exit_time"].dt.to_period("M")
        monthly = agg_df.groupby("month")["pnl_dollars"].sum()
        print(f"\n📅 Помесячный P&L:")
        for period, pnl in monthly.items():
            pct = pnl / total_equity * 100
            arrow = "📈" if pnl > 0 else "📉"
            print(f"   {period}  {arrow}  {pnl:+.2f}$  ({pct:+.1f}% от стартового)")


if __name__ == "__main__":
    main()
