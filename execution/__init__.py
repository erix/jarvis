"""Layer 6 — Execution: IBKR order placement via ib_insync.

Imports are lazy to avoid ib_insync event-loop initialization at module load time.
"""


def __getattr__(name):
    if name == "IBKRBroker":
        from execution.broker import IBKRBroker
        return IBKRBroker
    if name == "execute_trade":
        from execution.executor import execute_trade
        return execute_trade
    if name == "OrderManager":
        from execution.order_manager import OrderManager
        return OrderManager
    raise AttributeError(f"module 'execution' has no attribute {name!r}")


__all__ = ["IBKRBroker", "execute_trade", "OrderManager"]
