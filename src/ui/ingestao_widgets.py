"""Blocos de interface compartilhados pelas páginas de ingestão (Extratos e Omie)."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import pandas as pd
import streamlit as st

from src.database import PROJECT_ROOT
from src.services.ingestao import ResultadoImportacao, descobrir_arquivos_locais, importar_arquivo_local
from src.ui.context import AuditContext, flash

TipoFonte = Literal["EXTRATO", "OMIE"]

_DESTINOS: dict[TipoFonte, dict[str, str]] = {
    "EXTRATO": {"Extrato bancário (OFX/CSV/XLSX)": "EXTRATO"},
    "OMIE": {
        "Relatório Omie - Movimento CC / Baixas": "MOVIMENTO_CC",
        "Relatório Omie - Contas a Receber": "CONTAS_RECEBER",
        "Relatório Omie - Contas a Pagar": "CONTAS_PAGAR",
    },
}


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


def render_arquivos_locais(ctx: AuditContext, tipo: TipoFonte) -> None:
    """Importação rápida de arquivos salvos na raiz da pasta do projeto."""
    arquivos = descobrir_arquivos_locais(PROJECT_ROOT)
    if not arquivos:
        return
    destinos = _DESTINOS[tipo]
    with st.container(border=True):
        st.subheader(":material/folder_open: Arquivos detectados na pasta do projeto")
        st.caption("Arquivos `.OFX`, `.CSV` e `.XLSX` encontrados na raiz do projeto, prontos para importação.")
        c1, c2, c3 = st.columns([3, 2, 2], vertical_alignment="bottom")
        with c1:
            selecionado: Path = st.selectbox(
                "Arquivo local",
                options=arquivos,
                format_func=lambda p: f"{p.name} ({p.stat().st_size / 1024:.1f} KB)",
                key=f"sel_local_file_{tipo}",
            )
        with c2:
            destino_label = st.selectbox("Importar como", options=list(destinos), key=f"sel_local_dest_{tipo}")
        with c3:
            if st.button("Importar arquivo da pasta", key=f"btn_import_local_{tipo}", icon=":material/download_done:", width="stretch"):
                res = importar_arquivo_local(ctx.db, selecionado, destinos[destino_label])  # type: ignore[arg-type]
                reportar_resultado(res, f"'{selecionado.name}' importado", f"Erro ao importar {selecionado.name}")


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
