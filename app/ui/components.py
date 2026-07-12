from __future__ import annotations

from html import escape

import streamlit as st


def kpi_card(label: str, value: str, help_text: str | None = None):

    safe_label = escape(str(label))
    safe_value = escape(str(value))
    title = escape(str(help_text or value))

    st.markdown(
        f"""
        <div class="metric-card" title="{title}">
            <div class="metric-label">{safe_label}</div>
            <div class="metric-value">{safe_value}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
