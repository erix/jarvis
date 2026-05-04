"""Shared utilities for all factor modules."""
import sqlite3
import os
import numpy as np
import pandas as pd

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "cache", "jarvis.db")


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def sector_percentile_rank(series: pd.Series) -> pd.Series:
    """Rank values 0-100 within a group. Higher = better."""
    n = series.notna().sum()
    if n == 0:
        return series.fillna(50.0)
    ranked = series.rank(method="average", na_option="keep", pct=True) * 100
    return ranked.fillna(50.0)


def apply_sector_ranks(df: pd.DataFrame, col: str) -> pd.Series:
    """Apply sector-relative percentile rank. df must have 'sector' column."""
    return df.groupby("sector")[col].transform(sector_percentile_rank)
