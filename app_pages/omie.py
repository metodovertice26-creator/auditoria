"""Ingestão de lançamentos do ERP Omie (relatórios exportados ou API REST)."""

from __future__ import annotations

from datetime import date, timedelta

import streamlit as st

from src.services.ingestao import ORIGENS_OMIE, importar_relatorios_omie, sincronizar_omie_api
from src.ui import components as c
from src.ui.context import flash, get_context
from src.ui.ingestao_widgets import render_arquivos_locais, render_lotes, reportar_resultado

MODO_UPLOAD = "Upload de relatórios (CSV / XLSX)"
MODO_API = "Conexão API REST"

ctx = get_context()
c.render_page_header(
    "ERP Omie",
    "Movimento de Conta Corrente, Contas a Pagar/Receber ou Baixas — por planilha exportada ou API.",
    ctx,
)

with st.container(border=True):
    modo = st.segmented_control("Fonte de dados do Omie", options=[MODO_UPLOAD, MODO_API], default=MODO_UPLOAD, key="seg_omie_mode")

    if modo == MODO_API:
        st.caption("Sincronização direta com o endpoint `/financas/mf/` do ERP Omie.")
        try:
            default_key = str(st.secrets.get("OMIE_APP_KEY", ""))
            default_secret = str(st.secrets.get("OMIE_APP_SECRET", ""))
        except Exception:  # secrets.toml ausente
            default_key = default_secret = ""
        hoje = date.today()
        inicio_mes_anterior = (hoje.replace(day=1) - timedelta(days=1)).replace(day=1)
        with st.form("form_omie_api", border=False):
            api_key = st.text_input("Omie APP_KEY", value=default_key, type="password")
            api_secret = st.text_input("Omie APP_SECRET", value=default_secret, type="password")
            d1, d2 = st.columns(2)
            dt_ini = d1.date_input("Data inicial", value=inicio_mes_anterior)
            dt_fim = d2.date_input("Data final", value=hoje.replace(day=1) - timedelta(days=1))
            if st.form_submit_button("Sincronizar movimentos via API Omie", icon=":material/cloud_sync:", width="stretch"):
                try:
                    n = sincronizar_omie_api(ctx.db, api_key, api_secret, dt_ini, dt_fim)
                    flash("success", "Sincronização Omie concluída", f"{n} movimentos financeiros importados via API.")
                    st.rerun()
                except Exception as exc:
                    st.error(str(exc), title="Erro na comunicação com API Omie")
    else:
        origem_label = st.selectbox("Tipo de relatório Omie", options=list(ORIGENS_OMIE), key="sel_omie_report_type")
        arquivos = st.file_uploader(
            "Selecione relatórios exportados do Omie (.xlsx, .csv)",
            type=["xlsx", "xls", "csv"],
            accept_multiple_files=True,
            key="uploader_omie",
        )
        if st.button(
            "Importar e normalizar relatórios Omie",
            key="btn_process_omie",
            type="primary",
            icon=":material/table_view:",
            width="stretch",
            disabled=not arquivos,
        ):
            res = importar_relatorios_omie(
                ctx.db,
                [(f.name, f.getvalue()) for f in arquivos or []],
                origem_relatorio=ORIGENS_OMIE[origem_label],
            )
            reportar_resultado(res, "Relatórios Omie importados", "Falha ao importar relatório Omie")

render_arquivos_locais(ctx, "OMIE")
render_lotes(ctx, "OMIE")

if not ctx.omie_df.empty:
    st.page_link(c.PG_BASE, label="Conferir a base normalizada", icon=":material/arrow_forward:")
