"""Contratos e schemas Pandera para validação rigorosa pós-ETL."""

from __future__ import annotations

import pandas as pd
import pandera.pandas as pa

DATE_ISO_REGEX = r"^\d{4}-\d{2}-\d{2}$"

EXTRATO_COLUMNS = [
    "id_extrato",
    "arquivo_origem",
    "banco_codigo",
    "banco_nome",
    "conta",
    "data_lancamento",
    "tipo_movimento",
    "natureza_operacao",
    "descricao_original",
    "descricao_normalizada",
    "documento",
    "valor",
    "valor_absoluto",
    "valor_nominal",
    "valor_liquido",
    "prazo_dias",
    "data_vencimento_original",
]

OMIE_COLUMNS = [
    "id_omie",
    "arquivo_origem",
    "origem_relatorio",
    "banco_nome",
    "conta_corrente",
    "data_lancamento",
    "data_vencimento",
    "status_titulo",
    "tipo_lancamento",
    "categoria",
    "grupo_contabil",
    "cliente_fornecedor",
    "documento",
    "descricao_original",
    "descricao_normalizada",
    "valor",
    "valor_absoluto",
    "valor_nominal",
    "juros_multa",
    "desconto_tarifa",
    "contrato_ref",
]

DIVERGENCIA_COLUMNS = [
    "id_divergencia",
    "execucao_id",
    "data_ocorrencia",
    "codigo_regra",
    "nome_regra",
    "categoria",
    "severidade",
    "banco",
    "valor_esperado",
    "valor_realizado",
    "valor_divergente",
    "resumo",
    "memoria_calculo",
    "id_extrato",
    "id_omie",
    "detalhe_json",
]

ExtratoBancarioSchema = pa.DataFrameSchema(
    columns={
        "id_extrato": pa.Column(str, nullable=False, checks=pa.Check.str_length(min_value=4)),
        "arquivo_origem": pa.Column(str, nullable=False),
        "banco_codigo": pa.Column(str, nullable=False),
        "banco_nome": pa.Column(str, nullable=False),
        "conta": pa.Column(str, nullable=False),
        "data_lancamento": pa.Column(
            str,
            nullable=False,
            checks=pa.Check.str_matches(DATE_ISO_REGEX),
        ),
        "tipo_movimento": pa.Column(
            str,
            nullable=False,
            checks=pa.Check.isin(["CREDITO", "DEBITO"]),
        ),
        "natureza_operacao": pa.Column(str, nullable=False),
        "descricao_original": pa.Column(str, nullable=False),
        "descricao_normalizada": pa.Column(str, nullable=False),
        "documento": pa.Column(str, nullable=False),
        "valor": pa.Column(float, coerce=True, nullable=False),
        "valor_absoluto": pa.Column(
            float,
            coerce=True,
            nullable=False,
            checks=pa.Check.ge(0.0),
        ),
        "valor_nominal": pa.Column(float, coerce=True, nullable=True),
        "valor_liquido": pa.Column(float, coerce=True, nullable=True),
        "prazo_dias": pa.Column(float, coerce=True, nullable=True),
        "data_vencimento_original": pa.Column(str, nullable=True),
    },
    coerce=True,
    strict=False,
)

