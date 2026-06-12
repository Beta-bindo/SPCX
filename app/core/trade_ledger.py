"""Local trade settlement records for profit calculator."""


from __future__ import annotations
import json
import threading
from dataclasses import asdict, dataclass, field
from datetime import date, datetime

from app.core.paths import ledger_path

_ledger_lock = threading.Lock()


def hedge_sides(mode: str) -> tuple[str, str]:
    """Return (ba_side, mt5_side) for contraction/expansion."""
    if mode == "expansion":
        return "BUY", "SELL"
    return "SELL", "BUY"


@dataclass
class TradeRecord:
    settled_at: str
    preset_id: str
    mode: str
    action: str = "close"
    spread: float = 0.0
    ba_price: float = 0.0
    ex_price: float = 0.0
    ba_quantity: float = 0.0
    mt5_quantity: float = 0.0
    ba_side: str = ""
    mt5_side: str = ""
    ba_pnl: float = 0.0
    mt5_pnl: float = 0.0
    ba_fee: float = 0.0
    mt5_fee: float = 0.0

    @property
    def gross_pnl(self) -> float:
        return round(self.ba_pnl + self.mt5_pnl, 2)

    @property
    def total_fees(self) -> float:
        return round(self.ba_fee + self.mt5_fee, 4)

    @property
    def net_pnl(self) -> float:
        return round(self.gross_pnl - self.total_fees, 2)

    @property
    def direction(self) -> str:
        if self.ba_side and self.mt5_side:
            return f"BA {self.ba_side} / Ex {self.mt5_side}"
        ba_side, mt5_side = hedge_sides(self.mode)
        return f"BA {ba_side} / Ex {mt5_side}"


@dataclass
class TradeLedger:
    records: list[TradeRecord] = field(default_factory=list)

    def add(self, record: TradeRecord) -> None:
        self.records.append(record)
        save_ledger(self)

    def filter(
        self,
        start: date,
        end: date | None = None,
        preset_id: str | None = None,
    ) -> list[TradeRecord]:
        end = end or date.today()
        out: list[TradeRecord] = []
        for rec in self.records:
            try:
                d = date.fromisoformat(rec.settled_at[:10])
            except ValueError:
                continue
            if d < start or d > end:
                continue
            if preset_id and rec.preset_id != preset_id:
                continue
            out.append(rec)
        return out


def load_ledger() -> TradeLedger:
    path = ledger_path()
    if not path.exists():
        return TradeLedger()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        records = [TradeRecord(**item) for item in raw.get("records", [])]
        return TradeLedger(records=records)
    except (json.JSONDecodeError, TypeError, ValueError):
        return TradeLedger()


def save_ledger(ledger: TradeLedger) -> None:
    path = ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"records": [asdict(r) for r in ledger.records]}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def record_trade(
    preset_id: str,
    mode: str,
    action: str,
    *,
    spread: float = 0.0,
    ba_price: float = 0.0,
    ex_price: float = 0.0,
    ba_quantity: float = 0.0,
    mt5_quantity: float = 0.0,
    ba_side: str = "",
    mt5_side: str = "",
    ba_pnl: float = 0.0,
    mt5_pnl: float = 0.0,
    ba_fee: float = 0.0,
    mt5_fee: float = 0.0,
) -> TradeRecord:
    if not ba_side or not mt5_side:
        ba_side, mt5_side = hedge_sides(mode)
    rec = TradeRecord(
        settled_at=datetime.now().isoformat(timespec="seconds"),
        preset_id=preset_id,
        mode=mode,
        action=action,
        spread=round(spread, 3),
        ba_price=round(ba_price, 3),
        ex_price=round(ex_price, 3),
        ba_quantity=round(ba_quantity, 4),
        mt5_quantity=round(mt5_quantity, 4),
        ba_side=ba_side,
        mt5_side=mt5_side,
        ba_pnl=round(ba_pnl, 2),
        mt5_pnl=round(mt5_pnl, 2),
        ba_fee=round(ba_fee, 4),
        mt5_fee=round(mt5_fee, 4),
    )
    with _ledger_lock:
        ledger = load_ledger()
        ledger.records.append(rec)
        save_ledger(ledger)
    return rec


def record_close_settlement(
    preset_id: str,
    mode: str,
    ba_pnl: float,
    mt5_pnl: float,
    ba_fee: float,
    mt5_fee: float,
    *,
    spread: float = 0.0,
    ba_price: float = 0.0,
    ex_price: float = 0.0,
    ba_quantity: float = 0.0,
    mt5_quantity: float = 0.0,
    ba_side: str = "",
    mt5_side: str = "",
) -> TradeRecord:
    return record_trade(
        preset_id,
        mode,
        "close",
        spread=spread,
        ba_price=ba_price,
        ex_price=ex_price,
        ba_quantity=ba_quantity,
        mt5_quantity=mt5_quantity,
        ba_side=ba_side,
        mt5_side=mt5_side,
        ba_pnl=ba_pnl,
        mt5_pnl=mt5_pnl,
        ba_fee=ba_fee,
        mt5_fee=mt5_fee,
    )


def trade_record_to_payload(record: TradeRecord) -> dict:
    return {
        "settled_at": record.settled_at,
        "preset_id": record.preset_id,
        "mode": record.mode,
        "action": record.action,
        "spread": record.spread,
        "ba_price": record.ba_price,
        "ex_price": record.ex_price,
        "ba_quantity": record.ba_quantity,
        "mt5_quantity": record.mt5_quantity,
        "ba_side": record.ba_side,
        "mt5_side": record.mt5_side,
        "direction": record.direction,
        "ba_pnl": record.ba_pnl,
        "mt5_pnl": record.mt5_pnl,
        "ba_fee": record.ba_fee,
        "mt5_fee": record.mt5_fee,
        "net_pnl": record.net_pnl,
    }
