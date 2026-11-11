"""Vínculos manuais de conciliação (extrato x Omie) cadastrados durante as tratativas."""

from __future__ import annotations

import streamlit as st

from src.ui import components as c
from src.ui.context import flash, get_context

ctx = get_context()
c.render_page_header(
    "Vínculos manuais",
    "Pares extrato x Omie conciliados manualmente; são respeitados pelo motor nas próximas execuções.",
    ctx,
)

links = ctx.db.get_manual_links_df()
if links.empty:
    st.info(
        "Os vínculos são criados no modal de inspeção de uma exceção (página Exceções e tratativas).",
        title="Nenhum vínculo manual cadastrado",
        icon=":material/link_off:",
    )
    st.page_link(c.PG_EXCECOES, label="Ir para Exceções e tratativas", icon=":material/arrow_forward:")
    st.stop()

ext_cols = ctx.extrato_df[["id_extrato", "data_lancamento", "banco_nome", "descricao_normalizada", "valor"]].rename(
    columns={"data_lancamento": "data_extrato", "descricao_normalizada": "descricao_extrato", "valor": "valor_extrato"}
)
omie_cols = ctx.omie_df[["id_omie", "data_lancamento", "cliente_fornecedor", "valor"]].rename(
    columns={"data_lancamento": "data_omie", "valor": "valor_omie"}
)
view = links.merge(ext_cols, on="id_extrato", how="left").merge(omie_cols, on="id_omie", how="left")

with st.container(border=True):
    st.subheader(f":material/link: Vínculos ativos ({len(view)})")
    st.dataframe(
        view,
        hide_index=True,
        column_config={
            "id_extrato": st.column_config.TextColumn("ID Extrato"),
            "id_omie": st.column_config.TextColumn("ID Omie"),
            "observacao": st.column_config.TextColumn("Observação"),
            "criado_em": st.column_config.TextColumn("Criado em"),
            "data_extrato": st.column_config.TextColumn("Data Extrato"),
            "banco_nome": st.column_config.TextColumn("Banco"),
            "descricao_extrato": st.column_config.TextColumn("Descrição Extrato"),
            "valor_extrato": st.column_config.NumberColumn("Valor Extrato (R$)", format="R$ %.2f"),
            "data_omie": st.column_config.TextColumn("Data Omie"),
            "cliente_fornecedor": st.column_config.TextColumn("Cliente / Fornecedor"),
            "valor_omie": st.column_config.NumberColumn("Valor Omie (R$)", format="R$ %.2f"),
        },
        alt="Vínculos manuais de conciliação com detalhes do extrato e do Omie",
    )

    pares = list(zip(links["id_extrato"], links["id_omie"]))
    d1, d2 = st.columns([3, 1], vertical_alignment="bottom")
    with d1:
        par = st.selectbox(
            "Desfazer vínculo",
            options=pares,
            format_func=lambda p: f"Extrato {p[0]}  x  Omie {p[1]}",
            key="sel_delete_manual_link",
        )
    with d2:
        if st.button("Desfazer", key="btn_delete_manual_link", icon=":material/link_off:", width="stretch"):
            ctx.db.delete_manual_link(par[0], par[1])
            flash("info", "Vínculo desfeito", "Execute a auditoria novamente para refletir a alteração.")
            st.rerun()
