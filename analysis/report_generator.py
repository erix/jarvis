"""Generate per-ticker Markdown analysis reports."""
import logging
import os
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

_OUTPUT_BASE = os.path.join(os.path.dirname(__file__), "..", "output")


def _fmt(val, fmt=".1f", default="N/A"):
    if val is None:
        return default
    try:
        return format(val, fmt)
    except Exception:
        return str(val)


def _fmt_list(items, default="None identified"):
    if not items:
        return f"- {default}"
    return "\n".join(f"- {item}" for item in items)


def generate_report(
    ticker: str,
    scores_row: dict,
    analyses: dict,
    claude_score: Optional[float],
    combined_score: Optional[float],
    signal: Optional[str],
    is_long: bool,
    output_dir: str,
) -> str:
    """Generate a Markdown report for a single ticker. Returns the file path."""
    os.makedirs(output_dir, exist_ok=True)
    filepath = os.path.join(output_dir, f"{ticker}.md")

    direction = "LONG CANDIDATE" if is_long else "SHORT CANDIDATE"
    signal_str = signal or "N/A"
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    earnings = analyses.get("earnings")
    filing = analyses.get("filing")
    risk = analyses.get("risk")
    insider = analyses.get("insider")

    lines = [
        f"# {ticker} — {direction}",
        f"*Generated: {now}*",
        f"**Signal: {signal_str}** | Combined Score: {_fmt(combined_score)} | Claude Score: {_fmt(claude_score)}",
        "",
        "---",
        "",
        "## Quantitative Scores (Layer 2)",
        "",
        f"| Factor | Score |",
        f"|--------|-------|",
        f"| Composite | {_fmt(scores_row.get('composite_score'))} |",
        f"| Momentum | {_fmt(scores_row.get('momentum_score'))} |",
        f"| Value | {_fmt(scores_row.get('value_score'))} |",
        f"| Quality | {_fmt(scores_row.get('quality_score'))} |",
        f"| Growth | {_fmt(scores_row.get('growth_score'))} |",
        f"| Revisions | {_fmt(scores_row.get('revisions_score'))} |",
        f"| Short Interest | {_fmt(scores_row.get('short_interest_score'))} |",
        f"| Insider | {_fmt(scores_row.get('insider_score'))} |",
        f"| Institutional | {_fmt(scores_row.get('institutional_score'))} |",
        "",
        f"**Sector:** {scores_row.get('sector', 'N/A')} | "
        f"**Regime:** {scores_row.get('regime', 'N/A')} | "
        f"**VIX:** {_fmt(scores_row.get('vix'), '.1f')}",
        "",
        "---",
        "",
        "## Claude AI Analysis (Layer 3)",
        "",
    ]

    # Earnings section
    if earnings:
        lines += [
            "### Earnings Call Analysis",
            "",
            f"**Summary:** {earnings.get('one_line_summary', 'N/A')}",
            "",
            f"| Metric | Value |",
            f"|--------|-------|",
            f"| Management Confidence | {_fmt(earnings.get('management_confidence'), '.1f')}/10 |",
            f"| Revenue Guidance | {earnings.get('revenue_guidance', 'N/A')} |",
            f"| Margin Outlook | {earnings.get('margin_outlook', 'N/A')} |",
            f"| Guidance Change | {earnings.get('guidance_change', 'N/A')} |",
            f"| Overall Tone | {earnings.get('overall_tone', 'N/A')} |",
            "",
            f"**Capital Allocation:** {', '.join(earnings.get('capital_allocation', [])) or 'N/A'}",
            "",
            f"**Key Risks:**",
            _fmt_list(earnings.get("key_risks")),
            "",
        ]
    else:
        lines += ["### Earnings Call Analysis", "", "*No transcript available.*", ""]

    # Filing section
    if filing:
        lines += [
            "### Financial Forensics",
            "",
            f"**Summary:** {filing.get('one_line_summary', 'N/A')}",
            "",
            f"| Metric | Score |",
            f"|--------|-------|",
            f"| Financial Health | {_fmt(filing.get('financial_health'), '.1f')}/10 |",
            f"| Earnings Quality | {_fmt(filing.get('earnings_quality'), '.1f')}/10 |",
            f"| Revenue Quality | {_fmt(filing.get('revenue_quality'), '.1f')}/10 |",
            f"| Balance Sheet Health | {_fmt(filing.get('balance_sheet_health'), '.1f')}/10 |",
            "",
            f"**Green Flags:**",
            _fmt_list(filing.get("green_flags")),
            "",
            f"**Red Flags:**",
            _fmt_list(filing.get("red_flags")),
            "",
            f"**Accruals Commentary:** {filing.get('accruals_commentary', 'N/A')}",
            "",
        ]
    else:
        lines += ["### Financial Forensics", "", "*No fundamentals data available.*", ""]

    # Risk section
    if risk:
        lines += [
            "### Risk Factor Analysis",
            "",
            f"**Summary:** {risk.get('one_line_summary', 'N/A')}",
            "",
            f"| Metric | Value |",
            f"|--------|-------|",
            f"| Risk Severity | {risk.get('risk_severity', 'N/A')} |",
            f"| Risk Trend | {risk.get('risk_trend', 'N/A')} |",
            f"| Boilerplate % | {_fmt((risk.get('boilerplate_percentage') or 0) * 100, '.0f')}% |",
            "",
            f"**New/Emerging Risks:**",
            _fmt_list(risk.get("new_risks")),
            "",
            f"**Material Risks:**",
            _fmt_list(risk.get("material_risks")),
            "",
            f"**Macro Sensitivities:** {', '.join(risk.get('macro_sensitivity', [])) or 'N/A'}",
            "",
        ]
    else:
        lines += ["### Risk Factor Analysis", "", "*No 10-K filing text available.*", ""]

    # Insider section
    if insider:
        lines += [
            "### Insider Activity",
            "",
            f"**Summary:** {insider.get('one_line_summary', 'N/A')}",
            "",
            f"| Metric | Value |",
            f"|--------|-------|",
            f"| Signal Strength | {insider.get('signal_strength', 'N/A')} |",
            f"| Confidence | {_fmt((insider.get('confidence') or 0) * 100, '.0f')}% |",
            f"| Pattern | {insider.get('pattern', 'N/A')} |",
            "",
            f"**Timing Analysis:** {insider.get('timing_analysis', 'N/A')}",
            f"**Cluster Summary:** {insider.get('cluster_summary', 'N/A')}",
            "",
        ]
    else:
        lines += ["### Insider Activity", "", "*No insider transactions in last 90 days.*", ""]

    lines += [
        "---",
        "",
        "## Score Composition",
        "",
        f"- Layer 2 Composite (60%): {_fmt(scores_row.get('composite_score'))}",
        f"- Claude Fundamental Avg (40%): {_fmt(claude_score)}",
        f"- **Combined Score: {_fmt(combined_score)}**",
        f"- **Signal: {signal_str}**",
        "",
        "*Report generated by JARVIS Layer 3 AI Analysis*",
    ]

    with open(filepath, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    return filepath


def create_output_dir(timestamp: Optional[str] = None) -> str:
    ts = timestamp or datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_dir = os.path.join(_OUTPUT_BASE, f"reports_{ts}")
    os.makedirs(output_dir, exist_ok=True)
    return output_dir
