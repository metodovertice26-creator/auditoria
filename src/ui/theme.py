"""Identidade visual Vértice aplicada via CSS complementar ao tema nativo (.streamlit/config.toml)."""

from __future__ import annotations

import streamlit as st

_BRAND_CSS = """
<style>
    @import url('https://fonts.googleapis.com/css2?family=Cinzel:wght@500;600;700&display=swap');

    /* Cards com fundo branco puro sobre o canvas #F8F9FA */
    [data-testid="stVerticalBlockBorderWrapper"] {
        background-color: #FFFFFF;
        box-shadow: 0 1px 2px rgba(16, 24, 40, 0.03);
    }
    [data-testid="stMetric"] {
        background-color: #FFFFFF;
    }

    .vertice-header-wrap {
        display: flex;
        align-items: baseline;
        justify-content: space-between;
        flex-wrap: wrap;
        gap: 0.5rem;
        border-bottom: 1px solid #E4E7EC;
        padding-bottom: 0.75rem;
        margin-bottom: 1rem;
    }
    .vertice-cinzel-title {
        font-family: 'Cinzel', Georgia, serif;
        font-size: 22px;
        font-weight: 600;
        letter-spacing: 0.07em;
        text-transform: uppercase;
        color: #2C3531;
        margin: 0;
        line-height: 1.25;
    }
    .vertice-cinzel-sidebar {
        font-family: 'Cinzel', Georgia, serif;
        font-size: 15px;
        font-weight: 600;
        letter-spacing: 0.08em;
        text-transform: uppercase;
        color: #2C3531;
        margin: 0.45rem 0 0.15rem 0;
    }
    .vertice-subtitle {
        color: #667085;
        font-size: 0.9rem;
        margin: 0.2rem 0 0 0;
    }
    .vertice-pill-gold {
        display: inline-block;
        background: #FBF8F1;
        border: 1px solid #DEC387;
        color: #7A5E24;
        padding: 0.12rem 0.55rem;
        border-radius: 999px;
        font-size: 0.72rem;
        font-weight: 600;
        letter-spacing: 0.04em;
        text-transform: uppercase;
    }
    .vertice-nav-group {
        color: #98A2B3;
        font-size: 0.68rem;
        font-weight: 600;
        letter-spacing: 0.08em;
        text-transform: uppercase;
        margin: 0.9rem 0 0.1rem 0.15rem;
    }
</style>
"""


def apply_brand_theme() -> None:
    """Aplica os estilos globais da marca Vértice."""
    st.html(_BRAND_CSS)
