from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class LegResult:
    platform: str
    success: bool
    message: str = ""
    order_id: str = ""
    compensated: bool = False
    compensation_message: str = ""
    needs_reconciliation: bool = False


@dataclass
class HedgeTradeResult:
    action: str
    success: bool
    legs: list[LegResult] = field(default_factory=list)
    message: str = ""

    @property
    def partial(self) -> bool:
        if not self.legs:
            return False
        ok = sum(1 for leg in self.legs if leg.success or leg.needs_reconciliation)
        return 0 < ok < len(self.legs)
