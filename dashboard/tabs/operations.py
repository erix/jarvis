"""Page VIII - Operations: Safe runner controls."""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime

import streamlit as st


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DB_PATH = os.path.join(ROOT, "cache", "jarvis.db")


@dataclass(frozen=True)
class Runner:
    key: str
    label: str
    command: list[str]
    timeout_seconds: int
    help_text: str
    primary: bool = False
    clears_cache: bool = True


def _python_cmd(*args: str) -> list[str]:
    return [sys.executable, *args]


RUNNERS = [
    Runner(
        key="prices_only",
        label="Refresh Prices",
        command=_python_cmd("run_data.py", "--prices-only"),
        timeout_seconds=1800,
        help_text="Updates universe and price bars only.",
    ),
    Runner(
        key="macro",
        label="Refresh Macro",
        command=_python_cmd("run_data.py", "--macro-only"),
        timeout_seconds=600,
        help_text="Refreshes FRED rates, credit, inflation, and growth series only.",
    ),
    Runner(
        key="score_all",
        label="Run Scoring",
        command=_python_cmd("run_scoring.py"),
        timeout_seconds=1800,
        help_text="Recomputes factor and composite scores.",
    ),
    Runner(
        key="portfolio_whatif",
        label="Portfolio What-If",
        command=_python_cmd("run_portfolio.py", "--whatif", "--optimize-method", "mvo"),
        timeout_seconds=600,
        help_text="Shows proposed target and trades without saving positions.",
        primary=True,
        clears_cache=False,
    ),
    Runner(
        key="portfolio_rebalance",
        label="Save Pending Rebalance",
        command=_python_cmd("run_portfolio.py", "--rebalance", "--optimize-method", "mvo"),
        timeout_seconds=600,
        help_text="Saves pending positions and writes output/pending_trades.json.",
    ),
    Runner(
        key="risk_check",
        label="Run Risk Check",
        command=_python_cmd("run_risk_check.py"),
        timeout_seconds=600,
        help_text="Runs the full risk report and persists latest risk state.",
    ),
    Runner(
        key="reporting",
        label="Run Reporting",
        command=_python_cmd("run_reporting.py"),
        timeout_seconds=900,
        help_text="Generates attribution, tear sheet, and LP letter outputs.",
    ),
    Runner(
        key="execution_dry_run",
        label="Execution Dry-Run",
        command=_python_cmd("run_execution.py", "--dry-run"),
        timeout_seconds=600,
        help_text="Runs pre-trade veto checks without placing orders.",
    ),
]


SCHEDULED_CANDIDATES = [
    ("Nightly incremental", "python run_daily.py", "data + scoring + reporting"),
    ("Weekly reporting", "python run_reporting.py --weekly", "weekly commentary"),
    ("Deep data refresh", "python run_data.py", "full data refresh"),
]


FACTOR_INPUTS = [
    ("Prices", "daily_prices", "momentum, revisions, VIX/regime"),
    ("Fundamentals", "fundamentals", "value, quality, growth"),
    ("Short interest", "short_interest", "short-interest factor"),
    ("Macro", "macro_observations", "rates, credit, macro risk"),
    ("Insider filings", "insider_transactions", "insider factor"),
    ("13F holdings", "institutional_holdings", "institutional factor"),
]


def _display_command(command: list[str]) -> str:
    parts = ["python" if part == sys.executable else part for part in command]
    return " ".join(parts)


def _run_runner(runner: Runner) -> dict:
    started = time.time()
    try:
        result = subprocess.run(
            runner.command,
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=runner.timeout_seconds,
        )
        status = "completed" if result.returncode == 0 else "failed"
        output = (result.stdout or "") + (result.stderr or "")
        return {
            "status": status,
            "returncode": result.returncode,
            "duration": time.time() - started,
            "output": output.strip(),
            "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
    except subprocess.TimeoutExpired as exc:
        output = ((exc.stdout or "") + (exc.stderr or "")).strip()
        return {
            "status": "timeout",
            "returncode": None,
            "duration": time.time() - started,
            "output": output,
            "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }


def _table_count(table_name: str) -> int | None:
    if not os.path.exists(DB_PATH):
        return None
    try:
        conn = sqlite3.connect(DB_PATH)
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        ).fetchone()
        if not exists:
            conn.close()
            return None
        count = conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
        conn.close()
        return int(count)
    except Exception:
        return None


def _render_factor_inputs() -> None:
    import pandas as pd

    rows = []
    for label, table, scope in FACTOR_INPUTS:
        count = _table_count(table)
        rows.append({
            "Input": label,
            "Rows": "missing" if count is None else f"{count:,}",
            "Used By": scope,
        })

    st.markdown("### Factor Inputs")
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


def _render_runner(runner: Runner) -> None:
    left, right = st.columns([2, 3])
    with left:
        clicked = st.button(
            runner.label,
            key=f"ops_{runner.key}",
            type="primary" if runner.primary else "secondary",
            use_container_width=True,
            help=runner.help_text,
        )
    with right:
        st.code(_display_command(runner.command), language="bash")

    if clicked:
        with st.spinner(f"Running {runner.label}..."):
            result = _run_runner(runner)
        st.session_state[f"ops_result_{runner.key}"] = result
        if runner.clears_cache and result["status"] == "completed":
            st.cache_data.clear()

    result = st.session_state.get(f"ops_result_{runner.key}")
    if result:
        duration = f"{result['duration']:.1f}s"
        if result["status"] == "completed":
            st.success(f"Completed in {duration} at {result['started_at']}")
        elif result["status"] == "timeout":
            st.error(f"Timed out after {duration}")
        else:
            st.error(f"Exited with code {result['returncode']} after {duration}")
        if result["output"]:
            with st.expander("Output", expanded=result["status"] != "completed"):
                st.code(result["output"][-12_000:])


def render():
    _render_factor_inputs()

    st.markdown("<hr>", unsafe_allow_html=True)
    st.markdown("### Manual Runs")

    for runner in RUNNERS:
        _render_runner(runner)
        st.markdown("<hr>", unsafe_allow_html=True)

    st.markdown("### Scheduled Candidates")
    import pandas as pd

    st.dataframe(
        pd.DataFrame(
            SCHEDULED_CANDIDATES,
            columns=["Cadence", "Command", "Scope"],
        ),
        use_container_width=True,
        hide_index=True,
    )
