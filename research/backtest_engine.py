"""
Универсальный бэктест-движок под scalp/momentum-стратегии.

Принимает: OHLCV, серию сигналов (-1/0/+1), параметры trade-management.
Симулирует: вход по next bar open, SL/TP intrabar, комиссия + slippage round-trip.
Возвращает: список сделок, equity-curve, агрегированные метрики.

Cвой движок, а не существующий backtesting/backtest.py, потому что нужны:
  • Уровень notional > equity (плечо)
  • Поддержка short
  • SL/TP как % или как множитель ATR
  • Возможность держать N позиций параллельно (модель: одна стратегия = одна позиция за раз)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd


@dataclass
class Trade:
    entry_idx: int
    exit_idx: int
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    side: int  # +1 long, -1 short
    entry_price: float
    exit_price: float
    notional: float          # размер позиции в долларах (включая плечо)
    pnl_dollars: float
    pnl_pct_on_equity: float
    exit_reason: str         # "tp" / "sl" / "signal" / "end"
    bars_held: int


@dataclass
class BacktestResult:
    trades: list[Trade]
    initial_equity: float
    final_equity: float
    equity_curve: pd.Series
    metrics: dict = field(default_factory=dict)

    @property
    def total_return_pct(self) -> float:
        return (self.final_equity / self.initial_equity - 1) * 100

    @property
    def n_trades(self) -> int:
        return len(self.trades)

    @property
    def win_rate(self) -> float:
        if not self.trades:
            return 0.0
        wins = sum(1 for t in self.trades if t.pnl_dollars > 0)
        return wins / len(self.trades) * 100

    @property
    def max_drawdown_pct(self) -> float:
        if self.equity_curve.empty:
            return 0.0
        running_max = self.equity_curve.cummax()
        dd = (self.equity_curve / running_max - 1) * 100
        return float(dd.min())

    @property
    def avg_trade_pct(self) -> float:
        if not self.trades:
            return 0.0
        return float(np.mean([t.pnl_pct_on_equity for t in self.trades]))

    @property
    def sharpe(self) -> float:
        """Простой Sharpe по сделкам (не аннуализированный)."""
        if len(self.trades) < 2:
            return 0.0
        returns = np.array([t.pnl_pct_on_equity for t in self.trades])
        if returns.std() == 0:
            return 0.0
        return float(returns.mean() / returns.std() * np.sqrt(len(returns)))


def run_backtest(
    df: pd.DataFrame,
    signals: pd.Series,
    *,
    initial_equity: float = 200.0,
    notional_per_trade: float = 1250.0,
    sl_pct: Optional[float] = None,         # 0.01 = 1% от цены
    tp_pct: Optional[float] = None,
    sl_atr_mult: Optional[float] = None,    # альтернатива: SL = ATR_14 × множитель
    tp_atr_mult: Optional[float] = None,
    fee_round_trip: float = 0.001,          # 0.05% taker × 2 = 0.001
    slippage: float = 0.0005,               # 0.05% на вход + 0.05% на выход
) -> BacktestResult:
    """
    Прогон одной стратегии. Одна позиция за раз.

    df: OHLCV-DataFrame (колонки open/high/low/close/volume + open_time)
    signals: pd.Series (index aligned с df) — значения -1/0/+1
    """
    assert (sl_pct is not None) ^ (sl_atr_mult is not None), "укажи либо sl_pct, либо sl_atr_mult"
    assert (tp_pct is not None) ^ (tp_atr_mult is not None), "укажи либо tp_pct, либо tp_atr_mult"

    if sl_atr_mult is not None or tp_atr_mult is not None:
        import ta
        atr = ta.volatility.AverageTrueRange(df["high"], df["low"], df["close"], window=14).average_true_range()
    else:
        atr = pd.Series([np.nan] * len(df), index=df.index)

    equity = initial_equity
    trades: list[Trade] = []
    equity_curve: list[float] = [equity]

    in_position = False
    side = 0
    entry_price = 0.0
    entry_idx = 0
    sl_price = 0.0
    tp_price = 0.0
    notional = 0.0

    opens = df["open"].to_numpy()
    highs = df["high"].to_numpy()
    lows = df["low"].to_numpy()
    closes = df["close"].to_numpy()
    times = df["open_time"].to_numpy()
    atr_arr = atr.to_numpy()
    sig_arr = signals.to_numpy()
    n = len(df)

    fee_slip = fee_round_trip + slippage * 2  # round-trip всех издержек

    for i in range(n - 1):
        # 1. Если в позиции — проверяем SL/TP по high/low ТЕКУЩЕЙ свечи
        if in_position:
            hit_sl = (side == 1 and lows[i] <= sl_price) or (side == -1 and highs[i] >= sl_price)
            hit_tp = (side == 1 and highs[i] >= tp_price) or (side == -1 and lows[i] <= tp_price)

            exit_price = None
            exit_reason = None
            # Консервативно: если оба триггера в баре, считаем SL первым (worst case)
            if hit_sl:
                exit_price = sl_price
                exit_reason = "sl"
            elif hit_tp:
                exit_price = tp_price
                exit_reason = "tp"

            if exit_price is None and sig_arr[i] != 0 and sig_arr[i] != side:
                # Сигнал в противоположную сторону → закрытие по next bar open
                exit_price = opens[i + 1]
                exit_reason = "signal"

            if exit_price is not None:
                price_move_pct = (exit_price - entry_price) / entry_price * side
                pnl_pct_notional = price_move_pct - fee_slip
                pnl_dollars = notional * pnl_pct_notional
                pnl_pct_eq = pnl_dollars / initial_equity * 100  # PnL в % от начального equity для сравнимости

                trades.append(Trade(
                    entry_idx=entry_idx,
                    exit_idx=i,
                    entry_time=pd.Timestamp(times[entry_idx]),
                    exit_time=pd.Timestamp(times[i]),
                    side=side,
                    entry_price=entry_price,
                    exit_price=exit_price,
                    notional=notional,
                    pnl_dollars=pnl_dollars,
                    pnl_pct_on_equity=pnl_pct_eq,
                    exit_reason=exit_reason,
                    bars_held=i - entry_idx,
                ))
                equity += pnl_dollars
                equity_curve.append(equity)
                in_position = False
                side = 0

                # Если equity вылетел в ноль — стоп
                if equity <= 0:
                    break

                # Если был exit по signal и сигнал противоположный — открываем сразу на next bar
                if exit_reason == "signal":
                    # перепадает на блок открытия ниже
                    pass

        # 2. Если не в позиции — проверяем сигнал на текущей свече, входим по open следующей
        if not in_position and i + 1 < n:
            sig = int(sig_arr[i])
            if sig != 0:
                entry_price = opens[i + 1]
                side = sig
                entry_idx = i + 1

                if sl_atr_mult is not None:
                    cur_atr = atr_arr[i] if not np.isnan(atr_arr[i]) else 0.005 * entry_price
                    sl_dist = sl_atr_mult * cur_atr
                else:
                    sl_dist = sl_pct * entry_price

                if tp_atr_mult is not None:
                    cur_atr = atr_arr[i] if not np.isnan(atr_arr[i]) else 0.005 * entry_price
                    tp_dist = tp_atr_mult * cur_atr
                else:
                    tp_dist = tp_pct * entry_price

                if side == 1:
                    sl_price = entry_price - sl_dist
                    tp_price = entry_price + tp_dist
                else:
                    sl_price = entry_price + sl_dist
                    tp_price = entry_price - tp_dist
                notional = notional_per_trade
                in_position = True

    # Закрытие открытой позиции в конце окна
    if in_position:
        exit_price = closes[-1]
        price_move_pct = (exit_price - entry_price) / entry_price * side
        pnl_pct_notional = price_move_pct - fee_slip
        pnl_dollars = notional * pnl_pct_notional
        pnl_pct_eq = pnl_dollars / initial_equity * 100
        trades.append(Trade(
            entry_idx=entry_idx, exit_idx=n - 1,
            entry_time=pd.Timestamp(times[entry_idx]),
            exit_time=pd.Timestamp(times[-1]),
            side=side, entry_price=entry_price, exit_price=exit_price,
            notional=notional, pnl_dollars=pnl_dollars, pnl_pct_on_equity=pnl_pct_eq,
            exit_reason="end", bars_held=n - 1 - entry_idx,
        ))
        equity += pnl_dollars
        equity_curve.append(equity)

    eq_series = pd.Series(equity_curve)

    result = BacktestResult(
        trades=trades,
        initial_equity=initial_equity,
        final_equity=equity,
        equity_curve=eq_series,
    )
    result.metrics = {
        "total_return_pct": result.total_return_pct,
        "n_trades": result.n_trades,
        "win_rate": result.win_rate,
        "max_drawdown_pct": result.max_drawdown_pct,
        "avg_trade_pct": result.avg_trade_pct,
        "sharpe": result.sharpe,
        "final_equity": equity,
    }
    return result
