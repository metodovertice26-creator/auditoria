"""Blocos de interface compartilhados pelas páginas de ingestão (Extratos e Omie)."""

from __future__ import annotations

from typing import Literal

import pandas as pd
import streamlit as st

from src.services.ingestao import ResultadoImportacao
from src.ui.context import AuditContext, flash

TipoFonte = Literal["EXTRATO", "OMIE"]


def reportar_resultado(res: ResultadoImportacao, titulo_ok: str, titulo_erro: str) -> None:
    """Agenda mensagem de sucesso e exibe erros por arquivo; faz rerun apenas se não houver erros."""
    if res.sucesso:
        nota = (
            f" Aviso: {', '.join(res.duplicados)} já havia sido importado; registros atualizados com idempotência."
            if res.duplicados
            else ""
        )
        flash(
            "warning" if res.duplicados else "success",
            titulo_ok,
            f"{res.inseridos} lançamentos validados pelo Pandera e gravados no DuckDB.{nota}",
        )
        if not res.erros:
            st.rerun()
    for err in res.erros:
        st.error(err, title=titulo_erro, icon=":material/error:")


def render_lotes(ctx: AuditContext, tipo: TipoFonte) -> None:
    """Histórico de lotes importados (hash SHA-256) com exclusão seletiva por arquivo."""
    todos = ctx.db.get_imported_files_df()
    lotes = todos[todos["tipo_fonte"] == tipo].reset_index(drop=True) if not todos.empty else todos
    if lotes.empty:
        return
    with st.expander(f":material/inventory_2: Lotes importados ({len(lotes)})", expanded=False):
        st.dataframe(
            lotes,
            hide_index=True,
            column_config={
                "id_arquivo": None,
                "nome_arquivo": st.column_config.TextColumn("Arquivo"),
                "tipo_fonte": None,
                "banco_nome": st.column_config.TextColumn("Banco"),
                "conta": st.column_config.TextColumn("Conta"),
                "dt_inicio": st.column_config.TextColumn("Início Cobertura"),
                "dt_fim": st.column_config.TextColumn("Fim Cobertura"),
                "qtd_registros": st.column_config.NumberColumn("Registros", format="%d"),
                "soma_movimentos": st.column_config.NumberColumn("Soma Lote (R$)", format="R$ %.2f"),
                "saldo_final_ofx": st.column_config.NumberColumn("Saldo Final OFX", format="R$ %.2f"),
                "hash_curto": st.column_config.TextColumn("SHA-256"),
                "importado_em": st.column_config.TextColumn("Importado em"),
            },
            alt="Histórico de arquivos importados com hash SHA-256",
        )
        nomes = pd.Series(lotes["nome_arquivo"].values, index=lotes["id_arquivo"]).to_dict()
        d1, d2 = st.columns([3, 1], vertical_alignment="bottom")
        with d1:
            alvo = st.selectbox(
                "Excluir um arquivo enviado por engano",
                options=list(nomes),
                format_func=lambda fid: nomes[fid],
                key=f"sel_remove_file_{tipo}",
            )
        with d2:
            if st.button("Remover lote", key=f"btn_remove_file_{tipo}", icon=":material/delete:", width="stretch"):
                removidos = ctx.db.delete_imported_file(alvo)
                flash("info", "Arquivo removido", f"{removidos} registro(s) vinculados ao lote foram removidos.")
                st.rerun()
