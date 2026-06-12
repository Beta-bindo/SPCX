# FAILED <module>: Parse error at or near `LOAD_CONST' instruction at offset 0

# FAILED _format_ba_connection_error: Parse error at or near `LOAD_GLOBAL' instruction at offset 0

# FAILED BinanceConnector: Deparsing stopped due to parse error
super().__init__(parent)
self.config = config
self._client = None
self._state = ConnectionState.DISCONNECTED
self._last_latency_ms = None
self._order_books = {}
self._quotes = {}
self._demo_timer = None
self._poll_thread = None
self._stop_event = threading.Event()
self._api = ApiClient()
self._demo_positions = {}
self._effective_proxy_host = None
self._effective_proxy_port = None
self._leverage_applied = {}
self._positions_cache = []
self._positions_cache_at = 0.0
self._symbol_leverage = {}
self._positions_fetch_lock = threading.Lock()
self._positions_inflight = None
self._quote_poll_count = 0
return self._order_books
return self._order_books.get(symbol, OrderBook())
return self._quotes
return self._quotes.get(symbol, Quote(symbol=symbol))
xau = find_preset("xau").symbol_ba
return self._quotes.get(xau, Quote(symbol=xau))
return self._state
return self._last_latency_ms
self._last_latency_ms = ms
self.latency_updated.emit(ms)
# FAILED update_config: Parse error at or near `LOAD_FAST' instruction at offset 0

if should_log(self.config.log_level, level):
    self.log.emit(message)
return max(100, int(round(self.config.ba_refresh_interval_sec * 1000)))
# FAILED start: Parse error at or near `LOAD_FAST' instruction at offset 0

# FAILED stop: Parse error at or near `LOAD_FAST' instruction at offset 0

if not self._client:
    return
return getattr(self._client, "session", None)
# FAILED _run_ba_api: Parse error at or near `LOAD_DEREF' instruction at offset 0

return self._api.run(fn)
return max(1.0, float(self.config.ba_maker_timeout_sec))
return max(0.5, min(1.0, self.config.ba_refresh_interval_sec))
# FAILED _try_cancel_order: Parse error at or near `LOAD_DEREF' instruction at offset 0

self._client.futures_cancel_order(symbol=symbol, orderId=(int(order_id)))
# FAILED _wait_for_limit_order: Parse error at or near `LOAD_DEREF' instruction at offset 0

# FAILED _check: Parse error at or near `LOAD_DEREF' instruction at offset 0

return 0.25
# FAILED _refresh_positions_from_api: Parse error at or near `LOAD_CONST' instruction at offset 0

self._positions_cache_at = 0.0
# FAILED _parse_live_positions: Parse error at or near `LOAD_GLOBAL' instruction at offset 0

# FAILED _fetch_live_positions: Deparsing stopped due to parse error
# FAILED _fetch: Parse error at or near `LOAD_DEREF' instruction at offset 0

# FAILED <listcomp>: Parse error at or near `BUILD_LIST_0' instruction at offset 0

{str(row.get("marginType", "") or "").lower() for row in .0 if float(row.get("positionAmt", 0) or 0) != 0}# FAILED _position_from_cache: Parse error at or near `LOAD_FAST' instruction at offset 0

# FAILED _wait_for_live_position: Parse error at or near `LOAD_GLOBAL' instruction at offset 0

# FAILED _wait_until_flat: Parse error at or near `LOAD_GLOBAL' instruction at offset 0

# FAILED _wait_until_position_at_most: Parse error at or near `LOAD_GLOBAL' instruction at offset 0

# FAILED get_positions: Parse error at or near `LOAD_FAST' instruction at offset 0

self._demo_positions = {p.symbol: p for p in positions}
{p.symbol: p for p in .0}# FAILED open_hedge_leg: Parse error at or near `LOAD_CONST' instruction at offset 0

# FAILED _open: Parse error at or near `LOAD_DEREF' instruction at offset 0

# FAILED close_hedge_leg: Parse error at or near `LOAD_GLOBAL' instruction at offset 0

return [p for p in  for p in .0 if p.symbol == symbol_ba if p.symbol == symbol_ba]
# FAILED _close: Parse error at or near `LOAD_GLOBAL' instruction at offset 0

# FAILED _apply_leverage: Parse error at or near `LOAD_FAST' instruction at offset 0

