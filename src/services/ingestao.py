"""Serviço de ingestão: orquestra parsers ETL e persistência DuckDB para qualquer interface (Web, CLI, testes)."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Iterable, Literal

import pandas as pd
from pydantic import BaseModel, Field

from src.audit.engine import executar_auditoria_completa
from src.database import VerticeDB
from src.etl.bank_csv_parser import parse_bank_tabular_bytes
from src.etl.ofx_parser import extract_ofx_statement_metadata, parse_ofx_bytes
from src.etl.omie_api import OmieApiClient
from src.etl.omie_importer import parse_omie_report_bytes
from src.sample_data import ensure_sample_files

OrigemOmie = Literal["MOVIMENTO_CC", "BAIXAS", "CONTAS_RECEBER", "CONTAS_PAGAR"]

ORIGENS_OMIE: dict[str, OrigemOmie] = {
    "Movimento de Conta Corrente": "MOVIMENTO_CC",
    "Baixas de Títulos": "BAIXAS",
    "Contas a Receber": "CONTAS_RECEBER",
    "Contas a Pagar": "CONTAS_PAGAR",
}

BANCOS_SUPORTADOS: list[str] = [
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
]

EXTENSOES_LOCAIS = (".ofx", ".csv", ".xlsx")


class ResultadoImportacao(BaseModel):
    """Contrato de retorno de uma operação de importação."""

    inseridos: int = Field(default=0, ge=0)
    arquivos_ok: list[str] = Field(default_factory=list)
    duplicados: list[str] = Field(default_factory=list)
    erros: list[str] = Field(default_factory=list)

    @property
    def sucesso(self) -> bool:
        return bool(self.arquivos_ok)


ArquivoEntrada = tuple[str, bytes]


def _parse_extrato(nome: str, raw: bytes, bank_override: str | None) -> tuple[pd.DataFrame, dict | None]:
    if nome.lower().endswith(".ofx"):
        df = parse_ofx_bytes(raw, filename=nome, bank_override=bank_override)
        return df, extract_ofx_statement_metadata(raw, filename=nome)
    return parse_bank_tabular_bytes(raw, filename=nome, bank_override=bank_override), None


def importar_extratos(
    db: VerticeDB,
    arquivos: Iterable[ArquivoEntrada],
    bank_override: str | None = None,
) -> ResultadoImportacao:
    """Normaliza extratos OFX/CSV/XLSX, registra o lote (SHA-256) e grava no DuckDB."""
    resultado = ResultadoImportacao()
    frames: list[pd.DataFrame] = []
    for nome, raw in arquivos:
        try:
            df, ofx_meta = _parse_extrato(nome, raw, bank_override)
            reg = db.register_imported_file(raw, nome, "EXTRATO", df, ofx_meta=ofx_meta)
            if reg.get("duplicado"):
                resultado.duplicados.append(nome)
            frames.append(df)
            resultado.arquivos_ok.append(nome)
        except Exception as exc:  # erro técnico reportado por arquivo, sem interromper o lote
            resultado.erros.append(f"{nome}: {exc}")
    if frames:
        resultado.inseridos = db.upsert_extrato(pd.concat(frames, ignore_index=True))
    return resultado


def importar_relatorios_omie(
    db: VerticeDB,
    arquivos: Iterable[ArquivoEntrada],
    origem_relatorio: OrigemOmie,
) -> ResultadoImportacao:
    """Normaliza relatórios exportados do Omie e grava no DuckDB."""
    resultado = ResultadoImportacao()
    frames: list[pd.DataFrame] = []
    for nome, raw in arquivos:
        try:
            df = parse_omie_report_bytes(raw, filename=nome, origem_relatorio=origem_relatorio)
            db.register_imported_file(raw, nome, "OMIE", df)
            frames.append(df)
            resultado.arquivos_ok.append(nome)
        except Exception as exc:
            resultado.erros.append(f"{nome}: {exc}")
    if frames:
        resultado.inseridos = db.upsert_omie(pd.concat(frames, ignore_index=True))
    return resultado


def sincronizar_omie_api(db: VerticeDB, app_key: str, app_secret: str, dt_ini: date, dt_fim: date) -> int:
    """Importa movimentos financeiros diretamente da API REST do Omie."""
    client = OmieApiClient(app_key=app_key, app_secret=app_secret)
    df_api = client.importar_movimentos_financeiros(
        data_inicio_br=dt_ini.strftime("%d/%m/%Y"),
        data_fim_br=dt_fim.strftime("%d/%m/%Y"),
    )
    return db.upsert_omie(df_api)


def descobrir_arquivos_locais(base_dir: Path) -> list[Path]:
    """Localiza arquivos .ofx, .csv e .xlsx na raiz da pasta do projeto."""
    found = {
        p.resolve(): p
        for p in base_dir.iterdir()
        if p.is_file() and p.suffix.lower() in EXTENSOES_LOCAIS and not p.name.startswith("~$")
    }
    return sorted(found.values(), key=lambda x: x.name.lower())


def importar_arquivo_local(db: VerticeDB, caminho: Path, origem: OrigemOmie | Literal["EXTRATO"]) -> ResultadoImportacao:
    """Importa um arquivo da pasta do projeto como extrato bancário ou relatório Omie."""
    entrada = [(caminho.name, caminho.read_bytes())]
    if origem == "EXTRATO":
        return importar_extratos(db, entrada)
    return importar_relatorios_omie(db, entrada, origem_relatorio=origem)


def carregar_demonstracao(db: VerticeDB, competencia: str | None = None) -> tuple[int, int]:
    """Substitui os movimentos pela massa de demonstração e executa a auditoria. Retorna (extratos, omie)."""
    paths = ensure_sample_files()
    db.clear_movements("ALL")
    res_ext = importar_extratos(
        db,
        [(paths["ofx_itau"].name, paths["ofx_itau"].read_bytes()), (paths["csv_bradesco"].name, paths["csv_bradesco"].read_bytes())],
    )
    res_omie = importar_relatorios_omie(
        db,
        [(paths["omie_xlsx"].name, paths["omie_xlsx"].read_bytes())],
        origem_relatorio="MOVIMENTO_CC",
    )
    executar_auditoria_completa(db, competencia=competencia)
    return res_ext.inseridos, res_omie.inseridos
