"""Fechamento e saldos: conferência de movimentação diária e mensal entre Extrato e Omie."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from src.ui import components as c
from src.ui.context import get_context

ctx = get_context()
c.render_page_header(
    "Fechamento de saldos",
    "Conferência de fechamento diário e mensal: identifique em segundos qual dia ou mês apresenta divergência.",
    ctx,
)

tab_mensal, tab_diario = st.tabs([":material/calendar_month: Fechamento mensal", ":material/today: Fechamento diário"])

# =============================================================================
# FECHAMENTO MENSAL
# =============================================================================
with tab_mensal:
    with st.container(border=True):
        st.subheader(":material/date_range: Movimentação líquida por competência e banco")
        st.caption("Compara a soma das entradas, saídas e movimentação líquida total do Extrato contra o Omie por mês.")

        df_mes = ctx.db.get_fechamento_mensal_df(competencia=ctx.competencia)

        if df_mes.empty:
            st.info("Nenhuma movimentação encontrada para o período selecionado.", icon=":material/info:")
        else:
            df_mes["status"] = df_mes["diferenca"].apply(lambda d: "[OK]" if abs(d) <= 0.05 else "[DIVERGÊNCIA]")

            col1, col2, col3, col4 = st.columns(4)
            meses_analisados = len(df_mes)
            meses_ok = int((df_mes["status"] == "[OK]").sum())
            meses_quebrados = meses_analisados - meses_ok
            diff_total = float(df_mes["diferenca"].sum())

            col1.metric("Fechamentos analisados", f"{meses_analisados}", border=True)
            col2.metric("Meses conciliados [OK]", f"{meses_ok}", border=True)
            col3.metric(
                "Meses com divergência",
                f"{meses_quebrados}",
                delta="Requer atenção" if meses_quebrados > 0 else "100% conciliado",
                delta_color="inverse" if meses_quebrados > 0 else "normal",
                border=True,
            )
            col4.metric(
                "Divergência acumulada",
                c.format_brl(diff_total),
                delta="Diferença líquida total",
                delta_color="off",
                border=True,
            )

            st.dataframe(
                df_mes[
                    [
                        "status",
                        "competencia",
                        "banco_nome",
                        "entradas_extrato",
                        "entradas_omie",
                        "saidas_extrato",
                        "saidas_omie",
                        "mov_liquida_extrato",
                        "mov_liquida_omie",
                        "diferenca",
                        "qtd_extrato",
                        "qtd_omie",
                    ]
                ],
                hide_index=True,
                column_config={
                    "status": st.column_config.TextColumn("Status", pinned=True),
                    "competencia": st.column_config.TextColumn("Mês / Ano", pinned=True),
                    "banco_nome": st.column_config.TextColumn("Banco", pinned=True),
                    "entradas_extrato": st.column_config.NumberColumn("Entradas Extrato", format="R$ %.2f"),
                    "entradas_omie": st.column_config.NumberColumn("Entradas Omie", format="R$ %.2f"),
                    "saidas_extrato": st.column_config.NumberColumn("Saídas Extrato", format="R$ %.2f"),
                    "saidas_omie": st.column_config.NumberColumn("Saídas Omie", format="R$ %.2f"),
                    "mov_liquida_extrato": st.column_config.NumberColumn("Líquido Extrato", format="R$ %.2f"),
                    "mov_liquida_omie": st.column_config.NumberColumn("Líquido Omie", format="R$ %.2f"),
                    "diferenca": st.column_config.NumberColumn("Diferença (R$)", format="R$ %.2f"),
                    "qtd_extrato": st.column_config.NumberColumn("Qtd Ext", format="%d"),
                    "qtd_omie": st.column_config.NumberColumn("Qtd Omie", format="%d"),
                },
                alt="Tabela de fechamento mensal consolidado entre Extrato e Omie",
            )

# =============================================================================
# FECHAMENTO DIÁRIO
# =============================================================================
with tab_diario:
    with st.container(border=True):
        st.subheader(":material/view_day: Fechamento diário de saldos e movimentações")
        st.caption("Identifique exatamente em quais dias do calendário o total de débitos e créditos divergiu.")

        bancos_disponiveis = ["TODOS"] + sorted(
            list(
                set(
                    ctx.extrato_df["banco_nome"].dropna().unique().tolist()
                    + ctx.omie_df["banco_nome"].dropna().unique().tolist()
                )
            )
        )
        col_fbanco, col_fstatus = st.columns([2, 1])
        banco_sel = col_fbanco.selectbox("Filtrar banco", options=bancos_disponiveis, key="sel_banco_fechamento_dia")
        status_sel = col_fstatus.segmented_control(
            "Exibir",
            options=["Todos os dias", "Apenas divergentes", "Apenas [OK]"],
            default="Todos os dias",
            key="seg_status_fechamento_dia",
        )

        df_dia = ctx.db.get_fechamento_diario_df(competencia=ctx.competencia, banco=banco_sel)

        if df_dia.empty:
            st.info("Nenhuma movimentação diária para os filtros selecionados.", icon=":material/info:")
        else:
            df_dia["status"] = df_dia["diferenca"].apply(lambda d: "[OK]" if abs(d) <= 0.05 else "[DIVERGÊNCIA]")

            if status_sel == "Apenas divergentes":
                df_dia = df_dia[df_dia["status"] == "[DIVERGÊNCIA]"].reset_index(drop=True)
            elif status_sel == "Apenas [OK]":
                df_dia = df_dia[df_dia["status"] == "[OK]"].reset_index(drop=True)

            dias_totais = len(df_dia)
            dias_criticos = int((df_dia["status"] == "[DIVERGÊNCIA]").sum())

            c1, c2, c3 = st.columns(3)
            c1.metric("Dias listados", f"{dias_totais}", border=True)
            c2.metric(
                "Dias com divergência",
                f"{dias_criticos}",
                delta="Requer conciliação" if dias_criticos > 0 else "Todos os dias batem",
                delta_color="inverse" if dias_criticos > 0 else "normal",
                border=True,
            )
            c3.metric(
                "Divergência diária acumulada",
                c.format_brl(df_dia["diferenca"].sum() if not df_dia.empty else 0.0),
                delta="Soma das diferenças",
                delta_color="off",
                border=True,
            )

            st.dataframe(
                df_dia[
                    [
                        "status",
                        "data_lancamento",
                        "banco_nome",
                        "entradas_extrato",
                        "entradas_omie",
                        "saidas_extrato",
                        "saidas_omie",
                        "mov_liquida_extrato",
                        "mov_liquida_omie",
                        "diferenca",
                        "qtd_extrato",
                        "qtd_omie",
                    ]
                ],
                hide_index=True,
                column_config={
                    "status": st.column_config.TextColumn("Status", pinned=True),
                    "data_lancamento": st.column_config.TextColumn("Data", pinned=True),
                    "banco_nome": st.column_config.TextColumn("Banco", pinned=True),
                    "entradas_extrato": st.column_config.NumberColumn("Entradas Extrato", format="R$ %.2f"),
                    "entradas_omie": st.column_config.NumberColumn("Entradas Omie", format="R$ %.2f"),
                    "saidas_extrato": st.column_config.NumberColumn("Saídas Extrato", format="R$ %.2f"),
                    "saidas_omie": st.column_config.NumberColumn("Saídas Omie", format="R$ %.2f"),
                    "mov_liquida_extrato": st.column_config.NumberColumn("Líquido Extrato", format="R$ %.2f"),
                    "mov_liquida_omie": st.column_config.NumberColumn("Líquido Omie", format="R$ %.2f"),
                    "diferenca": st.column_config.NumberColumn("Diferença (R$)", format="R$ %.2f"),
                    "qtd_extrato": st.column_config.NumberColumn("Qtd Ext", format="%d"),
                    "qtd_omie": st.column_config.NumberColumn("Qtd Omie", format="%d"),
                },
                alt="Tabela de fechamento diário cruzando Extrato e Omie",
            )
