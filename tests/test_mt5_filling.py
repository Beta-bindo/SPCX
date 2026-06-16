"""MT5 成交模式(filling mode)选择回归测试。

锁定 get_mt5_filling_mode 的位掩码语义：symbol_info.filling_mode 是「品种支持模式」
位掩码(SYMBOL_FILLING_FOK=1, SYMBOL_FILLING_IOC=2)，须映射到下单用的 ORDER_FILLING_*
枚举(FOK=0 / IOC=1 / RETURN=2)，否则会触发 retcode=10030 Unsupported filling mode。
"""

from __future__ import annotations

import sys
import types
from types import SimpleNamespace


def _install_fake_mt5() -> None:
    mod = types.ModuleType("MetaTrader5")
    # 下单填充枚举（ORDER_FILLING_*）
    mod.ORDER_FILLING_FOK = 0
    mod.ORDER_FILLING_IOC = 1
    mod.ORDER_FILLING_RETURN = 2
    # 品种支持模式位掩码（SYMBOL_FILLING_*）
    mod.SYMBOL_FILLING_FOK = 1
    mod.SYMBOL_FILLING_IOC = 2
    sys.modules["MetaTrader5"] = mod


def _filling_for(supported: int) -> int:
    _install_fake_mt5()
    from app.core.exchange_utils import get_mt5_filling_mode

    return get_mt5_filling_mode(SimpleNamespace(filling_mode=supported))


def test_filling_fok_only():
    # 仅支持 FOK（位掩码 1）→ 必须下 ORDER_FILLING_FOK(0)，而不是 IOC
    assert _filling_for(1) == 0


def test_filling_ioc_only():
    # 仅支持 IOC（位掩码 2）→ ORDER_FILLING_IOC(1)
    assert _filling_for(2) == 1


def test_filling_fok_and_ioc_prefers_fok():
    # 同时支持 FOK|IOC（位掩码 3）→ 优先 FOK(0)
    assert _filling_for(3) == 0


def test_filling_none_falls_back_to_return():
    # 都不支持 → 回退 ORDER_FILLING_RETURN(2)
    assert _filling_for(0) == 2


def main() -> int:
    tests = [
        test_filling_fok_only,
        test_filling_ioc_only,
        test_filling_fok_and_ioc_prefers_fok,
        test_filling_none_falls_back_to_return,
    ]
    for fn in tests:
        fn()
        print(f"  ok {fn.__name__}")
    print("ALL MT5 FILLING TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
