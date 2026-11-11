"""Painel executivo: estado do ciclo de auditoria, execução e indicadores consolidados."""

from __future__ import annotations

import streamlit as st

from src.ui import components as c
from src.ui.context import get_context

ctx = get_context()
c.render_page_header(
    "Auditoria Vértice",
    "Conciliação e auditoria financeira automatizada — Extratos bancários (OFX/CSV) × ERP Omie",
    ctx,
)

c.render_stepper(ctx)
c.render_execucao_card(ctx)

if ctx.latest_summary is None:
    st.info(
        "Execute a auditoria acima para gerar os indicadores executivos e a matriz de divergências.",
        title="Aguardando primeira execução",
        icon=":material/analytics:",
    )
    st.stop()

k = ctx.kpis
k1, k2, k3, k4 = st.columns(4)
k1.metric(
    "Exposição divergente em aberto",
    c.format_brl(k.valor_em_aberto),
    delta=f"Total bruto: {c.format_brl(ctx.latest_summary['valor_total_divergente'])}",
    delta_color="off",
    border=True,
)
k2.metric(
    "Alertas críticos pendentes",
    f"{k.criticos_pendentes}",
    delta="Exigem tratativa" if k.criticos_pendentes > 0 else "Zero críticos pendentes",
    delta_color="inverse" if k.criticos_pendentes > 0 else "normal",
    border=True,
)
k3.metric(
    "Aderência do ERP Omie",
    c.format_pct(ctx.latest_summary["aderencia_erp_pct"]),
    delta=f"Extrato: {len(ctx.extrato_df)} | Omie: {len(ctx.omie_df)}",
    delta_color="off",
    border=True,
)
k4.metric(
    "Progresso das tratativas",
    f"{k.resolvidos}/{k.total}",
    delta=f"{k.pendentes} pendente(s)",
    delta_color="off",
    border=True,
)

divs = ctx.divs_df
if divs.empty:
    st.success(
        "Todos os lançamentos auditados no período selecionado estão aderentes às regras ativas e parâmetros contratuais.",
        title="Nenhuma divergência encontrada",
        icon=":material/verified:",
    )
    st.stop()

ch1, ch2 = st.columns(2)
with ch1.container(border=True):
    st.markdown("**Impacto financeiro por regra (R$)**")
    by_rule = divs.groupby("codigo_regra", as_index=False)["valor_divergente"].sum().sort_values("valor_divergente", ascending=False)
    st.bar_chart(
        by_rule,
        x="codigo_regra",
        y="valor_divergente",
        color="#C5A059",
        height=220,
        alt="Gráfico de barras do valor divergente acumulado por código de regra",
    )
with ch2.container(border=True):
    st.markdown("**Exposição divergente por banco (R$)**")
    by_bank = divs.groupby("banco", as_index=False)["valor_divergente"].sum().sort_values("valor_divergente", ascending=False)
    st.bar_chart(
        by_bank,
        x="banco",
        y="valor_divergente",
        color="#2C3531",
        height=220,
        alt="Gráfico de barras do valor divergente acumulado por instituição bancária",
    )

with st.container(border=True):
    h1, h2 = st.columns([3, 1], vertical_alignment="center")
    h1.markdown("**Maiores exceções críticas em aberto**")
    with h2:
        st.page_link(c.PG_EXCECOES, label="Abrir matriz completa", icon=":material/open_in_new:")
    top = (
        divs[(divs["severidade"] == "Crítico") & divs["status_tratativa"].isin(["Pendente", "Em Contestação"])]
        .nlargest(5, "valor_divergente")
    )
    if top.empty:
        st.caption("Nenhuma exceção crítica em aberto.")
    else:
        st.dataframe(
            top[["codigo_regra", "data_ocorrencia", "banco", "valor_divergente", "resumo"]],
            hide_index=True,
            column_config={
                "codigo_regra": st.column_config.TextColumn("Regra"),
                "data_ocorrencia": st.column_config.TextColumn("Data"),
                "banco": st.column_config.TextColumn("Banco"),
                "valor_divergente": st.column_config.NumberColumn("Divergência (R$)", format="R$ %.2f"),
                "resumo": st.column_config.TextColumn("Síntese"),
            },
            alt="Cinco maiores exceções críticas ainda sem tratativa",
        )
