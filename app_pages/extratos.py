"""Ingestão de extratos bancários (OFX / CSV / XLSX)."""

from __future__ import annotations

import streamlit as st

from src.services.ingestao import BANCOS_SUPORTADOS, importar_extratos
from src.ui import components as c
from src.ui.context import get_context
from src.ui.ingestao_widgets import render_arquivos_locais, render_lotes, reportar_resultado

AUTO = "Detectar automaticamente pelo arquivo (COMPE / ORG)"

ctx = get_context()
c.render_page_header(
    "Extratos bancários",
    "Importe extratos OFX (1.x SGML ou 2.x XML) ou planilhas CSV/XLSX de qualquer banco brasileiro.",
    ctx,
)

with st.container(border=True):
    st.subheader(":material/upload_file: Enviar extratos")
    banco = st.selectbox("Identificação da instituição bancária", options=[AUTO, *BANCOS_SUPORTADOS], key="sel_bank_override")
    arquivos = st.file_uploader(
        "Selecione um ou mais extratos (.ofx, .csv, .xlsx)",
        type=["ofx", "csv", "xlsx", "xls"],
        accept_multiple_files=True,
        key="uploader_extratos",
    )
    if st.button(
        "Processar e normalizar extratos",
        key="btn_process_extratos",
        type="primary",
        icon=":material/upload_file:",
        width="stretch",
        disabled=not arquivos,
    ):
        res = importar_extratos(
            ctx.db,
            [(f.name, f.getvalue()) for f in arquivos or []],
            bank_override=None if banco == AUTO else banco,
        )
        reportar_resultado(res, "Extratos normalizados e validados", "Falha ao ler arquivo de extrato")

render_arquivos_locais(ctx, "EXTRATO")

for gap in ctx.db.detect_statement_gaps():
    st.warning(
        f"Janela sem cobertura de **{gap['dias_faltantes']} dia(s)** ({gap['gap_inicio']} a {gap['gap_fim']}) "
        f"na conta **{gap['conta']} ({gap['banco_nome']})** entre `{gap['arquivo_anterior']}` e `{gap['arquivo_seguinte']}`.",
        title="Lacuna temporal entre extratos bancários",
        icon=":material/event_busy:",
    )

render_lotes(ctx, "EXTRATO")

if not ctx.extrato_df.empty:
    st.page_link(c.PG_BASE, label="Conferir a base normalizada", icon=":material/arrow_forward:")
