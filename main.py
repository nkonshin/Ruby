"""
Crypto Trading Bot — Точка входа.

Запуск:
    python main.py                  # Полный режим (бот + Telegram)
    python main.py --no-telegram    # Только бот, без Telegram
    python main.py --backtest       # Бэктест одной стратегии
    python main.py --compare        # Сравнение всех стратегий
    python main.py --compare --strategies ema_crossover,grid,supertrend
    python main.py --backtest --from 2025-01-01 --to 2025-06-01
"""

import asyncio
import logging
import argparse
import os
import sys
from datetime import datetime, timedelta
from typing import Optional

from config import settings, StrategyName
from bot.engine import TradingEngine
from bot.paper_trader import PaperTrader
from telegram_ui.bot import TelegramBot
from backtesting.backtest import Backtester
from strategies import STRATEGY_MAP
from utils.database import Database

# Настройка логирования
logging.basicConfig(
    level=getattr(logging, settings.log_level),
    format="%(asctime)s | %(name)-20s | %(levelname)-7s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("bot.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("main")


# === Утилиты для загрузки исторических данных ===

TIMEFRAME_MS = {
    "1m": 60_000, "5m": 300_000, "15m": 900_000,
    "30m": 1_800_000, "1h": 3_600_000, "4h": 14_400_000,
    "1d": 86_400_000, "1w": 604_800_000,
}


async def fetch_ohlcv_range(
    symbol: str, timeframe: str,
    since: int = None, until: int = None, limit: int = 1000,
) -> list:
    """
    Загружает OHLCV данные за указанный период.
    Если период длинный — делает несколько запросов (пагинация).
    """
    import ccxt.async_support as ccxt
    exchange = ccxt.binance({"enableRateLimit": True})

    try:
        if since is None and until is None:
            # Простой запрос — последние N свечей
            return await exchange.fetch_ohlcv(symbol, timeframe, limit=limit)

        all_candles = []
        tf_ms = TIMEFRAME_MS.get(timeframe, 3_600_000)
        batch_size = 1000  # Макс свечей за один запрос (лимит Binance)
        cursor = since

        while True:
            candles = await exchange.fetch_ohlcv(
                symbol, timeframe, since=cursor, limit=batch_size,
            )
            if not candles:
                break

            # Фильтруем по until
            if until:
                candles = [c for c in candles if c[0] <= until]

            all_candles.extend(candles)

            if len(candles) < batch_size:
                break  # Дошли до конца

            if until and candles[-1][0] >= until:
                break

            # Сдвигаем курсор
            cursor = candles[-1][0] + tf_ms
            await asyncio.sleep(0.5)  # Пауза для rate limit

        # Убираем дубликаты по timestamp
        seen = set()
        unique = []
        for c in all_candles:
            if c[0] not in seen:
                seen.add(c[0])
                unique.append(c)

        logger.info(f"Загружено {len(unique)} свечей {symbol} {timeframe}")
        return sorted(unique, key=lambda c: c[0])

    finally:
        await exchange.close()


def parse_date(date_str: str) -> int:
    """Парсит дату в миллисекунды (timestamp)."""
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M", "%d.%m.%Y"):
        try:
            dt = datetime.strptime(date_str, fmt)
            return int(dt.timestamp() * 1000)
        except ValueError:
            continue
    raise ValueError(f"Неверный формат даты: '{date_str}'. Используйте YYYY-MM-DD")


# === Торговые циклы ===

async def run_trading_loop(engine: TradingEngine, telegram: TelegramBot = None,
                           interval: int = 60) -> None:
    """Основной торговый цикл."""
    logger.info(f"Торговый цикл запущен (интервал: {interval}с)")

    while engine._running:
        try:
            sl_tp_actions = await engine.check_stop_losses()
            for action in sl_tp_actions:
                logger.info(f"SL/TP: {action}")
                if telegram:
                    await telegram.notify_trade(action)

            actions = await engine.run_cycle()
            for action in actions:
                logger.info(f"Действие: {action}")
                if telegram:
                    await telegram.notify_trade(action)

        except Exception as e:
            logger.error(f"Ошибка в торговом цикле: {e}", exc_info=True)

        await asyncio.sleep(interval)


async def _migrate_users_v3(db, settings, main_user_id):
    """Миграция: переносим TELEGRAM_ALLOWED_USERS из .env в БД при первом запуске v3."""
    from bot.paper_trader import LIVE_PAPER_CONFIGS

    # Если уже есть пользователи — пропускаем миграцию
    existing = await db.list_users()
    if existing:
        logger.info(f"v3 users: найдено {len(existing)} пользователей в БД, миграция пропущена")
        return

    logger.info("v3 migration: переносим пользователей из .env в БД")

    # Main user — admin, подписан на всё
    if main_user_id:
        await db.add_user(
            telegram_id=main_user_id, display_name="Main Admin",
            is_admin=True, added_by=None,
        )
        for cfg in LIVE_PAPER_CONFIGS:
            await db.subscribe(main_user_id, cfg["account_id"],
                                initial_balance=cfg.get("initial_balance", 10000.0),
                                from_start=True)
        logger.info(f"v3: main admin {main_user_id} добавлен, подписан на все стратегии")

    # Остальные пользователи — подписаны только на signal (notify_users=all в конфиге)
    signal_accounts = [cfg["account_id"] for cfg in LIVE_PAPER_CONFIGS if cfg.get("notify_users") == "all"]
    for uid in settings.allowed_user_ids:
        if uid == main_user_id:
            continue
        await db.add_user(telegram_id=uid, added_by=main_user_id)
        for acc_id in signal_accounts:
            await db.subscribe(uid, acc_id, initial_balance=10000.0, from_start=False)
        logger.info(f"v3: user {uid} добавлен, подписан на {len(signal_accounts)} signal-стратегий")


async def _subscribe_new_accounts(db, main_user_id):
    """
    Идемпотентная миграция при каждом запуске: подписывает админа на новые конфиги
    которые ещё не подписаны. Запускается ПОСЛЕ _migrate_users_v3.

    Это нужно когда добавляем новые аккаунты после первой миграции (например scalp_pack).
    Юзеры (не админ) НЕ получают подписки на новые admin_only / main_only аккаунты — только админ.
    """
    from bot.paper_trader import LIVE_PAPER_CONFIGS

    if not main_user_id:
        return

    admin_subs = await db.get_user_subscriptions(main_user_id)
    admin_account_ids = {s["account_id"] for s in admin_subs}

    new_admin_subs = 0
    for cfg in LIVE_PAPER_CONFIGS:
        acc_id = cfg["account_id"]
        if acc_id not in admin_account_ids:
            await db.subscribe(
                main_user_id, acc_id,
                initial_balance=cfg.get("initial_balance", 10000.0),
                from_start=True,
            )
            new_admin_subs += 1
            logger.info(f"  + admin подписан на новый аккаунт: {acc_id}")
    if new_admin_subs:
        logger.info(f"v3: admin получил {new_admin_subs} новых подписок (всего конфигов {len(LIVE_PAPER_CONFIGS)})")


async def _check_telegram_api(app, timeout: float = 6.0) -> tuple[bool, Optional[str]]:
    """
    Прямая проверка доступности Telegram Bot API через get_me().
    Возвращает (ok, error_message). Используется health monitor'ом, чтобы
    отличать «вообще нет связи» от «данные с биржи не приходят».
    """
    try:
        await asyncio.wait_for(app.bot.get_me(), timeout=timeout)
        return True, None
    except asyncio.TimeoutError:
        return False, f"timeout >{timeout:.0f}s"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


async def _flush_pending_alerts(db, notify_user_fn, admin_uid: int) -> int:
    """
    Шлёт накопленные недоставленные алерты по порядку. При первом фейле останавливается
    (нет смысла стучаться дальше — канал лежит). Возвращает количество доставленных.
    """
    pending = await db.get_undelivered_alerts(admin_uid, limit=50)
    if not pending:
        return 0
    delivered = 0
    for row in pending:
        ok = await notify_user_fn(admin_uid, row["text"])
        if ok:
            await db.mark_alert_delivered(row["id"])
            delivered += 1
        else:
            await db.record_alert_attempt(row["id"])
            break  # канал всё ещё лежит — остальные подождут до следующего тика
    if delivered:
        logger.info(f"Health monitor: доставлено {delivered} pending алертов из очереди")
    return delivered


async def _send_or_queue(db, notify_user_fn, admin_uid: int, text: str) -> bool:
    """Отправляет алерт сразу. Если канал лежит — кладёт в очередь и возвращает False."""
    ok = await notify_user_fn(admin_uid, text)
    if not ok:
        await db.enqueue_alert(admin_uid, text)
        logger.warning("Health monitor: алерт не доставлен — поставлен в очередь")
    return ok


async def _health_monitor_loop(paper_trader, db, app, notify_user_fn, admin_uid):
    """
    Раз в час:
      1. Проверяет Telegram API напрямую (get_me) и обновляет статус в paper_trader
      2. Дренирует очередь pending_alerts (доставка ранее накопленных)
      3. Делает health_check, при проблемах формирует алерт.
         При фейле доставки алерт уходит в pending_alerts — следующий тик повторит.
    Алерт не дублируется: те же проблемы не ре-шлются раньше 6ч.
    """
    if not admin_uid:
        logger.warning("Health monitor: admin_uid не задан, алерты не будут отправляться")
        return

    await asyncio.sleep(300)  # Первая проверка через 5 минут после старта

    last_alert_issues: set = set()
    last_alert_sent_at: Optional[datetime] = None

    while True:
        try:
            # 1. Проверка Telegram API канала
            tg_ok, tg_err = await _check_telegram_api(app)

            # 2. Подсчёт текущей очереди + дренаж если канал жив
            pending_before = await db.count_undelivered_alerts(admin_uid)
            if tg_ok and pending_before:
                await _flush_pending_alerts(db, notify_user_fn, admin_uid)
            pending_after = await db.count_undelivered_alerts(admin_uid)

            # Прокидываем статус во внутреннее состояние paper_trader (для health_check + UI)
            paper_trader.update_external_status(
                telegram_api_ok=tg_ok,
                telegram_api_error=tg_err,
                pending_alerts_count=pending_after,
            )

            # 3. Health check теперь видит и Telegram API, и очередь
            hc = paper_trader.health_check()
            current_issues = set(hc["issues"])

            now = datetime.utcnow()
            if current_issues:
                new_issues = current_issues - last_alert_issues
                time_since_last = (now - last_alert_sent_at) if last_alert_sent_at else timedelta(days=999)
                should_alert = bool(new_issues) or time_since_last > timedelta(hours=6)

                if should_alert:
                    msg = "⚠️ ALERT — обнаружены проблемы в работе бота\n"
                    msg += "━━━━━━━━━━━━━━━━━━━━\n\n"
                    for issue in hc["issues"]:
                        msg += f"• {issue}\n"
                    msg += "\nПроверь логи на сервере или нажми «📈 Мониторинг» в админ-панели."
                    delivered = await _send_or_queue(db, notify_user_fn, admin_uid, msg)
                    last_alert_sent_at = now
                    last_alert_issues = current_issues
                    if delivered:
                        logger.warning(f"Health monitor: отправлен алерт админу ({len(hc['issues'])} проблем)")
                    else:
                        logger.warning(
                            f"Health monitor: алерт ({len(hc['issues'])} проблем) в очереди, "
                            f"будет доставлен после восстановления Telegram API"
                        )
            else:
                if last_alert_issues:
                    msg = "✅ Восстановление — проблемы устранены\n━━━━━━━━━━━━━━━━━━━━\nБот снова работает штатно."
                    await _send_or_queue(db, notify_user_fn, admin_uid, msg)
                    logger.info("Health monitor: отправлено уведомление о восстановлении")
                    last_alert_issues = set()
                    last_alert_sent_at = None

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Health monitor error: {e}", exc_info=True)

        await asyncio.sleep(3600)  # Раз в час


async def _wait_for_network(timeout_total: int = 180, check_interval: int = 5) -> bool:
    """
    Ждёт доступности Telegram API перед стартом бота. Переживает race с VPN на ребуте:
    после ребута sing-box поднимает туннель ~30-60с, а бот стартует сразу — без ожидания
    get_me() падал бы с NetworkError.
    """
    import aiohttp
    import time as _time
    deadline = _time.monotonic() + timeout_total
    attempt = 0
    while _time.monotonic() < deadline:
        attempt += 1
        try:
            timeout = aiohttp.ClientTimeout(total=8)
            async with aiohttp.ClientSession(timeout=timeout) as s:
                async with s.get("https://api.telegram.org/") as r:
                    if r.status in (200, 301, 302, 401, 404):
                        logger.info(f"Сеть готова (попытка {attempt}, Telegram HTTP {r.status})")
                        return True
        except Exception as e:
            logger.warning(f"Сеть не готова, ждём VPN (попытка {attempt}): {type(e).__name__}")
        await asyncio.sleep(check_interval)
    logger.error(f"Telegram API недоступен за {timeout_total}с — стартуем как есть (watchdog поднимет VPN)")
    return False


async def run_with_telegram(engine: TradingEngine) -> None:
    """Запуск с Telegram ботом + Paper Trader."""
    # Pre-flight: дождаться готовности сети (VPN мог ещё не подняться после ребута)
    await _wait_for_network()

    telegram = TelegramBot(settings, engine)
    app = telegram.build()

    await engine.start()

    tf = engine.strategy.timeframe if engine.strategy else "1h"
    interval_map = {"1m": 30, "5m": 60, "15m": 120, "30m": 300, "1h": 600, "4h": 1800}
    interval = interval_map.get(tf, 300)

    # Paper Trader — параллельные демо-счета
    paper_db = Database(settings.db_path)
    await paper_db.connect()

    main_user_id = settings.main_user_id

    async def notify_user(user_id: int, text: str) -> bool:
        """Отправляет уведомление пользователю. Возвращает True/False для health monitor.
        False означает, что вызывающая сторона должна сама решить — ретраить или класть в очередь."""
        try:
            await app.bot.send_message(chat_id=user_id, text=text)
            return True
        except Exception as e:
            logger.error(f"Ошибка отправки {user_id}: {e}")
            return False

    paper_trader = PaperTrader(db=paper_db, notify_user_callback=notify_user)

    # Миграция .env → БД при первом запуске v3
    # Главный юзер (я) — admin, подписан на все стратегии
    # Остальные из TELEGRAM_ALLOWED_USERS — подписаны только на signal-стратегии
    await _migrate_users_v3(paper_db, settings, main_user_id)
    # Идемпотентная подписка админа на любые новые аккаунты (для scalp_pack и future-конфигов)
    await _subscribe_new_accounts(paper_db, main_user_id)

    # Сохраняем paper_trader в engine для доступа из Telegram UI
    engine.paper_trader = paper_trader

    async with app:
        await app.start()
        await app.updater.start_polling()
        logger.info("Telegram бот запущен")

        # Запускаем Paper Trader
        await paper_trader.start()
        paper_task = asyncio.create_task(paper_trader.run(), name="paper_trader")
        logger.info("Paper Trader запущен параллельно")

        # Health monitor: раз в час проверяет Telegram API + аккаунты, шлёт алерт админу
        # с очередью на случай отвала канала (см. pending_alerts в БД)
        monitor_task = asyncio.create_task(
            _health_monitor_loop(paper_trader, paper_db, app, notify_user, main_user_id),
            name="health_monitor",
        )
        logger.info("Health monitor запущен (раз в час, с очередью pending_alerts)")

        try:
            while engine._running:
                await asyncio.sleep(60)
        except (KeyboardInterrupt, asyncio.CancelledError):
            logger.info("Получен сигнал остановки")
        finally:
            await paper_trader.stop()
            paper_task.cancel()
            monitor_task.cancel()
            await paper_db.close()
            await app.updater.stop()
            await app.stop()
            await engine.stop()


async def run_without_telegram(engine: TradingEngine) -> None:
    """Запуск без Telegram (только торговля)."""
    await engine.start()

    tf = engine.strategy.timeframe if engine.strategy else "1h"
    interval_map = {"1m": 30, "5m": 60, "15m": 120, "30m": 300, "1h": 600, "4h": 1800}
    interval = interval_map.get(tf, 300)

    try:
        await run_trading_loop(engine, interval=interval)
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("Получен сигнал остановки")
    finally:
        await engine.stop()


# === Бэктест ===

async def run_backtest(strategy_name: str, symbol: str, balance: float,
                       date_from: str = None, date_to: str = None,
                       save_chart: bool = True) -> None:
    """Запуск бэктеста одной стратегии."""
    if strategy_name not in STRATEGY_MAP:
        logger.error(f"Стратегия '{strategy_name}' не найдена. Доступные: {list(STRATEGY_MAP.keys())}")
        return

    strategy = STRATEGY_MAP[strategy_name]()
    logger.info(f"Бэктест: {strategy.name} на {symbol}")

    # Загружаем данные
    since = parse_date(date_from) if date_from else None
    until = parse_date(date_to) if date_to else None
    ohlcv = await fetch_ohlcv_range(symbol, strategy.timeframe, since, until)

    if len(ohlcv) < strategy.min_candles:
        logger.error(f"Недостаточно данных: {len(ohlcv)} свечей (нужно минимум {strategy.min_candles})")
        return

    # Запускаем бэктест
    risk_params = settings.get_risk_params()
    bt = Backtester(
        strategy=strategy,
        initial_balance=balance,
        risk_per_trade_pct=risk_params["risk_per_trade_pct"],
        leverage=risk_params["max_leverage"],
        stop_loss_pct=risk_params["stop_loss_pct"],
        take_profit_pct=risk_params["take_profit_pct"],
    )

    result = bt.run(ohlcv, symbol)
    print("\n" + result.summary() + "\n")

    # Сохраняем график
    if save_chart:
        from backtesting.visualizer import plot_equity_curve
        chart_path = f"data/backtest_{strategy_name}_{symbol.replace('/', '_')}.png"
        plot_equity_curve(result, save_path=chart_path)
        print(f"График сохранён: {chart_path}")

    # Сохраняем Excel-отчёт
    from backtesting.excel_export import export_single_result
    xlsx_path = f"data/backtest_{strategy_name}_{symbol.replace('/', '_')}.xlsx"
    with open(xlsx_path, "wb") as f:
        f.write(export_single_result(result))
    print(f"Excel-отчёт сохранён: {xlsx_path}")


# === Сравнение стратегий ===

async def run_compare(strategy_names: list[str], symbol: str, balance: float,
                      date_from: str = None, date_to: str = None) -> None:
    """Сравнение нескольких стратегий на одних данных."""
    from backtesting.visualizer import (
        plot_comparison, format_comparison_table,
    )

    # Валидируем стратегии
    valid_strategies = []
    for name in strategy_names:
        if name in STRATEGY_MAP:
            valid_strategies.append(name)
        else:
            logger.warning(f"Стратегия '{name}' не найдена, пропускаю")

    if not valid_strategies:
        logger.error("Нет валидных стратегий для сравнения")
        return

    print(f"\nСравнение {len(valid_strategies)} стратегий на {symbol}...\n")

    # Загружаем данные один раз (используем самый мелкий таймфрейм)
    # Но каждая стратегия может иметь свой таймфрейм, поэтому загружаем отдельно
    since = parse_date(date_from) if date_from else None
    until = parse_date(date_to) if date_to else None

    risk_params = settings.get_risk_params()
    results = []

    for name in valid_strategies:
        strategy = STRATEGY_MAP[name]()
        print(f"  Тестирую {strategy.name}...", end=" ", flush=True)

        ohlcv = await fetch_ohlcv_range(symbol, strategy.timeframe, since, until)

        if len(ohlcv) < strategy.min_candles:
            print(f"ПРОПУСК (мало данных: {len(ohlcv)})")
            continue

        bt = Backtester(
            strategy=strategy,
            initial_balance=balance,
            risk_per_trade_pct=risk_params["risk_per_trade_pct"],
            leverage=risk_params["max_leverage"],
            stop_loss_pct=risk_params["stop_loss_pct"],
            take_profit_pct=risk_params["take_profit_pct"],
        )

        result = bt.run(ohlcv, symbol)
        results.append(result)
        print(f"OK | PnL: {result.total_pnl_pct:+.1f}% | Win Rate: {result.win_rate:.0f}%")

    if not results:
        print("\nНет результатов для сравнения.")
        return

    # Выводим таблицу
    print("\n" + format_comparison_table(results) + "\n")

    # Сохраняем график
    chart_path = f"data/compare_{symbol.replace('/', '_')}.png"
    plot_comparison(results, save_path=chart_path)
    print(f"Сравнительный график сохранён: {chart_path}")

    # Сохраняем индивидуальные графики
    from backtesting.visualizer import plot_equity_curve
    for r in results:
        path = f"data/backtest_{r.strategy}_{symbol.replace('/', '_')}.png"
        plot_equity_curve(r, save_path=path)

    print(f"Индивидуальные графики сохранены в data/")

    # Сохраняем Excel-отчёт
    from backtesting.excel_export import export_comparison
    xlsx_path = f"data/compare_{symbol.replace('/', '_')}.xlsx"
    with open(xlsx_path, "wb") as f:
        f.write(export_comparison(results))
    print(f"Excel-отчёт сохранён: {xlsx_path}\n")


# === Точка входа ===

def main():
    parser = argparse.ArgumentParser(
        description="Crypto Trading Bot",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Примеры:
  python main.py                                    # Бот + Telegram
  python main.py --no-telegram                      # Только бот
  python main.py --backtest                         # Бэктест (по умолчанию)
  python main.py --backtest --strategy grid --symbol ETH/USDT
  python main.py --backtest --from 2025-01-01 --to 2025-06-01
  python main.py --compare                          # Сравнить ВСЕ стратегии
  python main.py --compare --strategies ema_crossover,grid,supertrend
  python main.py --compare --symbol SOL/USDT --from 2025-03-01
        """,
    )
    parser.add_argument("--no-telegram", action="store_true", help="Запуск без Telegram")
    parser.add_argument("--backtest", action="store_true", help="Режим бэктеста")
    parser.add_argument("--compare", action="store_true", help="Сравнение стратегий")
    parser.add_argument("--strategy", type=str, default=None, help="Стратегия для бэктеста")
    parser.add_argument("--strategies", type=str, default=None,
                        help="Стратегии для сравнения (через запятую)")
    parser.add_argument("--symbol", type=str, default=None, help="Торговая пара")
    parser.add_argument("--balance", type=float, default=None, help="Стартовый баланс")
    parser.add_argument("--from", dest="date_from", type=str, default=None,
                        help="Дата начала (YYYY-MM-DD)")
    parser.add_argument("--to", dest="date_to", type=str, default=None,
                        help="Дата окончания (YYYY-MM-DD)")
    parser.add_argument("--no-chart", action="store_true", help="Без графиков")
    parser.add_argument("--hyperopt", action="store_true",
                        help="Оптимизация параметров стратегии (Hyperopt)")
    parser.add_argument("--trials", type=int, default=100,
                        help="Количество итераций Hyperopt (по умолчанию 100)")

    args = parser.parse_args()

    # Создаём папку для данных
    os.makedirs("data", exist_ok=True)

    symbol = args.symbol or settings.default_symbol
    balance = args.balance or settings.paper_balance

    if args.hyperopt:
        # Оптимизация параметров
        from backtesting.hyperopt import optimize_strategy, STRATEGY_FACTORIES

        strategy_name = args.strategy or "ema_crossover"
        if strategy_name not in STRATEGY_FACTORIES:
            logger.error(f"Hyperopt не поддерживает '{strategy_name}'. Доступные: {list(STRATEGY_FACTORIES.keys())}")
            sys.exit(1)

        async def run_hyperopt():
            since = parse_date(args.date_from) if args.date_from else None
            until = parse_date(args.date_to) if args.date_to else None
            ohlcv = await fetch_ohlcv_range(symbol, "4h", since, until)
            print(f"\nHyperopt: {strategy_name} | {len(ohlcv)} свечей | {args.trials} итераций\n")

            result = optimize_strategy(
                strategy_factory=STRATEGY_FACTORIES[strategy_name],
                ohlcv_data=ohlcv, symbol=symbol,
                initial_balance=balance, leverage=5,
                n_trials=args.trials, metric="sharpe",
            )
            r = result["result"]
            print(f"\n{'='*60}")
            print(f"Лучшие параметры: {result['best_params']}")
            print(f"PnL: {r.total_pnl:+.2f} ({r.total_pnl_pct:+.1f}%)")
            print(f"Win Rate: {r.win_rate:.1f}% | Сделок: {r.total_trades}")
            print(f"Просадка: {r.max_drawdown_pct:.1f}% | Profit Factor: {r.profit_factor:.2f}")
            print(f"Sharpe: {r.sharpe_ratio:.2f}")
            print(f"{'='*60}\n")

        asyncio.run(run_hyperopt())

    elif args.compare:
        # Сравнение стратегий
        if args.strategies:
            strategy_names = [s.strip() for s in args.strategies.split(",")]
        else:
            strategy_names = list(STRATEGY_MAP.keys())

        asyncio.run(run_compare(
            strategy_names, symbol, balance,
            args.date_from, args.date_to,
        ))

    elif args.backtest:
        strategy = args.strategy or settings.default_strategy.value
        asyncio.run(run_backtest(
            strategy, symbol, balance,
            args.date_from, args.date_to,
            save_chart=not args.no_chart,
        ))

    elif args.no_telegram:
        engine = TradingEngine(settings)
        if args.strategy:
            engine.set_strategy(args.strategy)
        if args.symbol:
            engine.set_symbols([args.symbol])
        asyncio.run(run_without_telegram(engine))

    else:
        if not settings.telegram_bot_token:
            logger.error("TELEGRAM_BOT_TOKEN не задан! Используйте --no-telegram или задайте токен в .env")
            sys.exit(1)
        engine = TradingEngine(settings)
        if args.strategy:
            engine.set_strategy(args.strategy)
        if args.symbol:
            engine.set_symbols([args.symbol])
        asyncio.run(run_with_telegram(engine))


if __name__ == "__main__":
    main()
