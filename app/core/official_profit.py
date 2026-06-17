"""兼容层：利润计算器与上报统一使用 hedge_trade_report。"""

from app.core.hedge_trade_report import (  # noqa: F401
    FIELD_LABELS,
    FIELD_ORDER,
    HedgeTradeReport,
    HedgeTradeRow,
    OfficialProfitReport,
    OfficialProfitRow,
    OFFICIAL_FIELD_LABELS,
    OFFICIAL_FIELD_ORDER,
    build_row_from_settlement,
    fetch_hedge_trade_report,
    fetch_official_profit_report,
)
