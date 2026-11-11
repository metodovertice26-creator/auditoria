"""Contratos de empréstimo Price / SAC auditados pela regra AUD-05."""

from __future__ import annotations

from datetime import date

import streamlit as st

from src.audit.finance_math import gerar_tabela_emprestimo
from src.ui import components as c
from src.ui.context import flash, get_context

ctx = get_context()
c.render_page_header(
    "Contratos de empréstimo",
    "Cronograma teórico Price/SAC cruzado mensalmente com as baixas do Omie (AUD-05).",
    ctx,
)

loans = ctx.db.get_loan_contracts_df()
col_list, col_form = st.columns([1.4, 1], gap="large")

with col_list.container(border=True):
    st.subheader(":material/request_quote: Contratos cadastrados")
    if loans.empty:
        st.info("Cadastre o primeiro contrato no formulário ao lado.", title="Sem contratos ativos")
    else:
        st.dataframe(
            loans,
            hide_index=True,
            column_config={
                "id_contrato": st.column_config.TextColumn("ID Contrato"),
                "nome_contrato": st.column_config.TextColumn("Descrição"),
                "banco_nome": st.column_config.TextColumn("Banco"),
                "sistema_amortizacao": st.column_config.TextColumn("Sistema"),
                "valor_principal": st.column_config.NumberColumn("Principal (R$)", format="R$ %.2f"),
                "taxa_juros_mensal": st.column_config.NumberColumn("Taxa (% a.m.)", format="%.2f%%"),
                "prazo_meses": st.column_config.NumberColumn("Prazo (Meses)", format="%d"),
                "data_primeira_parcela": st.column_config.TextColumn("1ª Parcela"),
                "palavra_chave_omie": st.column_config.TextColumn("Vínculo Omie"),
                "ativo": st.column_config.CheckboxColumn("Ativo"),
            },
            alt="Contratos de empréstimo cadastrados para auditoria Price e SAC",
        )
        nomes = dict(zip(loans["id_contrato"], loans["nome_contrato"]))
        sel_id = st.selectbox(
            "Cronograma teórico do contrato",
            options=list(nomes),
            format_func=lambda cid: f"{cid} — {nomes[cid]}",
            key="sel_inspect_loan",
        )
        row = loans[loans["id_contrato"] == sel_id].iloc[0]
        st.dataframe(
            gerar_tabela_emprestimo(
                valor_principal=float(row["valor_principal"]),
                taxa_juros_mensal_pct=float(row["taxa_juros_mensal"]),
                prazo_meses=int(row["prazo_meses"]),
                sistema_amortizacao=str(row["sistema_amortizacao"]),
                data_primeira_parcela=str(row["data_primeira_parcela"]),
            ),
            hide_index=True,
            height=260,
            column_config={
                "numero_parcela": st.column_config.NumberColumn("Parcela", format="%d"),
                "competencia": st.column_config.TextColumn("Competência"),
                "data_vencimento": st.column_config.TextColumn("Vencimento"),
                "sistema": st.column_config.TextColumn("Sistema"),
                "saldo_inicial": st.column_config.NumberColumn("Saldo Inicial", format="R$ %.2f"),
                "prestacao_teorica": st.column_config.NumberColumn("Prestação Teórica", format="R$ %.2f"),
                "amortizacao_teorica": st.column_config.NumberColumn("Amortização (Passivo)", format="R$ %.2f"),
                "juros_teoricos": st.column_config.NumberColumn("Juros (DRE)", format="R$ %.2f"),
                "saldo_final": st.column_config.NumberColumn("Saldo Devedor Final", format="R$ %.2f"),
            },
            alt="Cronograma teórico de parcelas calculado para o contrato selecionado",
        )
        if st.button(f"Remover contrato {sel_id}", key="btn_del_loan", icon=":material/delete:"):
            ctx.db.delete_loan_contract(sel_id)
            flash("info", "Contrato removido", f"O contrato {sel_id} foi excluído.")
            st.rerun()

with col_form.container(border=True):
    st.subheader(":material/add_business: Cadastrar / atualizar contrato")
    with st.form("form_loan_contract", border=False):
        f_id = st.text_input("Código do contrato", placeholder="Ex.: CTR-PRONAMPE-001")
        f_nome = st.text_input("Nome / identificação", placeholder="Ex.: Pronampe Banco do Brasil #7812")
        f_banco = st.text_input("Banco credor", value="Itaú Unibanco")
        s1, s2 = st.columns(2)
        f_sistema = s1.selectbox("Sistema", options=["PRICE", "SAC"])
        f_prazo = s2.number_input("Prazo (meses)", min_value=1, max_value=240, value=24, step=1)
        v1, v2 = st.columns(2)
        f_principal = v1.number_input("Principal contratado (R$)", min_value=100.0, value=100000.0, step=1000.0)
        f_taxa = v2.number_input("Taxa mensal (% a.m.)", min_value=0.0, max_value=30.0, value=1.50, step=0.05)
        f_dt1 = st.date_input("Vencimento da 1ª parcela", value=date.today().replace(day=15))
        f_kw = st.text_input(
            "Palavra-chave / nº contrato no Omie",
            placeholder="Ex.: 7812",
            help="Termo usado para vincular automaticamente as baixas mensais do Omie a este contrato.",
        )
        if st.form_submit_button("Salvar contrato de empréstimo", type="primary", icon=":material/save:", width="stretch"):
            if not f_id.strip() or not f_nome.strip() or not f_kw.strip():
                st.warning("Preencha Código, Nome e Palavra-chave de vínculo no Omie.", title="Campos obrigatórios")
            else:
                ctx.db.upsert_loan_contract(
                    {
                        "id_contrato": f_id,
                        "nome_contrato": f_nome,
                        "banco_nome": f_banco,
                        "sistema_amortizacao": f_sistema,
                        "valor_principal": f_principal,
                        "taxa_juros_mensal": f_taxa,
                        "prazo_meses": int(f_prazo),
                        "data_primeira_parcela": f_dt1.strftime("%Y-%m-%d"),
                        "palavra_chave_omie": f_kw,
                        "ativo": True,
                    }
                )
                flash("success", "Contrato salvo", f"O contrato '{f_nome}' ({f_sistema}) foi salvo com sucesso.")
                st.rerun()
