"""Testes de matemática financeira, regras AUD-01..AUD-05, SQL customizado e exportação."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.audit.engine import (
    build_sql_from_visual_rule,
    executar_auditoria_completa,
    preview_custom_sql_rule,
    validate_custom_sql_safety,
)
from src.audit.exporter import (
    gerar_csv_auditoria,
    gerar_dossie_contestacao_bancaria,
    gerar_excel_auditoria,
)
from src.audit.finance_math import (
    calcular_divergencia_boleto,
    calcular_vp_antecipacao,
    gerar_tabela_emprestimo,
)
from src.database import VerticeDB
from src.etl.bank_csv_parser import parse_bank_tabular_bytes
from src.etl.ofx_parser import parse_ofx_bytes
from src.etl.omie_importer import parse_omie_report_bytes
from src.sample_data import ensure_sample_files


def test_financial_math_boleto_antecipacao_price_sac() -> None:
    bol = calcular_divergencia_boleto(valor_nominal=1000.0, valor_liquido=993.10, tarifa_contratada=2.45)
    assert bol["divergente"] is True
    assert bol["tarifa_cobrada"] == 6.90
    assert bol["delta_indevido"] == 4.45

    vp_comp = calcular_vp_antecipacao(
        valor_nominal=60000.0,
        valor_liquido_creditado=57420.0,
        prazo_dias=45,
        taxa_mensal_pct=1.85,
        modalidade="COMPOSTO",
    )
    assert vp_comp["divergente"] is True
    assert vp_comp["vp_teorico"] > 58000.0
    assert vp_comp["spread_oculto_pp"] > 0.5

    vp_simp = calcular_vp_antecipacao(
        valor_nominal=10000.0,
        valor_liquido_creditado=9800.0,
        prazo_dias=30,
        taxa_mensal_pct=2.0,
        modalidade="SIMPLES",
    )
    assert vp_simp["vp_teorico"] == 9800.0
    assert vp_simp["divergente"] is False

    price_df = gerar_tabela_emprestimo(120000.0, 1.65, 12, "PRICE", "2026-08-15")
    assert len(price_df) == 12
    assert price_df.iloc[-1]["saldo_final"] == 0.0
    assert round(float(price_df["amortizacao_teorica"].sum()), 2) == 120000.0

    sac_df = gerar_tabela_emprestimo(180000.0, 1.40, 18, "SAC", "2026-09-20")
    assert len(sac_df) == 18
    assert sac_df.iloc[0]["amortizacao_teorica"] == 10000.0
    assert sac_df.iloc[-1]["saldo_final"] == 0.0


def test_full_audit_engine_covers_aud01_to_aud05_and_custom_sql(tmp_path: Path) -> None:
    paths = ensure_sample_files(tmp_path)
    db = VerticeDB(
        db_path=tmp_path / "test_vertice.duckdb",
        rules_json_path=tmp_path / "regras.json",
    )

    df_ofx = parse_ofx_bytes(paths["ofx_itau"].read_bytes(), paths["ofx_itau"].name)
    df_csv = parse_bank_tabular_bytes(paths["csv_bradesco"].read_bytes(), paths["csv_bradesco"].name)
    df_omie = parse_omie_report_bytes(paths["omie_xlsx"].read_bytes(), paths["omie_xlsx"].name)

    db.upsert_extrato(pd.concat([df_ofx, df_csv], ignore_index=True))
    db.upsert_omie(df_omie)

    # Cadastra regra SQL customizada exatamente no formato do exemplo de INSTRUÇÕES.MD
    cus_code = db.add_custom_rule(
        nome="Divergência nominal > R$ 5",
        descricao="Detecta pares onde extrato e Omie diferem em mais de R$ 5,00",
        condicao_sql="ABS(e.valor - o.valor) > 5.0 AND o.id_omie IS NOT NULL",
        severidade="Informativo",
        categoria="Conciliação Bancária",
    )
    assert cus_code == "CUS-01"

    metrics, divs = executar_auditoria_completa(db)
    triggered_codes = set(divs["codigo_regra"].unique())

    assert {"AUD-01", "AUD-02", "AUD-03", "AUD-04", "AUD-05", "CUS-01"}.issubset(triggered_codes)
    assert metrics["valor_total_divergente"] > 0
    assert metrics["alertas_criticos"] >= 3

    # Segurança contra SQL destrutivo
    ok, _ = validate_custom_sql_safety("DROP TABLE extrato_bancario")
    assert ok is False
    ok_prev, _, prev_df = preview_custom_sql_rule(db, "e.valor_absoluto > 10000")
    assert ok_prev is True
    assert not prev_df.empty

    # Exportação Excel, CSV e Dossiê de Contestação Bancária
    xlsx_bytes = gerar_excel_auditoria(divs, summary=metrics, extrato_df=df_ofx, omie_df=df_omie)
    csv_bytes = gerar_csv_auditoria(divs)
    carta_md, claims_df = gerar_dossie_contestacao_bancaria(divs)
    assert len(xlsx_bytes) > 2000
    assert b"AUD-04" in csv_bytes
    assert not claims_df.empty
    assert "ESTORNO" in carta_md.upper()

    # Verifica conciliação 1:N (Lote PIX 5.400 = OM-2004A 3.100 + OM-2004B 2.300 não deve cair como órfão/fantasma)
    assert "OM-2004A" not in set(divs["id_omie"].dropna())
    assert "OM-2004B" not in set(divs["id_omie"].dropna())


def test_file_ledger_gaps_workflow_and_visual_sql(tmp_path: Path) -> None:
    db = VerticeDB(
        db_path=tmp_path / "gov_vertice.duckdb",
        rules_json_path=tmp_path / "gov_regras.json",
    )
    sql_vis = build_sql_from_visual_rule("Valor absoluto no Extrato (R$)", "Maior que (>)", 5000.0)
    assert sql_vis == "e.valor_absoluto > 5000.00"

    # Simula dois lotes OFX da mesma conta com lacuna entre 2026-10-10 e 2026-10-14 (3 dias faltantes: 11, 12, 13)
    dummy_df1 = pd.DataFrame([{"banco_nome": "Itaú Unibanco", "conta": "45892-7", "data_lancamento": "2026-10-05", "valor": 100.0}])
    dummy_df2 = pd.DataFrame([{"banco_nome": "Itaú Unibanco", "conta": "45892-7", "data_lancamento": "2026-10-15", "valor": 200.0}])
    db.register_imported_file(b"lote-1", "itau_parte1.ofx", "EXTRATO", dummy_df1, ofx_meta={"dt_inicio": "2026-10-01", "dt_fim": "2026-10-10", "saldo_final": 1000.0})
    db.register_imported_file(b"lote-2", "itau_parte2.ofx", "EXTRATO", dummy_df2, ofx_meta={"dt_inicio": "2026-10-14", "dt_fim": "2026-10-31", "saldo_final": 1200.0})

    gaps = db.detect_statement_gaps()
    assert len(gaps) == 1
    assert gaps[0]["dias_faltantes"] == 3
    assert gaps[0]["gap_inicio"] == "2026-10-11"
    assert gaps[0]["gap_fim"] == "2026-10-13"

    # Persistência de Workflow
    sig = VerticeDB.build_issue_signature("AUD-03", "2026-10-08", "Itaú Unibanco", "EXT-1", "OM-1", "Tarifa boleto")
    db.save_issue_workflow(sig, "Em Contestação", "Protocolo #12345", "Daniel")
    wf = db.get_workflow_map()
    assert wf[sig]["status_tratativa"] == "Em Contestação"
    assert wf[sig]["justificativa"] == "Protocolo #12345"
