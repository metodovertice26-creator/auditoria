"""Auditoria Vértice — Plataforma de Conciliação e Auditoria Financeira (Extratos x ERP Omie)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from src.audit.engine import (
    build_sql_from_visual_rule,
    executar_auditoria_completa,
    preview_custom_sql_rule,
)
from src.audit.exporter import gerar_csv_auditoria, gerar_excel_auditoria
from src.audit.finance_math import gerar_tabela_emprestimo
from src.database import PROJECT_ROOT, VerticeDB
from src.etl.bank_csv_parser import parse_bank_tabular_bytes
from src.etl.ofx_parser import extract_ofx_statement_metadata, parse_ofx_bytes
from src.etl.omie_api import OmieApiClient
from src.etl.omie_importer import parse_omie_report_bytes
from src.sample_data import ensure_sample_files
from src.ui_dialogs import (
    WORKFLOW_STATUS_OPTIONS,
    abrir_modal_dossie_bancario,
    abrir_modal_drilldown,
)

st.set_page_config(
    page_title="Auditoria Vértice",
    page_icon=":material/account_balance:",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Cinzel restrito apenas a títulos pontuais de marca; corpo e seções em Outfit limpa
st.html(
    """
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Cinzel:wght@500;600;700&display=swap');

        /* Cards com fundo branco puro sobre o canvas #F8F9FA */
        [data-testid="stVerticalBlockBorderWrapper"] {
            background-color: #FFFFFF;
            box-shadow: 0 1px 2px rgba(16, 24, 40, 0.03);
        }
        [data-testid="stMetric"] {
            background-color: #FFFFFF;
        }

        .vertice-header-wrap {
            display: flex;
            align-items: baseline;
            justify-content: space-between;
            flex-wrap: wrap;
            gap: 0.5rem;
            border-bottom: 1px solid #E4E7EC;
            padding-bottom: 0.75rem;
            margin-bottom: 1rem;
        }
        .vertice-cinzel-title {
            font-family: 'Cinzel', Georgia, serif;
            font-size: 22px;
            font-weight: 600;
            letter-spacing: 0.07em;
            text-transform: uppercase;
            color: #2C3531;
            margin: 0;
            line-height: 1.25;
        }
        .vertice-cinzel-sidebar {
            font-family: 'Cinzel', Georgia, serif;
            font-size: 15px;
            font-weight: 600;
            letter-spacing: 0.08em;
            text-transform: uppercase;
            color: #2C3531;
            margin: 0.45rem 0 0.15rem 0;
        }
        .vertice-subtitle {
            color: #667085;
            font-size: 0.9rem;
            margin: 0.2rem 0 0 0;
        }
        .vertice-pill-gold {
            display: inline-block;
            background: #FBF8F1;
            border: 1px solid #DEC387;
            color: #7A5E24;
            padding: 0.12rem 0.55rem;
            border-radius: 999px;
            font-size: 0.72rem;
            font-weight: 600;
            letter-spacing: 0.04em;
            text-transform: uppercase;
        }
        .vertice-stepper {
            display: grid;
            grid-template-columns: repeat(4, minmax(0, 1fr));
            gap: 0.65rem;
            margin: 0.25rem 0 1.1rem 0;
        }
        .vertice-step {
            background: #FFFFFF;
            border: 1px solid #E4E7EC;
            border-left: 3px solid #D0D5DD;
            border-radius: 8px;
            padding: 0.55rem 0.8rem;
            font-size: 0.82rem;
        }
        .vertice-step.done {
            border-left-color: #3B6E52;
        }
        .vertice-step.warn {
            border-left-color: #C5A059;
        }
        .vertice-step-label {
            font-weight: 600;
            color: #2C3531;
            display: block;
        }
        .vertice-step-meta {
            color: #667085;
            font-size: 0.76rem;
        }
    </style>
    """
)


def get_db() -> VerticeDB:
    db_override = st.session_state.get("_vertice_db_path")
    if db_override:
        return VerticeDB(db_path=db_override)
    return VerticeDB()


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


def discover_local_data_files(base_dir: Path) -> list[Path]:
    """Localiza arquivos .ofx, .csv e .xlsx baixados pelo usuário na pasta raiz do projeto."""
    found: list[Path] = []
    for ext in ("*.ofx", "*.OFX", "*.csv", "*.CSV", "*.xlsx", "*.XLSX"):
        for p in base_dir.glob(ext):
            if p.is_file() and not p.name.startswith("~$"):
                found.append(p)
    unique = {p.resolve(): p for p in found}
    return sorted(unique.values(), key=lambda x: x.name.lower())


# Inicialização de estado da sessão
if "sql_draft" not in st.session_state:
    st.session_state.sql_draft = "ABS(e.valor - o.valor) > 10.0 AND o.id_omie IS NOT NULL"
if "flash_msg" not in st.session_state:
    st.session_state.flash_msg = None
if "last_opened_row_sig" not in st.session_state:
    st.session_state.last_opened_row_sig = None


def enrich_divergences_with_workflow(div_df: pd.DataFrame, wf_map: dict[str, dict[str, str]]) -> pd.DataFrame:
    if div_df.empty:
        out = div_df.copy()
        out["assinatura_ocorrencia"] = pd.Series(dtype="str")
        out["status_tratativa"] = pd.Series(dtype="str")
        out["justificativa"] = pd.Series(dtype="str")
        return out
    out = div_df.copy()
    sigs: list[str] = []
    statuses: list[str] = []
    notes: list[str] = []
    for _, row in out.iterrows():
        sig = None
        try:
            sig = json.loads(row["detalhe_json"]).get("assinatura_ocorrencia")
        except Exception:
            pass
        if not sig:
            sig = VerticeDB.build_issue_signature(
                str(row["codigo_regra"]),
                str(row["data_ocorrencia"]),
                str(row["banco"]),
                row["id_extrato"] if pd.notna(row["id_extrato"]) else None,
                row["id_omie"] if pd.notna(row["id_omie"]) else None,
                str(row["resumo"]),
            )
        wf = wf_map.get(sig, {})
        sigs.append(sig)
        statuses.append(wf.get("status_tratativa", "Pendente"))
        notes.append(wf.get("justificativa", ""))
    out["assinatura_ocorrencia"] = sigs
    out["status_tratativa"] = statuses
    out["justificativa"] = notes
    return out


db = get_db()
available_periods = db.get_available_periods()
period_options = ["TODAS"] + available_periods

# =============================================================================
# SIDEBAR — Competência Global, Estado da Base & Atalhos
# =============================================================================
with st.sidebar:
    st.markdown(
        '<span class="vertice-pill-gold">Governança Financeira</span>'
        '<div class="vertice-cinzel-sidebar">Auditoria Vértice</div>',
        unsafe_allow_html=True,
    )
    st.caption("Conciliação bancária, tarifas, antecipações e contratos Omie.")

    selected_period = st.selectbox(
        "Competência em análise",
        options=period_options,
        format_func=lambda p: "Todas as competências" if p == "TODAS" else f"Competência {p}",
        key="sel_global_period",
    )
    competencia_ativa = None if selected_period == "TODAS" else selected_period

    extrato_df = db.get_extrato_df(competencia=competencia_ativa)
    omie_df = db.get_omie_df(competencia=competencia_ativa)
    rules_df = db.get_rules_df()
    latest_summary, raw_divs_df = db.get_latest_audit_results()
    wf_map = db.get_workflow_map()
    latest_divs_df = enrich_divergences_with_workflow(raw_divs_df, wf_map)
    if competencia_ativa and not latest_divs_df.empty:
        latest_divs_df = latest_divs_df[
            latest_divs_df["data_ocorrencia"].astype(str).str.startswith(competencia_ativa)
        ].reset_index(drop=True)

    with st.container(border=True):
        st.markdown("**Resumo do escopo ativo**")
        st.caption(f":material/receipt_long: Extratos no filtro: **{len(extrato_df)}** linhas")
        st.caption(f":material/dns: Títulos Omie no filtro: **{len(omie_df)}** registros")
        active_cnt = int((rules_df["ativa"] == True).sum()) if not rules_df.empty else 0  # noqa: E712
        st.caption(f":material/gavel: Regras ativas: **{active_cnt}** de {len(rules_df)}")
        if not latest_divs_df.empty:
            pendentes_cnt = int((latest_divs_df["status_tratativa"] == "Pendente").sum())
            st.caption(f":material/pending_actions: Exceções pendentes: **{pendentes_cnt}**")
        if latest_summary:
            st.caption(f":material/schedule: Última execução: **{latest_summary['executado_em']}**")

    st.space("small")
    if st.button(
        "Carregar dados de demonstração",
        key="btn_load_demo",
        icon=":material/science:",
        width="stretch",
        help="Gera e importa extratos OFX Itaú, CSV Bradesco (com lote 1:N) e relatório XLSX Omie de exemplo.",
    ):
        sample_paths = ensure_sample_files()
        raw_ofx = sample_paths["ofx_itau"].read_bytes()
        raw_csv = sample_paths["csv_bradesco"].read_bytes()
        raw_omie = sample_paths["omie_xlsx"].read_bytes()

        df_ofx = parse_ofx_bytes(raw_ofx, filename=sample_paths["ofx_itau"].name)
        ofx_meta = extract_ofx_statement_metadata(raw_ofx, filename=sample_paths["ofx_itau"].name)
        df_csv = parse_bank_tabular_bytes(raw_csv, filename=sample_paths["csv_bradesco"].name)
        df_omie = parse_omie_report_bytes(
            raw_omie,
            filename=sample_paths["omie_xlsx"].name,
            origem_relatorio="MOVIMENTO_CC",
        )
        db.clear_movements("ALL")
        n_ext = db.upsert_extrato(pd.concat([df_ofx, df_csv], ignore_index=True))
        n_omi = db.upsert_omie(df_omie)
        db.register_imported_file(raw_ofx, sample_paths["ofx_itau"].name, "EXTRATO", df_ofx, ofx_meta=ofx_meta)
        db.register_imported_file(raw_csv, sample_paths["csv_bradesco"].name, "EXTRATO", df_csv)
        db.register_imported_file(raw_omie, sample_paths["omie_xlsx"].name, "OMIE", df_omie)
        executar_auditoria_completa(db, competencia=competencia_ativa)
        st.session_state.flash_msg = (
            "success",
            "Massa de demonstração carregada",
            f"{n_ext} lançamentos de extrato (incluindo lote PIX 1:N) e {n_omi} títulos do Omie auditados com sucesso.",
        )
        st.rerun()

    if st.button(
        "Limpar lançamentos importados",
        key="btn_clear_movements",
        icon=":material/delete_sweep:",
        width="stretch",
        help="Remove extratos e lançamentos Omie importados, mantendo suas regras customizadas, tratativas e contratos.",
    ):
        db.clear_movements("ALL")
        st.session_state.flash_msg = (
            "info",
            "Base de movimentos limpa",
            "Os extratos e relatórios foram removidos. Regras, contratos e histórico de tratativas foram preservados.",
        )
        st.rerun()

# =============================================================================
# CABEÇALHO PRINCIPAL + STEPPER DE PROGRESSO DO CICLO
# =============================================================================
step1_cls = "done" if not extrato_df.empty else "warn"
step2_cls = "done" if not omie_df.empty else "warn"
step3_cls = "done" if active_cnt > 0 else "warn"
pend_total = int((latest_divs_df["status_tratativa"] == "Pendente").sum()) if not latest_divs_df.empty else 0
step4_cls = "done" if (latest_summary is not None and pend_total == 0) else ("warn" if latest_summary else "")

step1_txt = f"{len(extrato_df)} lançamentos prontos" if not extrato_df.empty else "Aguardando upload OFX/CSV"
step2_txt = f"{len(omie_df)} títulos normalizados" if not omie_df.empty else "Aguardando planilha/API Omie"
step3_txt = f"{active_cnt} regras ativas no motor"
step4_txt = (
    f"{pend_total} pendente(s) de tratativa"
    if latest_summary
    else "Aguardando execução da auditoria"
)

st.html(
    f"""
    <div class="vertice-header-wrap">
        <div>
            <div class="vertice-cinzel-title">Auditoria Vértice</div>
            <p class="vertice-subtitle">Conciliação e auditoria financeira automatizada — Extratos bancários (OFX/CSV) × ERP Omie</p>
        </div>
        <span class="vertice-pill-gold">Escopo: {'Todas as competências' if selected_period == 'TODAS' else selected_period}</span>
    </div>
    <div class="vertice-stepper">
        <div class="vertice-step {step1_cls}">
            <span class="vertice-step-label">1. Extratos Bancários</span>
            <span class="vertice-step-meta">{step1_txt}</span>
        </div>
        <div class="vertice-step {step2_cls}">
            <span class="vertice-step-label">2. Movimentação Omie</span>
            <span class="vertice-step-meta">{step2_txt}</span>
        </div>
        <div class="vertice-step {step3_cls}">
            <span class="vertice-step-label">3. Regras & Contratos</span>
            <span class="vertice-step-meta">{step3_txt}</span>
        </div>
        <div class="vertice-step {step4_cls}">
            <span class="vertice-step-label">4. Governança & Tratativas</span>
            <span class="vertice-step-meta">{step4_txt}</span>
        </div>
    </div>
    """
)

if st.session_state.flash_msg:
    kind, title_txt, body_txt = st.session_state.flash_msg
    st.session_state.flash_msg = None
    if kind == "success":
        st.success(body_txt, title=title_txt, icon=":material/check_circle:")
    elif kind == "warning":
        st.warning(body_txt, title=title_txt, icon=":material/warning:")
    else:
        st.info(body_txt, title=title_txt, icon=":material/info:")

# =============================================================================
# NAVEGAÇÃO EM 3 ABAS (Arquitetura Oficial)
# =============================================================================
tab_etl, tab_rules, tab_exec = st.tabs(
    [
        ":material/cloud_upload: 1. Ingestão e conexão (ETL)",
        ":material/rule_settings: 2. Regras e cadastro dinâmico",
        ":material/fact_check: 3. Execução e matriz de divergências",
    ],
    key="main_tabs",
)

# =============================================================================
# ABA 1: INGESTÃO E CONEXÃO (ETL)
# =============================================================================
with tab_etl:
    col_bank, col_omie = st.columns(2, gap="large")

    # --- Coluna 1: Upload de Extratos Bancários (.OFX / .CSV / .XLSX) ---
    with col_bank:
        with st.container(border=True):
            st.subheader(":material/account_balance: Extratos bancários")
            st.caption(
                "Arraste arquivos `.OFX` (qualquer banco brasileiro — OFX 1.x SGML ou 2.x XML) ou planilhas `.CSV`/`.XLSX`."
            )
            bank_override_choice = st.selectbox(
                "Identificação da instituição bancária",
                options=[
                    "Detectar automaticamente pelo arquivo (COMPE / ORG)",
                    "Itaú Unibanco",
                    "Bradesco",
                    "Banco do Brasil",
                    "Santander",
                    "Banco Inter",
                    "Cora",
                    "Caixa Econômica Federal",
                    "BTG Pactual",
                    "Sicredi",
                    "Sicoob",
                    "Nubank",
                    "C6 Bank",
                    "Banco Safra",
                ],
                key="sel_bank_override",
            )
            uploaded_statements = st.file_uploader(
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
                disabled=not uploaded_statements,
            ):
                override = (
                    None
                    if bank_override_choice.startswith("Detectar")
                    else bank_override_choice
                )
                frames: list[pd.DataFrame] = []
                errors: list[str] = []
                dup_warnings: list[str] = []
                for uf in uploaded_statements or []:
                    try:
                        raw = uf.getvalue()
                        ofx_meta = None
                        if uf.name.lower().endswith(".ofx"):
                            parsed_df = parse_ofx_bytes(raw, filename=uf.name, bank_override=override)
                            ofx_meta = extract_ofx_statement_metadata(raw, filename=uf.name)
                        else:
                            parsed_df = parse_bank_tabular_bytes(
                                raw, filename=uf.name, bank_override=override
                            )
                        reg = db.register_imported_file(
                            raw, uf.name, "EXTRATO", parsed_df, ofx_meta=ofx_meta
                        )
                        if reg.get("duplicado"):
                            dup_warnings.append(uf.name)
                        frames.append(parsed_df)
                    except Exception as exc:
                        errors.append(f"{uf.name}: {exc}")

                if frames:
                    merged = pd.concat(frames, ignore_index=True)
                    inserted = db.upsert_extrato(merged)
                    dup_note = (
                        f" (Aviso: {', '.join(dup_warnings)} já havia sido importado antes — registros atualizados com idempotência)."
                        if dup_warnings
                        else ""
                    )
                    st.session_state.flash_msg = (
                        "warning" if dup_warnings else "success",
                        "Extratos normalizados e validados",
                        f"{inserted} movimentações bancárias validadas pelo Pandera e gravadas no DuckDB.{dup_note}",
                    )
                    if not errors:
                        st.rerun()
                for err in errors:
                    st.error(err, title="Falha ao ler arquivo de extrato", icon=":material/error:")

    # --- Coluna 2: Importação ERP Omie (Upload Prioritário + Conector API Modular) ---
    with col_omie:
        with st.container(border=True):
            st.subheader(":material/sync_alt: Lançamentos ERP Omie")
            omie_mode = st.segmented_control(
                "Fonte de dados do Omie",
                options=["Upload de relatórios (CSV / XLSX)", "Conexão API REST"],
                default="Upload de relatórios (CSV / XLSX)",
                key="seg_omie_mode",
            )

            if omie_mode == "Conexão API REST":
                st.caption(
                    "Conector modularizado para sincronização direta com `/financas/mf/` do ERP Omie."
                )
                default_key = ""
                default_secret = ""
                try:
                    default_key = str(st.secrets.get("OMIE_APP_KEY", ""))
                    default_secret = str(st.secrets.get("OMIE_APP_SECRET", ""))
                except Exception:
                    pass

                with st.form("form_omie_api", border=False):
                    api_key = st.text_input("Omie APP_KEY", value=default_key, type="password")
                    api_secret = st.text_input("Omie APP_SECRET", value=default_secret, type="password")
                    c_d1, c_d2 = st.columns(2)
                    with c_d1:
                        dt_ini = st.date_input("Data inicial", value=date(2026, 10, 1))
                    with c_d2:
                        dt_fim = st.date_input("Data final", value=date(2026, 10, 31))
                    submitted_api = st.form_submit_button(
                        "Sincronizar movimentos via API Omie",
                        icon=":material/cloud_sync:",
                        width="stretch",
                    )
                    if submitted_api:
                        try:
                            client = OmieApiClient(app_key=api_key, app_secret=api_secret)
                            df_api = client.importar_movimentos_financeiros(
                                data_inicio_br=dt_ini.strftime("%d/%m/%Y"),
                                data_fim_br=dt_fim.strftime("%d/%m/%Y"),
                            )
                            count_api = db.upsert_omie(df_api)
                            st.session_state.flash_msg = (
                                "success",
                                "Sincronização Omie concluída",
                                f"{count_api} movimentos financeiros importados via API.",
                            )
                            st.rerun()
                        except Exception as exc:
                            st.error(str(exc), title="Erro na comunicação com API Omie")
            else:
                st.caption(
                    "Importe planilhas exportadas do Omie: Movimento de Conta Corrente, Contas a Pagar/Receber ou Baixas."
                )
                origem_map = {
                    "Movimento de Conta Corrente": "MOVIMENTO_CC",
                    "Baixas de Títulos": "BAIXAS",
                    "Contas a Receber": "CONTAS_RECEBER",
                    "Contas a Pagar": "CONTAS_PAGAR",
                }
                origem_label = st.selectbox(
                    "Tipo de relatório Omie",
                    options=list(origem_map.keys()),
                    key="sel_omie_report_type",
                )
                uploaded_omie = st.file_uploader(
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
                    disabled=not uploaded_omie,
                ):
                    o_frames: list[pd.DataFrame] = []
                    o_errors: list[str] = []
                    for ofile in uploaded_omie or []:
                        try:
                            raw_o = ofile.getvalue()
                            parsed_o = parse_omie_report_bytes(
                                raw_o,
                                filename=ofile.name,
                                origem_relatorio=origem_map[origem_label],
                            )
                            db.register_imported_file(raw_o, ofile.name, "OMIE", parsed_o)
                            o_frames.append(parsed_o)
                        except Exception as exc:
                            o_errors.append(f"{ofile.name}: {exc}")

                    if o_frames:
                        merged_o = pd.concat(o_frames, ignore_index=True)
                        inserted_o = db.upsert_omie(merged_o)
                        st.session_state.flash_msg = (
                            "success",
                            "Relatórios Omie importados",
                            f"{inserted_o} lançamentos normalizados e validados pelo Pandera.",
                        )
                        if not o_errors:
                            st.rerun()
                    for err in o_errors:
                        st.error(err, title="Falha ao importar relatório Omie", icon=":material/error:")

    # --- Auto-descoberta de arquivos baixados diretamente na pasta do projeto ---
    local_files = discover_local_data_files(PROJECT_ROOT)
    if local_files:
        with st.container(border=True):
            st.subheader(":material/folder_open: Arquivos detectados na pasta do projeto")
            st.caption(
                "Arquivos `.OFX`, `.CSV` e `.XLSX` encontrados na raiz da pasta do projeto prontos para importação rápida."
            )
            c_sel_f, c_dest_f, c_act_f = st.columns([3, 2, 2], vertical_alignment="bottom")
            with c_sel_f:
                selected_local = st.selectbox(
                    "Arquivo local",
                    options=local_files,
                    format_func=lambda p: f"{p.name} ({p.stat().st_size / 1024:.1f} KB)",
                    key="sel_local_file",
                )
            with c_dest_f:
                default_dest_idx = 0 if selected_local.suffix.lower() == ".ofx" else 0
                local_dest = st.selectbox(
                    "Importar como",
                    options=[
                        "Extrato Bancário (OFX/CSV/XLSX)",
                        "Relatório Omie — Movimento CC / Baixas",
                        "Relatório Omie — Contas a Receber",
                        "Relatório Omie — Contas a Pagar",
                    ],
                    index=default_dest_idx,
                    key="sel_local_dest",
                )
            with c_act_f:
                if st.button(
                    "Importar arquivo da pasta",
                    key="btn_import_local_file",
                    icon=":material/download_done:",
                    width="stretch",
                ):
                    try:
                        raw_local = selected_local.read_bytes()
                        if local_dest.startswith("Extrato"):
                            ofx_m = None
                            if selected_local.suffix.lower() == ".ofx":
                                df_l = parse_ofx_bytes(raw_local, filename=selected_local.name)
                                ofx_m = extract_ofx_statement_metadata(raw_local, filename=selected_local.name)
                            else:
                                df_l = parse_bank_tabular_bytes(raw_local, filename=selected_local.name)
                            cnt = db.upsert_extrato(df_l)
                            db.register_imported_file(
                                raw_local, selected_local.name, "EXTRATO", df_l, ofx_meta=ofx_m
                            )
                            st.session_state.flash_msg = (
                                "success",
                                "Arquivo local importado como Extrato",
                                f"{cnt} lançamentos carregados de '{selected_local.name}'.",
                            )
                        else:
                            orig_code = (
                                "CONTAS_RECEBER"
                                if "Receber" in local_dest
                                else ("CONTAS_PAGAR" if "Pagar" in local_dest else "MOVIMENTO_CC")
                            )
                            df_lo = parse_omie_report_bytes(
                                raw_local,
                                filename=selected_local.name,
                                origem_relatorio=orig_code,
                            )
                            cnt = db.upsert_omie(df_lo)
                            db.register_imported_file(raw_local, selected_local.name, "OMIE", df_lo)
                            st.session_state.flash_msg = (
                                "success",
                                "Arquivo local importado como Omie",
                                f"{cnt} lançamentos carregados de '{selected_local.name}'.",
                            )
                        st.rerun()
                    except Exception as exc:
                        st.error(str(exc), title=f"Erro ao importar {selected_local.name}")

    # --- Detector de Lacunas de Datas entre Extratos + Gestão de Lotes SHA-256 ---
    statement_gaps = db.detect_statement_gaps()
    for gap in statement_gaps:
        st.warning(
            f"Detectada janela sem cobertura de **{gap['dias_faltantes']} dia(s)** ({gap['gap_inicio']} a {gap['gap_fim']}) "
            f"na conta **{gap['conta']} ({gap['banco_nome']})** entre `{gap['arquivo_anterior']}` e `{gap['arquivo_seguinte']}`.",
            title="Lacuna temporal entre extratos bancários",
            icon=":material/event_busy:",
        )

    imported_files_df = db.get_imported_files_df()
    if not imported_files_df.empty:
        with st.expander(
            f":material/inventory_2: Controle de lotes importados, saldo OFX e exclusão por arquivo ({len(imported_files_df)} lote(s))",
            expanded=False,
        ):
            st.dataframe(
                imported_files_df,
                hide_index=True,
                column_config={
                    "id_arquivo": None,
                    "nome_arquivo": st.column_config.TextColumn("Arquivo"),
                    "tipo_fonte": st.column_config.TextColumn("Fonte"),
                    "banco_nome": st.column_config.TextColumn("Banco"),
                    "conta": st.column_config.TextColumn("Conta"),
                    "dt_inicio": st.column_config.TextColumn("Início Cobertura"),
                    "dt_fim": st.column_config.TextColumn("Fim Cobertura"),
                    "qtd_registros": st.column_config.NumberColumn("Registros", format="%d"),
                    "soma_movimentos": st.column_config.NumberColumn("Soma Lote (R$)", format="R$ %.2f"),
                    "saldo_final_ofx": st.column_config.NumberColumn("Saldo Final OFX (<BALAMT>)", format="R$ %.2f"),
                    "hash_curto": st.column_config.TextColumn("SHA-256"),
                    "importado_em": st.column_config.TextColumn("Importado em"),
                },
                alt="Histórico de arquivos importados com hash SHA-256 e saldo final OFX",
            )
            c_del_f1, c_del_f2 = st.columns([3, 1], vertical_alignment="bottom")
            with c_del_f1:
                file_to_remove = st.selectbox(
                    "Excluir apenas um arquivo específico enviado por engano",
                    options=imported_files_df["id_arquivo"].tolist(),
                    format_func=lambda fid: (
                        f"[{imported_files_df[imported_files_df['id_arquivo'] == fid]['tipo_fonte'].iloc[0]}] "
                        f"{imported_files_df[imported_files_df['id_arquivo'] == fid]['nome_arquivo'].iloc[0]}"
                    ),
                    key="sel_remove_single_file",
                )
            with c_del_f2:
                if st.button(
                    "Remover lote",
                    key="btn_remove_single_file",
                    icon=":material/delete:",
                    width="stretch",
                ):
                    removed_rows = db.delete_imported_file(file_to_remove)
                    st.session_state.flash_msg = (
                        "info",
                        "Arquivo removido seletivamente",
                        f"{removed_rows} registro(s) vinculados ao lote selecionado foram removidos.",
                    )
                    st.rerun()

    # --- Inspeção da Base Normalizada ---
    st.space("small")
    with st.container(border=True):
        st.subheader(":material/dataset: Prévia dos dados normalizados (AAAA-MM-DD & Pandera)")
        m1, m2, m3, m4 = st.columns(4)
        bancos_unicos = extrato_df["banco_nome"].nunique() if not extrato_df.empty else 0
        creditos_ext = float(extrato_df[extrato_df["valor"] > 0]["valor"].sum()) if not extrato_df.empty else 0.0
        debitos_ext = float(extrato_df[extrato_df["valor"] < 0]["valor"].sum()) if not extrato_df.empty else 0.0
        m1.metric("Linhas no extrato", f"{len(extrato_df)}", border=True)
        m2.metric("Bancos identificados", f"{bancos_unicos}", border=True)
        m3.metric("Créditos no extrato", format_brl(creditos_ext), border=True)
        m4.metric("Lançamentos no Omie", f"{len(omie_df)}", delta=f"Saídas extrato: {format_brl(debitos_ext)}", delta_color="off", border=True)

        preview_choice = st.segmented_control(
            "Visualizar tabela normalizada",
            options=["Extrato bancário normalizado", "Lançamentos Omie normalizados"],
            default="Extrato bancário normalizado",
            key="seg_preview_etl",
        )
        if preview_choice == "Extrato bancário normalizado":
            if extrato_df.empty:
                st.info(
                    "Envie um arquivo `.OFX`/`.CSV` acima ou clique em 'Carregar dados de demonstração' na barra lateral.",
                    title="Nenhum extrato carregado ainda",
                    icon=":material/upload:",
                )
            else:
                st.dataframe(
                    extrato_df[
                        [
                            "data_lancamento",
                            "banco_nome",
                            "conta",
                            "tipo_movimento",
                            "natureza_operacao",
                            "documento",
                            "descricao_normalizada",
                            "valor",
                            "valor_nominal",
                            "prazo_dias",
                        ]
                    ],
                    hide_index=True,
                    column_config={
                        "data_lancamento": st.column_config.TextColumn("Data (ISO)"),
                        "banco_nome": st.column_config.TextColumn("Banco"),
                        "conta": st.column_config.TextColumn("Conta"),
                        "tipo_movimento": st.column_config.TextColumn("Tipo"),
                        "natureza_operacao": st.column_config.TextColumn("Natureza Identificada"),
                        "documento": st.column_config.TextColumn("Documento / FITID"),
                        "descricao_normalizada": st.column_config.TextColumn("Descrição Normalizada"),
                        "valor": st.column_config.NumberColumn("Valor (R$)", format="R$ %.2f"),
                        "valor_nominal": st.column_config.NumberColumn("Valor Nominal (R$)", format="R$ %.2f"),
                        "prazo_dias": st.column_config.NumberColumn("Prazo (Dias)", format="%d"),
                    },
                    alt="Tabela de extratos bancários normalizados pelo ETL",
                )
        else:
            if omie_df.empty:
                st.info(
                    "Importe um relatório exportado do Omie (`.xlsx` ou `.csv`) para visualizar os lançamentos.",
                    title="Nenhum lançamento Omie carregado",
                    icon=":material/table_chart:",
                )
            else:
                st.dataframe(
                    omie_df[
                        [
                            "id_omie",
                            "data_lancamento",
                            "banco_nome",
                            "status_titulo",
                            "categoria",
                            "grupo_contabil",
                            "cliente_fornecedor",
                            "documento",
                            "valor",
                            "valor_nominal",
                        ]
                    ],
                    hide_index=True,
                    column_config={
                        "id_omie": st.column_config.TextColumn("Código Omie"),
                        "data_lancamento": st.column_config.TextColumn("Data Baixa (ISO)"),
                        "banco_nome": st.column_config.TextColumn("Banco"),
                        "status_titulo": st.column_config.TextColumn("Status"),
                        "categoria": st.column_config.TextColumn("Categoria Omie"),
                        "grupo_contabil": st.column_config.TextColumn("Grupo Contábil"),
                        "cliente_fornecedor": st.column_config.TextColumn("Cliente / Fornecedor"),
                        "documento": st.column_config.TextColumn("Documento"),
                        "valor": st.column_config.NumberColumn("Valor Movimento (R$)", format="R$ %.2f"),
                        "valor_nominal": st.column_config.NumberColumn("Valor Nominal (R$)", format="R$ %.2f"),
                    },
                    alt="Tabela de lançamentos do ERP Omie normalizados pelo ETL",
                )

# =============================================================================
# ABA 2: REGRAS DE AUDITORIA & CADASTRO DINÂMICO
# =============================================================================
with tab_rules:
    rules_section = st.segmented_control(
        "Módulo de configuração de auditoria",
        options=[
            "Catálogo de regras e construtor SQL",
            "Tarifas e taxas contratadas por banco (AUD-03 / AUD-04)",
            "Contratos de empréstimos Price e SAC (AUD-05)",
        ],
        default="Catálogo de regras e construtor SQL",
        key="seg_rules_section",
    )

    # --- Subseção 2.1: Catálogo Nativo (AUD-01..05) + Construtor Customizado SQL ---
    if rules_section == "Catálogo de regras e construtor SQL":
        with st.container(border=True):
            st.subheader(":material/checklist: Catálogo de regras ativas")
            st.caption(
                "Ative ou desative regras nativas (`AUD-01` a `AUD-05`) e regras customizadas (`CUS-*`), ou ajuste sua severidade."
            )
            edited_rules = st.data_editor(
                rules_df[["ativa", "codigo", "nome", "severidade", "categoria", "tipo_regra", "descricao", "condicao_sql"]],
                key="editor_catalogo_regras",
                hide_index=True,
                disabled=["codigo", "nome", "categoria", "tipo_regra", "descricao", "condicao_sql"],
                column_config={
                    "ativa": st.column_config.CheckboxColumn("Ativa"),
                    "codigo": st.column_config.TextColumn("Código", pinned=True),
                    "nome": st.column_config.TextColumn("Regra"),
                    "severidade": st.column_config.SelectboxColumn(
                        "Severidade",
                        options=["Crítico", "Alerta", "Informativo"],
                        required=True,
                    ),
                    "categoria": st.column_config.TextColumn("Categoria"),
                    "tipo_regra": st.column_config.TextColumn("Origem"),
                    "descricao": st.column_config.TextColumn("Descrição Funcional"),
                    "condicao_sql": st.column_config.TextColumn("Condição SQL (Custom)"),
                },
                alt="Catálogo editável de regras de auditoria financeira",
            )

            col_save_r, col_del_r = st.columns([2, 2], vertical_alignment="bottom")
            with col_save_r:
                if st.button(
                    "Salvar alterações no catálogo",
                    key="btn_save_rule_toggles",
                    icon=":material/save:",
                    type="primary",
                ):
                    for _, rrow in edited_rules.iterrows():
                        db.update_rule_status(
                            codigo=str(rrow["codigo"]),
                            ativa=bool(rrow["ativa"]),
                            severidade=str(rrow["severidade"]),
                        )
                    st.session_state.flash_msg = (
                        "success",
                        "Catálogo atualizado",
                        "Status e severidades das regras foram persistidos no DuckDB.",
                    )
                    st.rerun()

            custom_only = rules_df[rules_df["tipo_regra"] == "CUSTOM"]
            if not custom_only.empty:
                with col_del_r:
                    c_del_sel, c_del_btn = st.columns([2, 1], vertical_alignment="bottom")
                    with c_del_sel:
                        del_code = st.selectbox(
                            "Remover regra customizada",
                            options=custom_only["codigo"].tolist(),
                            format_func=lambda c: f"{c} — {custom_only[custom_only['codigo'] == c]['nome'].iloc[0]}",
                            key="sel_delete_custom_rule",
                        )
                    with c_del_btn:
                        if st.button("Excluir", key="btn_delete_custom_rule", icon=":material/delete:"):
                            db.delete_custom_rule(del_code)
                            st.session_state.flash_msg = (
                                "info",
                                "Regra removida",
                                f"A regra customizada {del_code} foi excluída.",
                            )
                            st.rerun()

        st.space("small")
        with st.container(border=True):
            st.subheader(":material/auto_awesome: Construtor de regras customizadas (Assistente Visual & SQL)")
            st.caption(
                "Crie regras de auditoria em 3 cliques sem precisar escrever SQL, ou alterne para o modo SQL avançado."
            )

            builder_mode = st.segmented_control(
                "Modo de criação da regra",
                options=["Assistente Visual (Sem SQL)", "Modo Avançado (SQL DuckDB)"],
                default="Assistente Visual (Sem SQL)",
                key="seg_rule_builder_mode",
            )

            if builder_mode == "Assistente Visual (Sem SQL)":
                v_c1, v_c2, v_c3, v_c4 = st.columns([1.8, 1.2, 1.2, 1.0], vertical_alignment="bottom")
                with v_c1:
                    vis_field = st.selectbox(
                        "1. Campo monitorado",
                        options=[
                            "Diferença absoluta |Extrato - Omie| (R$)",
                            "Valor absoluto no Extrato (R$)",
                            "Juros / Multa pagos no Omie (R$)",
                            "Desconto / Tarifa no Omie (R$)",
                            "Valor absoluto no Omie (R$)",
                            "Descrição do Extrato (texto)",
                            "Categoria do Omie (texto)",
                            "Cliente / Fornecedor no Omie (texto)",
                        ],
                        key="sel_vis_field",
                    )
                is_text_field = "(texto)" in vis_field
                with v_c2:
                    vis_op = st.selectbox(
                        "2. Condição",
                        options=(
                            ["Contém texto", "Igual a (=)", "Diferente de (!=)"]
                            if is_text_field
                            else ["Maior que (>)", "Maior ou igual (>=)", "Menor que (<)", "Igual a (=)", "Diferente de (!=)"]
                        ),
                        key="sel_vis_op",
                    )
                with v_c3:
                    if is_text_field:
                        vis_val: float | str = st.text_input(
                            "3. Termo buscado",
                            value="TARIFA",
                            key="inp_vis_val_txt",
                        )
                    else:
                        vis_val = st.number_input(
                            "3. Valor limite (R$)",
                            min_value=0.0,
                            value=10.0,
                            step=5.0,
                            key="inp_vis_val_num",
                        )
                generated_sql = build_sql_from_visual_rule(vis_field, vis_op, vis_val)
                with v_c4:
                    if st.button(
                        "Aplicar regra",
                        key="btn_apply_visual_sql",
                        icon=":material/check:",
                        width="stretch",
                    ):
                        st.session_state.sql_draft = generated_sql
                        st.rerun()
                st.caption(f"Expressão gerada automaticamente: `{generated_sql}`")
                effective_sql_candidate = generated_sql
            else:
                templates_sql = {
                    "Diferença de valor entre Extrato e Omie > R$ 10,00": "ABS(e.valor - o.valor) > 10.0 AND o.id_omie IS NOT NULL",
                    "Saídas bancárias de alto valor (> R$ 10.000,00)": "e.tipo = 'DEBITO' AND e.valor_absoluto > 10000.0",
                    "Pagamento com juros/multa elevados no Omie (> R$ 100,00)": "o.juros_multa > 100.0",
                    "Tarifas bancárias avulsas acima de R$ 50,00": "e.natureza = 'TARIFA_BANCARIA' AND e.valor_absoluto > 50.0",
                }
                c_tpl, c_tpl_btn = st.columns([3, 1], vertical_alignment="bottom")
                with c_tpl:
                    chosen_tpl = st.selectbox(
                        "Modelos rápidos de condição SQL (opcional)",
                        options=list(templates_sql.keys()),
                        key="sel_sql_template",
                    )
                with c_tpl_btn:
                    if st.button("Usar modelo", key="btn_apply_sql_tpl", icon=":material/auto_fix_high:", width="stretch"):
                        st.session_state.sql_draft = templates_sql[chosen_tpl]
                        st.rerun()

            r_c1, r_c2, r_c3 = st.columns([2, 1.5, 1], vertical_alignment="bottom")
            with r_c1:
                new_rule_name = st.text_input(
                    "Nome da regra",
                    placeholder="Ex.: Alçada de pagamentos acima de R$ 10 mil",
                    key="inp_custom_name",
                )
            with r_c2:
                new_rule_cat = st.selectbox(
                    "Categoria",
                    options=[
                        "Conciliação Bancária",
                        "Tarifas Bancárias",
                        "Compliance & Alçadas",
                        "Operações de Crédito & Recebíveis",
                        "Endividamento & Contabilidade",
                    ],
                    key="sel_custom_cat",
                )
            with r_c3:
                new_rule_sev = st.selectbox(
                    "Severidade",
                    options=["Crítico", "Alerta", "Informativo"],
                    index=1,
                    key="sel_custom_sev",
                )

            new_rule_desc = st.text_input(
                "Descrição auditável da regra",
                placeholder="Descreva o risco financeiro ou contábil monitorado por esta regra.",
                key="inp_custom_desc",
            )

            if builder_mode == "Modo Avançado (SQL DuckDB)":
                new_rule_sql = st.text_area(
                    "Condição SQL (cláusula WHERE usando `e.` para extrato e `o.` para Omie, ou SELECT completo)",
                    key="sql_draft",
                    height=90,
                )
            else:
                new_rule_sql = effective_sql_candidate

            with st.expander("Consultar dicionário de colunas disponíveis para SQL (`e.*` e `o.*`)"):
                st.markdown(
                    """
                    - **`e` (`extrato`)**: `e.id_extrato`, `e.data_lancamento`, `e.banco_nome`, `e.conta`, `e.tipo` (`'CREDITO'`/`'DEBITO'`), `e.natureza`, `e.documento`, `e.descricao`, `e.valor`, `e.valor_absoluto`, `e.valor_nominal`, `e.valor_liquido`, `e.prazo_dias`
                    - **`o` (`omie`)**: `o.id_omie`, `o.data_lancamento`, `o.data_vencimento`, `o.banco_nome`, `o.status` (`'BAIXADO'`/`'CONCILIADO'`/`'ABERTO'`), `o.tipo` (`'ENTRADA'`/`'SAIDA'`), `o.categoria`, `o.grupo_contabil`, `o.cliente_fornecedor`, `o.documento`, `o.descricao`, `o.valor`, `o.valor_absoluto`, `o.valor_nominal`, `o.juros_multa`, `o.desconto_tarifa`
                    """
                )

            b_test, b_save = st.columns(2)
            with b_test:
                test_clicked = st.button(
                    "Simular regra na base atual",
                    key="btn_test_custom_sql",
                    icon=":material/play_circle:",
                    width="stretch",
                )
            with b_save:
                save_clicked = st.button(
                    "Salvar nova regra customizada",
                    key="btn_save_custom_sql",
                    type="primary",
                    icon=":material/add_task:",
                    width="stretch",
                )

            if test_clicked:
                ok_sql, msg_sql, sample_sql_df = preview_custom_sql_rule(db, new_rule_sql)
                if ok_sql:
                    st.success(msg_sql, title="Regra validada no DuckDB", icon=":material/check_circle:")
                    if not sample_sql_df.empty:
                        st.dataframe(
                            sample_sql_df,
                            hide_index=True,
                            alt="Amostra de registros retornados pelo teste da condição SQL",
                        )
                else:
                    st.error(msg_sql, title="Condição inválida", icon=":material/error:")

            if save_clicked:
                if not new_rule_name.strip():
                    st.warning(
                        "Informe um nome identificador antes de salvar a regra.",
                        title="Nome obrigatório",
                    )
                else:
                    ok_sql, msg_sql, _ = preview_custom_sql_rule(db, new_rule_sql)
                    if not ok_sql:
                        st.error(msg_sql, title="Corrija a condição antes de salvar")
                    else:
                        code_created = db.add_custom_rule(
                            nome=new_rule_name,
                            descricao=new_rule_desc or new_rule_name,
                            condicao_sql=new_rule_sql,
                            severidade=new_rule_sev,
                            categoria=new_rule_cat,
                        )
                        st.session_state.flash_msg = (
                            "success",
                            f"Regra {code_created} cadastrada",
                            f"A regra '{new_rule_name}' foi salva no DuckDB e sincronizada em JSON.",
                        )
                        st.rerun()

        manual_links_view = db.get_manual_links_df()
        if not manual_links_view.empty:
            with st.expander(f":material/link: Vínculos manuais de conciliação ativos ({len(manual_links_view)})"):
                st.dataframe(manual_links_view, hide_index=True, alt="Tabela de vínculos manuais cadastrados")

    # --- Subseção 2.2: Parâmetros Contratuais Bancários (AUD-03 / AUD-04) ---
    elif rules_section == "Tarifas e taxas contratadas por banco (AUD-03 / AUD-04)":
        with st.container(border=True):
            st.subheader(":material/Assured_Workload: Parâmetros contratuais por instituição bancária")
            st.caption(
                "Configure a tarifa contratada por boleto (`AUD-03`) e a taxa/modalidade contratada de antecipação de recebíveis (`AUD-04`). Você pode adicionar linhas para novos bancos."
            )
            bank_params_df = db.get_bank_params_df()
            edited_banks = st.data_editor(
                bank_params_df,
                key="editor_bank_params",
                num_rows="dynamic",
                hide_index=True,
                column_config={
                    "banco_nome": st.column_config.TextColumn("Instituição Bancária", required=True),
                    "banco_codigo": st.column_config.TextColumn("Código COMPE", default="000"),
                    "tarifa_boleto_contratada": st.column_config.NumberColumn(
                        "Tarifa Boleto Contratada (R$)",
                        min_value=0.0,
                        step=0.05,
                        format="R$ %.2f",
                    ),
                    "taxa_antecipacao_mensal": st.column_config.NumberColumn(
                        "Taxa Antecipação (% a.m.)",
                        min_value=0.0,
                        step=0.05,
                        format="%.2f%%",
                    ),
                    "modalidade_antecipacao": st.column_config.SelectboxColumn(
                        "Modalidade de Cálculo VP",
                        options=["COMPOSTO", "SIMPLES"],
                        required=True,
                    ),
                    "iof_tarifa_lote": st.column_config.NumberColumn(
                        "Tarifa/IOF Fixo por Lote (R$)",
                        min_value=0.0,
                        step=0.50,
                        format="R$ %.2f",
                    ),
                    "tolerancia_dias_conciliacao": st.column_config.NumberColumn(
                        "Janela Conciliação (Dias)",
                        min_value=0,
                        max_value=15,
                        step=1,
                        format="%d d",
                    ),
                },
                alt="Tabela editável de tarifas de boletos e taxas de antecipação contratadas por banco",
            )
            if st.button(
                "Salvar parâmetros contratuais dos bancos",
                key="btn_save_banks",
                type="primary",
                icon=":material/save:",
            ):
                db.save_bank_params_df(edited_banks)
                st.session_state.flash_msg = (
                    "success",
                    "Parâmetros bancários salvos",
                    "As tarifas de boleto e taxas de antecipação foram atualizadas para as próximas auditorias.",
                )
                st.rerun()

    # --- Subseção 2.3: Contratos de Empréstimo Price / SAC (AUD-05) ---
    else:
        loans_df = db.get_loan_contracts_df()
        col_l_list, col_l_form = st.columns([1.4, 1], gap="large")

        with col_l_list:
            with st.container(border=True):
                st.subheader(":material/request_quote: Contratos de empréstimo cadastrados")
                if loans_df.empty:
                    st.info("Nenhum contrato cadastrado.", title="Sem contratos ativos")
                else:
                    st.dataframe(
                        loans_df,
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

                    selected_ctr_id = st.selectbox(
                        "Inspecionar cronograma teórico Price / SAC do contrato",
                        options=loans_df["id_contrato"].tolist(),
                        format_func=lambda cid: f"{cid} — {loans_df[loans_df['id_contrato'] == cid]['nome_contrato'].iloc[0]}",
                        key="sel_inspect_loan",
                    )
                    ctr_row = loans_df[loans_df["id_contrato"] == selected_ctr_id].iloc[0]
                    sched_df = gerar_tabela_emprestimo(
                        valor_principal=float(ctr_row["valor_principal"]),
                        taxa_juros_mensal_pct=float(ctr_row["taxa_juros_mensal"]),
                        prazo_meses=int(ctr_row["prazo_meses"]),
                        sistema_amortizacao=str(ctr_row["sistema_amortizacao"]),
                        data_primeira_parcela=str(ctr_row["data_primeira_parcela"]),
                    )
                    st.dataframe(
                        sched_df,
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
                    if st.button(
                        f"Remover contrato {selected_ctr_id}",
                        key="btn_del_loan",
                        icon=":material/delete:",
                    ):
                        db.delete_loan_contract(selected_ctr_id)
                        st.rerun()

        with col_l_form:
            with st.container(border=True):
                st.subheader(":material/add_business: Cadastrar / atualizar contrato")
                with st.form("form_loan_contract", border=False):
                    f_id = st.text_input("Código do contrato", placeholder="Ex.: CTR-PRONAMPE-001")
                    f_nome = st.text_input("Nome / identificação", placeholder="Ex.: Pronampe Banco do Brasil #7812")
                    f_banco = st.text_input("Banco credor", value="Itaú Unibanco")
                    c_sis, c_prz = st.columns(2)
                    with c_sis:
                        f_sistema = st.selectbox("Sistema", options=["PRICE", "SAC"])
                    with c_prz:
                        f_prazo = st.number_input("Prazo (meses)", min_value=1, max_value=240, value=24, step=1)
                    c_val, c_tx = st.columns(2)
                    with c_val:
                        f_principal = st.number_input(
                            "Principal contratado (R$)", min_value=100.0, value=100000.0, step=1000.0
                        )
                    with c_tx:
                        f_taxa = st.number_input(
                            "Taxa mensal (% a.m.)", min_value=0.0, max_value=30.0, value=1.50, step=0.05
                        )
                    f_dt1 = st.date_input("Vencimento da 1ª parcela", value=date(2026, 9, 15))
                    f_kw = st.text_input(
                        "Palavra-chave / nº contrato no Omie",
                        placeholder="Ex.: 7812",
                        help="Termo usado para vincular automaticamente as baixas mensais do Omie a este contrato.",
                    )
                    sub_loan = st.form_submit_button(
                        "Salvar contrato de empréstimo",
                        type="primary",
                        icon=":material/save:",
                        width="stretch",
                    )
                    if sub_loan:
                        if not f_id.strip() or not f_nome.strip() or not f_kw.strip():
                            st.warning(
                                "Preencha Código, Nome e Palavra-chave de vínculo no Omie.",
                                title="Campos obrigatórios",
                            )
                        else:
                            db.upsert_loan_contract(
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
                            st.session_state.flash_msg = (
                                "success",
                                "Contrato salvo",
                                f"O contrato '{f_nome}' ({f_sistema}) foi salvo com sucesso.",
                            )
                            st.rerun()

# =============================================================================
# ABA 3: EXECUÇÃO & MATRIZ DE DIVERGÊNCIAS (com @st.fragment e @st.dialog)
# =============================================================================
@st.fragment
def render_interactive_matrix_fragment() -> None:
    """Fragmento reativo: filtra, exporta e abre os modais de tratativa e contestação sem recarregar toda a página."""
    with st.container(border=True):
        st.subheader(":material/table_eye: Matriz interativa de exceções e tratativas")

        f_col1, f_col2, f_col3, f_col4 = st.columns([1.2, 1.2, 1.2, 1.2])
        with f_col1:
            sev_options = ["Crítico", "Alerta", "Informativo"]
            sel_sevs = st.pills(
                "Severidade",
                options=sev_options,
                default=sev_options,
                selection_mode="multi",
                key="flt_severidade",
            )
        with f_col2:
            sel_wf = st.multiselect(
                "Status da tratativa",
                options=WORKFLOW_STATUS_OPTIONS,
                default=["Pendente", "Em Contestação"],
                key="flt_workflow_status",
            )
        with f_col3:
            bank_opts = sorted(latest_divs_df["banco"].dropna().unique().tolist())
            sel_banks = st.multiselect(
                "Banco",
                options=bank_opts,
                default=bank_opts,
                key="flt_banco",
            )
        with f_col4:
            rule_opts = sorted(latest_divs_df["codigo_regra"].dropna().unique().tolist())
            sel_rules = st.multiselect(
                "Regra",
                options=rule_opts,
                default=rule_opts,
                key="flt_regra",
            )

        filtered_divs = latest_divs_df.copy()
        if sel_sevs:
            filtered_divs = filtered_divs[filtered_divs["severidade"].isin(sel_sevs)]
        if sel_wf:
            filtered_divs = filtered_divs[filtered_divs["status_tratativa"].isin(sel_wf)]
        if sel_banks:
            filtered_divs = filtered_divs[filtered_divs["banco"].isin(sel_banks)]
        if sel_rules:
            filtered_divs = filtered_divs[filtered_divs["codigo_regra"].isin(sel_rules)]
        filtered_divs = filtered_divs.reset_index(drop=True)

        exp_c1, exp_c2, exp_c3, exp_c4 = st.columns([1.8, 1.15, 0.9, 0.85], vertical_alignment="center")
        with exp_c1:
            st.caption(
                f"Exibindo **{len(filtered_divs)}** de **{len(latest_divs_df)}** exceções ({format_brl(filtered_divs['valor_divergente'].sum() if not filtered_divs.empty else 0.0)}). **Clique em uma linha para abrir o modal de Inspeção & Tratativa.**"
            )
        with exp_c2:
            if st.button(
                "Dossiê de Estorno Bancário",
                key="btn_open_bank_dossier",
                icon=":material/gavel:",
                width="stretch",
                help="Gera carta formal e memória analítica de tarifas/taxas cobradas acima do contrato.",
            ):
                abrir_modal_dossie_bancario(latest_divs_df)
        with exp_c3:
            xlsx_bytes = gerar_excel_auditoria(
                div_df=filtered_divs,
                summary=latest_summary,
                extrato_df=extrato_df,
                omie_df=omie_df,
            )
            st.download_button(
                "Excel (.xlsx)",
                data=xlsx_bytes,
                file_name=f"auditoria_vertice_{latest_summary['execucao_id']}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                icon=":material/download:",
                width="stretch",
                key="btn_dl_xlsx",
            )
        with exp_c4:
            csv_bytes = gerar_csv_auditoria(filtered_divs)
            st.download_button(
                "CSV (.csv)",
                data=csv_bytes,
                file_name=f"auditoria_vertice_{latest_summary['execucao_id']}.csv",
                mime="text/csv",
                icon=":material/description:",
                width="stretch",
                key="btn_dl_csv",
            )

        selection_event = st.dataframe(
            filtered_divs[
                [
                    "status_tratativa",
                    "severidade",
                    "codigo_regra",
                    "nome_regra",
                    "data_ocorrencia",
                    "banco",
                    "valor_esperado",
                    "valor_realizado",
                    "valor_divergente",
                    "resumo",
                ]
            ],
            key="grid_matriz_divergencias",
            hide_index=True,
            on_select="rerun",
            selection_mode="single-row",
            column_config={
                "status_tratativa": st.column_config.TextColumn("Status", pinned=True),
                "severidade": st.column_config.TextColumn("Severidade", pinned=True),
                "codigo_regra": st.column_config.TextColumn("Regra", pinned=True),
                "nome_regra": st.column_config.TextColumn("Tipo de Divergência"),
                "data_ocorrencia": st.column_config.TextColumn("Data"),
                "banco": st.column_config.TextColumn("Banco"),
                "valor_esperado": st.column_config.NumberColumn("Esperado / ERP (R$)", format="R$ %.2f"),
                "valor_realizado": st.column_config.NumberColumn("Realizado / Banco (R$)", format="R$ %.2f"),
                "valor_divergente": st.column_config.NumberColumn("Divergência (R$)", format="R$ %.2f"),
                "resumo": st.column_config.TextColumn("Síntese Auditável"),
            },
            alt="Tabela interativa de exceções da auditoria com seleção de linha para abrir modal forense",
        )

        selected_rows = selection_event.selection.rows if selection_event and selection_event.selection else []
        if selected_rows and not filtered_divs.empty:
            sel_row = filtered_divs.iloc[selected_rows[0]].to_dict()
            row_sig = str(sel_row.get("assinatura_ocorrencia") or sel_row.get("id_divergencia"))
            c_inline_info, c_inline_btn = st.columns([2.6, 1.2], vertical_alignment="center")
            with c_inline_info:
                st.caption(
                    f"Linha selecionada: **[{sel_row['codigo_regra']}] {sel_row['resumo']}** — Status atual: **{sel_row['status_tratativa']}**"
                )
            with c_inline_btn:
                reopen_clicked = st.button(
                    "Abrir Inspeção & Tratativa",
                    key="btn_reopen_drilldown",
                    type="primary",
                    icon=":material/open_in_new:",
                    width="stretch",
                )
            if st.session_state.last_opened_row_sig != row_sig or reopen_clicked:
                st.session_state.last_opened_row_sig = row_sig
                abrir_modal_drilldown(
                    sel_item=sel_row,
                    db=db,
                    extrato_df=extrato_df,
                    omie_df=omie_df,
                    competencia_ativa=competencia_ativa,
                )
        else:
            st.session_state.last_opened_row_sig = None


with tab_exec:
    with st.container(border=True):
        c_act_info, c_act_btn = st.columns([2.4, 1.2], vertical_alignment="center")
        with c_act_info:
            st.subheader(":material/shield_locked: Central de execução de auditoria")
            st.caption(
                "Cruza extratos e Omie (incluindo lotes 1:N e vínculos manuais), recalcula tarifas, Valor Presente de antecipações, tabelas Price/SAC e regras customizadas."
            )
        with c_act_btn:
            if st.button(
                "Executar Auditoria Completa",
                key="btn_executar_auditoria_completa",
                type="primary",
                icon=":material/play_arrow:",
                width="stretch",
            ):
                if extrato_df.empty and omie_df.empty:
                    st.warning(
                        "Carregue extratos e relatórios na Aba 1 ou clique em 'Carregar dados de demonstração' na barra lateral antes de auditar.",
                        title="Base sem movimentos para auditar",
                        icon=":material/warning:",
                    )
                else:
                    metrics_now, _ = executar_auditoria_completa(db, competencia=competencia_ativa)
                    st.session_state.flash_msg = (
                        "success",
                        f"Auditoria concluída ({metrics_now['execucao_id']})",
                        f"Foram identificadas {metrics_now['total_divergencias']} divergências totalizando {format_brl(metrics_now['valor_total_divergente'])}.",
                    )
                    st.rerun()

    if latest_summary is None:
        st.info(
            "Clique no botão 'Executar Auditoria Completa' acima (ou carregue a massa de demonstração na barra lateral) para gerar os KPIs executivos e a Matriz de Divergências.",
            title="Aguardando primeira execução",
            icon=":material/analytics:",
        )
    else:
        # --- KPIs Executivos (4 cards alinhados incluindo Pendências de Workflow) ---
        pendentes_df = (
            latest_divs_df[latest_divs_df["status_tratativa"].isin(["Pendente", "Em Contestação"])]
            if not latest_divs_df.empty
            else latest_divs_df
        )
        valor_aberto = float(pendentes_df["valor_divergente"].sum()) if not pendentes_df.empty else 0.0
        criticos_abertos = int((pendentes_df["severidade"] == "Crítico").sum()) if not pendentes_df.empty else 0
        resolvidos_cnt = len(latest_divs_df) - len(pendentes_df)

        k1, k2, k3, k4 = st.columns(4)
        k1.metric(
            "Exposição divergente em aberto",
            format_brl(valor_aberto),
            delta=f"Total bruto: {format_brl(latest_summary['valor_total_divergente'])}",
            delta_color="off",
            border=True,
        )
        k2.metric(
            "Alertas críticos pendentes",
            f"{criticos_abertos}",
            delta="Exigem tratativa" if criticos_abertos > 0 else "Zero críticos pendentes",
            delta_color="inverse" if criticos_abertos > 0 else "normal",
            border=True,
        )
        k3.metric(
            "Aderência do ERP Omie",
            format_pct(latest_summary["aderencia_erp_pct"]),
            delta=f"Extrato: {len(extrato_df)} | Omie: {len(omie_df)}",
            delta_color="off",
            border=True,
        )
        k4.metric(
            "Progresso das tratativas",
            f"{resolvidos_cnt}/{len(latest_divs_df)}",
            delta=f"{len(pendentes_df)} pendente(s)",
            delta_color="off",
            border=True,
        )

        if latest_divs_df.empty:
            st.success(
                "Todos os lançamentos auditados no período selecionado estão 100% aderentes às regras ativas e parâmetros contratuais.",
                title="Nenhuma divergência encontrada",
                icon=":material/verified:",
            )
        else:
            ch1, ch2 = st.columns(2)
            with ch1:
                with st.container(border=True):
                    st.markdown("**Impacto financeiro por regra (R$)**")
                    by_rule = (
                        latest_divs_df.groupby("codigo_regra", as_index=False)["valor_divergente"]
                        .sum()
                        .sort_values("valor_divergente", ascending=False)
                    )
                    st.bar_chart(
                        by_rule,
                        x="codigo_regra",
                        y="valor_divergente",
                        color="#C5A059",
                        height=200,
                        alt="Gráfico de barras do valor divergente acumulado por código de regra",
                    )
            with ch2:
                with st.container(border=True):
                    st.markdown("**Exposição divergente por banco (R$)**")
                    by_bank = (
                        latest_divs_df.groupby("banco", as_index=False)["valor_divergente"]
                        .sum()
                        .sort_values("valor_divergente", ascending=False)
                    )
                    st.bar_chart(
                        by_bank,
                        x="banco",
                        y="valor_divergente",
                        color="#2C3531",
                        height=200,
                        alt="Gráfico de barras do valor divergente acumulado por instituição bancária",
                    )

            render_interactive_matrix_fragment()
