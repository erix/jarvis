"""CSS theme and Plotly layout defaults for JARVIS dashboard."""

CSS = """
@import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500&display=swap');

html, body, .stApp {
    background-color: #0b0e17 !important;
    color: #e2e8f0;
    font-family: 'Plus Jakarta Sans', sans-serif;
}

[data-testid="stHeader"] { display: none !important; }
[data-testid="stToolbar"] { display: none !important; }
.stDeployButton { display: none !important; }
footer { display: none !important; }
[data-testid="stSidebarNav"] { display: none !important; }

/* Nav buttons */
div[data-testid="column"] button {
    background: #131827 !important;
    border: 1px solid #1e2d45 !important;
    color: #94a3b8 !important;
    border-radius: 24px !important;
    font-size: 12px !important;
    font-weight: 600 !important;
    letter-spacing: 0.5px !important;
    transition: all 0.15s !important;
}
div[data-testid="column"] button:hover {
    background: #1e2d45 !important;
    color: #e2e8f0 !important;
}
div[data-testid="column"] button[kind="primary"] {
    background: linear-gradient(135deg, #6366f1 0%, #8b5cf6 100%) !important;
    border-color: transparent !important;
    color: white !important;
}

/* Metric cards */
div[data-testid="metric-container"] {
    background: linear-gradient(135deg, #131827 0%, #1a2035 100%) !important;
    border: 1px solid #1e2d45 !important;
    border-radius: 8px !important;
    padding: 12px 16px !important;
}
div[data-testid="metric-container"] label {
    color: #94a3b8 !important;
    font-size: 11px !important;
    font-weight: 600 !important;
    letter-spacing: 1px !important;
    text-transform: uppercase !important;
}
div[data-testid="metric-container"] div[data-testid="stMetricValue"] {
    font-family: 'JetBrains Mono', monospace !important;
    color: #e2e8f0 !important;
    font-size: 22px !important;
}

/* Input fields */
.stTextInput input, .stTextArea textarea {
    background-color: #131827 !important;
    border-color: #1e2d45 !important;
    color: #e2e8f0 !important;
    border-radius: 8px !important;
}

/* Primary buttons */
.stButton > button[kind="primary"], .stButton > button {
    background: linear-gradient(135deg, #6366f1 0%, #8b5cf6 100%) !important;
    color: white !important;
    border: none !important;
    border-radius: 8px !important;
    font-weight: 600 !important;
}
.stButton > button:hover {
    background: linear-gradient(135deg, #4f46e5 0%, #7c3aed 100%) !important;
    transform: translateY(-1px);
}

/* DataFrames */
.stDataFrame, [data-testid="stDataFrame"] {
    background: #131827 !important;
    border: 1px solid #1e2d45 !important;
    border-radius: 8px !important;
}

/* Selectbox / radio */
.stRadio label, .stSelectbox label { color: #94a3b8 !important; }
.stRadio div[role="radiogroup"] label { color: #e2e8f0 !important; }

/* Expander */
.streamlit-expanderHeader {
    background: #131827 !important;
    border: 1px solid #1e2d45 !important;
    border-radius: 8px !important;
    color: #e2e8f0 !important;
}

/* Divider */
hr { border-color: #1e2d45 !important; margin: 8px 0 !important; }

/* Scrollbar */
::-webkit-scrollbar { width: 6px; height: 6px; }
::-webkit-scrollbar-track { background: #0b0e17; }
::-webkit-scrollbar-thumb { background: #1e2d45; border-radius: 3px; }

/* Custom classes */
.jarvis-hero {
    font-size: 88px;
    font-weight: 800;
    background: linear-gradient(135deg, #6366f1 0%, #8b5cf6 50%, #a78bfa 100%);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
    line-height: 0.9;
    letter-spacing: -6px;
}
.jarvis-subtitle {
    font-size: 12px;
    letter-spacing: 5px;
    color: #6366f1;
    text-transform: uppercase;
    font-weight: 600;
    margin-top: 8px;
}
.long-color { color: #10b981 !important; }
.short-color { color: #f43f5e !important; }
.accent { color: #6366f1 !important; }
.muted { color: #64748b !important; }

.badge {
    display: inline-block;
    border-radius: 12px;
    padding: 3px 10px;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.5px;
}
.badge-green { background: #064e3b; color: #10b981; }
.badge-yellow { background: #451a03; color: #f59e0b; }
.badge-red { background: #450a0a; color: #f43f5e; }
.badge-blue { background: #1e1b4b; color: #818cf8; }
.badge-live { background: #064e3b; color: #10b981; }
"""

PLOTLY_LAYOUT = dict(
    paper_bgcolor="#0b0e17",
    plot_bgcolor="#131827",
    font=dict(color="#e2e8f0", family="Plus Jakarta Sans, sans-serif", size=12),
    xaxis=dict(gridcolor="#1e2d45", linecolor="#1e2d45", tickfont=dict(family="JetBrains Mono")),
    yaxis=dict(gridcolor="#1e2d45", linecolor="#1e2d45", tickfont=dict(family="JetBrains Mono")),
    margin=dict(l=48, r=16, t=40, b=40),
    legend=dict(bgcolor="rgba(0,0,0,0)", bordercolor="#1e2d45"),
    colorway=["#6366f1", "#10b981", "#f43f5e", "#f59e0b", "#06b6d4", "#8b5cf6"],
)

COLORS = {
    "bg": "#0b0e17",
    "card": "#131827",
    "border": "#1e2d45",
    "text": "#e2e8f0",
    "muted": "#64748b",
    "accent": "#6366f1",
    "green": "#10b981",
    "red": "#f43f5e",
    "yellow": "#f59e0b",
    "cyan": "#06b6d4",
}
