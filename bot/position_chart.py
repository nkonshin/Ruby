"""
Рендер графика открытой позиции в стиле TradingView:
свечи + точка входа (треугольник) + зоны Stop Loss (красная) и Take Profit (зелёная)
от entry → к концу графика, текущая цена пунктиром.
"""
from __future__ import annotations

import io
from datetime import datetime, timedelta
from typing import Optional

import matplotlib

matplotlib.use("Agg")  # headless: рендер на сервере без X
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle


# Период каждого таймфрейма в минутах — для перевода timestamp в индекс свечи
_TF_MINUTES = {
    "1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30,
    "1h": 60, "2h": 120, "4h": 240, "6h": 360, "8h": 480, "12h": 720,
    "1d": 1440, "1w": 10080,
}


def _candle_width_days(timeframe: str) -> float:
    minutes = _TF_MINUTES.get(timeframe, 60)
    return minutes / (60 * 24) * 0.7  # 70% от интервала, чтобы между свечами были зазоры


def _find_entry_index(timestamps: list[datetime], opened_at: datetime, tf_minutes: int) -> Optional[int]:
    """Ищет индекс свечи, в которую открыли позицию (свеча, чей open_time ближайший снизу)."""
    if not timestamps or opened_at is None:
        return None
    # Свеча открытия — последняя, у которой open_time <= opened_at
    candidates = [i for i, ts in enumerate(timestamps) if ts <= opened_at]
    return max(candidates) if candidates else None


