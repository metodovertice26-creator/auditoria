"""Regras de auditoria: catálogo nativo/customizado e construtor visual ou SQL."""

from __future__ import annotations

import streamlit as st

from src.audit.engine import build_sql_from_visual_rule, preview_custom_sql_rule
from src.ui import components as c
from src.ui.context import flash, get_context

MODO_VISUAL = "Assistente Visual (Sem SQL)"
MODO_SQL = "Modo Avançado (SQL DuckDB)"
SEVERIDADES = ["Crítico", "Alerta", "Informativo"]
CATEGORIAS = [
    "Conciliação Bancária",
    "Tarifas Bancárias",
    "Compliance & Alçadas",
    "Operações de Crédito & Recebíveis",
    "Endividamento & Contabilidade",
]
CAMPOS_VISUAIS = [
    "Diferença absoluta |Extrato - Omie| (R$)",
    "Valor absoluto no Extrato (R$)",
    "Juros / Multa pagos no Omie (R$)",
    "Desconto / Tarifa no Omie (R$)",
    "Valor absoluto no Omie (R$)",
    "Descrição do Extrato (texto)",
    "Categoria do Omie (texto)",
    "Cliente / Fornecedor no Omie (texto)",
]
MODELOS_SQL = {
    "Diferença de valor entre Extrato e Omie > R$ 10,00": "ABS(e.valor - o.valor) > 10.0 AND o.id_omie IS NOT NULL",
    "Saídas bancárias de alto valor (> R$ 10.000,00)": "e.tipo = 'DEBITO' AND e.valor_absoluto > 10000.0",
    "Pagamento com juros/multa elevados no Omie (> R$ 100,00)": "o.juros_multa > 100.0",
    "Tarifas bancárias avulsas acima de R$ 50,00": "e.natureza = 'TARIFA_BANCARIA' AND e.valor_absoluto > 50.0",
}
DICIONARIO_COLUNAS = """
- **`e` (`extrato`)**: `e.id_extrato`, `e.data_lancamento`, `e.banco_nome`, `e.conta`, `e.tipo` (`'CREDITO'`/`'DEBITO'`), `e.natureza`, `e.documento`, `e.descricao`, `e.valor`, `e.valor_absoluto`, `e.valor_nominal`, `e.valor_liquido`, `e.prazo_dias`
- **`o` (`omie`)**: `o.id_omie`, `o.data_lancamento`, `o.data_vencimento`, `o.banco_nome`, `o.status` (`'BAIXADO'`/`'CONCILIADO'`/`'ABERTO'`), `o.tipo` (`'ENTRADA'`/`'SAIDA'`), `o.categoria`, `o.grupo_contabil`, `o.cliente_fornecedor`, `o.documento`, `o.descricao`, `o.valor`, `o.valor_absoluto`, `o.valor_nominal`, `o.juros_multa`, `o.desconto_tarifa`
"""

ctx = get_context()
db = ctx.db
rules_df = ctx.rules_df
c.render_page_header(
    "Regras de auditoria",
    "Ative, ajuste a severidade ou crie novas regras sem alterar o código do motor.",
    ctx,
)

tab_catalogo, tab_construtor = st.tabs([":material/checklist: Catálogo de regras", ":material/auto_awesome: Nova regra customizada"])

# --- Catálogo -----------------------------------------------------------------
with tab_catalogo:
    with st.container(border=True):
        st.caption("Regras nativas (`AUD-01` a `AUD-05`) e customizadas (`CUS-*`). Marque/desmarque e salve.")
        edited = st.data_editor(
            rules_df[["ativa", "codigo", "nome", "severidade", "categoria", "tipo_regra", "descricao", "condicao_sql"]],
            key="editor_catalogo_regras",
            hide_index=True,
            disabled=["codigo", "nome", "categoria", "tipo_regra", "descricao", "condicao_sql"],
            column_config={
                "ativa": st.column_config.CheckboxColumn("Ativa"),
                "codigo": st.column_config.TextColumn("Código", pinned=True),
                "nome": st.column_config.TextColumn("Regra"),
                "severidade": st.column_config.SelectboxColumn("Severidade", options=SEVERIDADES, required=True),
                "categoria": st.column_config.TextColumn("Categoria"),
                "tipo_regra": st.column_config.TextColumn("Origem"),
                "descricao": st.column_config.TextColumn("Descrição Funcional"),
                "condicao_sql": st.column_config.TextColumn("Condição SQL (Custom)"),
            },
            alt="Catálogo editável de regras de auditoria financeira",
        )
        if st.button("Salvar alterações no catálogo", key="btn_save_rule_toggles", icon=":material/save:", type="primary"):
            for codigo, ativa, sev in zip(edited["codigo"], edited["ativa"], edited["severidade"]):
                db.update_rule_status(codigo=str(codigo), ativa=bool(ativa), severidade=str(sev))
            flash("success", "Catálogo atualizado", "Status e severidades das regras foram persistidos no DuckDB.")
            st.rerun()

    custom = rules_df[rules_df["tipo_regra"] == "CUSTOM"]
    if not custom.empty:
        with st.container(border=True):
            nomes = dict(zip(custom["codigo"], custom["nome"]))
            d1, d2 = st.columns([3, 1], vertical_alignment="bottom")
            with d1:
                del_code = st.selectbox(
                    "Remover regra customizada",
                    options=list(nomes),
                    format_func=lambda cod: f"{cod} — {nomes[cod]}",
                    key="sel_delete_custom_rule",
                )
            with d2:
                if st.button("Excluir", key="btn_delete_custom_rule", icon=":material/delete:", width="stretch"):
                    db.delete_custom_rule(del_code)
                    flash("info", "Regra removida", f"A regra customizada {del_code} foi excluída.")
                    st.rerun()

