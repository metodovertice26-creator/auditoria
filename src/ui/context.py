"""Contexto compartilhado entre páginas: conexão DuckDB, competência ativa e dados do escopo."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd
import streamlit as st

from src.database import VerticeDB
from src.services.auditoria import KpisAuditoria, calcular_kpis, carregar_divergencias

_CTX_KEY = "_vertice_ctx"
TODAS = "TODAS"


@dataclass
class AuditContext:
    """Dados do escopo ativo carregados uma única vez por execução do script."""

    db: VerticeDB
    competencia: str | None
    extrato_df: pd.DataFrame
    omie_df: pd.DataFrame
    rules_df: pd.DataFrame
    latest_summary: dict[str, Any] | None
    divs_df: pd.DataFrame
    kpis: KpisAuditoria = field(init=False)

    def __post_init__(self) -> None:
        self.kpis = calcular_kpis(self.divs_df)

    @property
    def periodo_label(self) -> str:
        return self.competencia or "Todas as competências"

    @property
    def regras_ativas(self) -> int:
        if self.rules_df.empty:
            return 0
        return int(self.rules_df["ativa"].astype(bool).sum())

    @property
    def tem_movimentos(self) -> bool:
        return not (self.extrato_df.empty and self.omie_df.empty)


def get_db() -> VerticeDB:
    """Instancia o gerenciador DuckDB (caminho sobrescrevível em testes via session_state)."""
    db_override = st.session_state.get("_vertice_db_path")
    return VerticeDB(db_path=db_override) if db_override else VerticeDB()


def init_session_state() -> None:
    defaults: dict[str, Any] = {
        "sql_draft": "ABS(e.valor - o.valor) > 10.0 AND o.id_omie IS NOT NULL",
        "flash_msg": None,
        "last_opened_row_sig": None,
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


def build_context(db: VerticeDB, competencia: str | None) -> AuditContext:
    summary, divs = carregar_divergencias(db, competencia)
    ctx = AuditContext(
        db=db,
        competencia=competencia,
        extrato_df=db.get_extrato_df(competencia=competencia),
        omie_df=db.get_omie_df(competencia=competencia),
        rules_df=db.get_rules_df(),
        latest_summary=summary,
        divs_df=divs,
    )
    st.session_state[_CTX_KEY] = ctx
    return ctx


def get_context() -> AuditContext:
    """Recupera o contexto montado pelo entrypoint (app.py) na execução corrente."""
    ctx = st.session_state.get(_CTX_KEY)
    if ctx is None:
        raise RuntimeError("Contexto de auditoria não inicializado. Execute a aplicação a partir de app.py.")
    return ctx


def flash(kind: str, title: str, body: str) -> None:
    """Agenda uma mensagem para ser exibida no topo da página após o próximo rerun."""
    st.session_state.flash_msg = (kind, title, body)
