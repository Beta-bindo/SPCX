"""本地成交/结算流水：持久化到 JSON，供收益统计与导出使用。"""


from __future__ import annotations
import json
import threading
from dataclasses import asdict, dataclass, field
from datetime import date, datetime

from app.core.paths import ledger_path

_ledger_lock = threading.Lock()  # 串行化"读-改-写"流水文件，避免并发覆盖


def hedge_sides(mode: str) -> tuple[str, str]:
    """对冲模式 → (BA 方向, MT5 方向)。收缩=BA 空/Ex 多；扩张=BA 多/Ex 空。"""
    if mode == "expansion":
        return "BUY", "SELL"
    return "SELL", "BUY"


@dataclass
class TradeRecord:
    """一条成交/结算记录。"""

    settled_at: str   # ISO 时间戳
    preset_id: str
    mode: str
    action: str = "close"  # open / close
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
    ba_funding_fee: float = 0.0  # BA 资金费（币安 FUNDING_FEE，负=支出，正=收入）
    ba_rebate: float = 0.0       # BA 返佣（币安 COMMISSION_REBATE 等，正=收入）
    ba_pnl_includes_fee: bool = False   # BA 盈亏若来自余额差，则已含该端平仓手续费
    mt5_pnl_includes_fee: bool = False  # MT5 盈亏若来自余额差，则已含该端平仓手续费

    @property
    def gross_pnl(self) -> float:
        """毛利 = 两端盈亏之和。"""
        return round(self.ba_pnl + self.mt5_pnl, 2)

    @property
    def total_fees(self) -> float:
        """交易手续费合计（不含资金费/返佣）。"""
        return round(self.ba_fee + self.mt5_fee, 4)

    @property
    def net_pnl(self) -> float:
        """净利 = 毛利 − 手续费 + 资金费 + 返佣。"""
        ba_fee = 0.0 if self.ba_pnl_includes_fee else self.ba_fee
        mt5_fee = 0.0 if self.mt5_pnl_includes_fee else self.mt5_fee
        return round(
            self.gross_pnl - ba_fee - mt5_fee + self.ba_funding_fee + self.ba_rebate,
            2,
        )

    @property
    def direction(self) -> str:
        """方向文本；缺字段时按对冲模式推断。"""
        if self.ba_side and self.mt5_side:
            return f"BA {self.ba_side} / Ex {self.mt5_side}"
        ba_side, mt5_side = hedge_sides(self.mode)
        return f"BA {ba_side} / Ex {mt5_side}"


@dataclass
class TradeLedger:
    """成交记录集合，提供追加与按日期/品种筛选。"""

    records: list[TradeRecord] = field(default_factory=list)

    def add(self, record: TradeRecord) -> None:
        """追加一条并立即落盘。"""
        self.records.append(record)
        save_ledger(self)

    def filter(
        self,
        start: date,
        end: date | None = None,
        preset_id: str | None = None,
    ) -> list[TradeRecord]:
        """按 [start, end] 日期区间与可选品种筛选记录。"""
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


def funding_period_start(preset_id: str, mode: str) -> datetime | None:
    """本次应对账的 BA 资金费起始时刻：最近一次同品种同模式的平仓或开仓。"""
    ledger = load_ledger()
    for rec in reversed(ledger.records):
        if rec.preset_id != preset_id or rec.mode != mode:
            continue
        if rec.action in ("close", "open"):
            try:
                return datetime.fromisoformat(rec.settled_at)
            except ValueError:
                return None
    return None


def load_ledger() -> TradeLedger:
    """从磁盘读取流水；文件缺失或损坏时返回空账本。"""
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
    """将整本流水写回磁盘。"""
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
    ba_funding_fee: float = 0.0,
    ba_rebate: float = 0.0,
    ba_pnl_includes_fee: bool = False,
    mt5_pnl_includes_fee: bool = False,
) -> TradeRecord:
    """构造一条成交记录、加锁追加落盘并返回（统一四舍五入）。"""
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
        ba_funding_fee=round(ba_funding_fee, 4),
        ba_rebate=round(ba_rebate, 4),
        ba_pnl_includes_fee=ba_pnl_includes_fee,
        mt5_pnl_includes_fee=mt5_pnl_includes_fee,
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
    ba_funding_fee: float = 0.0,
    ba_rebate: float = 0.0,
    *,
    spread: float = 0.0,
    ba_price: float = 0.0,
    ex_price: float = 0.0,
    ba_quantity: float = 0.0,
    mt5_quantity: float = 0.0,
    ba_side: str = "",
    mt5_side: str = "",
    ba_pnl_includes_fee: bool = False,
    mt5_pnl_includes_fee: bool = False,
) -> TradeRecord:
    """记录一次平仓结算（record_trade 的 action="close" 便捷封装）。"""
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
        ba_funding_fee=ba_funding_fee,
        ba_rebate=ba_rebate,
        ba_pnl_includes_fee=ba_pnl_includes_fee,
        mt5_pnl_includes_fee=mt5_pnl_includes_fee,
    )


def trade_record_to_payload(record: TradeRecord) -> dict:
    """把记录展开为含派生字段（direction/net_pnl）的字典，便于上报/导出。"""
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
        "ba_funding_fee": record.ba_funding_fee,
        "ba_rebate": record.ba_rebate,
        "ba_pnl_includes_fee": record.ba_pnl_includes_fee,
        "mt5_pnl_includes_fee": record.mt5_pnl_includes_fee,
        "net_pnl": record.net_pnl,
    }
