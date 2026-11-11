"""Shared database helpers for the Streamlit pages."""

import os

import pandas as pd
import streamlit as st
from sqlalchemy import create_engine, text


@st.cache_resource
def get_engine():
    url = os.environ.get("DATABASE_URL", "postgresql://hmda:hmda@localhost:5432/hmda").replace("postgresql://", "postgresql+psycopg://", 1)
    return create_engine(url)


@st.cache_data(ttl=600)
def query(sql: str, **params) -> pd.DataFrame:
    with get_engine().connect() as conn:
        return pd.read_sql(text(sql), conn, params=params)


def require_data() -> None:
    """Stop the page with a message if HMDA data hasn't been loaded yet."""
    if not query("SELECT to_regclass('hmda_clean') IS NOT NULL AS ok")["ok"].iloc[0]:
        st.warning(
            "No HMDA data loaded yet. Put a CSV in the `data/` folder and run:\n\n"
            "`python scripts/load_hmda.py data/<your_file>.csv`"
        )
        st.stop()
