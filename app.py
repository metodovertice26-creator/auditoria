"""Auditoria Vértice — Plataforma de Conciliação e Auditoria Financeira (Extratos x ERP Omie).

Entrypoint: configura a página, monta o contexto compartilhado (competência e dados do escopo),
desenha a barra lateral com a navegação agrupada e delega a renderização para app_pages/.
"""

from __future__ import annotations

import streamlit as st

from src.ui import components as c
from src.ui.context import TODAS, build_context, get_db, init_session_state
from src.ui.theme import apply_brand_theme

st.set_page_config(
    page_title="Auditoria Vértice",
    page_icon=":material/account_balance:",
    layout="wide",
    initial_sidebar_state="expanded",
)
apply_brand_theme()
init_session_state()

NAV_GROUPS: dict[str, list[st.Page]] = {
    "Início": [
        st.Page(c.PG_PAINEL, title="Painel executivo", icon=":material/space_dashboard:", default=True),
    ],
    "1. Dados": [
        st.Page(c.PG_EXTRATOS, title="Extratos bancários", icon=":material/account_balance:"),
        st.Page(c.PG_OMIE, title="ERP Omie", icon=":material/sync_alt:"),
        st.Page(c.PG_BASE, title="Base normalizada", icon=":material/dataset:"),
    ],
    "2. Auditoria": [
        st.Page(c.PG_EXCECOES, title="Exceções e tratativas", icon=":material/fact_check:"),
        st.Page(c.PG_FECHAMENTO, title="Fechamento de saldos", icon=":material/balance:"),
        st.Page(c.PG_VINCULOS, title="Vínculos manuais", icon=":material/link:"),
    ],
    "3. Configuração": [
        st.Page(c.PG_REGRAS, title="Regras de auditoria", icon=":material/rule_settings:"),
        st.Page(c.PG_BANCOS, title="Tarifas e taxas bancárias", icon=":material/assured_workload:"),
        st.Page(c.PG_CONTRATOS, title="Contratos de empréstimo", icon=":material/request_quote:"),
        st.Page(c.PG_MANUTENCAO, title="Manutenção da base", icon=":material/build:"),
    ],
}

# Menu desenhado manualmente para manter a marca e a competência acima da navegação
pg = st.navigation(NAV_GROUPS, position="hidden")

db = get_db()
periodos = [TODAS] + db.get_available_periods()

with st.sidebar:
    st.html(
        '<span class="vertice-pill-gold">Governança Financeira</span>'
        '<div class="vertice-cinzel-sidebar">Auditoria Vértice</div>'
    )
    selected = st.selectbox(
        "Competência em análise",
        options=periodos,
        format_func=lambda p: "Todas as competências" if p == TODAS else f"Competência {p}",
        key="sel_global_period",
    )

ctx = build_context(db, None if selected == TODAS else selected)

with st.sidebar:
    for group, pages in NAV_GROUPS.items():
        st.html(f'<div class="vertice-nav-group">{group}</div>')
        for page in pages:
            st.page_link(page)

    st.space("small")
    with st.container(border=True):
        st.markdown("**Resumo do escopo**")
        st.caption(f":material/receipt_long: Extratos: **{len(ctx.extrato_df)}** linhas")
        st.caption(f":material/dns: Títulos Omie: **{len(ctx.omie_df)}** registros")
        st.caption(f":material/gavel: Regras ativas: **{ctx.regras_ativas}** de {len(ctx.rules_df)}")
        if ctx.latest_summary:
            st.caption(f":material/pending_actions: Exceções em aberto: **{ctx.kpis.pendentes}**")
            st.caption(f":material/schedule: Última execução: **{ctx.latest_summary['executado_em']}**")

pg.run()
