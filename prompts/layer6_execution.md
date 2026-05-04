# JARVIS — Layer 6 Prompt

Build Layer 6 of the JARVIS long/short equity hedge fund system. Layers 1-5 are complete.

This layer is the **Execution** — connects to IBKR Gateway and places orders.

## IBKR Connection Config (from user's existing setup)
- Host: 192.168.11.202
- Port: 4001 (paper) / 4002 (live)
- Protocol: `ib_insync` (used in existing codebase)
- The user's existing wrapper at `/home/erix/agents/trader/trading/ib-options.py` shows the connection pattern

## Files to Create

```
execution/
├── broker.py             # IB_insync connection
├── executor.py           # Order submission + tracking
├── costs.py             # Slippage tracking
├── short_check.py       # HTB/ETB availability
├── order_manager.py     # Order state management
└── __init__.py
run_execution.py
```

---

## 1. Broker Connection (execution/broker.py)

```python
from ib_insync import IB, Stock, LimitOrder, MarketOrder

class IBKRBroker:
    def __init__(self, host="192.168.11.202", port=4001, client_id=1):
        self.ib = IB()
        self.host = host
        self.port = port
        self.client_id = client_id

    def connect(self):
        # Connect with retry logic
        for attempt in range(3):
            try:
                self.ib.connect(self.host, self.port, clientId=self.client_id)
                print(f"Connected to IB Gateway {self.host}:{self.port}")
                return True
            except Exception as e:
                print(f"Connection attempt {attempt+1} failed: {e}")
                time.sleep(2 ** attempt)
        return False

    def disconnect(self):
        self.ib.disconnect()

    def is_connected(self):
        return self.ib.isConnected()
```

Key features:
- Default to **port 4001 paper trading**
- Live trading (4002): require explicit confirmation message: "You are about to connect to IBKR LIVE trading. Confirm you understand the risk."
- Exponential backoff: 3 retry attempts with 1s, 2s, 4s delays
- Sync portfolio state after connection: `ib.positions()` and `ib.accountSummary()`
- Graceful disconnect on SIGINT/KeyboardInterrupt

---

## 2. Order Executor (execution/executor.py)

Entry: `execute_trade(ticker, action, shares, signal_price, dry_run=False)`

**Before every order:** Run `pre_trade_veto` from Layer 5 (`risk/pre_trade.py`)
- If veto rejects → log rejection, don't place order
- If approved → proceed

**Order flow:**
1. Look up contract: `Stock(ticker, 'SMART', 'USD')`
2. Qualify contract via `ib.qualifyContracts(stock)`
3. For **shorts**: check `short_check.is_shortable(ticker)` first
4. Calculate limit price:
   ```python
   bid = ticker.bid
   ask = ticker.ask
   spread = ask - bid
   mid = (bid + ask) / 2
   
   if action == "buy":
       limit_price = mid + 0.01  # Slight edge on buy
   elif action == "short":
       limit_price = mid - 0.01  # Slight edge on short
   ```
5. Create order: `LimitOrder(action, abs(shares), limit_price)`
6. Place order: `trade = ib.placeOrder(contract, order)`
7. Poll for fills: every 5 seconds via `ib.sleep(5)`, check `trade.orderStatus`
8. Timeout after 5 minutes — cancel if not filled

**Logging:**
- Signal price (from Layer 4 target)
- Limit price submitted
- Fill price (avgFillPrice)
- Slippage: `(avgFillPrice - signal_price) / signal_price * 10000` bps
- Commission: from `ib.commissionReport()`
- Timestamp, orderId, permId
- Save to SQLite `orders` table

---

## 3. Slippage Tracker (execution/costs.py)

Track every fill and compute slippage metrics:

```python
def track_slippage(order_record):
    slippage_bps = (order_record["fill_price"] - order_record["signal_price"]) / order_record["signal_price"] * 10000
    # Note: for shorts, negative slippage means paid MORE to short (bad)
    return slippage_bps
```

**Metrics:**
- Average slippage (30d trailing)
- Median slippage
- p95 slippage
- Worst 5 fills (for dashboard display)
- Total slippage cost in $ (30d)

