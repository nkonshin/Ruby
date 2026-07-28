"""
Загрузчик исторических свечей с Binance USDⓂ-Futures (fapi).
Кэширует в parquet чтобы не дёргать API повторно. Под наш scalp-research.

Использование:
    from research.data_loader import load_klines, WATCHLIST
    df = load_klines("BTCUSDT", "5m", days=90)
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd
import requests

logger = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).parent / "data"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

FAPI = "https://fapi.binance.com/fapi/v1/klines"

# Маппинг человекочитаемого имени → тикер на Binance Futures
WATCHLIST: dict[str, str] = {
    "BTC": "BTCUSDT",
    "ETH": "ETHUSDT",
    "SOL": "SOLUSDT",
    "ATOM": "ATOMUSDT",
    "WIF": "WIFUSDT",
    "SUI": "SUIUSDT",
    "APT": "APTUSDT",
    "PEPE": "1000PEPEUSDT",
    "TON": "TONUSDT",
    "STRK": "STRKUSDT",
    "HYPE": "HYPEUSDT",
    "RENDER": "RENDERUSDT",
    "AAVE": "AAVEUSDT",
    "LINK": "LINKUSDT",
}


def _cache_path(symbol: str, tf: str) -> Path:
    return CACHE_DIR / f"{symbol}_{tf}.parquet"


def _fetch_one_batch(symbol: str, tf: str, start_ms: int, end_ms: int, limit: int = 1500) -> list:
    """Один HTTP-вызов Binance klines."""
    params = {"symbol": symbol, "interval": tf, "limit": limit, "startTime": start_ms, "endTime": end_ms}
    r = requests.get(FAPI, params=params, timeout=20)
    r.raise_for_status()
    return r.json()


def _fetch_full_range(symbol: str, tf: str, days: int) -> pd.DataFrame:
    """Тянет весь нужный диапазон, пагинируя по 1500 свечей."""
    end_ms = int(time.time() * 1000)
    tf_ms = _tf_to_ms(tf)
    target_start = end_ms - days * 24 * 60 * 60 * 1000

    rows: list = []
    cursor = target_start
    while cursor < end_ms:
        batch = _fetch_one_batch(symbol, tf, cursor, end_ms, limit=1500)
        if not batch:
            break
        rows.extend(batch)
        last_ts = batch[-1][0]
        cursor = last_ts + tf_ms
        if len(batch) < 1500:
            break
        time.sleep(0.15)  # деликатно к rate-limit'у

    df = pd.DataFrame(rows, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades", "taker_buy_base", "taker_buy_quote", "ignore",
    ])
    if df.empty:
        return df
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
    df["close_time"] = pd.to_datetime(df["close_time"], unit="ms")
    for col in ["open", "high", "low", "close", "volume", "quote_volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.drop_duplicates(subset=["open_time"]).sort_values("open_time").reset_index(drop=True)
    return df


def _tf_to_ms(tf: str) -> int:
    mapping = {"1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000,
               "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}
    return mapping[tf]


def load_klines(symbol: str, tf: str, days: int = 90, *, force_refresh: bool = False) -> pd.DataFrame:
    """
    Возвращает DataFrame со свечами. Использует parquet-кэш в research/data/.
    force_refresh=True перетянет всё заново.
    """
    path = _cache_path(symbol, tf)
    if path.exists() and not force_refresh:
        df = pd.read_parquet(path)
        # Если кэш старше суток — догружаем добавочно с последней свечи
        last_dt = df["open_time"].iloc[-1]
        if (datetime.utcnow() - last_dt) < timedelta(hours=2):
            return _trim_to_days(df, days)
        # Иначе перетянем полный диапазон (это всё равно быстро)
    df = _fetch_full_range(symbol, tf, days)
    if not df.empty:
        df.to_parquet(path, index=False)
    return _trim_to_days(df, days)


def _trim_to_days(df: pd.DataFrame, days: int) -> pd.DataFrame:
    if df.empty:
        return df
    cutoff = datetime.utcnow() - timedelta(days=days)
    return df[df["open_time"] >= cutoff].reset_index(drop=True)


def load_all(tf: str = "5m", days: int = 90, *, force_refresh: bool = False) -> dict[str, pd.DataFrame]:
    """Грузит свечи по всему watchlist'у на одном таймфрейме."""
    out = {}
    for name, sym in WATCHLIST.items():
        try:
            df = load_klines(sym, tf, days=days, force_refresh=force_refresh)
            if df.empty:
                logger.warning(f"{name} ({sym}): пустой ответ от Binance")
                continue
            out[name] = df
            print(f"  {name:<8} ({sym:<16}) — {len(df):>6} свечей  ({df['open_time'].iloc[0].date()} → {df['open_time'].iloc[-1].date()})")
        except Exception as e:
            logger.error(f"{name} ({sym}): {type(e).__name__}: {e}")
    return out


if __name__ == "__main__":
    import sys
    tf = sys.argv[1] if len(sys.argv) > 1 else "5m"
    days = int(sys.argv[2]) if len(sys.argv) > 2 else 90
    print(f"Тяну {tf}, {days} дней истории, на {len(WATCHLIST)} монет:")
    data = load_all(tf=tf, days=days)
    print(f"\nГотово: {len(data)}/{len(WATCHLIST)} монет в кэше {CACHE_DIR}/")
