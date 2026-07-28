"""
Telegram бот — интерфейс управления трейдинг-ботом.
Позволяет управлять ботом, смотреть статистику, менять настройки.
"""

import logging
from typing import Optional
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    ContextTypes, MessageHandler, filters,
)

from config.settings import Settings, StrategyName, RiskLevel, TradingMode
from bot.engine import TradingEngine
from strategies import STRATEGY_MAP

logger = logging.getLogger(__name__)


class TelegramBot:
    """Telegram бот для управления трейдинг ботом."""

    def __init__(self, settings: Settings, engine: TradingEngine):
        self.settings = settings
        self.engine = engine
        self._app: Application = None

    def _main_menu_keyboard(self, user_id: Optional[int] = None) -> InlineKeyboardMarkup:
        """Возвращает клавиатуру главного меню. Админу добавляется кнопка управления."""
        keyboard = [
            [InlineKeyboardButton("📊 Статус", callback_data="status")],
            [InlineKeyboardButton("📈 Открытые позиции", callback_data="positions")],
            [InlineKeyboardButton("📜 История сигналов", callback_data="history"),
             InlineKeyboardButton("📋 Логи анализа", callback_data="paper_logs")],
            [InlineKeyboardButton("📖 Информация по стратегиям", callback_data="strategy_info")],
            [InlineKeyboardButton("❓ Справка", callback_data="help")],
        ]

        # Админские кнопки — только для main_user
        if user_id and user_id == self.settings.main_user_id:
            keyboard.append([
                InlineKeyboardButton("⚙️ Админ-панель", callback_data="admin_menu"),
            ])
            keyboard.append([
                InlineKeyboardButton("🔬 Бэктест", callback_data="backtest_menu"),
                InlineKeyboardButton("📊 Сравнить", callback_data="compare_all"),
            ])
            keyboard.append([
                InlineKeyboardButton("🏆 Лучшие стратегии", callback_data="best_strategies"),
            ])

        return InlineKeyboardMarkup(keyboard)

    def _is_admin(self, user_id: int) -> bool:
        return user_id == self.settings.main_user_id

    @staticmethod
    def _back_button() -> list[InlineKeyboardButton]:
        """Возвращает кнопку возврата в главное меню."""
        return [InlineKeyboardButton("◀️ Главное меню", callback_data="back_main")]

    @staticmethod
    async def _show_text(query, text: str, reply_markup=None) -> None:
        """Показать текстовое сообщение в ответ на callback. Если текущее сообщение — фото
        (или иначе нет редактируемого текста), шлёт новое; иначе делает edit_message_text.
        Глотает 'Message is not modified'."""
        msg = query.message
        is_photo = bool(getattr(msg, "photo", None)) or bool(getattr(msg, "video", None))
        if is_photo or msg.text is None:
            await msg.reply_text(text, reply_markup=reply_markup)
            return
        try:
            await query.edit_message_text(text, reply_markup=reply_markup)
        except BadRequest as e:
            if "not modified" in str(e).lower():
                return
            raise

    def _back_keyboard(self) -> InlineKeyboardMarkup:
        """Возвращает клавиатуру только с кнопкой назад."""
        return InlineKeyboardMarkup([self._back_button()])

    def _is_authorized(self, user_id: int) -> bool:
        """Проверяет авторизацию (fallback через .env).
        В async-версии используй _check_auth_db — он проверяет БД."""
        if user_id == self.settings.main_user_id:
            return True
        allowed = self.settings.allowed_user_ids
        return not allowed or user_id in allowed

    async def _check_auth(self, update: Update) -> bool:
        uid = update.effective_user.id
        # Админ всегда проходит
        if uid == self.settings.main_user_id:
            return True
        # Проверяем БД
        if hasattr(self.engine, "db") and self.engine.db is not None:
            user = await self.engine.db.get_user(uid)
            if user:
                # Обновляем username/display_name при каждом входе
                username = update.effective_user.username
                display = update.effective_user.full_name
                if username != user.get("username") or display != user.get("display_name"):
                    await self.engine.db.update_user_info(uid, username, display)
                return True
        # Fallback на .env
        if self._is_authorized(uid):
            return True
        await update.message.reply_text(
            "⛔ Доступ запрещён.\n\n"
            f"Ваш ID: `{uid}`\n"
            "Попросите администратора добавить вас.",
            parse_mode="Markdown",
        )
        return False

    # === Команды ===

    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Команда /start — приветствие и главное меню."""
        if not await self._check_auth(update):
            return

        uid = update.effective_user.id
        name = update.effective_user.first_name or "Пользователь"
        greeting = f"👋 Привет, {name}!\n\n"
        if self._is_admin(uid):
            greeting += "Вы вошли как администратор.\n"
        greeting += "Выберите действие:"

        await update.message.reply_text(
            greeting,
            reply_markup=self._main_menu_keyboard(uid),
            parse_mode="Markdown",
        )

    async def cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Команда /status — статус стратегий."""
        if not await self._check_auth(update):
            return
        uid = update.effective_user.id
        text = await self._format_status_v3(uid)
        await update.message.reply_text(text)

    async def cmd_trades(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Команда /trades — открытые позиции."""
        if not await self._check_auth(update):
            return
        trades = await self.engine.db.get_open_trades()
        if not trades:
            await update.message.reply_text("📭 Нет открытых позиций")
            return

        text = "📊 *Открытые позиции:*\n\n"
        for t in trades:
            emoji = "🟢" if t["side"] == "buy" else "🔴"
            text += (
                f"{emoji} *{t['symbol']}* {t['side'].upper()}\n"
                f"  Вход: `{t['price']:.2f}` | Размер: `{t['amount']:.6f}`\n"
                f"  SL: `{t['stop_loss']:.2f}` | TP: `{t['take_profit']:.2f}`\n"
                f"  Стратегия: `{t['strategy']}`\n\n"
            )
        await update.message.reply_text(text, parse_mode="Markdown")

    async def cmd_history(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Команда /history — история сделок."""
        if not await self._check_auth(update):
            return
        trades = await self.engine.db.get_trades_history(limit=10)
        if not trades:
            await update.message.reply_text("📭 История сделок пуста")
            return

        text = "📜 *Последние 10 сделок:*\n\n"
        for t in trades:
            emoji = "✅" if t["pnl"] > 0 else "❌" if t["pnl"] < 0 else "⏳"
            text += (
                f"{emoji} {t['symbol']} {t['side'].upper()} | "
                f"PnL: `{t['pnl']:+.2f}` USDT\n"
                f"  Вход: `{t['price']:.2f}` | {t['strategy']}\n\n"
            )
        await update.message.reply_text(text, parse_mode="Markdown")

    async def cmd_strategy(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Команда /strategy [name] — показать/сменить стратегию."""
        if not await self._check_auth(update):
            return

        args = context.args
        if args:
            result = self.engine.set_strategy(args[0])
            await update.message.reply_text(result)
        else:
            keyboard = []
            for name, cls in STRATEGY_MAP.items():
                s = cls()
                keyboard.append([InlineKeyboardButton(
                    f"{'✅ ' if self.engine.strategy and self.engine.strategy.name == name else ''}"
                    f"{s.name} ({s.risk_category})",
                    callback_data=f"set_strategy_{name}",
                )])
            await update.message.reply_text(
                "📈 *Выберите стратегию:*",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown",
            )

    async def cmd_risk(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Команда /risk [level] — показать/сменить уровень риска."""
        if not await self._check_auth(update):
            return

        risk_labels = {
            "conservative": "Safe — 1% риска на сделку",
            "moderate": "Normal — 2% риска на сделку",
            "aggressive": "Bold — 4% риска на сделку",
        }
        keyboard = [
            [InlineKeyboardButton(
                f"{'✅ ' if self.settings.risk_level == level else ''}"
                f"{risk_labels.get(level.value, level.value)}",
                callback_data=f"set_risk_{level.value}",
            )]
            for level in RiskLevel
        ]

        current = self.settings.get_risk_params()
        text = (
            f"🎯 *Риск на сделку*\n\n"
            f"Текущий: `{self.settings.risk_level.value}`\n"
            f"Риск: `{current['risk_per_trade_pct']}%` от баланса\n"
            f"Макс. позиций: `{current['max_open_positions']}`\n\n"
            f"При стопе теряете {current['risk_per_trade_pct']}% баланса."
        )
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard),
                                         parse_mode="Markdown")

    async def cmd_symbol(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Команда /symbol [pair] — добавить/показать торговые пары."""
        if not await self._check_auth(update):
            return

        args = context.args
        if args:
            symbols = [s.upper() for s in args]
            self.engine.set_symbols(symbols)
            await update.message.reply_text(f"Торговые пары: {', '.join(symbols)}")
        else:
            status = await self.engine.get_status()
            await update.message.reply_text(
                f"📊 Торговые пары: {', '.join(status['symbols'])}\n\n"
                f"Используйте `/symbol BTC/USDT ETH/USDT` для изменения",
                parse_mode="Markdown",
            )

    async def cmd_backtest(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """
        Команда /backtest [strategy] [from] [to] — запуск бэктеста.
        Примеры:
          /backtest
          /backtest grid
          /backtest ema_crossover 2025-01-01 2025-06-01
        """
        if not await self._check_auth(update):
            return

        await update.message.reply_text("🔬 Запускаю бэктест... Подождите.")

        try:
            from backtesting.backtest import Backtester
            from backtesting.visualizer import plot_equity_curve
            from main import fetch_ohlcv_range, parse_date

            args = context.args or []
            strategy_name = args[0] if args else self.engine.strategy.name
            date_from = args[1] if len(args) > 1 else None
            date_to = args[2] if len(args) > 2 else None

            if strategy_name not in STRATEGY_MAP:
                await update.message.reply_text(f"Стратегия '{strategy_name}' не найдена")
                return

            strategy = STRATEGY_MAP[strategy_name]()
            symbol = self.settings.default_symbol

            # Загружаем данные с поддержкой дат
            since = parse_date(date_from) if date_from else None
            until = parse_date(date_to) if date_to else None
            ohlcv = await fetch_ohlcv_range(symbol, strategy.timeframe, since, until)

            if len(ohlcv) < strategy.min_candles:
                await update.message.reply_text(
                    f"Недостаточно данных: {len(ohlcv)} свечей (нужно {strategy.min_candles})")
                return

            # Запускаем бэктест
            risk_params = self.settings.get_risk_params()
            bt = Backtester(
                strategy=strategy,
                initial_balance=self.settings.paper_balance,
                risk_per_trade_pct=risk_params["risk_per_trade_pct"],
                leverage=risk_params["max_leverage"],
                stop_loss_pct=risk_params["stop_loss_pct"],
                take_profit_pct=risk_params["take_profit_pct"],
            )

            result = bt.run(ohlcv, symbol)
            await update.message.reply_text(
                f"```\n{result.summary()}\n```", parse_mode="Markdown"
            )

            # Отправляем график
            chart_bytes = plot_equity_curve(result)
            if chart_bytes:
                import io
                await update.message.reply_photo(
                    photo=io.BytesIO(chart_bytes),
                    caption=f"📊 {strategy.name} | {symbol}",
                )

            # Отправляем Excel-отчёт
            from backtesting.excel_export import export_single_result
            import io as _io
            xlsx_bytes = export_single_result(result)
            await update.message.reply_document(
                document=_io.BytesIO(xlsx_bytes),
                filename=f"backtest_{strategy_name}_{symbol.replace('/', '_')}.xlsx",
                caption="📋 Подробный отчёт по сделкам",
            )

            # График сделок на BTC
            from backtesting.visualizer import plot_trades_on_chart
            trades_chart = plot_trades_on_chart(result, ohlcv)
            if trades_chart:
                await update.message.reply_photo(
                    photo=_io.BytesIO(trades_chart),
                    caption=f"📈 Сделки {strategy.name} на графике {symbol}",
                )

        except Exception as e:
            logger.error(f"Ошибка бэктеста: {e}", exc_info=True)
            await update.message.reply_text(f"Ошибка бэктеста: {e}")

    async def cmd_compare(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """
        Команда /compare [strategies] — сравнение стратегий.
        Примеры:
          /compare                              — все стратегии
          /compare ema_crossover,grid,supertrend — конкретные
        """
        if not await self._check_auth(update):
            return

        await update.message.reply_text(
            "🔬 Сравниваю стратегии... Это может занять 1-2 минуты.")

        try:
            from backtesting.backtest import Backtester
            from backtesting.visualizer import (
                plot_comparison, format_comparison_table_telegram,
            )
            from main import fetch_ohlcv_range

            args = context.args or []
            if args:
                strategy_names = [s.strip() for s in args[0].split(",")]
            else:
                strategy_names = list(STRATEGY_MAP.keys())

            symbol = self.settings.default_symbol
            risk_params = self.settings.get_risk_params()
            results = []

            for name in strategy_names:
                if name not in STRATEGY_MAP:
                    continue

                strategy = STRATEGY_MAP[name]()
                ohlcv = await fetch_ohlcv_range(symbol, strategy.timeframe)

                if len(ohlcv) < strategy.min_candles:
                    continue

                bt = Backtester(
                    strategy=strategy,
                    initial_balance=self.settings.paper_balance,
                    risk_per_trade_pct=risk_params["risk_per_trade_pct"],
                    leverage=risk_params["max_leverage"],
                    stop_loss_pct=risk_params["stop_loss_pct"],
                    take_profit_pct=risk_params["take_profit_pct"],
                )

                result = bt.run(ohlcv, symbol)
                results.append(result)

            if not results:
                await update.message.reply_text("Нет результатов для сравнения")
                return

            # Отправляем текст
            text = format_comparison_table_telegram(results)
            await update.message.reply_text(text, parse_mode="Markdown")

            # Отправляем график
            chart_bytes = plot_comparison(results)
            if chart_bytes:
                import io
                await update.message.reply_photo(
                    photo=io.BytesIO(chart_bytes),
                    caption=f"📊 Сравнение {len(results)} стратегий | {symbol}",
                )

            # Отправляем Excel-отчёт
            from backtesting.excel_export import export_comparison
            import io as _io
            xlsx_bytes = export_comparison(results)
            await update.message.reply_document(
                document=_io.BytesIO(xlsx_bytes),
                filename=f"comparison_{symbol.replace('/', '_')}.xlsx",
                caption="📋 Подробный отчёт — сравнение + сделки по каждой стратегии",
            )

        except Exception as e:
            logger.error(f"Ошибка сравнения: {e}", exc_info=True)
            await update.message.reply_text(f"Ошибка сравнения: {e}")

    async def cmd_stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Команда /stats [strategy] — статистика по стратегии."""
        if not await self._check_auth(update):
            return

        strategy_name = context.args[0] if context.args else (
            self.engine.strategy.name if self.engine.strategy else None
        )
        if not strategy_name:
            await update.message.reply_text("Укажите стратегию: /stats ema_crossover")
            return

        stats = await self.engine.db.get_strategy_stats(strategy_name)
        if not stats or stats.get("total_trades", 0) == 0:
            await update.message.reply_text(f"Нет данных для стратегии '{strategy_name}'")
            return

        text = (
            f"📊 *Статистика: {strategy_name}*\n\n"
            f"Сделок: `{stats['total_trades']}`\n"
            f"Win Rate: `{stats['win_rate']:.1f}%`\n"
            f"Прибыльных: `{stats['winning']}` | Убыточных: `{stats['losing']}`\n"
            f"Общий PnL: `{stats['total_pnl']:+.2f}` USDT\n"
            f"Средний PnL: `{stats['avg_pnl']:+.2f}` USDT\n"
            f"Лучшая сделка: `{stats['best_trade']:+.2f}` USDT\n"
            f"Худшая сделка: `{stats['worst_trade']:+.2f}` USDT"
        )
        await update.message.reply_text(text, parse_mode="Markdown")

    async def cmd_mode(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Команда /mode — переключение paper/live и spot/futures."""
        if not await self._check_auth(update):
            return

        keyboard = [
            [InlineKeyboardButton(
                f"{'✅ ' if self.settings.paper_trading else ''}Paper Trading",
                callback_data="set_mode_paper",
            ),
             InlineKeyboardButton(
                 f"{'✅ ' if not self.settings.paper_trading else ''}Live Trading",
                 callback_data="set_mode_live",
             )],
            [InlineKeyboardButton(
                f"{'✅ ' if self.settings.trading_mode == TradingMode.SPOT else ''}Спот",
                callback_data="set_type_spot",
            ),
             InlineKeyboardButton(
                 f"{'✅ ' if self.settings.trading_mode == TradingMode.FUTURES else ''}Фьючерсы",
                 callback_data="set_type_futures",
             )],
        ]

        await update.message.reply_text(
            f"⚙️ *Режим торговли*\n\n"
            f"Торговля: `{'Paper' if self.settings.paper_trading else 'LIVE'}`\n"
            f"Тип: `{self.settings.trading_mode.value}`\n"
            f"Биржа: `{self.settings.default_exchange}`",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown",
        )

    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Команда /help."""
        if not await self._check_auth(update):
            return

        text = (
            "🤖 *Crypto Trading Bot — Команды:*\n\n"
            "*Основные:*\n"
            "/start — Главное меню\n"
            "/status — Статус бота\n"
            "/balance — Баланс и PnL\n"
            "/trades — Открытые позиции\n"
            "/history — История сделок\n\n"
            "*Настройки:*\n"
            "/strategy — Выбор стратегии\n"
            "/risk — Управление рисками\n"
            "/symbol — Торговые пары\n"
            "/mode — Режим торговли\n\n"
            "*Аналитика:*\n"
            "/backtest — Бэктест стратегии\n"
            "/compare — Сравнение стратегий\n"
            "/stats — Статистика стратегии\n\n"
            "*Справка:*\n"
            "/help — Эта справка\n"
            "/info — Подробное описание стратегий\n\n"
            "*Бэктест с датами:*\n"
            "`/backtest grid 2025-01-01 2025-06-01`\n\n"
            "*Сравнение стратегий:*\n"
            "`/compare` — все стратегии\n"
            "`/compare ema_crossover,grid,supertrend`\n\n"
            "*Стратегии:*\n"
            "• `ema_crossover` — Трендовая (EMA пересечение)\n"
            "• `rsi_mean_reversion` — Контртренд (RSI + BB)\n"
            "• `grid` — Сеточная торговля\n"
            "• `smart_dca` — Умное усреднение\n"
            "• `supertrend` — Агрессивная трендовая\n"
            "• `multi_indicator` — Консенсус 6 индикаторов\n\n"
            "*Ключевые термины:*\n"
            "• *PnL* — Profit and Loss, прибыль/убыток\n"
            "• *Win Rate* — процент прибыльных сделок\n"
            "• *Просадка (DD)* — максимальное падение баланса от пика\n"
            "• *Профит-фактор (PF)* — отношение прибыли к убыткам (>1 = прибыльно)\n"
            "• *Sharpe Ratio* — доходность с учётом риска (>1 = хорошо)\n"
            "• *SL* — Stop Loss, автоматическое закрытие при убытке\n"
            "• *TP* — Take Profit, автоматическое закрытие при прибыли\n"
            "• *Paper Trading* — торговля на виртуальные деньги (без риска)"
        )
        await update.message.reply_text(text, parse_mode="Markdown")

    async def cmd_info(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Команда /info — подробное описание стратегий."""
        if not await self._check_auth(update):
            return

        text = (
            "📖 *Стратегии — подробное описание*\n\n"
            "*1. EMA Crossover* (Умеренный риск)\n"
            "Тип: Трендовая\n"
            "Индикаторы: EMA 9, EMA 21, EMA 200, объём\n"
            "Сигнал покупки: EMA 9 пересекает EMA 21 снизу вверх + цена выше EMA 200\n"
            "Сигнал продажи: EMA 9 пересекает EMA 21 сверху вниз + цена ниже EMA 200\n"
            "Лучше всего: в трендовых рынках\n"
            "Таймфрейм: 1h\n\n"
            "*2. RSI Mean Reversion* (Консервативный)\n"
            "Тип: Контртрендовая (возврат к среднему)\n"
            "Индикаторы: RSI (14), Bollinger Bands (20,2), Stochastic RSI\n"
            "Сигнал покупки: RSI < 30 или цена ниже нижней полосы Боллинджера\n"
            "Сигнал продажи: RSI > 70 или цена выше верхней полосы Боллинджера\n"
            "Лучше всего: в боковом рынке (флэт)\n"
            "Таймфрейм: 1h\n\n"
            "*3. Grid Trading* (Консервативный)\n"
            "Тип: Сеточная торговля\n"
            "Подход: ордера на равных ценовых уровнях в диапазоне\n"
            "Индикаторы: ATR (для ширины сетки), ADX < 30 (фильтр тренда)\n"
            "Лучше всего: в боковике, при низкой волатильности\n"
            "Таймфрейм: 15m\n\n"
            "*4. Smart DCA* (Консервативный)\n"
            "Тип: Умное усреднение\n"
            "Подход: докупка на просадках с подтверждением индикаторов\n"
            "Индикаторы: RSI, EMA 20/50, MACD, объём\n"
            "5 уровней докупки, каждый следующий x1.5 по объёму\n"
            "Лучше всего: для долгосрочного накопления, в нисходящем тренде\n"
            "Таймфрейм: 4h\n\n"
            "*5. Supertrend* (Агрессивный)\n"
            "Тип: Агрессивная трендовая\n"
            "Индикаторы: Supertrend (на основе ATR), ADX (сила тренда), объём\n"
            "Сигнал: разворот Supertrend + ADX > 20\n"
            "Закрытие: ADX падает ниже 15 (тренд слабеет)\n"
            "Лучше всего: сильные тренды, фьючерсы с плечом\n"
            "Таймфрейм: 1h\n\n"
            "*6. Multi Indicator* (Умеренный) — по умолчанию\n"
            "Тип: Консенсус 6 индикаторов\n"
            "Индикаторы: EMA (9/21/50), RSI, MACD, Bollinger Bands, OBV, ATR\n"
            "Решение: сделка открывается при 4+ совпадающих сигналах из 6\n"
            "SL/TP: динамический, на основе ATR\n"
            "Лучше всего: универсальная, работает на любом рынке\n"
            "Таймфрейм: 1h"
        )
        await update.message.reply_text(text, parse_mode="Markdown")

    # === Callback обработчики (inline кнопки) ===

    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Обрабатывает нажатия inline кнопок."""
        query = update.callback_query
        await query.answer()

        uid = query.from_user.id
        # Проверяем авторизацию (БД или admin)
        is_admin = uid == self.settings.main_user_id
        user_in_db = None
        if hasattr(self.engine, "db") and self.engine.db is not None:
            user_in_db = await self.engine.db.get_user(uid)
        if not is_admin and not user_in_db and not self._is_authorized(uid):
            await query.edit_message_text("⛔ Нет доступа")
            return

        data = query.data

        # === АДМИН-ПАНЕЛЬ ===
        if data.startswith("admin_") or data in ("admin_menu",):
            if not is_admin:
                await query.edit_message_text("⛔ Доступ только для администратора")
                return
            await self._handle_admin(query, data, context)
            return

        if data == "back_main":
            # Вернуться в главное меню (учитывая админ-права)
            await self._show_text(
                query, "Главное меню:",
                reply_markup=self._main_menu_keyboard(uid),
            )
            return

        if data == "status":
            text = await self._format_status_v3(uid)
            await query.edit_message_text(text, reply_markup=self._back_keyboard())

        elif data == "positions" or data == "positions_refresh":
            text, keyboard = await self._format_open_positions(uid)
            await self._show_text(query, text, reply_markup=keyboard)

        elif data.startswith("position_chart_"):
            account_id = data.replace("position_chart_", "")
            await self._send_position_chart(query, uid, account_id)

        elif data == "bot_start":
            if not self.engine._running:
                await self.engine.start()
                await query.edit_message_text("▶️ Бот запущен!",
                                              reply_markup=self._back_keyboard())
            else:
                await query.edit_message_text("Бот уже работает",
                                              reply_markup=self._back_keyboard())

        elif data == "bot_stop":
            if self.engine._running:
                self.engine._running = False
                await query.edit_message_text("⏹ Бот остановлен",
                                              reply_markup=self._back_keyboard())
            else:
                await query.edit_message_text("Бот уже остановлен",
                                              reply_markup=self._back_keyboard())

        elif data == "strategies":
            keyboard = []
            for name, cls in STRATEGY_MAP.items():
                s = cls()
                active = "✅ " if self.engine.strategy and self.engine.strategy.name == name else ""
                keyboard.append([InlineKeyboardButton(
                    f"{active}{s.name} — {s.description}",
                    callback_data=f"set_strategy_{name}",
                )])
            keyboard.append([InlineKeyboardButton("◀️ Назад", callback_data="back_main")])
            await query.edit_message_text(
                "📈 *Выберите стратегию:*",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown",
            )

        elif data.startswith("set_strategy_"):
            name = data.replace("set_strategy_", "")
            result = self.engine.set_strategy(name)
            await query.edit_message_text(f"📈 {result}",
                                          reply_markup=self._back_keyboard())

        elif data.startswith("set_risk_"):
            level = data.replace("set_risk_", "")
            self.settings.risk_level = RiskLevel(level)
            self.engine.risk_manager.risk_params = self.settings.get_risk_params()
            await query.edit_message_text(f"⚙️ Уровень риска: {level}",
                                          reply_markup=self._back_keyboard())

        elif data == "set_mode_paper":
            self.settings.paper_trading = True
            await query.edit_message_text("📝 Режим: Paper Trading",
                                          reply_markup=self._back_keyboard())

        elif data == "set_mode_live":
            self.settings.paper_trading = False
            await query.edit_message_text(
                "⚠️ *ВНИМАНИЕ: Live Trading активирован!*\n"
                "Бот будет торговать реальными средствами.",
                reply_markup=self._back_keyboard(),
                parse_mode="Markdown",
            )

        elif data == "set_type_spot":
            self.settings.trading_mode = TradingMode.SPOT
            await query.edit_message_text("📊 Тип: Спот",
                                          reply_markup=self._back_keyboard())

        elif data == "set_type_futures":
            self.settings.trading_mode = TradingMode.FUTURES
            await query.edit_message_text("📊 Тип: Фьючерсы",
                                          reply_markup=self._back_keyboard())

        elif data == "settings":
            status = await self.engine.get_status()
            text = (
                f"⚙️ *Настройки*\n\n"
                f"Биржа: `{status['exchange']}`\n"
                f"Режим: `{status['mode']}`\n"
                f"Тип: `{status['trading_mode']}`\n"
                f"Стратегия: `{status['strategy']}`\n"
                f"Риск: `{status['risk_level']}`\n"
                f"Пары: `{', '.join(status['symbols'])}`"
            )
            keyboard = [
                [InlineKeyboardButton("📈 Стратегия", callback_data="strategies"),
                 InlineKeyboardButton("⚙️ Риск", callback_data="show_risk")],
                [InlineKeyboardButton("◀️ Назад", callback_data="back_main")],
            ]
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard),
                                          parse_mode="Markdown")

        elif data == "show_risk":
            risk_labels = {
                "conservative": "Safe — 1% риска на сделку",
                "moderate": "Normal — 2% риска на сделку",
                "aggressive": "Bold — 4% риска на сделку",
            }
            keyboard = [
                [InlineKeyboardButton(
                    f"{'✅ ' if self.settings.risk_level == level else ''}"
                    f"{risk_labels.get(level.value, level.value)}",
                    callback_data=f"set_risk_{level.value}",
                )]
                for level in RiskLevel
            ]
            keyboard.append(self._back_button())
            current = self.settings.get_risk_params()
            await query.edit_message_text(
                f"🎯 *Риск на сделку*\n\n"
                f"Текущий: `{self.settings.risk_level.value}`\n"
                f"Риск: `{current['risk_per_trade_pct']}%` от баланса\n"
                f"Макс. позиций: `{current['max_open_positions']}`\n\n"
                f"Определяет сколько % баланса потеряете\n"
                f"при срабатывании стоп-лосса.",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown",
            )

        elif data == "history":
            text = await self._format_history_v3(uid)
            await query.edit_message_text(text, reply_markup=self._back_keyboard())

        elif data == "backtest_menu":
            keyboard = []
            for name, cls in STRATEGY_MAP.items():
                s = cls()
                keyboard.append([InlineKeyboardButton(
                    f"🔬 {s.name}",
                    callback_data=f"bt_strat_{name}",
                )])
            keyboard.append([InlineKeyboardButton(
                "📊 Сравнить ВСЕ", callback_data="compare_all")])
            keyboard.append([InlineKeyboardButton("◀️ Назад", callback_data="back_main")])
            await query.edit_message_text(
                "🔬 *Бэктест — выберите стратегию:*",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown",
            )

        elif data.startswith("bt_strat_"):
            # Шаг 2: выбор ТФ
            name = data.replace("bt_strat_", "")
            keyboard = [
                [InlineKeyboardButton("1h", callback_data=f"bt_tf_{name}_1h"),
                 InlineKeyboardButton("4h", callback_data=f"bt_tf_{name}_4h")],
                [InlineKeyboardButton("1d", callback_data=f"bt_tf_{name}_1d"),
                 InlineKeyboardButton("1w", callback_data=f"bt_tf_{name}_1w")],
                [InlineKeyboardButton("◀️ Назад", callback_data="backtest_menu")],
            ]
            await query.edit_message_text(
                f"🔬 *Бэктест: {name}*\n\nВыберите таймфрейм:",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown",
            )

        elif data.startswith("bt_tf_"):
            # Шаг 3: выбор периода
            parts = data.replace("bt_tf_", "").rsplit("_", 1)
            name, tf = parts[0], parts[1]
            tf_labels = {"1h": "1 час", "4h": "4 часа", "1d": "1 день", "1w": "1 неделя"}
            keyboard = [
                [InlineKeyboardButton("С июля 2024 (честный)", callback_data=f"bt_per_{name}_{tf}_wf")],
                [InlineKeyboardButton("1 мес", callback_data=f"bt_per_{name}_{tf}_1m"),
                 InlineKeyboardButton("3 мес", callback_data=f"bt_per_{name}_{tf}_3m")],
                [InlineKeyboardButton("6 мес", callback_data=f"bt_per_{name}_{tf}_6m"),
                 InlineKeyboardButton("1 год", callback_data=f"bt_per_{name}_{tf}_1y")],
                [InlineKeyboardButton("3 года", callback_data=f"bt_per_{name}_{tf}_3y"),
                 InlineKeyboardButton("5 лет", callback_data=f"bt_per_{name}_{tf}_5y")],
                [InlineKeyboardButton("◀️ Назад", callback_data=f"bt_strat_{name}")],
            ]
            await query.edit_message_text(
                f"🔬 *Бэктест: {name}*\n"
                f"Таймфрейм: `{tf_labels.get(tf, tf)}`\n\n"
                f"Выберите период:",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown",
            )

        elif data.startswith("bt_per_"):
            # Шаг 4: выбор TP-режима
            parts = data.replace("bt_per_", "").rsplit("_", 2)
            name, tf, period = parts[0], parts[1], parts[2]
            tf_labels = {"1h": "1 час", "4h": "4 часа", "1d": "1 день", "1w": "1 неделя"}
            period_labels = {"1m": "1 мес", "3m": "3 мес", "6m": "6 мес", "1y": "1 год", "3y": "3 года", "5y": "5 лет"}
            keyboard = [
                [InlineKeyboardButton("Полный TP", callback_data=f"run_backtest_{name}_{tf}_{period}_full")],
                [InlineKeyboardButton("50/50 + безубыток", callback_data=f"run_backtest_{name}_{tf}_{period}_half")],
                [InlineKeyboardButton("По третям", callback_data=f"run_backtest_{name}_{tf}_{period}_thirds")],
                [InlineKeyboardButton("Быстрая фиксация", callback_data=f"run_backtest_{name}_{tf}_{period}_scalp")],
                [InlineKeyboardButton("◀️ Назад", callback_data=f"bt_tf_{name}_{tf}")],
            ]
            await query.edit_message_text(
                f"🔬 *Бэктест: {name}*\n"
                f"ТФ: `{tf_labels.get(tf, tf)}` | Период: `{period_labels.get(period, period)}`\n\n"
                f"Режим тейк-профита:",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown",
            )

        elif data.startswith("run_backtest_"):
            parts = data.replace("run_backtest_", "").rsplit("_", 3)
            if len(parts) == 4:
                name, tf, period, tp_mode = parts
            elif len(parts) == 3:
                name, tf, period = parts
                tp_mode = "full"
            else:
                name = data.replace("run_backtest_", "")
                tf = None
                period = None
                tp_mode = "full"
            period_days = {"1m": 30, "3m": 90, "6m": 180, "1y": 365, "3y": 1095, "5y": 1825, "wf": 0}
            period_labels = {"1m": "1 мес", "3m": "3 мес", "6m": "6 мес", "1y": "1 год", "3y": "3 года", "5y": "5 лет", "wf": "С июля 2024"}
            tf_labels = {"1h": "1 час", "4h": "4 часа", "1d": "1 день", "1w": "1 неделя"}

            await query.edit_message_text(
                f"🔬 Запускаю бэктест `{name}`...\n"
                f"ТФ: `{tf_labels.get(tf, 'авто')}` | Период: `{period_labels.get(period, 'последние данные')}`",
                parse_mode="Markdown",
            )
            try:
                from backtesting.backtest import Backtester
                from backtesting.visualizer import plot_equity_curve
                from main import fetch_ohlcv_range
                from datetime import datetime, timedelta

                strategy = STRATEGY_MAP[name]()
                symbol = self.settings.default_symbol

                if tf:
                    strategy.timeframe = tf

                if period and period in period_days:
                    until_dt = datetime.utcnow()
                    if period == "wf":
                        since_dt = datetime(2024, 7, 1)
                    else:
                        since_dt = until_dt - timedelta(days=period_days[period])
                    since_ms = int(since_dt.timestamp() * 1000)
                    until_ms = int(until_dt.timestamp() * 1000)
                    ohlcv = await fetch_ohlcv_range(symbol, strategy.timeframe, since=since_ms, until=until_ms)
                else:
                    ohlcv = await fetch_ohlcv_range(symbol, strategy.timeframe)

                risk_params = self.settings.get_risk_params()
                leverage = risk_params["max_leverage"]

                bt = Backtester(
                    strategy=strategy,
                    initial_balance=self.settings.paper_balance,
                    risk_per_trade_pct=risk_params["risk_per_trade_pct"],
                    leverage=leverage,
                    stop_loss_pct=risk_params["stop_loss_pct"],
                    take_profit_pct=risk_params["take_profit_pct"],
                    tp_mode=tp_mode,
                )
                result = bt.run(ohlcv, symbol)
                await query.edit_message_text(
                    f"```\n{result.summary()}\n```", parse_mode="Markdown")

                chart_bytes = plot_equity_curve(result)
                if chart_bytes:
                    import io as _io
                    await query.message.reply_photo(
                        photo=_io.BytesIO(chart_bytes),
                        caption=f"📊 {strategy.name} | {symbol}",
                    )

                from backtesting.excel_export import export_single_result
                import io as _io2
                xlsx_bytes = export_single_result(result)
                await query.message.reply_document(
                    document=_io2.BytesIO(xlsx_bytes),
                    filename=f"backtest_{name}_{symbol.replace('/', '_')}.xlsx",
                    caption="📋 Подробный отчёт по сделкам",
                )

                # График сделок на BTC
                from backtesting.visualizer import plot_trades_on_chart
                trades_chart = plot_trades_on_chart(result, ohlcv)
                if trades_chart:
                    await query.message.reply_photo(
                        photo=_io2.BytesIO(trades_chart),
                        caption=f"📈 Сделки {strategy.name} на графике {symbol}",
                    )

                await query.message.reply_text(
                    "Выберите действие:",
                    reply_markup=self._main_menu_keyboard(),
                )
            except Exception as e:
                logger.error(f"Ошибка бэктеста: {e}", exc_info=True)
                await query.edit_message_text(f"Ошибка бэктеста: {e}",
                                              reply_markup=self._back_keyboard())

        elif data == "paper_logs":
            text = await self._format_logs_v3(uid)
            await query.edit_message_text(text, reply_markup=self._back_keyboard())

        elif data == "strategy_info":
            text = await self._format_strategy_info_v3(uid)
            await query.edit_message_text(text, reply_markup=self._back_keyboard())

        elif data == "paper_status":
            paper_trader = getattr(self.engine, 'paper_trader', None)
            if not paper_trader or not paper_trader.accounts:
                await query.edit_message_text(
                    "📡 Paper Trading не запущен.\n"
                    "Перезапустите бота для активации.",
                    reply_markup=self._back_keyboard(),
                )
            else:
                text = "📡 *Live Paper Trading*\n\n"
                for acc_info in paper_trader.get_status():
                    pnl_emoji = "🟢" if acc_info["pnl_pct"] >= 0 else "🔴"
                    pos_text = ""
                    if acc_info["in_position"] and acc_info.get("position"):
                        p = acc_info["position"]
                        pos_text = (
                            f"\n  📌 {p['side'].upper()} @ {p['entry']:.2f}"
                            f"\n  SL: {p['sl']:.2f} | TP: {p['tp']:.2f}"
                        )

                    text += (
                        f"*{acc_info['account_id']}*\n"
                        f"  {acc_info['strategy']} {acc_info['symbol']} {acc_info['timeframe']}\n"
                        f"  {pnl_emoji} Баланс: {acc_info['balance']:.2f}$ ({acc_info['pnl_pct']:+.1f}%)\n"
                        f"  Сделок: {acc_info['trade_count']} | WR: {acc_info['win_rate']:.0f}%"
                        f"{pos_text}\n\n"
                    )
                await query.edit_message_text(
                    text, parse_mode="Markdown",
                    reply_markup=self._back_keyboard(),
                )

        elif data == "best_strategies":
            from backtesting.best_configs import BEST_CONFIGS
            text = "🏆 *Лучшие стратегии (walk-forward verified)*\n\n"
            text += "Параметры обучены на 2020-2024.\n"
            text += "Результаты — на невиданных данных (2024.07+).\n\n"
            for i, cfg in enumerate(BEST_CONFIGS, 1):
                text += (
                    f"*{i}. {cfg['label']}*\n"
                    f"  PnL: `{cfg['wf_test_pnl']}` | DD: `{cfg['wf_test_dd']}`\n\n"
                )
            keyboard = [
                [InlineKeyboardButton(
                    "▶️ Прогнать все лучшие (с июля 2024)",
                    callback_data="run_best",
                )],
                [InlineKeyboardButton("◀️ Главное меню", callback_data="back_main")],
            ]
            await query.edit_message_text(
                text, reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown",
            )

        elif data == "run_best":
            from backtesting.best_configs import BEST_CONFIGS
            from backtesting.backtest import Backtester
            from backtesting.visualizer import format_comparison_table_telegram
            from backtesting.optimized_params import get_optimized_strategy, get_optimized_backtest_params
            from main import fetch_ohlcv_range
            from datetime import datetime as _dt
            from strategies import STRATEGY_MAP
            import time as _time
            import asyncio as _asyncio

            await query.edit_message_text("🏆 *Загрузка данных для лучших стратегий...*", parse_mode="Markdown")

            risk_params = self.settings.get_risk_params()
            since_ms = int(_dt(2024, 7, 1).timestamp() * 1000)
            until_ms = int(_dt.utcnow().timestamp() * 1000)

            results = []
            start_time = _time.time()
            total = len(BEST_CONFIGS)

            for idx, cfg in enumerate(BEST_CONFIGS):
                sym = cfg["symbol"]
                name = cfg["strategy"]
                tf = cfg["timeframe"]

                pct = int((idx / total) * 100)
                try:
                    await query.edit_message_text(
                        f"🏆 *Лучшие стратегии*\n\n"
                        f"Прогресс: {pct}% ({idx}/{total})\n"
                        f"Сейчас: `{cfg['label']}`",
                        parse_mode="Markdown",
                    )
                except Exception:
                    pass

                try:
                    ohlcv = await fetch_ohlcv_range(sym, tf, since=since_ms, until=until_ms)
                    if len(ohlcv) < 100:
                        continue

                    strategy = get_optimized_strategy(name, sym)
                    strategy.timeframe = tf
                    bp = get_optimized_backtest_params(name, sym)

                    bt = Backtester(
                        strategy=strategy,
                        initial_balance=self.settings.paper_balance,
                        risk_per_trade_pct=risk_params["risk_per_trade_pct"],
                        leverage=risk_params["max_leverage"],
                        stop_loss_pct=bp.get("stop_loss_pct", 5.0),
                        take_profit_pct=bp.get("take_profit_pct", 10.0),
                        slippage_pct=0.05,
                        tp_mode="full",
                    )
                    result = bt.run(ohlcv, sym)
                    result.strategy = cfg["label"]
                    result.symbol = sym
                    result.timeframe = tf
                    results.append(result)
                except Exception as e:
                    logger.error(f"Ошибка {cfg['label']}: {e}")

            elapsed = _time.time() - start_time
            elapsed_str = f"{int(elapsed)}с" if elapsed < 60 else f"{int(elapsed // 60)}м {int(elapsed % 60)}с"

            if not results:
                await query.edit_message_text("Нет результатов", reply_markup=self._back_keyboard())
                return

            # Формируем текст
            text = f"🏆 *Лучшие стратегии (с июля 2024)*\n"
            text += f"Прогон за {elapsed_str}\n\n"
            sorted_r = sorted(results, key=lambda r: r.total_pnl_pct, reverse=True)
            for i, r in enumerate(sorted_r, 1):
                emoji = "🟢" if r.total_pnl_pct > 0 else "🔴"
                text += (
                    f"{emoji} *{i}. {r.strategy}*\n"
                    f"  PnL: `{r.total_pnl_pct:+.1f}%` | WR: `{r.win_rate:.0f}%` | "
                    f"DD: `{r.max_drawdown_pct:.1f}%` | Сделок: `{r.total_trades}`\n\n"
                )

            async def _send_with_retry(coro_func, retries=3, delay=3):
                for attempt in range(retries):
                    try:
                        return await coro_func()
                    except Exception:
                        if attempt < retries - 1:
                            await _asyncio.sleep(delay)

            await _send_with_retry(lambda: query.edit_message_text(text, parse_mode="Markdown"))

            # Excel
            from backtesting.excel_export import export_comparison
            import io as _io
            xlsx = export_comparison(sorted_r)
            await _send_with_retry(lambda: query.message.reply_document(
                document=_io.BytesIO(xlsx),
                filename="best_strategies.xlsx",
                caption="📋 Подробный отчёт — лучшие стратегии",
            ))

            # График сравнения
            from backtesting.visualizer import plot_comparison
            chart = plot_comparison(sorted_r)
            if chart:
                await _send_with_retry(lambda: query.message.reply_photo(
                    photo=_io.BytesIO(chart),
                    caption="🏆 Сравнение лучших стратегий",
                ))

            await _send_with_retry(lambda: query.message.reply_text(
                "Выберите действие:", reply_markup=self._main_menu_keyboard(),
            ))

        elif data == "compare_all":
            # Шаг 0: выбор монеты/портфеля
            keyboard = [
                [InlineKeyboardButton("BTC/USDT", callback_data="cmp_coin_BTC-USDT"),
                 InlineKeyboardButton("ETH/USDT", callback_data="cmp_coin_ETH-USDT")],
                [InlineKeyboardButton("XRP/USDT", callback_data="cmp_coin_XRP-USDT"),
                 InlineKeyboardButton("DOGE/USDT", callback_data="cmp_coin_DOGE-USDT")],
                [InlineKeyboardButton("SOL/USDT", callback_data="cmp_coin_SOL-USDT")],
                [InlineKeyboardButton("Портфель BTC+ETH (50/50)", callback_data="cmp_coin_PORTFOLIO")],
                [InlineKeyboardButton("◀️ Назад", callback_data="back_main")],
            ]
            await query.edit_message_text(
                "📊 *Сравнение стратегий*\n\n"
                "Шаг 1/5: Выберите монету:",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown",
            )

        elif data.startswith("cmp_coin_"):
            # Шаг 1: сохраняем монету в контекст, выбор таймфрейма
            coin_key = data.replace("cmp_coin_", "")
            context.user_data["cmp_coin"] = coin_key
            coin_label = "Портфель BTC+ETH" if coin_key == "PORTFOLIO" else coin_key.replace("-", "/")
            keyboard = [
                [InlineKeyboardButton("1h", callback_data="cmp_tf_1h"),
                 InlineKeyboardButton("4h", callback_data="cmp_tf_4h")],
                [InlineKeyboardButton("1d", callback_data="cmp_tf_1d"),
                 InlineKeyboardButton("1w", callback_data="cmp_tf_1w")],
                [InlineKeyboardButton("◀️ Назад", callback_data="compare_all")],
            ]
            await query.edit_message_text(
                f"📊 *Сравнение стратегий*\n\n"
                f"Монета: `{coin_label}`\n"
                f"Шаг 2/5: Выберите таймфрейм:",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown",
            )

        elif data.startswith("cmp_tf_"):
            # Шаг 2: выбор периода
            tf = data.replace("cmp_tf_", "")
            keyboard = [
                [InlineKeyboardButton("С июля 2024 (честный тест)", callback_data=f"cmp_per_{tf}_wf")],
                [InlineKeyboardButton("1 мес", callback_data=f"cmp_per_{tf}_1m"),
                 InlineKeyboardButton("3 мес", callback_data=f"cmp_per_{tf}_3m")],
                [InlineKeyboardButton("6 мес", callback_data=f"cmp_per_{tf}_6m"),
                 InlineKeyboardButton("1 год", callback_data=f"cmp_per_{tf}_1y")],
                [InlineKeyboardButton("3 года", callback_data=f"cmp_per_{tf}_3y"),
                 InlineKeyboardButton("5 лет", callback_data=f"cmp_per_{tf}_5y")],
                [InlineKeyboardButton("8 лет", callback_data=f"cmp_per_{tf}_8y")],
                [InlineKeyboardButton("◀️ Назад", callback_data="compare_all")],
            ]
            tf_labels = {"1h": "1 час", "4h": "4 часа", "1d": "1 день", "1w": "1 неделя"}
            await query.edit_message_text(
                f"📊 *Сравнение стратегий*\n\n"
                f"Таймфрейм: `{tf_labels.get(tf, tf)}`\n"
                f"Шаг 3/5: Выберите период:\n\n"
                f"Параметры Hyperopt обучены на 2020-2024.\n"
                f"Для честного теста выбирайте 'С июля 2024'.",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown",
            )

        elif data.startswith("cmp_per_"):
            # Шаг 3: выбор TP-режима
            parts = data.replace("cmp_per_", "").split("_")
            tf = parts[0]
            period = parts[1]
            tf_labels = {"1h": "1 час", "4h": "4 часа", "1d": "1 день", "1w": "1 неделя"}
            keyboard = [
                [InlineKeyboardButton("Полный TP (100%)", callback_data=f"cmp_tp_{tf}_{period}_full")],
                [InlineKeyboardButton("50/50 + безубыток", callback_data=f"cmp_tp_{tf}_{period}_half")],
                [InlineKeyboardButton("По третям (33/33/34)", callback_data=f"cmp_tp_{tf}_{period}_thirds")],
                [InlineKeyboardButton("Быстрая фиксация (75/25)", callback_data=f"cmp_tp_{tf}_{period}_scalp")],
                [InlineKeyboardButton("◀️ Назад", callback_data=f"cmp_tf_{tf}")],
            ]
            period_labels = {"1m": "1 мес", "3m": "3 мес", "6m": "6 мес", "1y": "1 год", "3y": "3 года", "5y": "5 лет", "8y": "8 лет"}
            await query.edit_message_text(
                f"📊 *Сравнение стратегий*\n\n"
                f"Таймфрейм: `{tf_labels.get(tf, tf)}`\n"
                f"Период: `{period_labels.get(period, period)}`\n"
                f"Шаг 3/4: Режим тейк-профита:",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown",
            )

        elif data.startswith("cmp_tp_"):
            # Шаг 4: дефолт или оптимизированные
            parts = data.replace("cmp_tp_", "").split("_")
            tf, period, tp_mode = parts[0], parts[1], parts[2]
            tf_labels = {"1h": "1 час", "4h": "4 часа", "1d": "1 день", "1w": "1 неделя"}
            period_labels = {"1m": "1 мес", "3m": "3 мес", "6m": "6 мес", "1y": "1 год", "3y": "3 года", "5y": "5 лет", "8y": "8 лет"}
            tp_labels = {"full": "Полный", "half": "50/50", "thirds": "По третям", "scalp": "Быстрая"}
            keyboard = [
                [InlineKeyboardButton("Дефолтные параметры", callback_data=f"cmp_run_{tf}_{period}_{tp_mode}_def")],
                [InlineKeyboardButton("Оптимизированные (Hyperopt)", callback_data=f"cmp_run_{tf}_{period}_{tp_mode}_opt")],
                [InlineKeyboardButton("◀️ Назад", callback_data=f"cmp_per_{tf}_{period}")],
            ]
            await query.edit_message_text(
                f"📊 *Сравнение стратегий*\n\n"
                f"Таймфрейм: `{tf_labels.get(tf, tf)}`\n"
                f"Период: `{period_labels.get(period, period)}`\n"
                f"TP: `{tp_labels.get(tp_mode, tp_mode)}`\n"
                f"Шаг 4/4: Параметры стратегий:",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown",
            )

        elif data.startswith("cmp_run_"):
            # Запуск сравнения
            parts = data.replace("cmp_run_", "").split("_")
            tf = parts[0]
            period = parts[1]
            tp_mode = parts[2] if len(parts) > 2 else "full"
            use_optimized = parts[3] == "opt" if len(parts) > 3 else False

            # Монета из контекста
            coin_key = context.user_data.get("cmp_coin", "BTC-USDT")
            is_portfolio = coin_key == "PORTFOLIO"
            if is_portfolio:
                symbols = ["BTC/USDT", "ETH/USDT"]
            else:
                symbols = [coin_key.replace("-", "/")]

            period_days = {
                "1m": 30, "3m": 90, "6m": 180,
                "1y": 365, "3y": 1095, "5y": 1825, "8y": 2920,
            }
            period_labels = {
                "1m": "1 месяц", "3m": "3 месяца", "6m": "6 месяцев",
                "1y": "1 год", "3y": "3 года", "5y": "5 лет", "8y": "8 лет",
                "wf": "С июля 2024 (честный тест)",
            }
            tf_labels = {"1h": "1 час", "4h": "4 часа", "1d": "1 день", "1w": "1 неделя"}
            # "wf" -- walk-forward: с июля 2024 по сегодня
            from datetime import datetime as _dt
            if period == "wf":
                days = (_dt.utcnow() - _dt(2024, 7, 1)).days
            else:
                days = period_days.get(period, 30)

            # Оценка времени
            candles_estimate = {
                "1h": days * 24, "4h": days * 6, "1d": days, "1w": days // 7,
            }
            est_candles = candles_estimate.get(tf, days * 6)
            est_minutes = max(1, est_candles // 2000)

            await query.edit_message_text(
                f"🔬 *Сравниваю все стратегии...*\n\n"
                f"Таймфрейм: `{tf_labels.get(tf, tf)}`\n"
                f"Период: `{period_labels.get(period, period)}`\n"
                f"~{est_candles} свечей\n\n"
                f"Ожидание: ~{est_minutes} мин.",
                parse_mode="Markdown",
            )

            try:
                from backtesting.backtest import Backtester
                from backtesting.visualizer import (
                    plot_comparison, format_comparison_table_telegram,
                )
                from main import fetch_ohlcv_range, parse_date
                from datetime import datetime, timedelta

                risk_params = self.settings.get_risk_params()

                until_dt = datetime.utcnow()
                if period == "wf":
                    since_dt = datetime(2024, 7, 1)
                else:
                    since_dt = until_dt - timedelta(days=days)
                since_ms = int(since_dt.timestamp() * 1000)
                until_ms = int(until_dt.timestamp() * 1000)

                import time as _time

                coin_label = "Портфель BTC+ETH" if is_portfolio else symbols[0]
                await query.edit_message_text(
                    f"🔬 *Загрузка данных...*\n\n"
                    f"Монета: `{coin_label}`\n"
                    f"Таймфрейм: `{tf_labels.get(tf, tf)}`\n"
                    f"Период: `{period_labels.get(period, period)}`",
                    parse_mode="Markdown",
                )

                # Загружаем данные для всех монет
                ohlcv_map = {}
                for sym in symbols:
                    ohlcv_map[sym] = await fetch_ohlcv_range(sym, tf, since=since_ms, until=until_ms)

                # Для одиночной монеты -- как раньше
                symbol = symbols[0]
                ohlcv = ohlcv_map[symbol]

                if len(ohlcv) < 210:
                    await query.edit_message_text(
                        f"Недостаточно данных: {len(ohlcv)} свечей (нужно 210+)",
                        reply_markup=self._back_keyboard(),
                    )
                    return

                leverage = risk_params["max_leverage"]
                sl_pct = risk_params["stop_loss_pct"]
                tp_pct = risk_params["take_profit_pct"]

                strategy_items = list(STRATEGY_MAP.items())
                total_strategies = len(strategy_items)
                results = []
                start_time = _time.time()

                tp_labels = {"full": "Полный", "half": "50/50", "thirds": "По третям", "scalp": "Быстрая"}
                opt_label = "Оптимизированные" if use_optimized else "Дефолтные"

                for idx, (name, cls) in enumerate(strategy_items):
                    # Оптимизированные или дефолтные параметры
                    if use_optimized:
                        from backtesting.optimized_params import get_optimized_strategy, get_optimized_backtest_params
                        strategy = get_optimized_strategy(name, symbol)
                        opt_params = get_optimized_backtest_params(name, symbol)
                        strat_sl = opt_params.get("stop_loss_pct", sl_pct)
                        strat_tp = opt_params.get("take_profit_pct", tp_pct)
                    else:
                        strategy = cls()
                        strat_sl = sl_pct
                        strat_tp = tp_pct

                    strategy.timeframe = tf
                    if len(ohlcv) < strategy.min_candles:
                        continue

                    # Обновляем прогресс
                    pct = int((idx / total_strategies) * 100)
                    elapsed = _time.time() - start_time
                    if idx > 0:
                        eta = elapsed / idx * (total_strategies - idx)
                        eta_str = f"{int(eta)}с" if eta < 60 else f"{int(eta // 60)}м {int(eta % 60)}с"
                    else:
                        eta_str = "расчёт..."

                    bar_len = 20
                    filled = int(bar_len * idx / total_strategies)
                    bar = "█" * filled + "░" * (bar_len - filled)

                    try:
                        await query.edit_message_text(
                            f"🔬 *Сравнение стратегий*\n\n"
                            f"`[{bar}]` {pct}%\n\n"
                            f"Анализирую: `{strategy.name}`\n"
                            f"Готово: {idx}/{total_strategies}\n"
                            f"Свечей: {len(ohlcv)}\n"
                            f"Параметры: `{opt_label}` | TP: `{tp_labels.get(tp_mode, tp_mode)}`\n"
                            f"Риск: `{self.settings.risk_level.value}` | {leverage}x\n"
                            f"Осталось: ~{eta_str}",
                            parse_mode="Markdown",
                        )
                    except Exception:
                        pass

                    if is_portfolio:
                        # Портфель: прогоняем на каждой монете, усредняем
                        portfolio_pnl = 0
                        portfolio_trades = 0
                        portfolio_dd = 0
                        first_result = None
                        for sym in symbols:
                            s_copy = type(strategy)(**{k: getattr(strategy, k) for k in strategy.__init__.__code__.co_varnames[1:strategy.__init__.__code__.co_argcount] if hasattr(strategy, k)}) if hasattr(strategy.__init__, '__code__') else strategy
                            try:
                                if use_optimized:
                                    from backtesting.optimized_params import get_optimized_strategy, get_optimized_backtest_params
                                    s_copy = get_optimized_strategy(name, sym)
                                    sym_bp = get_optimized_backtest_params(name, sym)
                                    sym_sl = sym_bp.get("stop_loss_pct", strat_sl)
                                    sym_tp = sym_bp.get("take_profit_pct", strat_tp)
                                else:
                                    s_copy = type(strategy)()
                                    sym_sl, sym_tp = strat_sl, strat_tp
                                s_copy.timeframe = tf
                            except Exception:
                                s_copy = strategy
                                sym_sl, sym_tp = strat_sl, strat_tp
                            bt = Backtester(
                                strategy=s_copy,
                                initial_balance=self.settings.paper_balance / len(symbols),
                                risk_per_trade_pct=risk_params["risk_per_trade_pct"],
                                leverage=leverage,
                                stop_loss_pct=sym_sl, take_profit_pct=sym_tp,
                                tp_mode=tp_mode,
                            )
                            r = bt.run(ohlcv_map[sym], sym)
                            portfolio_pnl += r.total_pnl
                            portfolio_trades += r.total_trades
                            portfolio_dd = max(portfolio_dd, r.max_drawdown_pct)
                            if first_result is None:
                                first_result = r
                        # Создаём сводный результат
                        if first_result:
                            first_result.total_pnl = round(portfolio_pnl, 2)
                            first_result.total_pnl_pct = round(portfolio_pnl / self.settings.paper_balance * 100, 2)
                            first_result.total_trades = portfolio_trades
                            first_result.max_drawdown_pct = portfolio_dd
                            first_result.symbol = "BTC+ETH"
                            first_result.strategy = name
                            results.append(first_result)
                    else:
                        bt = Backtester(
                            strategy=strategy,
                            initial_balance=self.settings.paper_balance,
                            risk_per_trade_pct=risk_params["risk_per_trade_pct"],
                            leverage=leverage,
                            stop_loss_pct=strat_sl, take_profit_pct=strat_tp,
                            tp_mode=tp_mode,
                        )
                        result = bt.run(ohlcv, symbol)
                        results.append(result)

                elapsed_total = _time.time() - start_time
                elapsed_str = f"{int(elapsed_total)}с" if elapsed_total < 60 else f"{int(elapsed_total // 60)}м {int(elapsed_total % 60)}с"

                if not results:
                    await query.edit_message_text("Нет результатов",
                                                  reply_markup=self._back_keyboard())
                    return

                # Отправляем результаты с retry при таймауте
                import asyncio as _asyncio

                async def _send_with_retry(coro_func, retries=3, delay=3):
                    for attempt in range(retries):
                        try:
                            return await coro_func()
                        except Exception as send_err:
                            if attempt < retries - 1:
                                logger.warning(f"Telegram таймаут, повтор через {delay}с ({attempt+1}/{retries})")
                                await _asyncio.sleep(delay)
                            else:
                                logger.error(f"Не удалось отправить после {retries} попыток: {send_err}")

                text = format_comparison_table_telegram(results)
                await _send_with_retry(lambda: query.edit_message_text(
                    f"🔬 *Завершено за {elapsed_str}*\n\n" + text,
                    parse_mode="Markdown",
                ))

                chart_bytes = plot_comparison(results)
                if chart_bytes:
                    import io as _io
                    await _send_with_retry(lambda: query.message.reply_photo(
                        photo=_io.BytesIO(chart_bytes),
                        caption=f"📊 {len(results)} стратегий | {symbol} | {tf_labels.get(tf, tf)} | {period_labels.get(period, period)}",
                    ))

                from backtesting.excel_export import export_comparison
                import io as _io2
                xlsx_bytes = export_comparison(results)
                await _send_with_retry(lambda: query.message.reply_document(
                    document=_io2.BytesIO(xlsx_bytes),
                    filename=f"comparison_{symbol.replace('/', '_')}_{tf}_{period}.xlsx",
                    caption="📋 Подробный отчёт — сравнение + сделки по каждой стратегии",
                ))

                # График сделок для лучшей стратегии
                from backtesting.visualizer import plot_trades_on_chart
                best_result = sorted(results, key=lambda r: r.total_pnl_pct, reverse=True)[0]

                if is_portfolio:
                    # Для портфеля: прогоняем лучшую стратегию на каждой монете и рисуем графики
                    best_name = best_result.strategy
                    for sym in symbols:
                        if use_optimized:
                            from backtesting.optimized_params import get_optimized_strategy, OPTIMIZED_PARAMS
                            s_chart = get_optimized_strategy(best_name)
                            opt_p = OPTIMIZED_PARAMS.get(best_name, {}).get("backtest_params", {})
                            chart_sl = opt_p.get("stop_loss_pct", sl_pct)
                            chart_tp = opt_p.get("take_profit_pct", tp_pct)
                        else:
                            s_chart = STRATEGY_MAP[best_name]()
                            chart_sl, chart_tp = sl_pct, tp_pct
                        s_chart.timeframe = tf
                        bt_chart = Backtester(s_chart, self.settings.paper_balance / len(symbols),
                                              risk_params["risk_per_trade_pct"], leverage,
                                              stop_loss_pct=chart_sl, take_profit_pct=chart_tp,
                                              tp_mode=tp_mode)
                        r_chart = bt_chart.run(ohlcv_map[sym], sym)
                        chart_bytes = plot_trades_on_chart(r_chart, ohlcv_map[sym])
                        if chart_bytes:
                            _sym = sym
                            await _send_with_retry(lambda: query.message.reply_photo(
                                photo=_io2.BytesIO(chart_bytes),
                                caption=f"📈 {best_name} на {_sym} ({r_chart.total_pnl_pct:+.1f}%)",
                            ))
                else:
                    trades_chart = plot_trades_on_chart(best_result, ohlcv)
                    if trades_chart:
                        await _send_with_retry(lambda: query.message.reply_photo(
                            photo=_io2.BytesIO(trades_chart),
                            caption=f"📈 Сделки лучшей стратегии: {best_result.strategy} ({best_result.total_pnl_pct:+.1f}%)",
                        ))

                await _send_with_retry(lambda: query.message.reply_text(
                    "Выберите действие:",
                    reply_markup=self._main_menu_keyboard(),
                ))

            except Exception as e:
                logger.error(f"Ошибка сравнения: {e}", exc_info=True)
                try:
                    await query.edit_message_text(f"Ошибка сравнения: {e}",
                                                  reply_markup=self._back_keyboard())
                except Exception:
                    pass  # Если даже ошибку не можем отправить — не падаем

        elif data == "help":
            text = (
                "🤖 *Crypto Trading Bot — Команды:*\n\n"
                "*Основные:*\n"
                "/start — Главное меню\n"
                "/status — Статус бота\n"
                "/balance — Баланс и PnL\n"
                "/trades — Открытые позиции\n"
                "/history — История сделок\n\n"
                "*Настройки:*\n"
                "/strategy — Выбор стратегии\n"
                "/risk — Управление рисками\n"
                "/symbol — Торговые пары\n"
                "/mode — Режим торговли\n\n"
                "*Аналитика:*\n"
                "/backtest — Бэктест стратегии\n"
                "/compare — Сравнение стратегий\n"
                "/stats — Статистика стратегии\n\n"
                "*Справка:*\n"
                "/help — Эта справка\n"
                "/info — Подробное описание стратегий\n\n"
                "*Ключевые термины:*\n"
                "• *PnL* — Profit and Loss, прибыль/убыток\n"
                "• *Win Rate* — процент прибыльных сделок\n"
                "• *Просадка (DD)* — максимальное падение баланса от пика\n"
                "• *Профит-фактор (PF)* — отношение прибыли к убыткам (>1 = прибыльно)\n"
                "• *Sharpe Ratio* — доходность с учётом риска (>1 = хорошо)\n"
                "• *SL* — Stop Loss, автоматическое закрытие при убытке\n"
                "• *TP* — Take Profit, автоматическое закрытие при прибыли\n"
                "• *Paper Trading* — торговля на виртуальные деньги (без риска)"
            )
            await query.edit_message_text(text, reply_markup=self._back_keyboard(),
                                          parse_mode="Markdown")

        elif data == "strategies_info":
            text = (
                "📖 *Стратегии — подробное описание*\n\n"
                "*1. EMA Crossover* (Умеренный риск)\n"
                "Тип: Трендовая\n"
                "Индикаторы: EMA 9, EMA 21, EMA 200, объём\n"
                "Сигнал покупки: EMA 9 пересекает EMA 21 снизу вверх + цена выше EMA 200\n"
                "Сигнал продажи: EMA 9 пересекает EMA 21 сверху вниз + цена ниже EMA 200\n"
                "Лучше всего: в трендовых рынках\n"
                "Таймфрейм: 1h\n\n"
                "*2. RSI Mean Reversion* (Консервативный)\n"
                "Тип: Контртрендовая (возврат к среднему)\n"
                "Индикаторы: RSI (14), Bollinger Bands (20,2), Stochastic RSI\n"
                "Сигнал покупки: RSI < 30 или цена ниже нижней полосы Боллинджера\n"
                "Сигнал продажи: RSI > 70 или цена выше верхней полосы Боллинджера\n"
                "Лучше всего: в боковом рынке (флэт)\n"
                "Таймфрейм: 1h\n\n"
                "*3. Grid Trading* (Консервативный)\n"
                "Тип: Сеточная торговля\n"
                "Подход: ордера на равных ценовых уровнях в диапазоне\n"
                "Индикаторы: ATR (для ширины сетки), ADX < 30 (фильтр тренда)\n"
                "Лучше всего: в боковике, при низкой волатильности\n"
                "Таймфрейм: 15m\n\n"
                "*4. Smart DCA* (Консервативный)\n"
                "Тип: Умное усреднение\n"
                "Подход: докупка на просадках с подтверждением индикаторов\n"
                "Индикаторы: RSI, EMA 20/50, MACD, объём\n"
                "5 уровней докупки, каждый следующий x1.5 по объёму\n"
                "Лучше всего: для долгосрочного накопления, в нисходящем тренде\n"
                "Таймфрейм: 4h\n\n"
                "*5. Supertrend* (Агрессивный)\n"
                "Тип: Агрессивная трендовая\n"
                "Индикаторы: Supertrend (на основе ATR), ADX (сила тренда), объём\n"
                "Сигнал: разворот Supertrend + ADX > 20\n"
                "Закрытие: ADX падает ниже 15 (тренд слабеет)\n"
                "Лучше всего: сильные тренды, фьючерсы с плечом\n"
                "Таймфрейм: 1h\n\n"
                "*6. Multi Indicator* (Умеренный) — по умолчанию\n"
                "Тип: Консенсус 6 индикаторов\n"
                "Индикаторы: EMA (9/21/50), RSI, MACD, Bollinger Bands, OBV, ATR\n"
                "Решение: сделка открывается при 4+ совпадающих сигналах из 6\n"
                "SL/TP: динамический, на основе ATR\n"
                "Лучше всего: универсальная, работает на любом рынке\n"
                "Таймфрейм: 1h"
            )
            await query.edit_message_text(text, reply_markup=self._back_keyboard(),
                                          parse_mode="Markdown")

        elif data == "back_main":
            await query.edit_message_text(
                "🤖 *Crypto Trading Bot*\nВыберите действие:",
                reply_markup=self._main_menu_keyboard(),
                parse_mode="Markdown",
            )

    # === АДМИН-ПАНЕЛЬ ===

    async def _handle_admin(self, query, data: str, context) -> None:
        """Обрабатывает все админские callback'и."""
        db = self.engine.db

        if data == "admin_menu":
            users = await db.list_users()
            keyboard = [
                [InlineKeyboardButton("👥 Пользователи", callback_data="admin_users"),
                 InlineKeyboardButton("➕ Добавить", callback_data="admin_add")],
                [InlineKeyboardButton("📊 Подписки", callback_data="admin_subs_overview")],
                [InlineKeyboardButton("📈 Мониторинг", callback_data="admin_monitoring")],
                self._back_button(),
            ]
            await query.edit_message_text(
                f"⚙️ *Админ-панель*\n\n"
                f"Всего пользователей: {len(users)}\n"
                f"Активных стратегий: {len(self.engine.paper_trader.accounts) if hasattr(self.engine, 'paper_trader') else 0}\n\n"
                f"Выберите действие:",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown",
            )

        elif data == "admin_users":
            users = await db.list_users()
            if not users:
                await query.edit_message_text(
                    "📭 Пользователей пока нет",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="admin_menu")]]),
                )
                return
            text = "👥 *Пользователи*\n\n"
            keyboard = []
            for u in users[:20]:
                badge = "👑" if u.get("is_admin") else "👤"
                name = u.get("display_name") or u.get("username") or f"ID {u['telegram_id']}"
                subs = await db.get_user_subscriptions(u["telegram_id"])
                text += f"{badge} `{u['telegram_id']}` · {name} · подписок: {len(subs)}\n"
                keyboard.append([
                    InlineKeyboardButton(f"⚙️ {name[:20]}", callback_data=f"admin_user_{u['telegram_id']}")
                ])
            keyboard.append([InlineKeyboardButton("◀️ Назад", callback_data="admin_menu")])
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

        elif data == "admin_add":
            # Просим ввести ID
            context.user_data["admin_action"] = "add_user"
            await query.edit_message_text(
                "➕ *Добавить пользователя*\n\n"
                "Отправь Telegram ID нового пользователя в ответном сообщении.\n\n"
                "Чтобы получить ID — попроси человека написать любому @userinfobot "
                "или @getmyid\\_bot в Telegram, он покажет ID.\n\n"
                "Для отмены — нажми кнопку ниже.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("❌ Отмена", callback_data="admin_menu")
                ]]),
                parse_mode="Markdown",
            )

        elif data.startswith("admin_user_"):
            try:
                uid = int(data.split("_")[2])
            except (ValueError, IndexError):
                await query.edit_message_text("⛔ Неверный ID")
                return
            user = await db.get_user(uid)
            if not user:
                await query.edit_message_text(
                    "Пользователь не найден",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="admin_users")]]),
                )
                return
            subs = await db.get_user_subscriptions(uid)
            sub_ids = {s["account_id"] for s in subs}
            name = user.get("display_name") or user.get("username") or f"ID {uid}"
            badge = "👑 Admin" if user.get("is_admin") else "👤 User"
            text = f"*{name}*\n{badge}\nID: `{uid}`\nUsername: @{user.get('username', '—')}\n\n"
            text += "*Подписки на стратегии:*\n"

            paper_trader = getattr(self.engine, "paper_trader", None)
            keyboard = []
            if paper_trader:
                for acc_id, acc in paper_trader.accounts.items():
                    is_sub = acc_id in sub_ids
                    mark = "✅" if is_sub else "⬜"
                    title = self._account_title(acc)
                    text += f"{mark} {title}\n"
                    keyboard.append([InlineKeyboardButton(
                        f"{mark} {title}",
                        callback_data=f"admin_toggle_{uid}_{acc_id}",
                    )])

            if not user.get("is_admin"):
                keyboard.append([InlineKeyboardButton("🗑 Удалить пользователя", callback_data=f"admin_del_{uid}")])
            keyboard.append([InlineKeyboardButton("◀️ Назад", callback_data="admin_users")])
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

        elif data.startswith("admin_toggle_"):
            parts = data.split("_", 3)
            try:
                uid = int(parts[2])
                acc_id = parts[3]
            except (ValueError, IndexError):
                await query.edit_message_text("⛔ Ошибка параметров")
                return
            sub = await db.get_subscription(uid, acc_id)
            if sub:
                await db.unsubscribe(uid, acc_id)
            else:
                await db.subscribe(uid, acc_id, initial_balance=10000.0, from_start=False)
            # Возвращаемся к карточке пользователя
            await self._handle_admin(query, f"admin_user_{uid}", context)

        elif data.startswith("admin_del_"):
            try:
                uid = int(data.split("_")[2])
            except (ValueError, IndexError):
                await query.edit_message_text("⛔ Неверный ID")
                return
            if uid == self.settings.main_user_id:
                await query.edit_message_text("⛔ Нельзя удалить главного администратора")
                return
            await db.remove_user(uid)
            await query.edit_message_text(
                f"🗑 Пользователь `{uid}` удалён",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ К списку", callback_data="admin_users")]]),
                parse_mode="Markdown",
            )

        elif data == "admin_subs_overview":
            paper_trader = getattr(self.engine, "paper_trader", None)
            text = "📊 *Подписки по стратегиям*\n\n"
            if paper_trader:
                for acc_id, acc in paper_trader.accounts.items():
                    subs = await db.get_subscribers(acc_id)
                    text += f"*{self._account_title(acc)}*\n  Подписчиков: {len(subs)}\n\n"
            await query.edit_message_text(
                text,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="admin_menu")]]),
                parse_mode="Markdown",
            )

        elif data == "admin_monitoring":
            text = self._format_monitoring()
            await query.edit_message_text(
                text,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Обновить", callback_data="admin_monitoring")],
                    [InlineKeyboardButton("◀️ Назад", callback_data="admin_menu")],
                ]),
            )

    def _format_monitoring(self) -> str:
        """Состояние бота для админа — когда был последний анализ, ошибки и т.д."""
        paper_trader = getattr(self.engine, "paper_trader", None)
        if not paper_trader:
            return "📡 Paper Trader не инициализирован"

        hc = paper_trader.health_check()
        from datetime import datetime, timedelta
        now_perm = datetime.utcnow() + timedelta(hours=5)
        text = "📈 Мониторинг бота\n━━━━━━━━━━━━━━━━━━━━\n"
        text += f"Проверка: {now_perm.strftime('%Y-%m-%d %H:%M')} (Пермь)\n\n"

        overall = "✅ Всё в порядке" if hc["overall_ok"] else "⚠️ Есть проблемы"
        text += f"Общий статус: {overall}\n\n"

        # Telegram API канал — критичная метрика
        tg_ok = hc.get("telegram_api_ok")
        tg_checked_at = hc.get("telegram_api_checked_at")
        tg_err = hc.get("telegram_api_error")
        if tg_ok is None:
            tg_line = "Telegram API: ⚪ ещё не проверялся"
        elif tg_ok:
            tg_line = "Telegram API: 🟢 доступен"
        else:
            tg_line = f"Telegram API: 🔴 НЕДОСТУПЕН ({tg_err or 'unknown'})"
        if tg_checked_at:
            try:
                checked_dt = datetime.fromisoformat(tg_checked_at) + timedelta(hours=5)
                tg_line += f"  ·  проверка: {checked_dt.strftime('%H:%M')}"
            except (ValueError, TypeError):
                pass
        text += tg_line + "\n"

        # Очередь недоставленных алертов
        pending = hc.get("pending_alerts_count", 0)
        if pending > 0:
            text += f"Очередь алертов: 📬 {pending} ждут доставки\n"
        else:
            text += "Очередь алертов: пусто\n"
        text += "\n"

        if hc["issues"]:
            text += "🔴 Обнаруженные проблемы:\n"
            for issue in hc["issues"]:
                text += f"  • {issue}\n"
            text += "\n"

        text += "━━━━━━━━━━━━━━━━━━━━\nСтратегии:\n\n"

        for acc_status in hc["accounts"]:
            acc_id = acc_status["account_id"]
            acc = paper_trader.accounts.get(acc_id)
            title = paper_trader._account_title(acc) if acc else acc_id

            st = acc_status["status"]
            if st == "ok":
                icon = "🟢"
            elif st == "stale":
                icon = "🟡"
            else:
                icon = "⚪"

            age_s = acc_status["last_analysis_age_s"]
            expected_s = acc_status["expected_interval_s"]
            if age_s is None:
                age_str = "ни одного анализа"
            elif age_s < 60:
                age_str = f"{int(age_s)}с назад"
            elif age_s < 3600:
                age_str = f"{int(age_s/60)} мин назад"
            else:
                age_str = f"{age_s/3600:.1f} ч назад"
            expected_str = f"{int(expected_s/60)} мин" if expected_s >= 60 else f"{int(expected_s)} с"

            text += f"{icon} {title}\n"
            text += f"   Последний анализ: {age_str} (ожидался каждые {expected_str})\n"

            if acc_status["error_count_1h"] > 0:
                text += f"   ⚠️ Ошибок за час: {acc_status['error_count_1h']}\n"
                if acc_status["last_error_msg"]:
                    err = acc_status["last_error_msg"][:100]
                    text += f"   Последняя: {err}\n"

            if acc_status["open_trade"]:
                text += f"   📌 Открыта позиция · Баланс: ${acc_status['equity']:,.0f}\n"
            else:
                text += f"   ⏳ Без позиции · Баланс: ${acc_status['balance']:,.0f}\n"
            text += "\n"

        if len(text) > 4000:
            text = text[:3950] + "\n\n... обрезано"
        return text

    def _account_title(self, account) -> str:
        """Красивое название аккаунта (дублирует paper_trader для UI)."""
        titles = {
            "eth_combined_4h": "ETH Combined 4h",
            "btc_combined_4h": "BTC Combined 4h",
            "eth_micro_15m": "ETH Micro Breakout 15m",
            "eth_pure_fake_4h": "ETH Fake Breakout 4h",
            "sol_combined_4h": "SOL Combined 4h",
            "scalp_pepe_rsi_15m":     "PEPE Scalp RSI 15m",
            "scalp_atom_rsi_15m":     "ATOM Scalp RSI 15m",
            "scalp_render_rsi_15m":   "RENDER Scalp RSI 15m",
            "scalp_ton_donchian_15m": "TON Scalp Donchian 15m",
            "scalp_link_emavol_15m":  "LINK Scalp EMA+Vol 15m",
        }
        aid = getattr(account, "account_id", str(account))
        return titles.get(aid, aid)

    async def handle_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Обрабатывает текстовые сообщения (для флоу добавления пользователя)."""
        if not await self._check_auth(update):
            return
        uid = update.effective_user.id
        if not self._is_admin(uid):
            return
        action = context.user_data.get("admin_action")
        if action == "add_user":
            text = update.message.text.strip()
            try:
                new_uid = int(text)
            except ValueError:
                await update.message.reply_text("⛔ Некорректный ID. Должно быть число.")
                return
            # Добавляем
            ok = await self.engine.db.add_user(
                telegram_id=new_uid, added_by=uid,
            )
            if not ok:
                await update.message.reply_text(
                    f"ℹ️ Пользователь `{new_uid}` уже существует",
                    parse_mode="Markdown",
                )
            else:
                await update.message.reply_text(
                    f"✅ Пользователь `{new_uid}` добавлен.\n"
                    f"Теперь настрой подписки через админ-панель.",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("⚙️ Управление", callback_data=f"admin_user_{new_uid}")
                    ]]),
                    parse_mode="Markdown",
                )
            context.user_data["admin_action"] = None

    # === Уведомления ===

    async def send_notification(self, chat_id: int, text: str) -> None:
        """Отправляет уведомление в Telegram."""
        if self._app:
            await self._app.bot.send_message(chat_id=chat_id, text=text, parse_mode="Markdown")

    async def notify_trade(self, action: dict) -> None:
        """Уведомляет о сделке всех авторизованных пользователей."""
        if action.get("action") in ("buy", "sell"):
            emoji = "🟢" if action["action"] == "buy" else "🔴"
            text = (
                f"{emoji} *Новая сделка*\n\n"
                f"Пара: `{action['symbol']}`\n"
                f"Направление: `{action['action'].upper()}`\n"
                f"Цена: `{action['price']:.2f}`\n"
                f"Размер: `{action['amount']:.6f}`\n"
                f"SL: `{action['stop_loss']:.2f}` | TP: `{action['take_profit']:.2f}`\n"
                f"Плечо: `{action['leverage']}x`\n"
                f"Стратегия: `{action['strategy']}`\n"
                f"Причина: {action['reason']}"
            )
        elif action.get("action") == "closed":
            total_pnl = sum(p["pnl"] for p in action.get("positions", []))
            emoji = "✅" if total_pnl > 0 else "❌"
            text = (
                f"{emoji} *Позиция закрыта*\n\n"
                f"Пара: `{action['symbol']}`\n"
                f"PnL: `{total_pnl:+.2f}` USDT\n"
                f"Причина: {action['reason']}"
            )
        elif action.get("action") == "stopped":
            text = f"⚠️ *Торговля приостановлена*\n\nПричина: {action['reason']}"
        else:
            return

        for user_id in self.settings.allowed_user_ids:
            try:
                await self.send_notification(user_id, text)
            except Exception as e:
                logger.error(f"Ошибка отправки уведомления {user_id}: {e}")

    # === Хелперы ===

    def _format_paper_status(self) -> str:
        """Форматирует статус paper trading аккаунтов."""
        paper_trader = getattr(self.engine, 'paper_trader', None)
        if not paper_trader or not paper_trader.accounts:
            return "📡 Paper Trading не запущен."

        text = "📡 *Live Paper Trading*\n\n"
        for acc in paper_trader.accounts.values():
            pnl_emoji = "🟢" if acc.pnl_pct >= 0 else "🔴"
            pos_text = "⏳ Ожидает сигнал"

            def fmt_money(v):
                return f"${v:,.0f}" if v >= 1000 else f"${v:.2f}"

            if acc.open_trade:
                t = acc.open_trade
                side = "LONG" if t["side"] == "buy" else "SHORT"
                cost_str = fmt_money(t['cost'])
                pos_text = (
                    f"📌 {side} @ ${t['entry_price']:,.2f} ({cost_str})\n"
                    f"    SL: ${t['sl_price']:,.2f} | TP: ${t['tp_price']:,.2f}"
                )

            mode_str = "🎯 Signal-only" if getattr(acc, 'signal_only', False) else f"Risk {acc.risk_pct}% · {acc.leverage}x"
            text += (
                f"*{acc.account_id}*\n"
                f"  {acc.strategy.name} · {acc.symbol} · {acc.strategy.timeframe}\n"
                f"  Режим: {mode_str}\n"
                f"  {pnl_emoji} Капитал: `{fmt_money(acc.equity)}` ({acc.pnl_pct:+.1f}%)\n"
                f"  Сделок: `{acc.trade_count}` | WR: `{acc.win_rate:.0f}%`\n"
                f"  {pos_text}\n\n"
            )
        return text

    def _format_paper_balance(self) -> str:
        """Форматирует балансы paper trading."""
        paper_trader = getattr(self.engine, 'paper_trader', None)
        if not paper_trader or not paper_trader.accounts:
            return "📡 Paper Trading не запущен."

        text = "💰 *Балансы Paper Trading*\n\n"
        total_equity = 0
        total_initial = 0
        for acc in paper_trader.accounts.values():
            pnl_emoji = "📈" if acc.pnl_pct >= 0 else "📉"
            in_pos = " (в позиции)" if acc.open_trade else ""
            text += (
                f"*{acc.account_id}*\n"
                f"  {acc.strategy.name} {acc.symbol} {acc.strategy.timeframe}\n"
                f"  Старт: `{acc.initial_balance:.2f}$`\n"
                f"  Капитал: `{acc.equity:.2f}$`{in_pos}\n"
                f"  Свободно: `{acc.balance:.2f}$`\n"
                f"  {pnl_emoji} PnL: `{acc.pnl_pct:+.1f}%` (`{acc.equity - acc.initial_balance:+.2f}$`)\n\n"
            )
            total_equity += acc.equity
            total_initial += acc.initial_balance

        total_pnl_pct = (total_equity - total_initial) / total_initial * 100 if total_initial > 0 else 0
        pnl_emoji = "📈" if total_pnl_pct >= 0 else "📉"
        text += (
            f"*ИТОГО:*\n"
            f"  Капитал: `{total_equity:.2f}$` из `{total_initial:.2f}$`\n"
            f"  {pnl_emoji} PnL: `{total_pnl_pct:+.1f}%`"
        )
        return text

    async def _format_paper_history(self) -> str:
        """Форматирует историю сделок paper trading."""
        paper_trader = getattr(self.engine, 'paper_trader', None)
        if not paper_trader or not paper_trader.accounts:
            return "📡 Paper Trading не запущен."

        # Собираем сделки из БД (live_ prefix)
        trades = await self.engine.db.get_trades_history(limit=10)
        live_trades = [t for t in trades if t.get("strategy", "").startswith("live_")]

        if not live_trades:
            text = "📜 *История Paper Trading*\n\n"
            text += "Сделок пока нет. Стратегии анализируют рынок...\n\n"
            for acc in paper_trader.accounts.values():
                text += f"• {acc.account_id}: ожидает сигнал\n"
            return text

        text = "📜 *Последние сделки Paper Trading:*\n\n"
        for t in live_trades:
            emoji = "✅" if t["pnl"] > 0 else "❌"
            strat = t["strategy"].replace("live_", "")
            pnl_pct = t["pnl"] / t["cost"] * 100 if t["cost"] > 0 else 0
            text += (
                f"{emoji} `{strat}` {t['symbol']} {t['side'].upper()}\n"
                f"   {t['price']:.2f} | PnL: `{t['pnl']:+.2f}$` ({pnl_pct:+.1f}%)\n"
            )
        return text

    # === v3: статус / история / логи (без Markdown, красиво) ===

    @staticmethod
    def _fmt_money(v: float) -> str:
        """Форматирование денег: $10,559 или $95.43"""
        if abs(v) >= 1000:
            return f"${v:,.0f}"
        return f"${v:.2f}"

    @staticmethod
    def _fmt_utc_to_perm(iso_str: str) -> str:
        """ISO UTC timestamp → строка в Пермском времени (UTC+5): '2026-04-23 22:23'"""
        if not iso_str:
            return ""
        try:
            from datetime import datetime, timedelta
            # Парсим ISO без Z
            s = iso_str.replace("Z", "").split(".")[0]  # отрезаем microseconds и Z
            dt = datetime.fromisoformat(s)
            dt_perm = dt + timedelta(hours=5)
            return dt_perm.strftime("%Y-%m-%d %H:%M")
        except Exception:
            return iso_str[:16].replace("T", " ")

    async def _format_status_v3(self, user_id: int) -> str:
        """Статус: подписки пользователя + общее состояние каждой стратегии."""
        paper_trader = getattr(self.engine, "paper_trader", None)
        if not paper_trader or not paper_trader.accounts:
            return "📡 Paper Trading не запущен"

        db = self.engine.db
        subs = await db.get_user_subscriptions(user_id) if db else []
        sub_ids = {s["account_id"] for s in subs}

        if not sub_ids:
            return (
                "📡 Статус\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "У вас пока нет подписок на стратегии.\n"
                "Обратитесь к администратору."
            )

        text = "📊 Статус стратегий\n━━━━━━━━━━━━━━━━━━━━\n\n"

        # Разделим аккаунты на «обычные» и «scalp pack» — pack-аккаунты группируем в свою секцию
        my_accounts = [(aid, a) for aid, a in paper_trader.accounts.items() if aid in sub_ids]
        regular = [(aid, a) for aid, a in my_accounts if not getattr(a, "pack", None)]
        scalp = [(aid, a) for aid, a in my_accounts if getattr(a, "pack", None) == "scalp_pack"]

        def render_acc(acc) -> str:
            title = paper_trader._account_title(acc)
            bal_str = self._fmt_money(acc.equity)
            pnl_emoji = "🟢" if acc.pnl_pct >= 0 else "🔴"
            out = f"▸ {title}\n"
            out += f"  {pnl_emoji} Баланс: {bal_str} ({acc.pnl_pct:+.1f}%)\n"
            out += f"  Сделок: {acc.trade_count} · WR: {acc.win_rate:.0f}%\n"
            if acc.open_trade:
                t = acc.open_trade
                side = "LONG" if t["side"] == "buy" else "SHORT"
                side_emoji = "🟢" if t["side"] == "buy" else "🔴"
                out += f"  {side_emoji} В позиции: {side} @ ${t['entry_price']:,.4f}\n"
                out += f"     SL ${t['sl_price']:,.4f}  ·  TP ${t['tp_price']:,.4f}\n"
            else:
                out += "  ⏳ Нет открытых позиций\n"
            return out + "\n"

        for _, acc in regular:
            text += render_acc(acc)

        if scalp:
            total_eq = sum(a.equity for _, a in scalp)
            total_init = sum(a.initial_balance for _, a in scalp)
            pack_pnl_pct = (total_eq / total_init - 1) * 100 if total_init else 0
            pack_emoji = "🟢" if pack_pnl_pct >= 0 else "🔴"
            text += f"━━━━━━━━━━━━━━━━━━━━\n"
            text += f"⚡ Scalp Pack ({len(scalp)} стратегий, Binance Futures)\n"
            text += f"{pack_emoji} Общий: ${total_eq:,.2f} (стартовый ${total_init:,.0f}, "
            text += f"{pack_pnl_pct:+.1f}%)\n\n"
            for _, acc in scalp:
                text += render_acc(acc)

        return text

    async def _format_open_positions(self, user_id: int) -> tuple[str, InlineKeyboardMarkup]:
        """
        Список открытых позиций пользователя с актуальной ценой и PnL.
        Возвращает (text, keyboard) — клавиатура содержит кнопки графиков и «Обновить».
        """
        from datetime import datetime, timedelta as _td
        paper_trader = getattr(self.engine, "paper_trader", None)
        db = self.engine.db
        if not paper_trader or not paper_trader.accounts or not db:
            return "📡 Paper Trading не запущен", self._back_keyboard()

        subs = await db.get_user_subscriptions(user_id)
        sub_ids = {s["account_id"] for s in subs}
        if not sub_ids:
            return (
                "📈 Открытые позиции\n━━━━━━━━━━━━━━━━━━━━\nУ вас нет подписок на стратегии.",
                self._back_keyboard(),
            )

        positions = await paper_trader.get_open_positions_detail(account_ids=sub_ids)

        # Header — таймстамп Перм. ТЗ; нужен ещё и для обхода BadRequest "not modified"
        now_perm = datetime.utcnow() + _td(hours=5)
        header = f"📈 Открытые позиции\n━━━━━━━━━━━━━━━━━━━━\nОбновлено: {now_perm.strftime('%H:%M:%S')} (Пермь)\n\n"

        if not positions:
            text = header + "⏳ Сейчас открытых позиций нет.\nКак только стратегия откроет сделку — она появится здесь."
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Обновить", callback_data="positions_refresh")],
                [InlineKeyboardButton("◀️ Главное меню", callback_data="back_main")],
            ])
            return text, keyboard

        text = header
        chart_buttons: list[list[InlineKeyboardButton]] = []
        for p in positions:
            side_emoji = "🟢" if p["side"] == "buy" else "🔴"
            pnl_emoji = "🟢" if p["pnl_pct"] >= 0 else "🔴"
            age_str = self._fmt_age(p["age_seconds"])
            current_str = f"{p['current_price']:,.2f}" if p["current_price_ok"] else f"{p['current_price']:,.2f} (n/a)"

            text += f"{side_emoji} {p['side_label']} · {p['symbol']} {p['timeframe']}\n"
            text += f"   Стратегия: {paper_trader._account_title(paper_trader.accounts[p['account_id']])}\n"
            text += f"   Вход: ${p['entry_price']:,.2f}  →  Сейчас: ${current_str}\n"
            text += (
                f"   {pnl_emoji} PnL: {p['pnl_dollars']:+,.2f}$ ({p['pnl_pct']:+.2f}%)"
            )
            if p["leverage"] and p["leverage"] != 1:
                text += f"  ·  плечо ×{p['leverage']}"
            text += "\n"
            text += (
                f"   🛡 SL: ${p['sl_price']:,.2f} ({p['distance_to_sl_pct']:+.2f}%)"
                f"  ·  🎯 TP: ${p['tp_price']:,.2f} ({p['distance_to_tp_pct']:+.2f}%)\n"
            )
            text += f"   ⏱ В позиции: {age_str}\n\n"

            chart_buttons.append([InlineKeyboardButton(
                f"📈 График: {p['side_label']} {p['symbol']}",
                callback_data=f"position_chart_{p['account_id']}",
            )])

        if len(text) > 4000:
            text = text[:3950] + "\n\n... обрезано"

        keyboard = InlineKeyboardMarkup(
            chart_buttons + [
                [InlineKeyboardButton("🔄 Обновить", callback_data="positions_refresh")],
                [InlineKeyboardButton("◀️ Главное меню", callback_data="back_main")],
            ]
        )
        return text, keyboard

    @staticmethod
    def _fmt_age(seconds: Optional[int]) -> str:
        if seconds is None:
            return "—"
        if seconds < 60:
            return f"{seconds}с"
        if seconds < 3600:
            return f"{seconds // 60} мин"
        if seconds < 86400:
            h = seconds // 3600
            m = (seconds % 3600) // 60
            return f"{h}ч {m}м" if m else f"{h}ч"
        d = seconds // 86400
        h = (seconds % 86400) // 3600
        return f"{d}д {h}ч" if h else f"{d}д"

    async def _send_position_chart(self, query, user_id: int, account_id: str) -> None:
        """Рисует график позиции и отправляет картинкой. Возвращает в раздел кнопкой."""
        paper_trader = getattr(self.engine, "paper_trader", None)
        db = self.engine.db
        if not paper_trader or not db:
            await query.message.reply_text("📡 Paper Trading не запущен")
            return

        # Доступ: только если пользователь подписан на эту стратегию
        subs = await db.get_user_subscriptions(user_id)
        sub_ids = {s["account_id"] for s in subs}
        if account_id not in sub_ids:
            await query.message.reply_text("⛔ У вас нет доступа к этой стратегии")
            return

        chart_data = await paper_trader.fetch_chart_data(account_id, candles=80)
        if not chart_data:
            await query.message.reply_text(
                "Не удалось получить данные для графика — позиция уже закрыта или сеть упала.\n"
                "Попробуй ещё раз через минуту.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("◀️ К позициям", callback_data="positions")],
                ]),
            )
            return

        try:
            from bot.position_chart import render_position_chart
            png_bytes = render_position_chart(
                ohlcv=chart_data["ohlcv"],
                trade=chart_data["trade"],
                current_price=chart_data["current_price"],
                title=chart_data["title"],
                symbol=chart_data["symbol"],
                timeframe=chart_data["timeframe"],
            )
        except Exception as e:
            logger.exception(f"Ошибка рендера графика для {account_id}: {e}")
            await query.message.reply_text(
                f"Ошибка при построении графика: {type(e).__name__}",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("◀️ К позициям", callback_data="positions")],
                ]),
            )
            return

        # Caption с кратким PnL — чтобы было видно прямо под картинкой
        positions = await paper_trader.get_open_positions_detail(account_ids={account_id})
        caption_lines = []
        if positions:
            p = positions[0]
            pnl_emoji = "🟢" if p["pnl_pct"] >= 0 else "🔴"
            caption_lines.append(f"{p['side_label']} · {p['symbol']} {p['timeframe']}")
            caption_lines.append(
                f"Вход ${p['entry_price']:,.2f} → ${p['current_price']:,.2f}"
            )
            caption_lines.append(
                f"{pnl_emoji} {p['pnl_dollars']:+,.2f}$ ({p['pnl_pct']:+.2f}%)"
            )
        caption = "\n".join(caption_lines) if caption_lines else None

        import io as _io
        from telegram import InputMediaPhoto

        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Обновить график", callback_data=f"position_chart_{account_id}")],
            [InlineKeyboardButton("◀️ К позициям", callback_data="positions")],
        ])

        # Если текущее сообщение уже фото (значит это refresh) — заменяем его, чтобы не плодить
        # картинки в чате. Иначе шлём новое.
        msg = query.message
        if getattr(msg, "photo", None):
            try:
                await query.edit_message_media(
                    media=InputMediaPhoto(media=_io.BytesIO(png_bytes), caption=caption),
                    reply_markup=markup,
                )
                return
            except BadRequest as e:
                # Если по какой-то причине не получилось заменить (например, картинка идентична) —
                # тихо игнорируем и шлём новое
                if "not modified" not in str(e).lower():
                    logger.warning(f"edit_message_media fail: {e}; fallback to reply_photo")

        await msg.reply_photo(
            photo=_io.BytesIO(png_bytes),
            caption=caption,
            reply_markup=markup,
        )

    async def _format_history_v3(self, user_id: int) -> str:
        """История сигналов: последние сделки только по подписанным стратегиям."""
        paper_trader = getattr(self.engine, "paper_trader", None)
        db = self.engine.db
        if not paper_trader or not db:
            return "📡 Paper Trading не запущен"

        subs = await db.get_user_subscriptions(user_id)
        sub_ids = {s["account_id"] for s in subs}
        if not sub_ids:
            return "📜 История сигналов\n━━━━━━━━━━━━━━━━━━━━\nНет подписок."

        # Выбираем сделки стратегий на которые пользователь подписан
        all_trades = await db.get_trades_history(limit=50)
        live_trades = []
        for t in all_trades:
            notes = t.get("notes", "")
            if notes:
                try:
                    import json as _json
                    n = _json.loads(notes)
                    if n.get("account_id") in sub_ids:
                        live_trades.append((t, n["account_id"]))
                except Exception:
                    pass

        if not live_trades:
            return (
                "📜 История сигналов\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "Сделок пока нет.\n"
                "Стратегии анализируют рынок — при сигнале придёт уведомление."
            )

        text = "📜 История сигналов\n━━━━━━━━━━━━━━━━━━━━\n\n"

        # Берём последние 10
        for t, acc_id in live_trades[:10]:
            acc = paper_trader.accounts.get(acc_id)
            title = paper_trader._account_title(acc) if acc else acc_id

            # Эмодзи результата
            pnl = t.get("pnl", 0) or 0
            emoji = "✅" if pnl > 0 else ("❌" if pnl < 0 else "⏳")
            side = "LONG" if t.get("side") == "buy" else "SHORT"

            # PnL в %
            cost = t.get("cost", 0) or 1
            pnl_pct = pnl / cost * 100 if cost else 0

            # Дата-время (UTC в БД → Пермь UTC+5 для отображения)
            closed_at = t.get("closed_at") or t.get("opened_at") or ""
            dt_str = self._fmt_utc_to_perm(closed_at)

            # Цены
            entry = t.get("price", 0) or 0

            text += f"{emoji} {title}\n"
            text += f"   {side} @ ${entry:,.2f}  ·  {dt_str}\n"
            text += f"   PnL: {self._fmt_money(pnl)} ({pnl_pct:+.1f}%)\n\n"

        return text

    async def _format_logs_v3(self, user_id: int) -> str:
        """Логи анализа: что бот видит при каждой проверке."""
        paper_trader = getattr(self.engine, "paper_trader", None)
        db = self.engine.db
        if not paper_trader or not paper_trader.accounts:
            return "📡 Paper Trading не запущен"

        subs = await db.get_user_subscriptions(user_id) if db else []
        sub_ids = {s["account_id"] for s in subs}
        if not sub_ids:
            return "📋 Логи анализа\n━━━━━━━━━━━━━━━━━━━━\nНет подписок."

        logs = paper_trader.get_logs(last_n=5)
        text = "📋 Логи анализа\n━━━━━━━━━━━━━━━━━━━━\n\n"

        for acc_id, entries in logs.items():
            if acc_id not in sub_ids:
                continue
            acc = paper_trader.accounts.get(acc_id)
            if not acc:
                continue
            title = paper_trader._account_title(acc)
            text += f"▸ {title}\n"

            if not entries:
                text += "   Анализов ещё не было\n\n"
                continue

            # Показываем последние 5
            for e in entries[-5:]:
                sig = e["signal"]
                if sig == "hold":
                    icon = "⚪"
                    status = "Ждём"
                elif sig == "buy":
                    icon = "🟢"
                    status = "LONG"
                elif sig == "sell":
                    icon = "🔴"
                    status = "SHORT"
                elif sig == "filtered":
                    icon = "⏸"
                    status = "Фильтр"
                elif sig == "warning":
                    icon = "⚠️"
                    status = "Warning"
                else:
                    icon = "·"
                    status = sig

                time_str = e.get("time", "")
                price = e.get("price", 0)
                price_str = f"${price:,.2f}" if price else ""
                reason = (e.get("reason") or "")[:60]

                text += f"   {icon} {time_str}  {status}  {price_str}\n"
                if reason and sig in ("hold", "filtered"):
                    text += f"      {reason}\n"
            text += "\n"

        # Ограничиваем длину
        if len(text) > 3900:
            text = text[:3850] + "\n\n... обрезано"
        return text

    # Описания стратегий для пользователя
    STRATEGY_DESCRIPTIONS = {
        "eth_combined_4h": {
            "title": "ETH Combined 4h",
            "type": "Signal · только лонги · без плеч",
            "desc": (
                "Meta-стратегия с определением фазы рынка: бычий тренд / боковик / медвежий.\n"
                "Фазу определяет по двум индикаторам — ADX (сила тренда) и Bollinger Bands Width\n"
                "(ширина канала). В зависимости от фазы включается разная логика:\n"
                "  • в тренде — Momentum Breakout (пробой канала)\n"
                "  • в боковике — Fake Breakout (ложные пробои)\n"
                "  • в медвежке — закрытие лонгов, сигнал на выход"
            ),
            "params": "SL 7% · TP 14% · R:R 1:2 · вход на весь депозит",
            "check": "каждые 30 мин",
            "stats": "Walk-forward (4 года данных): +91% годовых · DD 24% · PF 2.1 · WR 59%",
        },
        "btc_combined_4h": {
            "title": "BTC Combined 4h",
            "type": "Signal · только лонги · без плеч",
            "desc": (
                "Та же логика что у ETH Combined, но параметры оптимизированы под BTC.\n"
                "BTC менее волатилен — более низкий ADX-порог определения тренда\n"
                "и более узкие SL/TP. Больше сделок, но меньше годовая доходность."
            ),
            "params": "SL 5% · TP 10% · R:R 1:2 · вход на весь депозит",
            "check": "каждые 30 мин",
            "stats": "Walk-forward (4 года данных): +52% годовых · DD 23% · PF 1.7 · WR 49%",
        },
        "eth_micro_15m": {
            "title": "ETH Micro Breakout 15m",
            "type": "Test · long+short · leverage 5x",
            "desc": (
                "Внутридневная стратегия. Ищет периоды минимальной волатильности\n"
                "(сжатие ATR на 7+ свечей, это ~2 часа «затишья»). После затишья\n"
                "входит на пробое канала с подтверждением объёмом и ADX > 20.\n"
                "Исключает аномально большие свечи (новостные спайки)."
            ),
            "params": "SL 3.5% · TP 7% · R:R 1:2 · 4% риска на сделку",
            "check": "каждые 2 мин",
            "stats": "Walk-forward: +59% на 6 мес (с ADX-фильтром), DD 39%, PF 1.18",
        },
        "eth_pure_fake_4h": {
            "title": "ETH Fake Breakout 4h",
            "type": "Test · long+short · leverage 5x",
            "desc": (
                "Агрессивная range-стратегия. Ловит ложные пробои: свеча пробивает\n"
                "границу Donchian Channel тенью (wick > 0.5 ATR), но закрывается\n"
                "обратно внутри канала. Входит в противоположном направлении.\n"
                "Работает отлично в боковике, теряет в тренде."
            ),
            "params": "SL по ATR (2-8%) · TP 1.5×SL · 4% риска на сделку",
            "check": "каждые 30 мин",
            "stats": "Walk-forward: +82% за 6 мес (боковик), DD 24%, PF 1.58",
        },
        "sol_combined_4h": {
            "title": "SOL Combined 4h",
            "type": "Test · long+short · leverage 5x",
            "desc": (
                "Combined-стратегия для SOL. SOL более волатилен — мягче порог\n"
                "Bollinger Bands Width (40 вместо 25-30). В тренде — Momentum,\n"
                "в боковике — Fake Breakout."
            ),
            "params": "SL по ATR · TP зависит от фазы · 4% риска на сделку",
            "check": "каждые 30 мин",
            "stats": "Walk-forward: +46% на 6 мес, DD 23%, PF 1.4",
        },
    }

    async def _format_strategy_info_v3(self, user_id: int) -> str:
        """Информация по стратегиям, на которые подписан пользователь."""
        paper_trader = getattr(self.engine, "paper_trader", None)
        db = self.engine.db
        if not paper_trader or not db:
            return "📡 Paper Trading не запущен"

        subs = await db.get_user_subscriptions(user_id)
        sub_ids = {s["account_id"] for s in subs}
        if not sub_ids:
            return (
                "📖 Информация по стратегиям\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "У вас нет подписок на стратегии.\n"
                "Обратитесь к администратору для подключения."
            )

        text = "📖 Информация по стратегиям\n━━━━━━━━━━━━━━━━━━━━\n\n"

        for acc_id in paper_trader.accounts.keys():
            if acc_id not in sub_ids:
                continue
            info = self.STRATEGY_DESCRIPTIONS.get(acc_id)
            if not info:
                continue

            text += f"▸ {info['title']}\n"
            text += f"   {info['type']}\n\n"
            text += f"{info['desc']}\n\n"
            text += f"⚙️ Параметры: {info['params']}\n"
            text += f"🕒 Проверка: {info['check']}\n"
            text += f"📊 Результаты тестов: {info['stats']}\n\n"
            text += "━━━━━━━━━━━━━━━━━━━━\n\n"

        text += (
            "ℹ️ Подробнее о методологии тестирования и принципах работы — "
            "в PDF-отчётах, которые были отправлены с предыдущими апдейтами."
        )

        # Ограничиваем длину (Telegram limit 4096)
        if len(text) > 4000:
            text = text[:3950] + "\n\n... обрезано"
        return text

    def _format_status(self, status: dict) -> str:
        """Форматирует статус для отображения (legacy)."""
        running_emoji = "🟢" if status["running"] else "🔴"
        pnl_emoji = "📈" if status["total_pnl"] >= 0 else "📉"

        return (
            f"🤖 *Статус бота*\n\n"
            f"{running_emoji} Статус: `{'Работает' if status['running'] else 'Остановлен'}`\n"
            f"Режим: `{status['mode']}` | `{status['trading_mode']}`\n"
            f"Биржа: `{status['exchange']}`\n"
            f"Стратегия: `{status['strategy']}`\n"
            f"Риск: `{status['risk_level']}`\n\n"
            f"💰 Баланс: `{status['balance']:.2f}` USDT\n"
            f"{pnl_emoji} PnL сегодня: `{status['daily_pnl']:+.2f}` USDT\n"
            f"{pnl_emoji} PnL всего: `{status['total_pnl']:+.2f}` USDT\n"
            f"📉 Просадка: `{status['drawdown_pct']:.1f}%`\n"
            f"📊 Открыто позиций: `{status['open_positions']}`\n"
            f"Пары: `{', '.join(status['symbols'])}`"
        )

    # === Запуск ===

    def build(self) -> Application:
        """Создаёт Telegram Application."""
        self._app = Application.builder().token(self.settings.telegram_bot_token).build()

        # Регистрируем команды
        self._app.add_handler(CommandHandler("start", self.cmd_start))
        self._app.add_handler(CommandHandler("status", self.cmd_status))
        self._app.add_handler(CommandHandler("trades", self.cmd_trades))
        self._app.add_handler(CommandHandler("history", self.cmd_history))
        self._app.add_handler(CommandHandler("strategy", self.cmd_strategy))
        self._app.add_handler(CommandHandler("risk", self.cmd_risk))
        self._app.add_handler(CommandHandler("symbol", self.cmd_symbol))
        self._app.add_handler(CommandHandler("backtest", self.cmd_backtest))
        self._app.add_handler(CommandHandler("compare", self.cmd_compare))
        self._app.add_handler(CommandHandler("stats", self.cmd_stats))
        self._app.add_handler(CommandHandler("mode", self.cmd_mode))
        self._app.add_handler(CommandHandler("help", self.cmd_help))
        self._app.add_handler(CommandHandler("info", self.cmd_info))

        # Callback для inline кнопок
        self._app.add_handler(CallbackQueryHandler(self.handle_callback))

        # Текстовые сообщения (для флоу добавления пользователя)
        self._app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_text))

        return self._app
