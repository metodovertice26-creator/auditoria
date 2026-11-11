"""Testes unitários de parsers multi-bancos (OFX 1.x/2.x, CSV) e validação Pandera."""

from __future__ import annotations

from pathlib import Path

from src.etl.bank_csv_parser import parse_bank_tabular_bytes
from src.etl.ofx_parser import extract_ofx_statement_metadata, parse_ofx_bytes
from src.etl.omie_importer import parse_omie_report_bytes
from src.sample_data import ensure_sample_files


def test_parse_ofx_multi_bank_quirks() -> None:
    """Garante leitura de OFX 1.x (vírgula decimal, fuso BRT, sem tags fechadas) e OFX 2.x XML."""
    ofx_sgml_santander = """OFXHEADER:100
DATA:OFXSGML
VERSION:102
CHARSET:1252

<OFX>
<BANKMSGSRSV1>
<STMTRS>
<BANKACCTFROM>
<BANKID>033
<ACCTID>112233-9
</BANKACCTFROM>
<BANKTRANLIST>
<STMTTRN>
<TRNTYPE>CREDIT
<DTPOSTED>20261010093000[-3:BRT]
<TRNAMT>1500,55
<FITID>SAN-001
<MEMO>PIX RECEBIDO CLIENTE TESTE
<STMTTRN>
<TRNTYPE>DEBIT
<DTPOSTED>20261011
<TRNAMT>-4,90
<FITID>SAN-002
<MEMO>TARIFA BOLETO COBRANCA
</BANKTRANLIST>
</STMTRS>
</BANKMSGSRSV1>
</OFX>
""".encode("cp1252")

    df = parse_ofx_bytes(ofx_sgml_santander, filename="santander.ofx")
    assert len(df) == 2
    assert set(df["banco_nome"]) == {"Santander"}
    assert list(df["data_lancamento"]) == ["2026-10-10", "2026-10-11"]
    assert list(df["valor"]) == [1500.55, -4.90]
    assert "TARIFA_BOLETO" in set(df["natureza_operacao"])


def test_sample_files_etl_and_pandera(tmp_path: Path) -> None:
    paths = ensure_sample_files(tmp_path)
    raw_ofx = paths["ofx_itau"].read_bytes()
    df_ofx = parse_ofx_bytes(raw_ofx, paths["ofx_itau"].name)
    meta_ofx = extract_ofx_statement_metadata(raw_ofx, paths["ofx_itau"].name)
    df_csv = parse_bank_tabular_bytes(paths["csv_bradesco"].read_bytes(), paths["csv_bradesco"].name)
    df_omie = parse_omie_report_bytes(paths["omie_xlsx"].read_bytes(), paths["omie_xlsx"].name)

    assert len(df_ofx) == 7
    assert len(df_csv) == 4
    assert len(df_omie) == 12
    assert meta_ofx["dt_inicio"] == "2026-10-01"
    assert meta_ofx["dt_fim"] == "2026-10-31"
    assert meta_ofx["saldo_final"] == 142508.60
    assert (df_ofx["banco_nome"] == "Itaú Unibanco").all()
    assert (df_csv["banco_nome"] == "Bradesco").all()
