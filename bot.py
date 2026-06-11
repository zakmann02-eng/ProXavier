"""
Xavier — World-Class Quant Trading Bot

Trading schedule (Eastern Time)
---------------------------------
09:30  Market opens — no trades yet
09:46  ORB captured; ETF rankings refreshed; day scan begins
09:46 – 15:30  Active day-trading window (ORB + VWAP reversion + Crypto)
10:00 & 14:00  Swing scan (Momentum breakout + ETF rotation)
Every 30 s      Position monitor — TP/SL checks, trailing stop ratchet
15:45           Force-close all remaining DAY positions
16:00           Daily P&L report

Strategies (5 running in parallel)
-------------------------------------
  ORBStrategy           — Opening Range Breakout       [day,   equity/ETF]
  VWAPReversionStrategy — VWAP mean-reversion fade     [day,   equity/ETF]
  MomentumStrategy      — 20-day high/low breakout     [swing, equity/ETF]
  ETFRotationStrategy   — Sector ETF relative strength [swing, ETF]
  CryptoTrendStrategy   — Multi-TF crypto trend        [day+swing, crypto]
"""
from __future__ import annotations

import asyncio
import logging
import sys
from datetime import datetime, time as dt_time
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from .alpaca_client import AlpacaClient
from .config import XavierConfig
from .data_manager import DataManager
from .position_manager import PositionManager
from .risk_manager import RiskManager
from .scanner import Scanner
from .signal_model import Signal, SignalBus
from .strategies import (
    CryptoTrendStrategy,
    ETFRotationStrategy,
    MomentumStrategy,
    ORBStrategy,
    VWAPReversionStrategy,
)

logger = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")

_TRADE_START  = dt_time(9, 46)
_TRADE_END    = dt_time(15, 30)
_EOD_CLOSE_H  = 15
_EOD_CLOSE_M  = 45


