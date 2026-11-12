"""Componentes visuais reutilizáveis entre as páginas."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from src.services.auditoria import executar_auditoria
from src.ui.context import AuditContext, flash

# Caminhos das páginas (relativos ao entrypoint app.py), usados em st.page_link
PG_PAINEL = "app_pages/painel.py"
PG_EXTRATOS = "app_pages/extratos.py"
PG_OMIE = "app_pages/omie.py"
PG_BASE = "app_pages/base_normalizada.py"
PG_EXCECOES = "app_pages/excecoes.py"
PG_FECHAMENTO = "app_pages/fechamento.py"
PG_VINCULOS = "app_pages/vinculos.py"
PG_REGRAS = "app_pages/regras.py"
PG_BANCOS = "app_pages/parametros_bancarios.py"
PG_CONTRATOS = "app_pages/contratos.py"
PG_MANUTENCAO = "app_pages/manutencao.py"


def format_brl(value: float | int | None) -> str:
    if value is None or pd.isna(value):
        return "R$ 0,00"
    num = float(value)
    sign = "-" if num < 0 else ""
    formatted = f"{abs(num):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{sign}R$ {formatted}"


def format_pct(value: float | int | None) -> str:
    if value is None or pd.isna(value):
        return "0,00%"
    return f"{float(value):.2f}%".replace(".", ",")


def render_flash_messages() -> None:
    msg = st.session_state.get("flash_msg")
    if not msg:
        return
    kind, title_txt, body_txt = msg
    st.session_state.flash_msg = None
    if kind == "success":
        st.success(body_txt, title=title_txt, icon=":material/check_circle:")
    elif kind == "warning":
        st.warning(body_txt, title=title_txt, icon=":material/warning:")
    else:
        st.info(body_txt, title=title_txt, icon=":material/info:")


def render_page_header(title: str, subtitle: str, ctx: AuditContext) -> None:
    """Cabeçalho padrão: título Cinzel, subtítulo, escopo da competência e mensagens pendentes."""
    st.html(
        f"""
        <div class="vertice-header-wrap">
            <div>
                <div class="vertice-cinzel-title">{title}</div>
                <p class="vertice-subtitle">{subtitle}</p>
            </div>
            <span class="vertice-pill-gold">Escopo: {ctx.periodo_label}</span>
        </div>
        """
    )
    render_flash_messages()


def render_stepper(ctx: AuditContext) -> None:
    """Etapas do ciclo de auditoria; cada cartão leva à página onde a etapa é resolvida."""
    k = ctx.kpis
    etapas = [
        (
            "1. Extratos bancários",
            not ctx.extrato_df.empty,
            f"{len(ctx.extrato_df)} lançamentos prontos" if not ctx.extrato_df.empty else "Aguardando upload OFX/CSV",
            PG_EXTRATOS,
            "Importar extratos",
        ),
        (
            "2. Movimentação Omie",
            not ctx.omie_df.empty,
            f"{len(ctx.omie_df)} títulos normalizados" if not ctx.omie_df.empty else "Aguardando planilha/API Omie",
            PG_OMIE,
            "Importar Omie",
        ),
        (
            "3. Regras e contratos",
            ctx.regras_ativas > 0,
            f"{ctx.regras_ativas} regras ativas no motor",
            PG_REGRAS,
            "Configurar regras",
        ),
        (
            "4. Tratativas",
            ctx.latest_summary is not None and k.pendentes == 0,
            f"{k.pendentes} pendente(s) de tratativa" if ctx.latest_summary else "Aguardando execução da auditoria",
            PG_EXCECOES,
            "Tratar exceções",
        ),
    ]
    cols = st.columns(4)
    for col, (label, ok, meta, page, link_label) in zip(cols, etapas):
        with col.container(border=True):
            icon = ":green[:material/check_circle:]" if ok else ":orange[:material/radio_button_unchecked:]"
            st.markdown(f"{icon} **{label}**")
            st.caption(meta)
            st.page_link(page, label=link_label, icon=":material/arrow_forward:")


def render_execucao_card(ctx: AuditContext) -> None:
    """Cartão com o botão único de execução da auditoria completa."""
    with st.container(border=True):
        c_info, c_btn = st.columns([2.4, 1.2], vertical_alignment="center")
        with c_info:
            st.subheader(":material/shield_locked: Central de execução de auditoria")
            st.caption(
                "Cruza extratos e Omie (incluindo lotes 1:N e vínculos manuais), recalcula tarifas, "
                "Valor Presente de antecipações, tabelas Price/SAC e regras customizadas."
            )
        with c_btn:
            clicked = st.button(
                "Executar Auditoria Completa",
                key="btn_executar_auditoria_completa",
                type="primary",
                icon=":material/play_arrow:",
                width="stretch",
            )
        if clicked:
            if not ctx.tem_movimentos:
                st.warning(
                    "Importe extratos e relatórios Omie (menu Dados) ou carregue a massa de demonstração "
                    "em Configuração > Manutenção da base antes de auditar.",
                    title="Base sem movimentos para auditar",
                    icon=":material/warning:",
                )
            else:
                m = executar_auditoria(ctx.db, ctx.competencia)
                flash(
                    "success",
                    f"Auditoria concluída ({m['execucao_id']})",
                    f"Foram identificadas {m['total_divergencias']} divergências totalizando "
                    f"{format_brl(m['valor_total_divergente'])}.",
                )
                st.rerun()
