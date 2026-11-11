"""Inspeção da base normalizada (datas ISO, valores padronizados e validação Pandera)."""

from __future__ import annotations

import streamlit as st

from src.ui import components as c
from src.ui.context import get_context

ctx = get_context()
c.render_page_header(
    "Base normalizada",
    "Confira os dados após limpeza de descrições, conversão de datas (AAAA-MM-DD) e validação de contrato.",
    ctx,
)

ext, omie = ctx.extrato_df, ctx.omie_df
m1, m2, m3, m4 = st.columns(4)
m1.metric("Linhas no extrato", f"{len(ext)}", border=True)
m2.metric("Bancos identificados", f"{ext['banco_nome'].nunique() if not ext.empty else 0}", border=True)
m3.metric("Créditos no extrato", c.format_brl(ext.loc[ext["valor"] > 0, "valor"].sum() if not ext.empty else 0.0), border=True)
m4.metric(
    "Lançamentos no Omie",
    f"{len(omie)}",
    delta=f"Saídas extrato: {c.format_brl(ext.loc[ext['valor'] < 0, 'valor'].sum() if not ext.empty else 0.0)}",
    delta_color="off",
    border=True,
)

tab_ext, tab_omie = st.tabs([":material/account_balance: Extrato bancário", ":material/sync_alt: Lançamentos Omie"])

with tab_ext:
    if ext.empty:
        st.info("Nenhum extrato carregado no escopo selecionado.", title="Sem extratos", icon=":material/upload:")
        st.page_link(c.PG_EXTRATOS, label="Importar extratos", icon=":material/arrow_forward:")
    else:
        st.dataframe(
            ext[
                [
                    "data_lancamento",
                    "banco_nome",
                    "conta",
                    "tipo_movimento",
                    "natureza_operacao",
                    "documento",
                    "descricao_normalizada",
                    "valor",
                    "valor_nominal",
                    "prazo_dias",
                ]
            ],
            hide_index=True,
            column_config={
                "data_lancamento": st.column_config.TextColumn("Data (ISO)"),
                "banco_nome": st.column_config.TextColumn("Banco"),
                "conta": st.column_config.TextColumn("Conta"),
                "tipo_movimento": st.column_config.TextColumn("Tipo"),
                "natureza_operacao": st.column_config.TextColumn("Natureza Identificada"),
                "documento": st.column_config.TextColumn("Documento / FITID"),
                "descricao_normalizada": st.column_config.TextColumn("Descrição Normalizada"),
                "valor": st.column_config.NumberColumn("Valor (R$)", format="R$ %.2f"),
                "valor_nominal": st.column_config.NumberColumn("Valor Nominal (R$)", format="R$ %.2f"),
                "prazo_dias": st.column_config.NumberColumn("Prazo (Dias)", format="%d"),
            },
            alt="Tabela de extratos bancários normalizados pelo ETL",
        )

with tab_omie:
    if omie.empty:
        st.info("Nenhum lançamento Omie no escopo selecionado.", title="Sem lançamentos Omie", icon=":material/table_chart:")
        st.page_link(c.PG_OMIE, label="Importar Omie", icon=":material/arrow_forward:")
    else:
        st.dataframe(
            omie[
                [
                    "id_omie",
                    "data_lancamento",
                    "banco_nome",
                    "status_titulo",
                    "categoria",
                    "grupo_contabil",
                    "cliente_fornecedor",
                    "documento",
                    "valor",
                    "valor_nominal",
                ]
            ],
            hide_index=True,
            column_config={
                "id_omie": st.column_config.TextColumn("Código Omie"),
                "data_lancamento": st.column_config.TextColumn("Data Baixa (ISO)"),
                "banco_nome": st.column_config.TextColumn("Banco"),
                "status_titulo": st.column_config.TextColumn("Status"),
                "categoria": st.column_config.TextColumn("Categoria Omie"),
                "grupo_contabil": st.column_config.TextColumn("Grupo Contábil"),
                "cliente_fornecedor": st.column_config.TextColumn("Cliente / Fornecedor"),
                "documento": st.column_config.TextColumn("Documento"),
                "valor": st.column_config.NumberColumn("Valor Movimento (R$)", format="R$ %.2f"),
                "valor_nominal": st.column_config.NumberColumn("Valor Nominal (R$)", format="R$ %.2f"),
            },
            alt="Tabela de lançamentos do ERP Omie normalizados pelo ETL",
        )