# FAILED refresh_platform_leverage: Parse error at or near `LOAD_FAST' instruction at offset 0

self._set_state(ConnectionState.SIMULATED)
self._demo_timer = QTimer(self)
self._demo_timer.timeout.connect(self._emit_demo_quotes)
self._demo_timer.start(self._ba_refresh_interval_ms())
self._emit_demo_quotes()
self._log(LogLevel.DEBUG, f"BA 模拟行情 · 黄金 + 白银 · 刷新间隔 {self.config.ba_refresh_interval_sec:.1f}s")
t = demo_tick_time(time.time(), self.config.ba_refresh_interval_sec)
self._record_latency(random.uniform(3, 12))
pairs = generate_all_demo_pairs(t)
for preset_id in WATCHED_PRESETS:
    ba, _ = pairs[preset_id]
    symbol = ba.symbol
    preset = find_preset(preset_id)
    mid = (ba.bid + ba.ask) / 2
    self._quotes[symbol] = ba
    self._order_books[symbol] = self._build_demo_book(mid, preset_id == "xau")
    self.quote_received.emit(ba)

return max(3, int(round(3.0 / max(0.3, self.config.ba_refresh_interval_sec))))
book = self._order_books.get(symbol)
if book and book.bids:
    if book.asks:
        book.bids[0] = OrderBookLevel(bid, book.bids[0].quantity)
        book.asks[0] = OrderBookLevel(ask, book.asks[0].quantity)
        book.is_simulated = False
        return
    self._order_books[symbol] = OrderBook(bids=[
     OrderBookLevel(bid, 1.0)],
      asks=[
     OrderBookLevel(ask, 1.0)],
      is_simulated=False)
# FAILED _fetch_watched_quotes: Parse error at or near `LOAD_FAST' instruction at offset 0

for symbol in watched:
    book = self._client.futures_order_book(symbol=symbol, limit=10)
    self._order_books[symbol] = OrderBook(bids=[OrderBookLevel(float(p), float(q)) for p, q in book["bids"][:10]], asks=[OrderBookLevel(float(p), float(q)) for p, q in book["asks"][:10]], is_simulated=False)
    bid = float(book["bids"][0][0])
    ask = float(book["asks"][0][0])
    self._quotes[symbol] = Quote(symbol=symbol,
      bid=bid,
      ask=ask,
      timestamp=(time.time()),
      is_simulated=False)

return [OrderBookLevel(float(p), float(q)) for (p, q) in  for (p, q) in .0]
return [OrderBookLevel(float(p), float(q)) for (p, q) in  for (p, q) in .0]
step = 0.05 if is_gold else 0.002
bids, asks = [], []
for i in range(10):
    offset = (i + 1) * step
    bids.append(OrderBookLevel(price=(mid - offset), quantity=(round(random.uniform(0.5, 8), 2))))
    asks.append(OrderBookLevel(price=(mid + offset), quantity=(round(random.uniform(0.5, 8), 2))))

return OrderBook(bids=bids, asks=asks, is_simulated=True)
client = Client((self.config.ba_api_key),
  (self.config.ba_api_secret),
  ping=False)
verify = ensure_ca_bundle()
through_proxy = bool(self.config.use_proxy)
proxies = {}
if through_proxy:
    host, port, fallback = resolve_http_proxy(self.config.proxy_host, self.config.proxy_port)
    self._effective_proxy_host = host
    self._effective_proxy_port = port
    if fallback:
        self._log(LogLevel.DEBUG, f"BA 代理 {self.config.proxy_host}:{self.config.proxy_port} 不可用，已改用 {host}:{port}")
    proxy_url = f"http://{host}:{port}"
    proxies = {'http':proxy_url,  'https':proxy_url}
else:
    self._effective_proxy_host = None
    self._effective_proxy_port = None
configure_requests_session((client.session),
  verify=verify,
  proxies=proxies,
  through_proxy=through_proxy,
  retry_on_rate_limit=False)
return client
# FAILED _poll_loop: Parse error at or near `SETUP_FINALLY' instruction at offset 0_2

return self._apply_leverage(s)
self._quote_poll_count += 1
if not self._order_books or self._quote_poll_count % depth_every == 1:
    self._fetch_watched_depths(watched)
return self._fetch_watched_quotes(watched)
self._state = state
self.state_changed.emit(state.value)
