"""Utilitários compartilhados de interface de usuário e estado (Streamlit)."""

from __future__ import annotations

import json
from datetime import date
from typing import Any

import pandas as pd
import streamlit as st

from src.database import VerticeDB


def get_db() -> VerticeDB:
    """Instancia ou recupera o gerenciador do banco de dados (DuckDB)."""
    db_override = st.session_state.get("_vertice_db_path")
    if db_override:
        return VerticeDB(db_path=db_override)
    return VerticeDB()


def init_session_state() -> None:
    """Inicializa variáveis de estado essenciais para a sessão do usuário."""
    if "sql_draft" not in st.session_state:
        st.session_state.sql_draft = "ABS(e.valor - o.valor) > 10.0 AND o.id_omie IS NOT NULL"
    if "flash_msg" not in st.session_state:
        st.session_state.flash_msg = None
    if "last_opened_row_sig" not in st.session_state:
        st.session_state.last_opened_row_sig = None


def apply_brand_theme() -> None:
    """Aplica os estilos globais da marca Vértice (CSS customizado)."""
    st.html(
        """
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
            .vertice-stepper {
                display: grid;
                grid-template-columns: repeat(4, minmax(0, 1fr));
                gap: 0.65rem;
                margin: 0.25rem 0 1.1rem 0;
            }
            .vertice-step {
                background: #FFFFFF;
                border: 1px solid #E4E7EC;
                border-left: 3px solid #D0D5DD;
                border-radius: 8px;
                padding: 0.55rem 0.8rem;
                font-size: 0.82rem;
            }
            .vertice-step.done {
                border-left-color: #3B6E52;
            }
            .vertice-step.warn {
                border-left-color: #C5A059;
            }
            .vertice-step-label {
                font-weight: 600;
                color: #2C3531;
                display: block;
            }
            .vertice-step-meta {
                color: #667085;
                font-size: 0.76rem;
            }
        </style>
        """
    )


def render_flash_messages() -> None:
    """Renderiza mensagens de alerta e limpa o estado."""
    if st.session_state.get("flash_msg"):
        kind, title_txt, body_txt = st.session_state.flash_msg
        st.session_state.flash_msg = None
        if kind == "success":
            st.success(body_txt, title=title_txt, icon=":material/check_circle:")
        elif kind == "warning":
            st.warning(body_txt, title=title_txt, icon=":material/warning:")
        else:
            st.info(body_txt, title=title_txt, icon=":material/info:")


def format_brl(value: float | int | None) -> str:
    if value is None or pd.isna(value):
        return "R$ 0,00"
    num = float(value)
    sign = "-" if num < 0 else ""
    formatted = f"{abs(num):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{sign}R$ {formatted}"


def format_pct(value: float | int | None) -> str:
    if value is None or pd.isna(value):
        return "0,00%"
    return f"{float(value):.2f}%".replace(".", ",")


def enrich_divergences_with_workflow(div_df: pd.DataFrame, wf_map: dict[str, dict[str, str]]) -> pd.DataFrame:
    """Aplica o status de tratativa salvo nas divergências carregadas do banco."""
    if div_df.empty:
        out = div_df.copy()
        out["assinatura_ocorrencia"] = pd.Series(dtype="str")
        out["status_tratativa"] = pd.Series(dtype="str")
        out["justificativa"] = pd.Series(dtype="str")
        return out
    out = div_df.copy()
    sigs: list[str] = []
    statuses: list[str] = []
    notes: list[str] = []
    for _, row in out.iterrows():
        sig = None
        try:
            sig = json.loads(row["detalhe_json"]).get("assinatura_ocorrencia")
        except Exception:
            pass
        if not sig:
            sig = VerticeDB.build_issue_signature(
                str(row["codigo_regra"]),
                str(row["data_ocorrencia"]),
                str(row["banco"]),
                row["id_extrato"] if pd.notna(row["id_extrato"]) else None,
                row["id_omie"] if pd.notna(row["id_omie"]) else None,
                str(row["resumo"]),
            )
        wf = wf_map.get(sig, {})
        sigs.append(sig)
        statuses.append(wf.get("status_tratativa", "Pendente"))
        notes.append(wf.get("justificativa", ""))
    out["assinatura_ocorrencia"] = sigs
    out["status_tratativa"] = statuses
    out["justificativa"] = notes
    return out


def render_page_header(title: str, subtitle: str, period: str | None = None) -> None:
    """Renderiza o cabeçalho padrão de página com título Cinzel e escopo."""
    scope_text = f"Escopo: {'Todas as competências' if not period or period == 'TODAS' else period}"
    st.html(
        f"""
        <div class="vertice-header-wrap">
            <div>
                <div class="vertice-cinzel-title">{title}</div>
                <p class="vertice-subtitle">{subtitle}</p>
            </div>
            <span class="vertice-pill-gold">{scope_text}</span>
        </div>
        """
    )
    render_flash_messages()
