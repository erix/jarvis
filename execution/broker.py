"""IBKR Gateway connection via ib_insync."""
import logging
import signal
import sys
import time

from ib_insync import IB

logger = logging.getLogger(__name__)

PAPER_PORT = 4001
LIVE_PORT = 4002
IBKR_HOST = "192.168.11.202"


class IBKRBroker:
    def __init__(self, host: str = IBKR_HOST, port: int = PAPER_PORT, client_id: int = 1):
        self.ib = IB()
        self.host = host
        self.port = port
        self.client_id = client_id
        self._live = port == LIVE_PORT

        if self._live:
            # Extra safety: require explicit confirmation before connecting to live
            print("WARNING: You are about to connect to IBKR LIVE trading.")
            print("Confirm you understand the risk. Type exactly: I UNDERSTAND")
            answer = input("> ").strip()
            if answer != "I UNDERSTAND":
                raise RuntimeError("Live trading confirmation not given — aborting.")

        # Graceful shutdown on Ctrl-C
        signal.signal(signal.SIGINT, self._sigint_handler)
        signal.signal(signal.SIGTERM, self._sigint_handler)

    def connect(self) -> bool:
        for attempt in range(3):
            try:
                self.ib.connect(self.host, self.port, clientId=self.client_id, readonly=False)
                logger.info("Connected to IB Gateway %s:%d (live=%s)", self.host, self.port, self._live)
                return True
            except Exception as exc:
                delay = 2 ** attempt
                logger.warning("Connection attempt %d/3 failed: %s — retrying in %ds", attempt + 1, exc, delay)
                time.sleep(delay)
        logger.error("All connection attempts failed")
        return False

    def disconnect(self) -> None:
        if self.ib.isConnected():
            self.ib.disconnect()
            logger.info("Disconnected from IB Gateway")

    def is_connected(self) -> bool:
        return self.ib.isConnected()

    def sync_portfolio_state(self) -> dict:
        """Pull current positions and account summary from IBKR."""
        if not self.is_connected():
            return {"positions": [], "account": {}}

        positions = self.ib.positions()
        account = {item.tag: item.value for item in self.ib.accountSummary()}
        logger.info("Synced %d positions from IBKR", len(positions))
        return {
            "positions": [
                {
                    "account": p.account,
                    "ticker": p.contract.symbol,
                    "shares": p.position,
                    "avg_cost": p.avgCost,
                }
                for p in positions
            ],
            "account": account,
        }

    def _sigint_handler(self, signum, frame):
        logger.warning("Received signal %d — disconnecting", signum)
        self.disconnect()
        sys.exit(0)
