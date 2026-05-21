from __future__ import annotations

import logging
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)


SCHEMA = """
CREATE TABLE IF NOT EXISTS situations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    exchange TEXT NOT NULL,
    symbol TEXT NOT NULL,
    opened_at REAL NOT NULL,
    closed_at REAL,
    open_spread_pct REAL NOT NULL,
    max_abs_spread_pct REAL NOT NULL,
    max_spread_pct REAL NOT NULL,
    close_spread_pct REAL,
    open_last_price REAL NOT NULL,
    open_fair_price REAL NOT NULL,
    close_last_price REAL,
    close_fair_price REAL,
    open_volume_usdt REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_situations_exchange ON situations(exchange);
CREATE INDEX IF NOT EXISTS idx_situations_opened ON situations(opened_at DESC);
CREATE INDEX IF NOT EXISTS idx_situations_closed ON situations(closed_at);
CREATE INDEX IF NOT EXISTS idx_situations_symbol ON situations(symbol);
"""


@dataclass
class SituationRow:
    id: int
    exchange: str
    symbol: str
    opened_at: float
    closed_at: Optional[float]
    open_spread_pct: float
    max_abs_spread_pct: float
    max_spread_pct: float
    close_spread_pct: Optional[float]
    open_last_price: float
    open_fair_price: float
    close_last_price: Optional[float]
    close_fair_price: Optional[float]
    open_volume_usdt: float


class HistoryStore:
    """SQLite-backed store for spread "situations" (open → converged events)."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(
            self.path,
            isolation_level=None,  # autocommit
            check_same_thread=False,
            timeout=10.0,
        )
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    # -- writes --------------------------------------------------------------

    def open_situation(
        self,
        *,
        exchange: str,
        symbol: str,
        opened_at: float,
        spread_pct: float,
        last_price: float,
        fair_price: float,
        volume_usdt: float,
    ) -> int:
        with self._lock, self._conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO situations
                  (exchange, symbol, opened_at,
                   open_spread_pct, max_abs_spread_pct, max_spread_pct,
                   open_last_price, open_fair_price, open_volume_usdt)
                VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (
                    exchange,
                    symbol,
                    opened_at,
                    spread_pct,
                    abs(spread_pct),
                    spread_pct,
                    last_price,
                    fair_price,
                    volume_usdt,
                ),
            )
            return int(cur.lastrowid)

    def update_max(self, sid: int, spread_pct: float) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                """
                UPDATE situations
                SET max_abs_spread_pct = ?,
                    max_spread_pct = ?
                WHERE id = ? AND ? > max_abs_spread_pct
                """,
                (abs(spread_pct), spread_pct, sid, abs(spread_pct)),
            )

    def close_situation(
        self,
        sid: int,
        *,
        closed_at: float,
        spread_pct: float,
        last_price: float,
        fair_price: float,
    ) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                """
                UPDATE situations
                SET closed_at = ?,
                    close_spread_pct = ?,
                    close_last_price = ?,
                    close_fair_price = ?
                WHERE id = ? AND closed_at IS NULL
                """,
                (closed_at, spread_pct, last_price, fair_price, sid),
            )

    # -- reads ---------------------------------------------------------------

    def list_open(self) -> List[SituationRow]:
        with self._lock, self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM situations WHERE closed_at IS NULL"
            ).fetchall()
        return [self._to_row(r) for r in rows]

    def query(
        self,
        *,
        exchange: Optional[str] = None,
        symbol: Optional[str] = None,
        status: Optional[str] = None,
        from_ts: Optional[float] = None,
        to_ts: Optional[float] = None,
        min_abs_spread: Optional[float] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Tuple[List[SituationRow], int]:
        wheres: List[str] = []
        params: List[object] = []
        if exchange:
            wheres.append("exchange = ?")
            params.append(exchange)
        if symbol:
            wheres.append("symbol LIKE ?")
            params.append(f"%{symbol}%")
        if status == "open":
            wheres.append("closed_at IS NULL")
        elif status == "closed":
            wheres.append("closed_at IS NOT NULL")
        if from_ts is not None:
            wheres.append("opened_at >= ?")
            params.append(from_ts)
        if to_ts is not None:
            wheres.append("opened_at <= ?")
            params.append(to_ts)
        if min_abs_spread is not None:
            wheres.append("max_abs_spread_pct >= ?")
            params.append(min_abs_spread)
        where_sql = ("WHERE " + " AND ".join(wheres)) if wheres else ""

        limit = max(1, min(int(limit), 500))
        offset = max(0, int(offset))

        with self._lock, self._conn() as conn:
            total = conn.execute(
                f"SELECT COUNT(*) FROM situations {where_sql}", params
            ).fetchone()[0]
            rows = conn.execute(
                f"""
                SELECT * FROM situations {where_sql}
                ORDER BY opened_at DESC
                LIMIT ? OFFSET ?
                """,
                (*params, limit, offset),
            ).fetchall()
        return [self._to_row(r) for r in rows], int(total)

    @staticmethod
    def _to_row(r: sqlite3.Row) -> SituationRow:
        return SituationRow(**dict(r))