LancamentoOmieSchema = pa.DataFrameSchema(
    columns={
        "id_omie": pa.Column(str, nullable=False, checks=pa.Check.str_length(min_value=3)),
        "arquivo_origem": pa.Column(str, nullable=False),
        "origem_relatorio": pa.Column(str, nullable=False),
        "banco_nome": pa.Column(str, nullable=False),
        "conta_corrente": pa.Column(str, nullable=False),
        "data_lancamento": pa.Column(
            str,
            nullable=False,
            checks=pa.Check.str_matches(DATE_ISO_REGEX),
        ),
        "data_vencimento": pa.Column(str, nullable=True),
        "status_titulo": pa.Column(
            str,
            nullable=False,
            checks=pa.Check.isin(["BAIXADO", "CONCILIADO", "ABERTO", "ATRASADO", "CANCELADO"]),
        ),
        "tipo_lancamento": pa.Column(
            str,
            nullable=False,
            checks=pa.Check.isin(["ENTRADA", "SAIDA"]),
        ),
        "categoria": pa.Column(str, nullable=False),
        "grupo_contabil": pa.Column(str, nullable=False),
        "cliente_fornecedor": pa.Column(str, nullable=False),
        "documento": pa.Column(str, nullable=False),
        "descricao_original": pa.Column(str, nullable=False),
        "descricao_normalizada": pa.Column(str, nullable=False),
        "valor": pa.Column(float, coerce=True, nullable=False),
        "valor_absoluto": pa.Column(
            float,
            coerce=True,
            nullable=False,
            checks=pa.Check.ge(0.0),
        ),
        "valor_nominal": pa.Column(float, coerce=True, nullable=True),
        "juros_multa": pa.Column(float, coerce=True, nullable=False),
        "desconto_tarifa": pa.Column(float, coerce=True, nullable=False),
        "contrato_ref": pa.Column(str, nullable=True),
    },
    coerce=True,
    strict=False,
)

DivergenciaSchema = pa.DataFrameSchema(
    columns={
        "id_divergencia": pa.Column(str, nullable=False),
        "execucao_id": pa.Column(str, nullable=False),
        "data_ocorrencia": pa.Column(str, nullable=False),
        "codigo_regra": pa.Column(str, nullable=False),
        "nome_regra": pa.Column(str, nullable=False),
        "categoria": pa.Column(str, nullable=False),
        "severidade": pa.Column(
            str,
            nullable=False,
            checks=pa.Check.isin(["Crítico", "Alerta", "Informativo"]),
        ),
        "banco": pa.Column(str, nullable=False),
        "valor_esperado": pa.Column(float, coerce=True, nullable=False),
        "valor_realizado": pa.Column(float, coerce=True, nullable=False),
        "valor_divergente": pa.Column(
            float,
            coerce=True,
            nullable=False,
            checks=pa.Check.ge(0.0),
        ),
        "resumo": pa.Column(str, nullable=False),
        "memoria_calculo": pa.Column(str, nullable=False),
        "id_extrato": pa.Column(str, nullable=True),
        "id_omie": pa.Column(str, nullable=True),
        "detalhe_json": pa.Column(str, nullable=False),
    },
    coerce=True,
    strict=False,
)


def validate_extrato_df(df: pd.DataFrame) -> pd.DataFrame:
    """Garante colunas, tipos e valida regras Pandera para o extrato bancário."""
    if df.empty:
        return pd.DataFrame(columns=EXTRATO_COLUMNS)
    ordered = df.copy()
    for col in EXTRATO_COLUMNS:
        if col not in ordered.columns:
            ordered[col] = None
    ordered = ordered[EXTRATO_COLUMNS]
    return ExtratoBancarioSchema.validate(ordered)


def validate_omie_df(df: pd.DataFrame) -> pd.DataFrame:
    """Garante colunas, tipos e valida regras Pandera para os lançamentos Omie."""
    if df.empty:
        return pd.DataFrame(columns=OMIE_COLUMNS)
    ordered = df.copy()
    for col in OMIE_COLUMNS:
        if col not in ordered.columns:
            ordered[col] = None
    ordered = ordered[OMIE_COLUMNS]
    return LancamentoOmieSchema.validate(ordered)


def validate_divergencia_df(df: pd.DataFrame) -> pd.DataFrame:
    """Valida a matriz final de divergências gerada pelo motor de auditoria."""
    if df.empty:
        return pd.DataFrame(columns=DIVERGENCIA_COLUMNS)
    ordered = df.copy()
    for col in DIVERGENCIA_COLUMNS:
        if col not in ordered.columns:
            ordered[col] = None
    ordered = ordered[DIVERGENCIA_COLUMNS]
    return DivergenciaSchema.validate(ordered)