SQLite table:
```sql
CREATE TABLE orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    submitted_at TEXT,
    ticker TEXT,
    action TEXT,  -- buy/sell/short/cover
    qty REAL,
    signal_price REAL,
    limit_price REAL,
    fill_price REAL,
    slippage_bps REAL,
    commission REAL,
    status TEXT,  -- pending/partial/filled/cancelled
    order_id TEXT,
    perm_id TEXT
);
```

---

## 4. Short Availability (execution/short_check.py)

Check if stock can be shorted via IBKR:
```python
def is_shortable(ticker):
    stock = Stock(ticker, 'SMART', 'USD')
    details = ib.reqContractDetails(stock)
    if details:
        # IBKR contract details include shortable info
        # Return True if shortable and easy_to_borrow
        return getattr(details[0].contract, 'shortable', True)
    return False
```

Known issue: IBKR doesn't always expose `shortableShares` via ib_insync easily.
Fall back: try placing a small short test order and check for rejection error.

Cache results for 1 hour.
Log: "Short check for TICKER: {result}"

---

## 5. Order Manager (execution/order_manager.py)

Track order lifecycle:
- `pending` → `partial` → `filled` or `pending` → `cancelled`

**Functions:**
- `get_orders(status=None)` — list all orders, optionally filtered
- `get_open_orders()` — orders not yet filled/cancelled
- `get_filled_orders(days=30)` — filled in last N days
- `cancel_order(order_id)` — cancel pending order
- `sync_with_portfolio()` — reconcile executed fills with Layer 4 positions table

**Keyboard interrupt handling:**
```python
import signal

def signal_handler(signum, frame):
    print("\nReceived interrupt. Cancelling open orders...")
    for order in get_open_orders():
        cancel_order(order["order_id"])
    broker.disconnect()
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)
```

---

## Entry Point (run_execution.py)

```bash
python run_execution.py --dry-run       # Log what would happen, don't place orders (DEFAULT)
python run_execution.py --execute       # Actually place orders (requires explicit confirmation)
```

**CRITICAL: NO AUTO-EXECUTION**
- There is NO automatic order submission in this system.
- The ONLY way to place orders is via `run_execution.py --execute` with manual confirmation.
- The dashboard's "Execute" button does NOT auto-trigger Layer 6. It generates a trade list for human review.
- Scheduled jobs (e.g., daily automation) NEVER call `run_execution.py`. They only refresh data and scores.

**Execution flow:**
1. Connect to IBKR Gateway (`--dry-run` can skip connection or do readonly check)
2. Load pending trades from portfolio.rebalance output (or read from a JSON file generated by Layer 4)
3. For each trade:
   a. Run pre-trade veto (Layer 5)
   b. If dry-run: print what WOULD be done
   c. If execute: place order, track fill
4. Print summary:
```
Execution Summary
Mode: DRY-RUN / LIVE
Orders attempted: X
Approved: X
Rejected: X
Filled: X
Partial: X
Cancelled: X
Avg slippage: X.X bps
Total commission: $X.XX
```

**For `--execute` mode, require explicit confirmation:**
```
You are about to place X orders totaling $XX,XXX in notional value.
Paper trading? False
Confirm: y/N: 
```

---

## Implementation Notes
- MUST install `ib_insync` into the `.venv` (it's already in requirements.txt from L1)
- The `ib` object from ib_insync is event-based. Use `ib.sleep(N)` to wait.
- For portfolio sync: after fills, update `positions` table in SQLite (Layer 4)
- Masonry order: one at a time or small batches. Don't fire all orders simultaneously — rate limit.
- Handle IBKR-specific errors:
  - `Order held` = needs manual confirmation
  - `Exchange not available` = after hours
  - `Short sale slot not available` = can't short right now
- Short order action in IBKR/ib_insync: use "sell" for shorting (same as selling long, but position goes negative)
- Actually for true short positions in IBKR, you "SELL" what you don't have, resulting in a negative position
- The user's code at `/home/erix/agents/trader/trading/ib-options.py` shows: Stock(symbol, 'SMART', 'USD') and Option() — same pattern for stocks

## What This Builds
- IBKR Gateway integration via ib_insync (paper port 4001, live port 4002)
- Limit order execution with slippage tracking
- Short availability checks
- Order lifecycle management (pending → filled/cancelled)
- Graceful shutdown on interrupt
- Dry-run mode for testing
- Automatic portfolio state sync after fills
