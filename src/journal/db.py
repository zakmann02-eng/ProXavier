"""SQLite journal: trades, per-day outcomes, and small bot state (halt flag etc.)."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    day TEXT NOT NULL,
    mode TEXT NOT NULL,
    setup TEXT, direction TEXT, zone TEXT,
    legs TEXT, expiry TEXT, qty INTEGER,
    entry_time TEXT, entry_price REAL, spy_entry REAL, structural_stop REAL, target REAL,
    exit_time TEXT, exit_price REAL, exit_reason TEXT,
    fees REAL, pnl REAL, pnl_pct REAL
);
CREATE TABLE IF NOT EXISTS days (
    day TEXT PRIMARY KEY,
    status TEXT NOT NULL,        -- running | skipped | no_trade | traded | error
    reason TEXT,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT);
"""


@dataclass
class TradeRow:
    id: int
    day: date
    setup: str
    direction: str
    pnl: float | None
    pnl_pct: float | None
    exit_reason: str | None


class Journal:
    def __init__(self, path: str | Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # ── days ──
    def day_status(self, d: date) -> str | None:
        r = self.conn.execute("SELECT status FROM days WHERE day=?", (d.isoformat(),)).fetchone()
        return r["status"] if r else None

    def set_day(self, d: date, status: str, reason: str = "") -> None:
        self.conn.execute(
            "INSERT INTO days(day,status,reason,updated_at) VALUES(?,?,?,?) "
            "ON CONFLICT(day) DO UPDATE SET status=excluded.status, reason=excluded.reason, "
            "updated_at=excluded.updated_at",
            (d.isoformat(), status, reason, datetime.now().isoformat(timespec="seconds")))
        self.conn.commit()

    def days_between(self, start: date, end: date) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM days WHERE day BETWEEN ? AND ? ORDER BY day",
                                 (start.isoformat(), end.isoformat())).fetchall()

    # ── trades ──
    def open_trade(self, **fields) -> int:
        fields = {k: (json.dumps(v) if isinstance(v, (list, dict)) else v) for k, v in fields.items()}
        fields = {k: (v.isoformat() if isinstance(v, (date, datetime)) else v) for k, v in fields.items()}
        cols = ",".join(fields)
        cur = self.conn.execute(f"INSERT INTO trades({cols}) VALUES({','.join('?' * len(fields))})",
                                tuple(fields.values()))
        self.conn.commit()
        return int(cur.lastrowid)

    def close_trade(self, trade_id: int, exit_time: datetime, exit_price: float, exit_reason: str,
                    fees: float, pnl: float, pnl_pct: float) -> None:
        self.conn.execute(
            "UPDATE trades SET exit_time=?, exit_price=?, exit_reason=?, fees=?, pnl=?, pnl_pct=? WHERE id=?",
            (exit_time.isoformat(), exit_price, exit_reason, fees, pnl, pnl_pct, trade_id))
        self.conn.commit()

    def unclosed_trades(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM trades WHERE exit_time IS NULL").fetchall()

    def closed_trades(self, start: date | None = None, end: date | None = None) -> list[TradeRow]:
        q = "SELECT * FROM trades WHERE pnl IS NOT NULL"
        args: list = []
        if start:
            q += " AND day >= ?"
            args.append(start.isoformat())
        if end:
            q += " AND day <= ?"
            args.append(end.isoformat())
        rows = self.conn.execute(q + " ORDER BY id", args).fetchall()
        return [TradeRow(r["id"], date.fromisoformat(r["day"]), r["setup"], r["direction"], r["pnl"],
                         r["pnl_pct"], r["exit_reason"]) for r in rows]

    def trades_on(self, d: date) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM trades WHERE day=?", (d.isoformat(),)).fetchone()[0]

    # ── state ──
    def get_state(self, key: str, default: str | None = None) -> str | None:
        r = self.conn.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return r["value"] if r else default

    def set_state(self, key: str, value: str) -> None:
        self.conn.execute("INSERT INTO state(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                          (key, value))
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()
