"""Contexto compartilhado entre páginas: conexão DuckDB, competência ativa e dados do escopo."""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import tempfile
from typing import Any
from uuid import uuid4

import pandas as pd
import streamlit as st

from src.database import VerticeDB
from src.services.auditoria import KpisAuditoria, calcular_kpis, carregar_divergencias

_CTX_KEY = "_vertice_ctx"
TODAS = "TODAS"
TODOS = "TODOS"


@dataclass
class AuditContext:
    """Dados do escopo ativo carregados uma única vez por execução do script."""

    db: VerticeDB
    competencia: str | None
    banco: str | None
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
        banco_desc = f" ({self.banco})" if self.banco else ""
        return (self.competencia or "Todas as competências") + banco_desc

    @property
    def regras_ativas(self) -> int:
        if self.rules_df.empty:
            return 0
        return int(self.rules_df["ativa"].astype(bool).sum())

    @property
    def tem_movimentos(self) -> bool:
        return not (self.extrato_df.empty and self.omie_df.empty)


def _should_isolate_session_db() -> bool:
    try:
        if "ISOLATE_SESSION_DB" in st.secrets:
            return bool(st.secrets["ISOLATE_SESSION_DB"])
    except Exception:
        pass
    if os.environ.get("ISOLATE_SESSION_DB", "").lower() in ("1", "true", "yes"):
        return True
    return Path("/mount/src").exists()


def get_db() -> VerticeDB:
    """Instancia o gerenciador DuckDB (isolado por sessão na nuvem ou sobrescrevível em testes)."""
    db_override = st.session_state.get("_vertice_db_path")
    rules_override = st.session_state.get("_vertice_rules_path")
    if not db_override and _should_isolate_session_db():
        session_dir = Path(tempfile.gettempdir()) / f"vertice_{uuid4().hex}"
        session_dir.mkdir(parents=True, exist_ok=True)
        db_override = str(session_dir / "vertice_auditoria.duckdb")
        rules_override = str(session_dir / "regras_customizadas.json")
        st.session_state["_vertice_db_path"] = db_override
        st.session_state["_vertice_rules_path"] = rules_override

    if db_override:
        if rules_override:
            return VerticeDB(db_path=db_override, rules_json_path=rules_override)
        return VerticeDB(db_path=db_override)
    return VerticeDB()


def init_session_state() -> None:
    defaults: dict[str, Any] = {
        "sql_draft": "ABS(e.valor - o.valor) > 10.0 AND o.id_omie IS NOT NULL",
        "flash_msg": None,
        "last_opened_row_sig": None,
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


def build_context(db: VerticeDB, competencia: str | None, banco: str | None = None) -> AuditContext:
    summary, divs = carregar_divergencias(db, competencia, banco=banco)
    ctx = AuditContext(
        db=db,
        competencia=competencia,
        banco=banco,
        extrato_df=db.get_extrato_df(competencia=competencia, banco=banco),
        omie_df=db.get_omie_df(competencia=competencia, banco=banco),
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