def render_position_chart(
    ohlcv: list[list[float]],
    trade: dict,
    current_price: float,
    *,
    title: str,
    symbol: str,
    timeframe: str,
) -> bytes:
    """
    Рендерит PNG свечного графика с зонами SL/TP.
    ohlcv: [[ts_ms, open, high, low, close, volume], ...]
    trade: dict из PaperAccount.open_trade с ключами side, entry_price, sl_price, tp_price, opened_at
    Возвращает bytes готовой картинки.
    """
    if not ohlcv:
        raise ValueError("ohlcv пустой — нечего рисовать")

    side = trade["side"]
    entry = trade["entry_price"]
    sl = trade["sl_price"]
    tp = trade["tp_price"]
    try:
        opened_at = datetime.fromisoformat(trade["opened_at"])
    except (KeyError, ValueError, TypeError):
        opened_at = None

    timestamps = [datetime.utcfromtimestamp(c[0] / 1000) for c in ohlcv]
    opens = [c[1] for c in ohlcv]
    highs = [c[2] for c in ohlcv]
    lows = [c[3] for c in ohlcv]
    closes = [c[4] for c in ohlcv]

    tf_minutes = _TF_MINUTES.get(timeframe, 60)
    entry_idx = _find_entry_index(timestamps, opened_at, tf_minutes) if opened_at else None

    fig, ax = plt.subplots(figsize=(12.8, 7.2), dpi=100)
    fig.patch.set_facecolor("#ffffff")
    ax.set_facecolor("#fafafa")

    # Свечи
    width_days = _candle_width_days(timeframe)
    width_wick = width_days * 0.08
    for ts, o, h, l, c in zip(timestamps, opens, highs, lows, closes):
        is_up = c >= o
        body_color = "#26a69a" if is_up else "#ef5350"  # стиль TradingView
        edge_color = body_color
        # Тень
        ax.add_patch(Rectangle(
            (mdates.date2num(ts) - width_wick / 2, l),
            width_wick, h - l,
            facecolor=body_color, edgecolor=edge_color, linewidth=0.5,
        ))
        # Тело
        body_low = min(o, c)
        body_height = max(abs(c - o), (h - l) * 0.001)  # минимальная толщина для doji
        ax.add_patch(Rectangle(
            (mdates.date2num(ts) - width_days / 2, body_low),
            width_days, body_height,
            facecolor=body_color, edgecolor=edge_color, linewidth=0.7,
        ))

    # X-ось — даты, авто-форматирование
    ax.xaxis_date()
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b %H:%M" if tf_minutes < 1440 else "%d %b"))
    fig.autofmt_xdate()

    # Расширяем границы для зон (вправо от последней свечи на 5 свечей — будущая проекция)
    last_ts = timestamps[-1]
    future_pad = timedelta(minutes=tf_minutes * 5)
    x_min = timestamps[0] - timedelta(minutes=tf_minutes * 0.7)
    x_max = last_ts + future_pad
    ax.set_xlim(x_min, x_max)

    # Зоны SL / TP — от entry-свечи до правого края
    if entry_idx is not None:
        zone_start_x = timestamps[entry_idx]
    else:
        # Если вход случился до старта окна — зоны от левого края
        zone_start_x = timestamps[0]
    zone_end_x = x_max

    if side == "buy":
        # LONG: TP сверху (зелёная), SL снизу (красная)
        tp_low, tp_high = entry, tp
        sl_low, sl_high = sl, entry
    else:
        # SHORT: TP снизу, SL сверху
        tp_low, tp_high = tp, entry
        sl_low, sl_high = entry, sl

    ax.fill_betweenx(
        [tp_low, tp_high], mdates.date2num(zone_start_x), mdates.date2num(zone_end_x),
        color="#26a69a", alpha=0.18, zorder=1,
    )
    ax.fill_betweenx(
        [sl_low, sl_high], mdates.date2num(zone_start_x), mdates.date2num(zone_end_x),
        color="#ef5350", alpha=0.18, zorder=1,
    )

    # Линия Entry (тонкая горизонтальная, от entry-свечи до конца)
    ax.hlines(
        entry, mdates.date2num(zone_start_x), mdates.date2num(zone_end_x),
        colors="#1976d2", linewidth=1.0, linestyle="-", zorder=3,
    )
    ax.annotate(
        f"Entry  {entry:,.2f}",
        xy=(mdates.date2num(zone_end_x), entry),
        xytext=(4, 0), textcoords="offset points",
        va="center", ha="left", fontsize=9, color="#1976d2", fontweight="bold",
    )

    # Маркер входа: треугольник на свече открытия
    if entry_idx is not None:
        marker = "^" if side == "buy" else "v"
        marker_color = "#1976d2"
        # Расположим маркер чуть ниже low (для лонга) / выше high (для шорта)
        candle_low = lows[entry_idx]
        candle_high = highs[entry_idx]
        candle_range = candle_high - candle_low
        if side == "buy":
            marker_y = candle_low - candle_range * 0.5
        else:
            marker_y = candle_high + candle_range * 0.5
        ax.scatter(
            timestamps[entry_idx], marker_y,
            marker=marker, s=140, color=marker_color, edgecolors="white", linewidths=1.2,
            zorder=5,
        )

    # Текущая цена — пунктирная горизонталь
    ax.axhline(current_price, color="#616161", linewidth=0.9, linestyle="--", zorder=4)
    ax.annotate(
        f"{current_price:,.2f}",
        xy=(mdates.date2num(zone_end_x), current_price),
        xytext=(4, 0), textcoords="offset points",
        va="center", ha="left", fontsize=9, color="#212121",
        bbox=dict(boxstyle="round,pad=0.25", facecolor="#eeeeee", edgecolor="#bdbdbd"),
    )

    # SL / TP — подписи на правом краю
    ax.annotate(
        f"TP  {tp:,.2f}",
        xy=(mdates.date2num(zone_end_x), tp),
        xytext=(4, 0), textcoords="offset points",
        va="center", ha="left", fontsize=9, color="#1b5e20", fontweight="bold",
    )
    ax.annotate(
        f"SL  {sl:,.2f}",
        xy=(mdates.date2num(zone_end_x), sl),
        xytext=(4, 0), textcoords="offset points",
        va="center", ha="left", fontsize=9, color="#b71c1c", fontweight="bold",
    )

    # Y-границы — учитываем все ключевые уровни + небольшой запас
    all_y = lows + highs + [sl, tp, current_price, entry]
    y_min, y_max = min(all_y), max(all_y)
    y_pad = (y_max - y_min) * 0.05
    ax.set_ylim(y_min - y_pad, y_max + y_pad)

    ax.set_title(f"{title} · {symbol} · {timeframe}", fontsize=12, loc="left", pad=10)
    ax.grid(True, linestyle="--", linewidth=0.4, color="#cfcfcf", alpha=0.6)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    # Y-цифры — тысячные разделители
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:,.0f}"))

    plt.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf.read()
