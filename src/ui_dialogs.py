"""Modais (@st.dialog) de Inspeção Forense, Tratativa de Workflow e Dossiê Bancário."""

from __future__ import annotations

import json
from typing import Any

import pandas as pd
import streamlit as st

from src.audit.engine import executar_auditoria_completa, sugerir_candidatos_conciliacao
from src.audit.exporter import gerar_dossie_contestacao_bancaria
from src.database import VerticeDB

WORKFLOW_STATUS_OPTIONS = [
    "Pendente",
    "Em Contestação",
    "Ajustado no Omie",
    "Ignorado / Aceito",
]


def _fmt_brl(value: float | int | None) -> str:
    if value is None or pd.isna(value):
        return "R$ 0,00"
    num = float(value)
    sign = "-" if num < 0 else ""
    formatted = f"{abs(num):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{sign}R$ {formatted}"


@st.dialog("Inspeção forense e tratativa da divergência", width="large", icon=":material/troubleshoot:")
def abrir_modal_drilldown(
    sel_item: dict[str, Any],
    db: VerticeDB,
    extrato_df: pd.DataFrame,
    omie_df: pd.DataFrame,
    competencia_ativa: str | None,
) -> None:
    """Modal completo com comparativo Extrato x Omie, Workflow de Status e Vínculo Manual Assistido."""
    try:
        detalhe_obj: dict[str, Any] = json.loads(sel_item.get("detalhe_json") or "{}")
    except Exception:
        detalhe_obj = {}

    assinatura = detalhe_obj.get("assinatura_ocorrencia") or VerticeDB.build_issue_signature(
        codigo_regra=str(sel_item["codigo_regra"]),
        data_ocorrencia=str(sel_item["data_ocorrencia"]),
        banco=str(sel_item["banco"]),
        id_extrato=sel_item.get("id_extrato") if pd.notna(sel_item.get("id_extrato")) else None,
        id_omie=sel_item.get("id_omie") if pd.notna(sel_item.get("id_omie")) else None,
        resumo=str(sel_item["resumo"]),
    )

    ext_rec = detalhe_obj.get("extrato")
    omi_rec = detalhe_obj.get("omie")
    calc_rec = detalhe_obj.get("calculo") or {}

    st.info(
        str(sel_item["memoria_calculo"]),
        title=f"[{sel_item['severidade']}] {sel_item['codigo_regra']} — Impacto: {_fmt_brl(sel_item['valor_divergente'])}",
        icon=":material/calculate:",
    )

    d_tab_comp, d_tab_wf, d_tab_link = st.tabs(
        [
            ":material/compare_arrows: Comparativo Extrato × Omie",
            ":material/task_alt: Status e parecer (Workflow)",
            ":material/link: Conciliação manual assistida",
        ]
    )

    with d_tab_comp:
        c_ext, c_omi = st.columns(2, gap="medium")
        with c_ext:
            st.markdown("**Lançamento no extrato bancário**")
            if ext_rec:
                st.table(
                    {
                        "ID Extrato": str(ext_rec.get("id_extrato", "-")),
                        "Arquivo": str(ext_rec.get("arquivo_origem", "-")),
                        "Banco / Conta": f"{ext_rec.get('banco_nome', '-')} | CC {ext_rec.get('conta', '-')}",
                        "Data": str(ext_rec.get("data_lancamento", "-")),
                        "Natureza": str(ext_rec.get("natureza_operacao", "-")),
                        "Documento": str(ext_rec.get("documento", "-")),
                        "Descrição": str(ext_rec.get("descricao_original", "-")),
                        "Valor Extrato": _fmt_brl(ext_rec.get("valor")),
                        "Valor Nominal": (
                            _fmt_brl(ext_rec.get("valor_nominal"))
                            if ext_rec.get("valor_nominal") is not None and not pd.isna(ext_rec.get("valor_nominal"))
                            else "—"
                        ),
                    },
                    border="horizontal",
                    alt="Dados do lançamento bancário no modal de inspeção",
                )
            else:
                st.warning(
                    "Sem movimentação correspondente no extrato bancário (Órfão no Omie).",
                    title="Ausente no extrato",
                    icon=":material/search_off:",
                )

        with c_omi:
            st.markdown("**Lançamento no ERP Omie**")
            if omi_rec:
                st.table(
                    {
                        "Código Omie": str(omi_rec.get("id_omie", "-")),
                        "Relatório": f"{omi_rec.get('origem_relatorio', '-')} ({omi_rec.get('arquivo_origem', '-')})",
                        "Banco / Conta": f"{omi_rec.get('banco_nome', '-')} | CC {omi_rec.get('conta_corrente', '-')}",
                        "Data Baixa": f"{omi_rec.get('data_lancamento', '-')} (Venc: {omi_rec.get('data_vencimento') or '—'})",
                        "Status": str(omi_rec.get("status_titulo", "-")),
                        "Categoria": str(omi_rec.get("categoria", "-")),
                        "Grupo Contábil": str(omi_rec.get("grupo_contabil", "-")),
                        "Favorecido": str(omi_rec.get("cliente_fornecedor", "-")),
                        "Valor Omie": _fmt_brl(omi_rec.get("valor")),
                    },
                    border="horizontal",
                    alt="Dados do lançamento no ERP Omie no modal de inspeção",
                )
            else:
                st.warning(
                    "Sem baixa ou título correspondente no ERP Omie (Fantasma no Extrato).",
                    title="Ausente no Omie",
                    icon=":material/link_off:",
                )

        if calc_rec:
            with st.expander("Memória técnica estruturada (JSON)"):
                st.json(calc_rec)

    with d_tab_wf:
        wf_map = db.get_workflow_map()
        current_wf = wf_map.get(assinatura, {})
        cur_status = current_wf.get("status_tratativa", "Pendente")
        cur_idx = WORKFLOW_STATUS_OPTIONS.index(cur_status) if cur_status in WORKFLOW_STATUS_OPTIONS else 0

        with st.form(f"form_wf_{assinatura}", border=False):
            c_st, c_resp = st.columns([1.4, 1])
            with c_st:
                novo_status = st.selectbox(
                    "Status da tratativa",
                    options=WORKFLOW_STATUS_OPTIONS,
                    index=cur_idx,
                )
            with c_resp:
                responsavel = st.text_input(
                    "Responsável pela análise",
                    value=current_wf.get("responsavel", "Controladoria"),
                )
            justificativa = st.text_area(
                "Parecer / protocolo de contestação ou ajuste realizado",
                value=current_wf.get("justificativa", ""),
                placeholder="Ex.: Protocolo Itaú #884920 aberto para estorno de tarifa ou baixa ajustada no Omie.",
                height=90,
            )
            if current_wf.get("atualizado_em"):
                st.caption(f"Última atualização em: {current_wf['atualizado_em']}")

            if st.form_submit_button("Salvar tratativa", type="primary", icon=":material/save:", width="stretch"):
                db.save_issue_workflow(
                    assinatura=assinatura,
                    status_tratativa=novo_status,
                    justificativa=justificativa,
                    responsavel=responsavel,
                )
                st.session_state.flash_msg = (
                    "success",
                    "Tratativa registrada",
                    f"A exceção {sel_item['codigo_regra']} foi marcada como '{novo_status}'. O status é preservado nas próximas execuções.",
                )
                st.rerun()

    with d_tab_link:
        cod_regra = str(sel_item["codigo_regra"])
        if cod_regra == "AUD-02" and ext_rec and not omie_df.empty:
            st.caption(
                "Este lançamento está no Extrato mas não foi pareado automaticamente no Omie. Abaixo estão os lançamentos do Omie mais próximos para vínculo manual:"
            )
            sug_df = sugerir_candidatos_conciliacao(ext_rec, omie_df, is_extrato_target=True, top_n=6)
            if sug_df.empty:
                st.info("Nenhum lançamento candidato disponível no Omie.", title="Sem candidatos")
            else:
                st.dataframe(
                    sug_df[
                        [
                            "id_omie",
                            "data_lancamento",
                            "banco_nome",
                            "cliente_fornecedor",
                            "valor",
                            "dif_valor_abs",
                            "dias_distancia",
                            "similaridade",
                        ]
                    ],
                    hide_index=True,
                    column_config={
                        "id_omie": st.column_config.TextColumn("ID Omie"),
                        "data_lancamento": st.column_config.TextColumn("Data"),
                        "banco_nome": st.column_config.TextColumn("Banco"),
                        "cliente_fornecedor": st.column_config.TextColumn("Cliente / Fornecedor"),
                        "valor": st.column_config.NumberColumn("Valor Omie", format="R$ %.2f"),
                        "dif_valor_abs": st.column_config.NumberColumn("|Δ Valor|", format="R$ %.2f"),
                        "dias_distancia": st.column_config.NumberColumn("Δ Dias", format="%d d"),
                        "similaridade": st.column_config.NumberColumn("Score Texto", format="%.2f"),
                    },
                    alt="Sugestões de títulos no Omie para conciliação manual",
                )
                chosen_omie = st.selectbox(
                    "Selecione o lançamento Omie para vincular a esta transação bancária",
                    options=sug_df["id_omie"].tolist(),
                    format_func=lambda oid: (
                        f"{oid} | {sug_df[sug_df['id_omie'] == oid]['cliente_fornecedor'].iloc[0]} "
                        f"({_fmt_brl(sug_df[sug_df['id_omie'] == oid]['valor'].iloc[0])})"
                    ),
                    key=f"sel_mlink_omie_{ext_rec['id_extrato']}",
                )
                obs_link = st.text_input(
                    "Motivo do vínculo manual",
                    placeholder="Ex.: Pagamento liquidado com 4 dias de compensação / favorecido com razão social diferente",
                    key=f"obs_mlink_{ext_rec['id_extrato']}",
                )
                if st.button(
                    "Confirmar vínculo manual e reexecutar auditoria",
                    type="primary",
                    icon=":material/add_link:",
                    width="stretch",
                    key=f"btn_mlink_{ext_rec['id_extrato']}",
                ):
                    db.add_manual_link(
                        id_extrato=str(ext_rec["id_extrato"]),
                        id_omie=str(chosen_omie),
                        observacao=obs_link or "Vinculado manualmente pelo analista",
                    )
                    executar_auditoria_completa(db, competencia=competencia_ativa)
                    st.session_state.flash_msg = (
                        "success",
                        "Vínculo manual aplicado",
                        f"Transação {ext_rec['id_extrato']} vinculada ao título {chosen_omie} e auditoria atualizada.",
                    )
                    st.rerun()

        elif cod_regra == "AUD-01" and omi_rec and not extrato_df.empty:
            st.caption(
                "Este título consta baixado no Omie sem par automático no Extrato. Selecione abaixo caso corresponda a uma linha específica do extrato:"
            )
            sug_ext = sugerir_candidatos_conciliacao(omi_rec, extrato_df, is_extrato_target=False, top_n=6)
            if not sug_ext.empty:
                st.dataframe(
                    sug_ext[
                        [
                            "id_extrato",
                            "data_lancamento",
                            "banco_nome",
                            "descricao_normalizada",
                            "valor",
                            "dif_valor_abs",
                            "dias_distancia",
                        ]
                    ],
                    hide_index=True,
                    column_config={
                        "id_extrato": st.column_config.TextColumn("ID Extrato"),
                        "data_lancamento": st.column_config.TextColumn("Data"),
                        "banco_nome": st.column_config.TextColumn("Banco"),
                        "descricao_normalizada": st.column_config.TextColumn("Descrição Extrato"),
                        "valor": st.column_config.NumberColumn("Valor Extrato", format="R$ %.2f"),
                        "dif_valor_abs": st.column_config.NumberColumn("|Δ Valor|", format="R$ %.2f"),
                        "dias_distancia": st.column_config.NumberColumn("Δ Dias", format="%d d"),
                    },
                    alt="Sugestões de transações no extrato para conciliação manual",
                )
                chosen_ext = st.selectbox(
                    "Selecione a transação do extrato correspondente",
                    options=sug_ext["id_extrato"].tolist(),
                    format_func=lambda eid: (
                        f"{eid} | {sug_ext[sug_ext['id_extrato'] == eid]['descricao_normalizada'].iloc[0][:40]} "
                        f"({_fmt_brl(sug_ext[sug_ext['id_extrato'] == eid]['valor'].iloc[0])})"
                    ),
                    key=f"sel_mlink_ext_{omi_rec['id_omie']}",
                )
                if st.button(
                    "Confirmar vínculo manual e reexecutar auditoria",
                    type="primary",
                    icon=":material/add_link:",
                    width="stretch",
                    key=f"btn_mlink_o_{omi_rec['id_omie']}",
                ):
                    db.add_manual_link(
                        id_extrato=str(chosen_ext),
                        id_omie=str(omi_rec["id_omie"]),
                        observacao="Vinculado manualmente pelo analista",
                    )
                    executar_auditoria_completa(db, competencia=competencia_ativa)
                    st.session_state.flash_msg = (
                        "success",
                        "Vínculo manual aplicado",
                        f"Título {omi_rec['id_omie']} vinculado ao extrato {chosen_ext}.",
                    )
                    st.rerun()
        else:
            st.info(
                "O vínculo manual aplica-se principalmente a lançamentos Órfãos (AUD-01) e Fantasmas (AUD-02). Para divergências de tarifas, antecipação ou contratos, registre o parecer na aba 'Status e parecer'.",
                title="Lançamento já pareado",
                icon=":material/info:",
            )


