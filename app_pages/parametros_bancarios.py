"""Parâmetros contratuais por instituição bancária (AUD-03 tarifas de boleto / AUD-04 antecipações)."""

from __future__ import annotations

import streamlit as st

from src.ui import components as c
from src.ui.context import flash, get_context

ctx = get_context()
c.render_page_header(
    "Tarifas e taxas bancárias",
    "Valores contratados usados pelas regras AUD-03 (tarifa de boleto) e AUD-04 (antecipação de recebíveis).",
    ctx,
)

with st.container(border=True):
    st.caption("Edite as células diretamente. Use a última linha da tabela para adicionar um novo banco.")
    edited = st.data_editor(
        ctx.db.get_bank_params_df(),
        key="editor_bank_params",
        num_rows="dynamic",
        hide_index=True,
        column_config={
            "banco_nome": st.column_config.TextColumn("Instituição Bancária", required=True),
            "banco_codigo": st.column_config.TextColumn("Código COMPE", default="000"),
            "tarifa_boleto_contratada": st.column_config.NumberColumn("Tarifa Boleto Contratada (R$)", min_value=0.0, step=0.05, format="R$ %.2f"),
            "taxa_antecipacao_mensal": st.column_config.NumberColumn("Taxa Antecipação (% a.m.)", min_value=0.0, step=0.05, format="%.2f%%"),
            "modalidade_antecipacao": st.column_config.SelectboxColumn("Modalidade de Cálculo VP", options=["COMPOSTO", "SIMPLES"], required=True),
            "iof_tarifa_lote": st.column_config.NumberColumn("Tarifa/IOF Fixo por Lote (R$)", min_value=0.0, step=0.50, format="R$ %.2f"),
            "tolerancia_dias_conciliacao": st.column_config.NumberColumn("Janela Conciliação (Dias)", min_value=0, max_value=15, step=1, format="%d d"),
        },
        alt="Tabela editável de tarifas de boletos e taxas de antecipação contratadas por banco",
    )
    if st.button("Salvar parâmetros contratuais", key="btn_save_banks", type="primary", icon=":material/save:"):
        ctx.db.save_bank_params_df(edited)
        flash("success", "Parâmetros bancários salvos", "Tarifas e taxas atualizadas para as próximas auditorias.")
        st.rerun()
