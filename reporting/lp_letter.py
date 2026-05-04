"""Daily LP letter generator — calls Claude via OpenRouter."""
import json
import logging
import os
import sqlite3
from datetime import date, datetime

logger = logging.getLogger(__name__)

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "output")
LETTERS_DIR = os.path.join(OUTPUT_DIR, "letters")
DB_PATH = os.path.join(os.path.dirname(__file__), "..", "cache", "jarvis.db")


def _build_snapshot(db_path: str = DB_PATH) -> dict:
    if not os.path.exists(db_path):
        return {"date": date.today().isoformat(), "aum": 0, "positions": [], "trades": []}
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    positions = conn.execute(
        "SELECT ticker, shares, entry_price, current_price, pnl, pnl_pct, sector "
        "FROM positions WHERE is_active=1"
    ).fetchall()

    history = conn.execute(
        "SELECT date, total_value FROM portfolio_history ORDER BY date DESC LIMIT 5"
    ).fetchall()

    recent_orders = []
    try:
        recent_orders = conn.execute(
            "SELECT ticker, action, qty, fill_price, submitted_at "
            "FROM orders WHERE status='filled' ORDER BY submitted_at DESC LIMIT 10"
        ).fetchall()
    except Exception:
        pass

    conn.close()

    aum = sum(
        abs(p["shares"] or 0) * (p["current_price"] or p["entry_price"] or 0)
        for p in positions
    )
    top_gainers = sorted(
        [p for p in positions if (p["pnl"] or 0) > 0],
        key=lambda x: x["pnl"] or 0, reverse=True
    )[:3]
    top_losers = sorted(
        [p for p in positions if (p["pnl"] or 0) < 0],
        key=lambda x: x["pnl"] or 0
    )[:3]

    return {
        "date": date.today().isoformat(),
        "aum": round(aum, 2),
        "long_count": sum(1 for p in positions if (p["shares"] or 0) > 0),
        "short_count": sum(1 for p in positions if (p["shares"] or 0) < 0),
        "total_positions": len(positions),
        "top_contributors": [dict(p) for p in top_gainers],
        "top_detractors": [dict(p) for p in top_losers],
        "portfolio_history": [dict(h) for h in history],
        "trades_today": [dict(o) for o in recent_orders],
    }


def generate_lp_letter(
    target_date: date = None,
    force: bool = False,
    db_path: str = DB_PATH,
) -> str:
    """Generate daily LP letter. Returns path to saved markdown file.

    Skips regeneration if file already exists unless force=True.
    """
    if target_date is None:
        target_date = date.today()

    os.makedirs(LETTERS_DIR, exist_ok=True)
    date_str = target_date.isoformat()
    out_path = os.path.join(LETTERS_DIR, f"daily_{date_str}.md")

    if os.path.exists(out_path) and not force:
        logger.info("LP letter for %s already exists — skip (force=True to regenerate)", date_str)
        return out_path

    snapshot = _build_snapshot(db_path)
    snapshot_json = json.dumps(snapshot, default=str, indent=2)
    doc_id = f"MCP-IM-{target_date.strftime('%Y-%m%d')}"
    aum_str = f"${snapshot['aum']:,.0f}" if snapshot["aum"] else "$N/A"

    system_prompt = (
        "You are JARVIS, Chief Investment Analyst at Meridian Capital Partners, "
        "a long/short equity hedge fund. Write formal, direct investor communication. "
        "Be honest about losses. Never use marketing language or hollow reassurances. "
        "Institutional voice only. No bullet points — flowing prose paragraphs."
    )
    user_prompt = f"""Write the body of a daily investor letter for Meridian Capital Partners LP.

Portfolio data:
{snapshot_json}

Requirements:
- 3-4 substantive paragraphs
- Paragraph 1: Portfolio return today, key attribution
- Paragraph 2: Top contributors and detractors (use ticker names from data)
- Paragraph 3: Risk snapshot (drawdown, factor concentration if notable)
- Paragraph 4: Trades executed today + brief market context
- End with: "Respectfully, JARVIS"

Write ONLY the letter body (no letterhead, no JSON). Plain prose."""

    body = "[Letter generation requires OPENROUTER_API_KEY environment variable]"
    try:
        import openai as _oi
        api_key = os.getenv("OPENROUTER_API_KEY")
        if api_key:
            client = _oi.OpenAI(
                api_key=api_key,
                base_url="https://openrouter.ai/api/v1",
            )
            resp = client.chat.completions.create(
                model="anthropic/claude-sonnet-4-6",
                max_tokens=1200,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                extra_headers={
                    "HTTP-Referer": "https://jarvis.internal",
                    "X-Title": "JARVIS Hedge Fund",
                },
            )
            body = resp.choices[0].message.content or body
    except Exception as exc:
        logger.error("LP letter generation failed: %s", exc)
        body = f"[Letter generation failed: {exc}]"

    content = f"""# Meridian Capital Partners
Delaware Limited Partnership &nbsp;|&nbsp; Inception: January 2026
AUM: {aum_str}

**CONFIDENTIAL — LIMITED PARTNERS ONLY**
Doc ID: {doc_id} &nbsp;|&nbsp; Date: {target_date.strftime('%B %d, %Y')}

---

Dear Limited Partners,

{body}

---

*COMPLIANCE NOTICE: Past performance does not guarantee future results. This communication is intended solely for the named limited partners of Meridian Capital Partners LP and may not be reproduced or distributed without prior written consent.*
"""
    with open(out_path, "w") as f:
        f.write(content)
    logger.info("LP letter saved: %s", out_path)
    return out_path


def get_letter_content(target_date: date = None, force: bool = False) -> str:
    """Return letter markdown content, generating if not cached."""
    if target_date is None:
        target_date = date.today()
    path = generate_lp_letter(target_date, force=force)
    with open(path) as f:
        return f.read()