@st.dialog("Dossiê formal de contestação e estorno bancário", width="large", icon=":material/gavel:")
def abrir_modal_dossie_bancario(enriched_divs_df: pd.DataFrame) -> None:
    """Gera carta formal e planilha de cobranças bancárias indevidas pronta para enviar ao gerente."""
    bancos_disp = ["Todos os bancos"] + sorted(
        enriched_divs_df[enriched_divs_df["codigo_regra"].isin(["AUD-03", "AUD-04", "AUD-05"])]["banco"]
        .dropna()
        .unique()
        .tolist()
    )
    banco_escolhido = st.selectbox(
        "Filtrar instituição bancária destinatária",
        options=bancos_disp,
        key="sel_banco_dossie_modal",
    )
    carta_md, claims_df = gerar_dossie_contestacao_bancaria(enriched_divs_df, banco_alvo=banco_escolhido)

    if claims_df.empty:
        st.info(carta_md, title="Sem itens pendentes de contestação")
        return

    total_estorno = float(claims_df["valor_divergente"].sum())
    c1, c2 = st.columns([2, 1], vertical_alignment="center")
    with c1:
        st.metric(
            "Montante apurado para solicitação de estorno",
            _fmt_brl(total_estorno),
            delta=f"{len(claims_df)} apontamento(s) fundamentado(s)",
            delta_color="off",
            border=True,
        )
    with c2:
        slug = banco_escolhido.lower().replace(" ", "_")
        st.download_button(
            "Baixar minuta (.md / texto)",
            data=carta_md.encode("utf-8"),
            file_name=f"dossie_contestacao_{slug}.md",
            mime="text/markdown",
            icon=":material/download:",
            type="primary",
            width="stretch",
        )

    with st.container(border=True, height=360):
        st.markdown(carta_md)
