# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec — macOS / Windows"""

import platform
from pathlib import Path

block_cipher = None
project_root = Path(SPECPATH)
system = platform.system()

hiddenimports = [
    "binance",
    "binance.client",
    "binance.exceptions",
    "app",
    "app.main_window",
    "app.core.branding",
    "app.core.trading_service",
    "app.core.models",
    "app.widgets.trade_confirm_dialog",
    "app.widgets.connection_settings_dialog",
    "app.widgets.symbol_trade_panel",
    "app.widgets.symbol_ratio_fields",
    "app.widgets.spread_value_label",
    "app.widgets.symbol_alert_settings",
    "app.widgets.symbol_auto_trade_settings",
    "app.core.auto_trade",
    "app.core.config",
    "app.core.paths",
    "app.core.spread_engine",
    "app.core.trade_ledger",
    "app.core.profit_export",
    "app.core.profit_calculator",
    "app.core.liquidation",
    "app.core.xlsx_writer",
    "app.connectors.binance_connector",
    "app.connectors.mt5_connector",
    "app.core.ssl_certs",
    "certifi",
    "certifi.core",
    "app.widgets.profit_calculator_dialog",
    "app.widgets.pnl_detail_panel",
    "app.widgets.table_pagination",
]
extra_binaries = []
extra_datas = []

try:
    import certifi
    import shutil
    from PyInstaller.utils.hooks import collect_data_files

    _resource_cert = project_root / "app" / "resources" / "cacert.pem"
    if not _resource_cert.is_file() or _resource_cert.stat().st_size < 1000:
        shutil.copyfile(certifi.where(), _resource_cert)
    extra_datas += collect_data_files("certifi")
    extra_datas.append((str(_resource_cert), "app/resources"))
except Exception:
    pass

if system == "Windows":
    hiddenimports.extend(
        [
            "MetaTrader5",
            "numpy",
            "numpy._core",
            "numpy._core._multiarray_umath",
            "numpy._core.multiarray",
        ]
    )
    try:
        from PyInstaller.utils.hooks import collect_all, collect_dynamic_libs

        _mt5_datas, _mt5_binaries, _mt5_hidden = collect_all("MetaTrader5")
        extra_datas += _mt5_datas
        extra_binaries += _mt5_binaries
        hiddenimports += _mt5_hidden
        extra_binaries += collect_dynamic_libs("numpy")
    except Exception:
        pass

icon_path = project_root / "app" / "resources" / "icon.ico"
if system == "Darwin":
    icon_path = project_root / "app" / "resources" / "icon.icns"

a = Analysis(
    [str(project_root / "main.py")],
    pathex=[str(project_root)],
    binaries=extra_binaries,
    datas=[
        (str(project_root / "app" / "styles" / "dark.qss"), "app/styles"),
        (str(project_root / "app" / "styles" / "light.qss"), "app/styles"),
        (str(project_root / "app" / "styles" / "icons"), "app/styles/icons"),
        (str(project_root / "app" / "resources" / "icon.png"), "app/resources"),
        (str(project_root / "app" / "resources" / "icon.ico"), "app/resources"),
        (str(project_root / "app" / "resources" / "icon.icns"), "app/resources"),
    ]
    + extra_datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[str(project_root / "runtime" / "pyi_rth_ssl.py")],
    excludes=[
        "tests",
        "pytest",
        "_pytest",
        "test",
        "unittest",
        "pdb",
        "pydoc",
        "doctest",
        "IPython",
        "notebook",
        "matplotlib",
        "pandas",
        "scipy",
        "tkinter",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="TradeAssistant",
    icon=str(icon_path) if icon_path.is_file() else None,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=system == "Darwin",
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

if system == "Darwin":
    app = BUNDLE(
        exe,
        name="TradeAssistant.app",
        icon=str(icon_path) if icon_path.is_file() else None,
        bundle_identifier="com.tradeassistant.app",
        info_plist={
            "NSHighResolutionCapable": True,
            "CFBundleName": "交易助手",
            "CFBundleDisplayName": "交易助手",
        },
    )
