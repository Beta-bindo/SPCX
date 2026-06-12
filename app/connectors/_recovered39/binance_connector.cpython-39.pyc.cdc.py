
from __future__ import annotations
import math
import random
import threading
import time
from typing import Optional
import requests
from PySide6.QtCore import QObject, QTimer, Signal
from app.core.api_client import ApiClient
from app.core.http_session import configure_requests_session, is_transient_network_error, run_with_network_retry
from app.core.ssl_certs import ensure_ca_bundle
from app.core.system_proxy import resolve_http_proxy
from app.core.exchange_utils import format_binance_price, format_binance_qty, get_binance_lot_step, get_binance_price_tick
from app.core.models import AppConfig, ConnectionState, GoldOrderMode, OrderBook, OrderBookLevel, Position, Quote, Side
from app.core.order_mode import resolve_execution_flags
from app.core.demo_market import demo_tick_time, generate_all_demo_pairs
from app.core.symbols import WATCHED_PRESETS, find_preset, resolve_symbols, watched_ba_symbols
from app.core.trade_result import LegResult
from app.core.app_log import LogLevel, hedge_action_label, hedge_mode_word, should_log, trade_leg_success_msg
# WARNING: Decompyle incomplete
