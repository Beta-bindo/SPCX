"""对冲下单结果的数据结构（单腿结果与整笔对冲结果）。"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class LegResult:
    """单条腿（BA 或 MT5）的下单结果。"""

    platform: str                       # "BA" 或 "MT5"
    success: bool                       # 是否成交
    message: str = ""
    order_id: str = ""
    compensated: bool = False           # 失败时是否已成功回滚该腿
    compensation_message: str = ""
    needs_reconciliation: bool = False  # 状态未知、需人工/后续对账（防止漏判真实成交）
    filled_quantity: float = 0.0        # 实际成交量：BA 为合约/币数，MT5 为手数
    filled_price: float = 0.0           # 实际成交均价；拿不到时保持 0，由调用方回退快照价
    fee: float = 0.0                    # 实际交易费用成本；负数表示返还/正向库存费
    fee_known: bool = False             # fee 是否来自交易所/MT5 历史，而非本地估算
    realized_pnl: float = 0.0           # 平仓成交的官方已实现盈亏；开仓通常为 0
    pnl_known: bool = False             # realized_pnl 是否来自平台成交/历史明细


@dataclass
class HedgeTradeResult:
    """一笔对冲交易（两腿）的整体结果。"""

    action: str   # "open" 或 "close"
    success: bool  # 两腿是否全部成功
    legs: list[LegResult] = field(default_factory=list)
    message: str = ""

    @property
    def partial(self) -> bool:
        """是否部分成交（一腿成/需对账、另一腿未成）——存在单边敞口风险。"""
        if not self.legs:
            return False
        if all(leg.success for leg in self.legs):
            return False
        return any(
            leg.success or leg.needs_reconciliation or leg.filled_quantity > 0
            for leg in self.legs
        )
