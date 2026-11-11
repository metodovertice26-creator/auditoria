"""Exceções e tratativas: matriz interativa, drill-down forense, dossiê bancário e exportação."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from src.audit.exporter import gerar_csv_auditoria, gerar_excel_auditoria
from src.ui import components as c
from src.ui.context import AuditContext, get_context
from src.ui.dialogs import WORKFLOW_STATUS_OPTIONS, abrir_modal_dossie_bancario, abrir_modal_drilldown

ctx = get_context()
c.render_page_header(
    "Exceções e tratativas",
    "Filtre, inspecione e trate cada divergência apontada pelo motor de auditoria.",
    ctx,
)
c.render_execucao_card(ctx)


def _aplicar_filtros(divs: pd.DataFrame, sevs: list[str], wf: list[str], banks: list[str], rules: list[str]) -> pd.DataFrame:
    mask = pd.Series(True, index=divs.index)
    for col, sel in (("severidade", sevs), ("status_tratativa", wf), ("banco", banks), ("codigo_regra", rules)):
        if sel:
            mask &= divs[col].isin(sel)
    return divs[mask].reset_index(drop=True)


@st.fragment
def render_matriz(ctx: AuditContext) -> None:
    """Fragmento reativo: filtra, exporta e abre os modais sem recarregar a página inteira."""
    divs = ctx.divs_df
    with st.container(border=True):
        st.subheader(":material/table_eye: Matriz interativa de exceções")

        f1, f2, f3, f4 = st.columns(4)
        sev_options = ["Crítico", "Alerta", "Informativo"]
        with f1:
            sel_sevs = st.pills("Severidade", options=sev_options, default=sev_options, selection_mode="multi", key="flt_severidade")
        with f2:
            sel_wf = st.multiselect(
                "Status da tratativa",
                options=WORKFLOW_STATUS_OPTIONS,
                default=["Pendente", "Em Contestação"],
                key="flt_workflow_status",
            )
        with f3:
            bank_opts = sorted(divs["banco"].dropna().unique().tolist())
            sel_banks = st.multiselect("Banco", options=bank_opts, default=bank_opts, key="flt_banco")
        with f4:
            rule_opts = sorted(divs["codigo_regra"].dropna().unique().tolist())
            sel_rules = st.multiselect("Regra", options=rule_opts, default=rule_opts, key="flt_regra")

        filtered = _aplicar_filtros(divs, sel_sevs or [], sel_wf, sel_banks, sel_rules)
        exec_id = ctx.latest_summary["execucao_id"]

        e1, e2, e3, e4 = st.columns([1.8, 1.15, 0.9, 0.85], vertical_alignment="center")
        with e1:
            total_filtrado = filtered["valor_divergente"].sum() if not filtered.empty else 0.0
            st.caption(
                f"Exibindo **{len(filtered)}** de **{len(divs)}** exceções ({c.format_brl(total_filtrado)}). "
                "**Clique em uma linha para abrir a inspeção e tratativa.**"
            )
        with e2:
            if st.button(
                "Dossiê de Estorno Bancário",
                key="btn_open_bank_dossier",
                icon=":material/gavel:",
                width="stretch",
                help="Gera carta formal e memória analítica de tarifas/taxas cobradas acima do contrato.",
            ):
                abrir_modal_dossie_bancario(divs)
        with e3:
            st.download_button(
                "Excel (.xlsx)",
                data=gerar_excel_auditoria(div_df=filtered, summary=ctx.latest_summary, extrato_df=ctx.extrato_df, omie_df=ctx.omie_df),
                file_name=f"auditoria_vertice_{exec_id}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                icon=":material/download:",
                width="stretch",
                key="btn_dl_xlsx",
            )
        with e4:
            st.download_button(
                "CSV (.csv)",
                data=gerar_csv_auditoria(filtered),
                file_name=f"auditoria_vertice_{exec_id}.csv",
                mime="text/csv",
                icon=":material/description:",
                width="stretch",
                key="btn_dl_csv",
            )

        event = st.dataframe(
            filtered[
                [
                    "status_tratativa",
                    "severidade",
                    "codigo_regra",
                    "nome_regra",
                    "data_ocorrencia",
                    "banco",
                    "valor_esperado",
                    "valor_realizado",
                    "valor_divergente",
                    "resumo",
                ]
            ],
            key="grid_matriz_divergencias",
            hide_index=True,
            on_select="rerun",
            selection_mode="single-row",
            column_config={
                "status_tratativa": st.column_config.TextColumn("Status", pinned=True),
                "severidade": st.column_config.TextColumn("Severidade", pinned=True),
                "codigo_regra": st.column_config.TextColumn("Regra", pinned=True),
                "nome_regra": st.column_config.TextColumn("Tipo de Divergência"),
                "data_ocorrencia": st.column_config.TextColumn("Data"),
                "banco": st.column_config.TextColumn("Banco"),
                "valor_esperado": st.column_config.NumberColumn("Esperado / ERP (R$)", format="R$ %.2f"),
                "valor_realizado": st.column_config.NumberColumn("Realizado / Banco (R$)", format="R$ %.2f"),
                "valor_divergente": st.column_config.NumberColumn("Divergência (R$)", format="R$ %.2f"),
                "resumo": st.column_config.TextColumn("Síntese Auditável"),
            },
            alt="Tabela interativa de exceções da auditoria com seleção de linha para abrir modal forense",
        )

        rows = event.selection.rows if event and event.selection else []
        if not rows or filtered.empty:
            st.session_state.last_opened_row_sig = None
            return

        sel_row = filtered.iloc[rows[0]].to_dict()
        row_sig = str(sel_row.get("assinatura_ocorrencia") or sel_row.get("id_divergencia"))
        i1, i2 = st.columns([2.6, 1.2], vertical_alignment="center")
        with i1:
            st.caption(
                f"Linha selecionada: **[{sel_row['codigo_regra']}] {sel_row['resumo']}** — "
                f"Status atual: **{sel_row['status_tratativa']}**"
            )
        with i2:
            reopen = st.button(
                "Abrir Inspeção e Tratativa",
                key="btn_reopen_drilldown",
                type="primary",
                icon=":material/open_in_new:",
                width="stretch",
            )
        if st.session_state.last_opened_row_sig != row_sig or reopen:
            st.session_state.last_opened_row_sig = row_sig
            abrir_modal_drilldown(
                sel_item=sel_row,
                db=ctx.db,
                extrato_df=ctx.extrato_df,
                omie_df=ctx.omie_df,
                competencia_ativa=ctx.competencia,
            )


if ctx.latest_summary is None:
    st.info(
        "Execute a auditoria acima para gerar a matriz de divergências.",
        title="Aguardando primeira execução",
        icon=":material/analytics:",
    )
elif ctx.divs_df.empty:
    st.success(
        "Nenhuma divergência na última execução para o escopo selecionado.",
        title="Base aderente",
        icon=":material/verified:",
    )
else:
    render_matriz(ctx)
