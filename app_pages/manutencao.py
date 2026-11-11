"""Manutenção da base: carga da massa de demonstração e limpeza de movimentos (ações destrutivas)."""

from __future__ import annotations

import streamlit as st

from src.services.ingestao import carregar_demonstracao
from src.ui import components as c
from src.ui.context import flash, get_context

ctx = get_context()
c.render_page_header(
    "Manutenção da base",
    "Operações que substituem ou apagam lançamentos importados. Regras, contratos e tratativas são preservados.",
    ctx,
)

st.warning(
    f"A base atual contém **{len(ctx.extrato_df)}** linhas de extrato e **{len(ctx.omie_df)}** títulos Omie "
    "(no escopo selecionado). As duas ações abaixo removem todos os lançamentos importados.",
    title="Ações irreversíveis",
    icon=":material/warning:",
)

confirmado = st.checkbox(
    "Estou ciente de que os extratos e lançamentos Omie importados serão removidos.",
    key="chk_confirm_manutencao",
)

col_demo, col_clear = st.columns(2, gap="large")

with col_demo.container(border=True):
    st.subheader(":material/science: Dados de demonstração")
    st.caption("Substitui os movimentos por extrato OFX Itaú, CSV Bradesco (com lote PIX 1:N) e relatório Omie de exemplo, e executa a auditoria.")
    if st.button(
        "Carregar dados de demonstração",
        key="btn_load_demo",
        icon=":material/science:",
        width="stretch",
        disabled=not confirmado,
    ):
        n_ext, n_omie = carregar_demonstracao(ctx.db, competencia=ctx.competencia)
        flash(
            "success",
            "Massa de demonstração carregada",
            f"{n_ext} lançamentos de extrato e {n_omie} títulos do Omie importados e auditados.",
        )
        st.switch_page(c.PG_PAINEL)

with col_clear.container(border=True):
    st.subheader(":material/delete_sweep: Limpar lançamentos")
    st.caption("Remove extratos e lançamentos Omie importados, mantendo regras customizadas, tratativas e contratos.")
    if st.button(
        "Limpar lançamentos importados",
        key="btn_clear_movements",
        icon=":material/delete_sweep:",
        width="stretch",
        disabled=not confirmado,
    ):
        ctx.db.clear_movements("ALL")
        flash("info", "Base de movimentos limpa", "Extratos e relatórios removidos. Regras, contratos e tratativas preservados.")
        st.rerun()
