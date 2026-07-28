"""
SQLite база данных для хранения сделок, состояния бота и статистики.
Использует aiosqlite для асинхронной работы.
"""

import aiosqlite
import json
import logging
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)


class Database:
    def __init__(self, db_path: str = "data/trades.db"):
        self.db_path = db_path
        self._db: Optional[aiosqlite.Connection] = None

    async def connect(self) -> None:
        """Подключение и инициализация таблиц."""
        self._db = await aiosqlite.connect(self.db_path)
        self._db.row_factory = aiosqlite.Row
        await self._create_tables()
        logger.info(f"База данных инициализирована: {self.db_path}")

    async def _create_tables(self) -> None:
        await self._db.executescript("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                exchange TEXT NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                type TEXT NOT NULL,
                amount REAL NOT NULL,
                price REAL NOT NULL,
                cost REAL NOT NULL,
                fee REAL DEFAULT 0,
                pnl REAL DEFAULT 0,
                strategy TEXT,
                order_id TEXT,
                status TEXT DEFAULT 'open',
                leverage INTEGER DEFAULT 1,
                stop_loss REAL,
                take_profit REAL,
                notes TEXT,
                opened_at TEXT NOT NULL,
                closed_at TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS balance_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                exchange TEXT NOT NULL,
                balance REAL NOT NULL,
                unrealized_pnl REAL DEFAULT 0,
                timestamp TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS bot_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS strategy_signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy TEXT NOT NULL,
                symbol TEXT NOT NULL,
                signal TEXT NOT NULL,
                strength REAL DEFAULT 0,
                indicators TEXT,
                timestamp TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol);
            CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status);
            CREATE INDEX IF NOT EXISTS idx_trades_strategy ON trades(strategy);
            CREATE INDEX IF NOT EXISTS idx_balance_timestamp ON balance_history(timestamp);

            -- Пользователи бота (v3)
            CREATE TABLE IF NOT EXISTS users (
                telegram_id INTEGER PRIMARY KEY,
                username TEXT,
                display_name TEXT,
                is_admin INTEGER DEFAULT 0,
                added_by INTEGER,
                added_at TEXT DEFAULT CURRENT_TIMESTAMP,
                active INTEGER DEFAULT 1
            );

            -- Подписки пользователей на стратегии
            -- subscribed_at: с какого момента пользователь видит сделки стратегии
            -- individual_balance: персональный виртуальный баланс с момента подписки
            CREATE TABLE IF NOT EXISTS user_subscriptions (
                telegram_id INTEGER NOT NULL,
                account_id TEXT NOT NULL,
                subscribed_at TEXT DEFAULT CURRENT_TIMESTAMP,
                initial_balance REAL DEFAULT 10000,
                from_start INTEGER DEFAULT 0,
                PRIMARY KEY (telegram_id, account_id),
                FOREIGN KEY (telegram_id) REFERENCES users(telegram_id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_subs_account ON user_subscriptions(account_id);

            -- Очередь алертов для health-monitor: при отвале связи (VPN/Telegram)
            -- сообщения пишутся сюда и ретраятся на следующих тиках.
            CREATE TABLE IF NOT EXISTS pending_alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                text TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                last_attempt_at TEXT,
                attempts INTEGER NOT NULL DEFAULT 0,
                delivered_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_pending_alerts_user_undelivered
                ON pending_alerts(user_id, delivered_at);
        """)
        await self._db.commit()

    # === Pending alerts (health monitor durability) ===

    async def enqueue_alert(self, user_id: int, text: str) -> int:
        cursor = await self._db.execute(
            "INSERT INTO pending_alerts (user_id, text, created_at) VALUES (?, ?, ?)",
            (user_id, text, datetime.utcnow().isoformat()),
        )
        await self._db.commit()
        return cursor.lastrowid

    async def get_undelivered_alerts(self, user_id: int, limit: int = 50) -> list[dict]:
        async with self._db.execute(
            "SELECT id, user_id, text, created_at, attempts, last_attempt_at "
            "FROM pending_alerts WHERE user_id = ? AND delivered_at IS NULL "
            "ORDER BY id ASC LIMIT ?",
            (user_id, limit),
        ) as cursor:
            rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def count_undelivered_alerts(self, user_id: int) -> int:
        async with self._db.execute(
            "SELECT COUNT(*) FROM pending_alerts WHERE user_id = ? AND delivered_at IS NULL",
            (user_id,),
        ) as cursor:
            row = await cursor.fetchone()
        return int(row[0]) if row else 0

    async def mark_alert_delivered(self, alert_id: int) -> None:
        await self._db.execute(
            "UPDATE pending_alerts SET delivered_at = ? WHERE id = ?",
            (datetime.utcnow().isoformat(), alert_id),
        )
        await self._db.commit()

    async def record_alert_attempt(self, alert_id: int) -> None:
        await self._db.execute(
            "UPDATE pending_alerts SET attempts = attempts + 1, last_attempt_at = ? WHERE id = ?",
            (datetime.utcnow().isoformat(), alert_id),
        )
        await self._db.commit()

    async def cleanup_delivered_alerts(self, older_than_days: int = 7) -> int:
        cutoff = (datetime.utcnow() - timedelta(days=older_than_days)).isoformat()
        cursor = await self._db.execute(
            "DELETE FROM pending_alerts WHERE delivered_at IS NOT NULL AND delivered_at < ?",
            (cutoff,),
        )
        await self._db.commit()
        return cursor.rowcount or 0

    # === Trades ===

    async def insert_trade(self, trade: dict) -> int:
        """Записывает сделку.

        PaperTrader пишет уже закрытую сделку одной вставкой, а не парой
        insert_trade/close_trade, поэтому pnl и closed_at обязаны попадать
        в INSERT: без них каждая закрытая сделка оседала в истории с
        pnl=0 и пустым closed_at, и статистика по стратегиям была нулевой.
        """
        cursor = await self._db.execute(
            """INSERT INTO trades (exchange, symbol, side, type, amount, price, cost, fee,
               pnl, strategy, order_id, status, leverage, stop_loss, take_profit, notes,
               opened_at, closed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                trade["exchange"], trade["symbol"], trade["side"], trade["type"],
                trade["amount"], trade["price"], trade["cost"], trade.get("fee", 0),
                trade.get("pnl", 0.0),
                trade.get("strategy"), trade.get("order_id"), trade.get("status", "open"),
                trade.get("leverage", 1), trade.get("stop_loss"), trade.get("take_profit"),
                trade.get("notes"), trade.get("opened_at", datetime.utcnow().isoformat()),
                trade.get("closed_at"),
            ),
        )
        await self._db.commit()
        return cursor.lastrowid

    async def close_trade(self, trade_id: int, close_price: float, pnl: float) -> None:
        """Закрывает сделку."""
        await self._db.execute(
            """UPDATE trades SET status='closed', pnl=?, closed_at=?
               WHERE id=?""",
            (pnl, datetime.utcnow().isoformat(), trade_id),
        )
        await self._db.commit()

    async def get_open_trades(self, symbol: Optional[str] = None) -> list[dict]:
        """Получает открытые сделки."""
        if symbol:
            cursor = await self._db.execute(
                "SELECT * FROM trades WHERE status='open' AND symbol=?", (symbol,)
            )
        else:
            cursor = await self._db.execute("SELECT * FROM trades WHERE status='open'")
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def get_trades_history(self, limit: int = 50, strategy: Optional[str] = None) -> list[dict]:
        """Получает историю сделок."""
        if strategy:
            cursor = await self._db.execute(
                "SELECT * FROM trades WHERE strategy=? ORDER BY created_at DESC LIMIT ?",
                (strategy, limit),
            )
        else:
            cursor = await self._db.execute(
                "SELECT * FROM trades ORDER BY created_at DESC LIMIT ?", (limit,)
            )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def get_daily_pnl(self, date: Optional[str] = None) -> float:
        """Получает PnL за день."""
        if date is None:
            date = datetime.utcnow().strftime("%Y-%m-%d")
        cursor = await self._db.execute(
            "SELECT COALESCE(SUM(pnl), 0) as total FROM trades WHERE closed_at LIKE ? AND status='closed'",
            (f"{date}%",),
        )
        row = await cursor.fetchone()
        return float(row["total"]) if row else 0.0

    async def get_total_pnl(self) -> float:
        """Получает общий PnL."""
        cursor = await self._db.execute(
            "SELECT COALESCE(SUM(pnl), 0) as total FROM trades WHERE status='closed'"
        )
        row = await cursor.fetchone()
        return float(row["total"]) if row else 0.0

    async def get_strategy_stats(self, strategy: str) -> dict:
        """Статистика по стратегии."""
        cursor = await self._db.execute(
            """SELECT
                COUNT(*) as total_trades,
                SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as winning,
                SUM(CASE WHEN pnl < 0 THEN 1 ELSE 0 END) as losing,
                COALESCE(SUM(pnl), 0) as total_pnl,
                COALESCE(AVG(pnl), 0) as avg_pnl,
                COALESCE(MAX(pnl), 0) as best_trade,
                COALESCE(MIN(pnl), 0) as worst_trade
               FROM trades WHERE strategy=? AND status='closed'""",
            (strategy,),
        )
        row = await cursor.fetchone()
        if not row:
            return {}
        result = dict(row)
        total = result["total_trades"]
        result["win_rate"] = (result["winning"] / total * 100) if total > 0 else 0
        return result

    # === Balance History ===

    async def record_balance(self, exchange: str, balance: float, unrealized_pnl: float = 0) -> None:
        """Записывает баланс."""
        await self._db.execute(
            "INSERT INTO balance_history (exchange, balance, unrealized_pnl) VALUES (?, ?, ?)",
            (exchange, balance, unrealized_pnl),
        )
        await self._db.commit()

    async def get_peak_balance(self) -> float:
        """Возвращает пиковый баланс (для расчёта drawdown)."""
        cursor = await self._db.execute(
            "SELECT COALESCE(MAX(balance), 0) as peak FROM balance_history"
        )
        row = await cursor.fetchone()
        return float(row["peak"]) if row else 0.0

    async def get_balance_history(self, limit: int = 100) -> list[dict]:
        """История баланса."""
        cursor = await self._db.execute(
            "SELECT * FROM balance_history ORDER BY timestamp DESC LIMIT ?", (limit,)
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    # === Bot State ===

    async def set_state(self, key: str, value) -> None:
        """Сохраняет состояние."""
        val = json.dumps(value) if not isinstance(value, str) else value
        await self._db.execute(
            "INSERT OR REPLACE INTO bot_state (key, value, updated_at) VALUES (?, ?, ?)",
            (key, val, datetime.utcnow().isoformat()),
        )
        await self._db.commit()

    async def get_state(self, key: str, default=None):
        """Получает состояние."""
        cursor = await self._db.execute("SELECT value FROM bot_state WHERE key=?", (key,))
        row = await cursor.fetchone()
        if not row:
            return default
        try:
            return json.loads(row["value"])
        except (json.JSONDecodeError, TypeError):
            return row["value"]

    # === Users (v3) ===

    async def add_user(self, telegram_id: int, username: Optional[str] = None,
                       display_name: Optional[str] = None, is_admin: bool = False,
                       added_by: Optional[int] = None) -> bool:
        """Добавляет пользователя. Возвращает True если добавлен, False если уже существует."""
        try:
            await self._db.execute(
                "INSERT INTO users (telegram_id, username, display_name, is_admin, added_by) VALUES (?, ?, ?, ?, ?)",
                (telegram_id, username, display_name, 1 if is_admin else 0, added_by),
            )
            await self._db.commit()
            return True
        except Exception:
            return False

    async def remove_user(self, telegram_id: int) -> None:
        """Деактивирует пользователя (не удаляет для аудита)."""
        await self._db.execute("UPDATE users SET active = 0 WHERE telegram_id = ?", (telegram_id,))
        await self._db.execute("DELETE FROM user_subscriptions WHERE telegram_id = ?", (telegram_id,))
        await self._db.commit()

    async def get_user(self, telegram_id: int) -> Optional[dict]:
        cursor = await self._db.execute(
            "SELECT * FROM users WHERE telegram_id = ? AND active = 1", (telegram_id,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def list_users(self, include_inactive: bool = False) -> list[dict]:
        if include_inactive:
            cursor = await self._db.execute("SELECT * FROM users ORDER BY added_at DESC")
        else:
            cursor = await self._db.execute(
                "SELECT * FROM users WHERE active = 1 ORDER BY added_at DESC"
            )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def update_user_info(self, telegram_id: int, username: Optional[str] = None,
                                display_name: Optional[str] = None) -> None:
        if username is not None:
            await self._db.execute(
                "UPDATE users SET username = ? WHERE telegram_id = ?", (username, telegram_id)
            )
        if display_name is not None:
            await self._db.execute(
                "UPDATE users SET display_name = ? WHERE telegram_id = ?", (display_name, telegram_id)
            )
        await self._db.commit()

    # === Subscriptions ===

    async def subscribe(self, telegram_id: int, account_id: str,
                        initial_balance: float = 10000.0, from_start: bool = False) -> None:
        """Подписывает пользователя на стратегию."""
        await self._db.execute(
            """INSERT OR REPLACE INTO user_subscriptions
               (telegram_id, account_id, initial_balance, from_start) VALUES (?, ?, ?, ?)""",
            (telegram_id, account_id, initial_balance, 1 if from_start else 0),
        )
        await self._db.commit()

    async def unsubscribe(self, telegram_id: int, account_id: str) -> None:
        await self._db.execute(
            "DELETE FROM user_subscriptions WHERE telegram_id = ? AND account_id = ?",
            (telegram_id, account_id),
        )
        await self._db.commit()

    async def get_subscribers(self, account_id: str) -> list[int]:
        """Возвращает telegram_id всех активных подписчиков на стратегию."""
        cursor = await self._db.execute(
            """SELECT s.telegram_id FROM user_subscriptions s
               JOIN users u ON u.telegram_id = s.telegram_id
               WHERE s.account_id = ? AND u.active = 1""",
            (account_id,),
        )
        rows = await cursor.fetchall()
        return [r["telegram_id"] for r in rows]

    async def get_user_subscriptions(self, telegram_id: int) -> list[dict]:
        cursor = await self._db.execute(
            "SELECT * FROM user_subscriptions WHERE telegram_id = ?", (telegram_id,)
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def get_subscription(self, telegram_id: int, account_id: str) -> Optional[dict]:
        cursor = await self._db.execute(
            "SELECT * FROM user_subscriptions WHERE telegram_id = ? AND account_id = ?",
            (telegram_id, account_id),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    # === Signals ===

    async def record_signal(self, strategy: str, symbol: str, signal: str,
                            strength: float = 0, indicators: Optional[dict] = None) -> None:
        """Записывает сигнал стратегии."""
        await self._db.execute(
            "INSERT INTO strategy_signals (strategy, symbol, signal, strength, indicators) VALUES (?, ?, ?, ?, ?)",
            (strategy, symbol, signal, strength, json.dumps(indicators) if indicators else None),
        )
        await self._db.commit()

    async def close(self) -> None:
        if self._db:
            await self._db.close()