# --- Construtor ---------------------------------------------------------------
with tab_construtor:
    with st.container(border=True):
        modo = st.segmented_control("Modo de criação da regra", options=[MODO_VISUAL, MODO_SQL], default=MODO_VISUAL, key="seg_rule_builder_mode")

        if modo == MODO_SQL:
            t1, t2 = st.columns([3, 1], vertical_alignment="bottom")
            with t1:
                modelo = st.selectbox("Modelos rápidos de condição SQL (opcional)", options=list(MODELOS_SQL), key="sel_sql_template")
            with t2:
                if st.button("Usar modelo", key="btn_apply_sql_tpl", icon=":material/auto_fix_high:", width="stretch"):
                    st.session_state.sql_draft = MODELOS_SQL[modelo]
                    st.rerun()
            nova_sql = st.text_area(
                "Condição SQL (cláusula WHERE usando `e.` para extrato e `o.` para Omie, ou SELECT completo)",
                key="sql_draft",
                height=90,
            )
            with st.expander("Dicionário de colunas disponíveis (`e.*` e `o.*`)"):
                st.markdown(DICIONARIO_COLUNAS)
        else:
            v1, v2, v3 = st.columns([1.8, 1.2, 1.2], vertical_alignment="bottom")
            with v1:
                campo = st.selectbox("1. Campo monitorado", options=CAMPOS_VISUAIS, key="sel_vis_field")
            texto = "(texto)" in campo
            with v2:
                operador = st.selectbox(
                    "2. Condição",
                    options=(
                        ["Contém texto", "Igual a (=)", "Diferente de (!=)"]
                        if texto
                        else ["Maior que (>)", "Maior ou igual (>=)", "Menor que (<)", "Igual a (=)", "Diferente de (!=)"]
                    ),
                    key="sel_vis_op",
                )
            with v3:
                valor: float | str = (
                    st.text_input("3. Termo buscado", value="TARIFA", key="inp_vis_val_txt")
                    if texto
                    else st.number_input("3. Valor limite (R$)", min_value=0.0, value=10.0, step=5.0, key="inp_vis_val_num")
                )
            nova_sql = build_sql_from_visual_rule(campo, operador, valor)
            st.caption(f"Expressão gerada automaticamente: `{nova_sql}`")

        st.divider()
        r1, r2, r3 = st.columns([2, 1.5, 1], vertical_alignment="bottom")
        nome = r1.text_input("Nome da regra", placeholder="Ex.: Alçada de pagamentos acima de R$ 10 mil", key="inp_custom_name")
        categoria = r2.selectbox("Categoria", options=CATEGORIAS, key="sel_custom_cat")
        severidade = r3.selectbox("Severidade", options=SEVERIDADES, index=1, key="sel_custom_sev")
        descricao = st.text_input(
            "Descrição auditável da regra",
            placeholder="Descreva o risco financeiro ou contábil monitorado por esta regra.",
            key="inp_custom_desc",
        )

        b1, b2 = st.columns(2)
        testar = b1.button("Simular regra na base atual", key="btn_test_custom_sql", icon=":material/play_circle:", width="stretch")
        salvar = b2.button("Salvar nova regra customizada", key="btn_save_custom_sql", type="primary", icon=":material/add_task:", width="stretch")

        if testar:
            ok, msg, amostra = preview_custom_sql_rule(db, nova_sql)
            if ok:
                st.success(msg, title="Regra validada no DuckDB", icon=":material/check_circle:")
                if not amostra.empty:
                    st.dataframe(amostra, hide_index=True, alt="Amostra de registros retornados pelo teste da condição SQL")
            else:
                st.error(msg, title="Condição inválida", icon=":material/error:")

        if salvar:
            if not nome.strip():
                st.warning("Informe um nome identificador antes de salvar a regra.", title="Nome obrigatório")
            else:
                ok, msg, _ = preview_custom_sql_rule(db, nova_sql)
                if not ok:
                    st.error(msg, title="Corrija a condição antes de salvar")
                else:
                    codigo = db.add_custom_rule(
                        nome=nome,
                        descricao=descricao or nome,
                        condicao_sql=nova_sql,
                        severidade=severidade,
                        categoria=categoria,
                    )
                    flash("success", f"Regra {codigo} cadastrada", f"A regra '{nome}' foi salva no DuckDB e sincronizada em JSON.")
                    st.rerun()