class XavierBot:
    def __init__(self, cfg: XavierConfig):
        self.cfg    = cfg
        self.paused = cfg.paused

        # ── Core components ───────────────────────────────────────────────────
        self._client  = AlpacaClient(
            cfg.alpaca_api_key, cfg.alpaca_api_secret, paper=cfg.alpaca_paper
        )
        self._scanner = Scanner(enable_crypto=cfg.enable_crypto)
        self._data    = DataManager(self._client)
        self._bus     = SignalBus()

        # ── Strategies ────────────────────────────────────────────────────────
        self._orb = ORBStrategy(
            orb_minutes=cfg.orb_minutes,
            min_rr=cfg.min_rr_ratio,
            min_price=cfg.min_price,
            allow_short=cfg.allow_short,
        )
        self._vwap_rev = VWAPReversionStrategy(
            min_rr=1.2,
            min_price=cfg.min_price,
            allow_short=cfg.allow_short,
        )
        self._momentum = MomentumStrategy(
            min_rr=2.0,
            min_price=cfg.min_price,
            allow_short=cfg.allow_short,
        )
        self._etf_rot = ETFRotationStrategy(min_rr=1.5, top_n=2)
        self._crypto  = CryptoTrendStrategy(
            min_rr=1.5,
            allow_short=cfg.allow_short,
        )

        # ── Risk / position management ────────────────────────────────────────
        self._risk_mgr = RiskManager(
            max_portfolio_risk_pct=cfg.max_portfolio_risk_pct,
            daily_loss_limit_pct=cfg.daily_loss_limit_pct,
            consecutive_loss_halt=cfg.consecutive_loss_halt,
            max_crypto_alloc_pct=cfg.max_crypto_alloc_pct,
            max_open_positions=cfg.max_positions,
        )

        # Telegram Application and PositionManager initialised in run()
        self._app: Application | None     = None
        self._pos_mgr: PositionManager | None = None

        self._orb_date: object = None   # date of last ORB capture

    # ── Telegram notification ──────────────────────────────────────────────────

    async def _notify(self, text: str) -> None:
        if self._app is None:
            return
        try:
            await self._app.bot.send_message(
                chat_id=self.cfg.telegram_chat_id,
                text=text,
                parse_mode="Markdown",
                disable_web_page_preview=True,
            )
        except Exception as exc:
            logger.warning("Telegram send failed: %s", exc)

    # ── Scheduled jobs ─────────────────────────────────────────────────────────

    async def _job_orb_and_rank(self) -> None:
        """9:46 AM — capture ORB for all equities + update ETF rankings."""
        today = datetime.now(ET).date()
        if self._orb_date == today:
            return

        # Day-start bookkeeping
        try:
            equity = self._client.get_equity()
            self._risk_mgr.set_day_start(equity)
        except Exception as exc:
            logger.warning("Could not read equity at day start: %s", exc)

        # Capture ORBs
        captured = 0
        for sym in self._scanner.all_equity:
            try:
                df = self._data.today_1m(sym)
                if self._orb.capture(sym, df):
                    captured += 1
            except Exception as exc:
                logger.warning("ORB capture failed %s: %s", sym, exc)

        # ETF rankings for rotation strategy
        if self.cfg.enable_etf_rotation:
            etf_dfs = {}
            for sym in self._scanner.etf_symbols:
                df = self._data.daily(sym, lookback_days=10)
                if not df.empty:
                    etf_dfs[sym] = df
            self._etf_rot.update_rankings(etf_dfs)

        self._orb_date = today
        n_eq = len(self._scanner.all_equity)
        logger.info("ORB captured %d/%d | ETF rankings updated", captured, n_eq)
        await self._notify(
            f"📐 *Xavier armed* — ORB {captured}/{n_eq} | "
            f"ETF ranked | trading window open"
        )

    async def _job_day_scan(self) -> None:
        """Every 2 min during trading hours — ORB + VWAP reversion + Crypto."""
        if self.paused or self._risk_mgr.is_halted:
            return

        now_et = datetime.now(ET).time()
        if not (_TRADE_START <= now_et <= _TRADE_END):
            return

        try:
            if not self._client.is_market_open():
                return
        except Exception as exc:
            logger.warning("Market status check failed: %s", exc)
            return

        if self._orb_date != datetime.now(ET).date():
            return

        pos_mgr = self._pos_mgr
        assert pos_mgr is not None

        try:
            equity = self._client.get_equity()
        except Exception as exc:
            logger.error("Could not get equity: %s", exc)
            return

        self._bus.clear()

        # ── Equities & ETFs (ORB + VWAP reversion) ───────────────────────────
        for sym in self._scanner.all_equity:
            if pos_mgr.has_position(sym):
                continue
            try:
                df = self._data.today_1m(sym)
                asset_cls = "etf" if sym in self._scanner.etf_symbols else "equity"

                sig = self._orb.evaluate(sym, df, equity, self.cfg.risk_pct, asset_cls)
                if sig:
                    self._bus.submit(sig)

                if self.cfg.enable_vwap_reversion:
                    sig2 = self._vwap_rev.evaluate(sym, df, equity, self.cfg.risk_pct, asset_cls)
                    if sig2:
                        self._bus.submit(sig2)
            except Exception as exc:
                logger.debug("Day scan error %s: %s", sym, exc)

        # ── Crypto ────────────────────────────────────────────────────────────
        if self.cfg.enable_crypto:
            for sym in self._scanner.crypto_symbols:
                if pos_mgr.has_position(sym):
                    continue
                try:
                    df_1m    = self._data.crypto_1m(sym)
                    df_daily = self._data.crypto_daily(sym, days=30)
                    sig = self._crypto.evaluate(sym, df_1m, df_daily, equity, self.cfg.risk_pct)
                    if sig:
                        self._bus.submit(sig)
                except Exception as exc:
                    logger.debug("Crypto scan error %s: %s", sym, exc)

        await self._execute_signals(equity, pos_mgr)

    async def _job_swing_scan(self) -> None:
        """10:00 AM and 2:00 PM — Momentum breakout + ETF rotation."""
        if self.paused or not self.cfg.enable_swing:
            return

        now_et = datetime.now(ET)
        if now_et.weekday() >= 5:
            return

        pos_mgr = self._pos_mgr
        assert pos_mgr is not None

        try:
            equity = self._client.get_equity()
        except Exception as exc:
            logger.error("Swing scan: could not get equity: %s", exc)
            return

        self._bus.clear()

        # ── Momentum breakout ─────────────────────────────────────────────────
        if self.cfg.enable_momentum:
            for sym in self._scanner.all_equity:
                if pos_mgr.has_position(sym):
                    continue
                try:
                    df_daily  = self._data.daily(sym, lookback_days=60)
                    asset_cls = "etf" if sym in self._scanner.etf_symbols else "equity"
                    sig = self._momentum.evaluate(sym, df_daily, equity, self.cfg.risk_pct, asset_cls)
                    if sig:
                        self._bus.submit(sig)
                except Exception as exc:
                    logger.debug("Momentum scan error %s: %s", sym, exc)

        # ── ETF rotation ──────────────────────────────────────────────────────
        if self.cfg.enable_etf_rotation:
            etf_dfs: dict = {}
            for sym in self._scanner.etf_symbols:
                df = self._data.daily(sym, lookback_days=10)
                if not df.empty:
                    etf_dfs[sym] = df
            self._etf_rot.update_rankings(etf_dfs)

            for sym in self._scanner.etf_symbols:
                if pos_mgr.has_position(sym):
                    continue
                try:
                    df_daily = self._data.daily(sym, lookback_days=60)
                    sig = self._etf_rot.evaluate(sym, df_daily, equity, self.cfg.risk_pct)
                    if sig:
                        self._bus.submit(sig)
                except Exception as exc:
                    logger.debug("ETF rotation error %s: %s", sym, exc)

        await self._execute_signals(equity, pos_mgr)

    async def _execute_signals(self, equity: float, pos_mgr: PositionManager) -> None:
        """Gate, size, and place orders for all signals in the bus."""
        signals = self._bus.flush()
        if not signals:
            return

        logger.info("Signal bus: %d candidate(s)", len(signals))
        placed = 0

        for sig in signals:
            if pos_mgr.has_position(sig.symbol):
                continue

            # Respect per-type position limits
            if sig.trade_type == "day"   and pos_mgr.day_count   >= self.cfg.max_day_positions:
                continue
            if sig.trade_type == "swing" and pos_mgr.swing_count >= self.cfg.max_swing_positions:
                continue

            decision = self._risk_mgr.check(sig, equity, pos_mgr.open_count)
            if not decision.allowed:
                logger.info("BLOCKED %s: %s", sig.symbol, decision.reason)
                continue

            # Apply any qty scaling the risk manager recommended
            if decision.adjusted_qty is not None:
                sig = self._scale_signal(sig, decision.adjusted_qty)

            success = await self._place_order(sig)
            if success:
                pos_mgr.record_entry(sig)
                placed += 1
                await self._notify_entry(sig)

        if placed:
            logger.info("Placed %d trade(s) this scan", placed)

    def _scale_signal(self, sig: Signal, new_qty: float) -> Signal:
        from dataclasses import replace as dc_replace
        risk_ratio = new_qty / sig.qty if sig.qty > 0 else 1.0
        return dc_replace(sig, qty=new_qty, risk_usd=sig.risk_usd * risk_ratio)

    async def _place_order(self, sig: Signal) -> bool:
        from alpaca.trading.enums import OrderSide
        side = OrderSide.BUY if sig.direction == "LONG" else OrderSide.SELL
        try:
            qty = sig.qty if sig.asset_class == "crypto" else int(sig.qty)
            self._client.place_market_order(sig.symbol, qty, side)
            return True
        except Exception as exc:
            logger.error("Order failed %s: %s", sig.symbol, exc)
            return False

    async def _notify_entry(self, sig: Signal) -> None:
        mode = "PAPER" if self.cfg.alpaca_paper else "LIVE"
        await self._notify(
            f"{'📈' if sig.direction == 'LONG' else '📉'} "
            f"*{sig.symbol}* [{sig.strategy_id}] {sig.trade_type.upper()}\n"
            f"Mode: {mode}  |  {sig.direction} x{sig.qty:.4f} @ ${sig.entry_price:.4f}\n"
            f"TP1: ${sig.tp1_price:.4f}  |  SL: ${sig.sl_price:.4f}\n"
            f"R:R {sig.rr_ratio:.1f}:1  |  Risk ${sig.risk_usd:.2f}  |  Score {sig.score}/100\n"
            f"Signals: {', '.join(sig.triggers)}"
        )

    async def _job_check_positions(self) -> None:
        if self._pos_mgr:
            await self._pos_mgr.check_positions()

    async def _job_eod_close(self) -> None:
        if datetime.now(ET).weekday() >= 5:
            return
        logger.info("EOD: closing all day positions")
        if self._pos_mgr:
            await self._pos_mgr.close_day_positions(reason="EOD")
        self._orb.clear()
        self._orb_date = None
        self._data.invalidate()

    async def _job_daily_report(self) -> None:
        if datetime.now(ET).weekday() >= 5:
            return
        if self._pos_mgr:
            await self._pos_mgr.send_daily_report()

    # ── Telegram commands ──────────────────────────────────────────────────────

    async def _cmd_status(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            acct   = self._client.get_account()
            equity = float(acct.equity)
            bp     = float(acct.buying_power)
            mkt    = self._client.is_market_open()
        except Exception as exc:
            await update.message.reply_text(f"Error fetching account: {exc}")
            return

        pm   = self._pos_mgr
        mode = "PAPER" if self.cfg.alpaca_paper else "LIVE"
        sign = "+" if (pm.daily_pnl if pm else 0) >= 0 else ""
        halted = self._risk_mgr.is_halted

        await update.message.reply_text(
            f"📊 *Xavier Status*\n"
            f"Mode: {mode}  |  Market: {'Open' if mkt else 'Closed'}\n"
            f"Equity: ${equity:,.2f}  |  BP: ${bp:,.2f}\n"
            f"Positions: {pm.open_count if pm else 0}/{self.cfg.max_positions} "
            f"(day {pm.day_count if pm else 0} / swing {pm.swing_count if pm else 0})\n"
            f"Daily P&L: ${sign}{pm.daily_pnl:.2f}\n"
            f"Paused: {self.paused}  |  Halted: {halted}",
            parse_mode="Markdown",
        )

    async def _cmd_positions(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        text = self._pos_mgr.summary_text() if self._pos_mgr else "Bot not ready."
        await update.message.reply_text(text, parse_mode="Markdown")

    async def _cmd_risk(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        rm  = self._risk_mgr
        try:
            equity = self._client.get_equity()
        except Exception:
            equity = 0.0
        await update.message.reply_text(
            f"🛡 *Risk Dashboard*\n"
            f"Open risk: ${rm._open_risk_usd:.2f} "
            f"({rm._open_risk_usd / equity * 100:.1f}% of equity)\n"
            f"Crypto notional: ${rm._crypto_notional:.2f}\n"
            f"Consecutive losses: {rm._consecutive_losses}\n"
            f"Daily P&L: ${rm.daily_pnl:.2f}\n"
            f"Halted: {rm.is_halted}",
            parse_mode="Markdown",
        )

    async def _cmd_strategies(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        cfg = self.cfg
        await update.message.reply_text(
            f"⚙️ *Active Strategies*\n"
            f"✅ ORB ({cfg.orb_minutes}min opening range)\n"
            f"{'✅' if cfg.enable_vwap_reversion else '⏸'} VWAP Reversion\n"
            f"{'✅' if cfg.enable_momentum else '⏸'} Momentum Breakout (swing)\n"
            f"{'✅' if cfg.enable_etf_rotation else '⏸'} ETF Sector Rotation (swing)\n"
            f"{'✅' if cfg.enable_crypto else '⏸'} Crypto Trend\n\n"
            f"Risk/trade: {cfg.risk_pct}%  |  Min R:R {cfg.min_rr_ratio}:1\n"
            f"Max positions: {cfg.max_positions} "
            f"(day {cfg.max_day_positions} / swing {cfg.max_swing_positions})",
            parse_mode="Markdown",
        )

    async def _cmd_pause(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        self.paused = True
        await update.message.reply_text("⏸ Xavier paused — no new trades.")

    async def _cmd_resume(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        self.paused = False
        await update.message.reply_text("▶️ Xavier resumed.")

    async def _cmd_closeall(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if self._pos_mgr:
            await self._pos_mgr.close_all(reason="MANUAL")
        await update.message.reply_text("🔒 All positions closed.")

    async def _cmd_closeday(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if self._pos_mgr:
            await self._pos_mgr.close_day_positions(reason="MANUAL_EOD")
        await update.message.reply_text("🔒 Day positions closed.")

    async def _cmd_watchlist(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        eq  = ", ".join(self._scanner.equity_symbols)
        etf = ", ".join(self._scanner.etf_symbols)
        cry = ", ".join(self._scanner.crypto_symbols)
        await update.message.reply_text(
            f"*Watchlist*\n\n"
            f"Equities ({len(self._scanner.equity_symbols)}): {eq}\n\n"
            f"ETFs ({len(self._scanner.etf_symbols)}): {etf}\n\n"
            f"Crypto ({len(self._scanner.crypto_symbols)}): {cry}",
            parse_mode="Markdown",
        )

    async def _cmd_help(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text(
            "*Xavier Commands*\n"
            "/status — account, equity, P&L\n"
            "/positions — open positions with TP/SL\n"
            "/risk — portfolio risk exposure\n"
            "/strategies — active strategy settings\n"
            "/pause — stop new trades\n"
            "/resume — re-enable trading\n"
            "/closeall — close every open position\n"
            "/closeday — close day positions only\n"
            "/watchlist — current watchlist\n"
            "/help — this message",
            parse_mode="Markdown",
        )

    # ── Entry point ────────────────────────────────────────────────────────────

    async def run(self) -> None:
        self._app = Application.builder().token(self.cfg.telegram_token).build()

        self._pos_mgr = PositionManager(
            self._client,
            self._notify,
            self._risk_mgr,
        )

        for cmd, handler in [
            ("status",    self._cmd_status),
            ("positions", self._cmd_positions),
            ("risk",      self._cmd_risk),
            ("strategies",self._cmd_strategies),
            ("pause",     self._cmd_pause),
            ("resume",    self._cmd_resume),
            ("closeall",  self._cmd_closeall),
            ("closeday",  self._cmd_closeday),
            ("watchlist", self._cmd_watchlist),
            ("help",      self._cmd_help),
        ]:
            self._app.add_handler(CommandHandler(cmd, handler))

        scheduler = AsyncIOScheduler(timezone="America/New_York")

        # 9:46 AM — capture ORB and rank ETFs
        scheduler.add_job(self._job_orb_and_rank,    "cron",     hour=9,  minute=46, day_of_week="mon-fri")
        # Day scan every 2 min
        scheduler.add_job(self._job_day_scan,        "interval", minutes=2)
        # Swing scan at 10:00 AM and 2:00 PM
        scheduler.add_job(self._job_swing_scan,      "cron",     hour=10, minute=0,  day_of_week="mon-fri")
        scheduler.add_job(self._job_swing_scan,      "cron",     hour=14, minute=0,  day_of_week="mon-fri")
        # Position monitor every 30 s
        scheduler.add_job(self._job_check_positions, "interval", seconds=30)
        # EOD force-close day positions at 3:45 PM
        scheduler.add_job(self._job_eod_close,       "cron",     hour=_EOD_CLOSE_H, minute=_EOD_CLOSE_M, day_of_week="mon-fri")
        # Daily report at 4:00 PM
        scheduler.add_job(self._job_daily_report,    "cron",     hour=16, minute=0,  day_of_week="mon-fri")

        scheduler.start()

        async with self._app:
            await self._app.initialize()
            await self._app.start()
            await self._app.updater.start_polling(drop_pending_updates=True)

            mode = "PAPER" if self.cfg.alpaca_paper else "LIVE"
            n_eq = len(self._scanner.equity_symbols)
            n_etf = len(self._scanner.etf_symbols)
            n_cry = len(self._scanner.crypto_symbols)
            strategies = (
                "ORB"
                + (" + VWAP-Rev"   if self.cfg.enable_vwap_reversion else "")
                + (" + Momentum"   if self.cfg.enable_momentum       else "")
                + (" + ETF-Rot"    if self.cfg.enable_etf_rotation   else "")
                + (" + Crypto"     if self.cfg.enable_crypto         else "")
            )
            await self._notify(
                f"🚀 *Xavier — Online*\n"
                f"Mode: {mode}  |  {strategies}\n"
                f"Watchlist: {n_eq} equities · {n_etf} ETFs · {n_cry} crypto\n"
                f"Risk/trade: {self.cfg.risk_pct}%  |  Max positions: {self.cfg.max_positions}\n"
                f"TP scale: TP1 (50%)→breakeven→trailing  TP2 (full exit)\n"
                f"Commands: /help"
            )

            logger.info("Xavier is running.")
            try:
                await asyncio.Event().wait()
            except (KeyboardInterrupt, SystemExit):
                pass
            finally:
                scheduler.shutdown(wait=False)
                await self._app.updater.stop()
                await self._app.stop()
