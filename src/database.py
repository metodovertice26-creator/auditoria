"""Camada de persistência e motor analítico DuckDB da Auditoria Vértice."""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

import duckdb
import pandas as pd

from src.schemas import (
    DIVERGENCIA_COLUMNS,
    EXTRATO_COLUMNS,
    OMIE_COLUMNS,
    validate_divergencia_df,
    validate_extrato_df,
    validate_omie_df,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "vertice_auditoria.duckdb"
DEFAULT_RULES_JSON_PATH = PROJECT_ROOT / "data" / "regras_customizadas.json"

DEFAULT_AUDIT_RULES: list[dict[str, Any]] = [
    {
        "codigo": "AUD-01",
        "nome": "Lançamentos Órfãos no Omie",
        "descricao": "Lançamentos baixados/conciliados no ERP Omie sem correspondência financeira no extrato bancário.",
        "categoria": "Conciliação Bancária",
        "severidade": "Crítico",
        "tipo_regra": "NATIVA",
        "condicao_sql": None,
        "ativa": True,
    },
    {
        "codigo": "AUD-02",
        "nome": "Lançamentos Fantasmas no Extrato",
        "descricao": "Movimentações efetivadas no extrato bancário sem respectiva baixa ou registro no ERP Omie.",
        "categoria": "Conciliação Bancária",
        "severidade": "Crítico",
        "tipo_regra": "NATIVA",
        "condicao_sql": None,
        "ativa": True,
    },
    {
        "codigo": "AUD-03",
        "nome": "Divergência em Tarifas de Boletos",
        "descricao": "Tarifa efetivamente cobrada na liquidação/compensação de boletos superior à tarifa contratada por banco.",
        "categoria": "Tarifas Bancárias",
        "severidade": "Alerta",
        "tipo_regra": "NATIVA",
        "condicao_sql": None,
        "ativa": True,
    },
    {
        "codigo": "AUD-04",
        "nome": "Desconto Indevido em Antecipação de Recebíveis",
        "descricao": "Diferença superior a R$ 0,10 entre o Valor Presente (VP) teórico contratado e o valor líquido creditado pelo banco.",
        "categoria": "Operações de Crédito & Recebíveis",
        "severidade": "Crítico",
        "tipo_regra": "NATIVA",
        "condicao_sql": None,
        "ativa": True,
    },
    {
        "codigo": "AUD-05",
        "nome": "Conformidade de Contratos de Empréstimos (Price / SAC)",
        "descricao": "Auditoria das parcelas pagas frente à tabela Price/SAC teórica e verificação da alocação entre Passivo (Principal) e Despesa Financeira (Juros).",
        "categoria": "Endividamento & Contabilidade",
        "severidade": "Crítico",
        "tipo_regra": "NATIVA",
        "condicao_sql": None,
        "ativa": True,
    },
]

DEFAULT_BANK_PARAMS: list[dict[str, Any]] = [
    {
        "banco_codigo": "341",
        "banco_nome": "Itaú Unibanco",
        "tarifa_boleto_contratada": 2.45,
        "taxa_antecipacao_mensal": 1.85,
        "modalidade_antecipacao": "COMPOSTO",
        "iof_tarifa_lote": 0.0,
        "tolerancia_dias_conciliacao": 2,
    },
    {
        "banco_codigo": "237",
        "banco_nome": "Bradesco",
        "tarifa_boleto_contratada": 2.30,
        "taxa_antecipacao_mensal": 1.95,
        "modalidade_antecipacao": "SIMPLES",
        "iof_tarifa_lote": 0.0,
        "tolerancia_dias_conciliacao": 2,
    },
    {
        "banco_codigo": "001",
        "banco_nome": "Banco do Brasil",
        "tarifa_boleto_contratada": 2.15,
        "taxa_antecipacao_mensal": 1.75,
        "modalidade_antecipacao": "COMPOSTO",
        "iof_tarifa_lote": 0.0,
        "tolerancia_dias_conciliacao": 2,
    },
    {
        "banco_codigo": "033",
        "banco_nome": "Santander",
        "tarifa_boleto_contratada": 2.60,
        "taxa_antecipacao_mensal": 2.05,
        "modalidade_antecipacao": "SIMPLES",
        "iof_tarifa_lote": 0.0,
        "tolerancia_dias_conciliacao": 2,
    },
    {
        "banco_codigo": "077",
        "banco_nome": "Banco Inter",
        "tarifa_boleto_contratada": 0.00,
        "taxa_antecipacao_mensal": 1.90,
        "modalidade_antecipacao": "COMPOSTO",
        "iof_tarifa_lote": 0.0,
        "tolerancia_dias_conciliacao": 2,
    },
    {
        "banco_codigo": "403",
        "banco_nome": "Cora",
        "tarifa_boleto_contratada": 0.00,
        "taxa_antecipacao_mensal": 2.10,
        "modalidade_antecipacao": "COMPOSTO",
        "iof_tarifa_lote": 0.0,
        "tolerancia_dias_conciliacao": 2,
    },
]

DEFAULT_LOAN_CONTRACTS: list[dict[str, Any]] = [
    {
        "id_contrato": "CTR-GIRO-341",
        "nome_contrato": "Capital de Giro Empresarial Itaú #9941",
        "banco_nome": "Itaú Unibanco",
        "sistema_amortizacao": "PRICE",
        "valor_principal": 120000.00,
        "taxa_juros_mensal": 1.65,
        "prazo_meses": 12,
        "data_primeira_parcela": "2026-08-15",
        "palavra_chave_omie": "9941",
        "ativo": True,
    },
    {
        "id_contrato": "CTR-SAC-237",
        "nome_contrato": "Financiamento Expansão Bradesco #4410",
        "banco_nome": "Bradesco",
        "sistema_amortizacao": "SAC",
        "valor_principal": 180000.00,
        "taxa_juros_mensal": 1.40,
        "prazo_meses": 18,
        "data_primeira_parcela": "2026-09-20",
        "palavra_chave_omie": "4410",
        "ativo": True,
    },
]


class VerticeDB:
    """Gerenciador DuckDB para armazenamento local e processamento analítico em SQL."""

    def __init__(
        self,
        db_path: Path | str = DEFAULT_DB_PATH,
        rules_json_path: Path | str = DEFAULT_RULES_JSON_PATH,
    ) -> None:
        self.db_path = str(db_path)
        self.rules_json_path = Path(rules_json_path)
        self._mem_con: duckdb.DuckDBPyConnection | None = None
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        else:
            self._mem_con = duckdb.connect(":memory:")
        self.rules_json_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def connect(self) -> Iterator[duckdb.DuckDBPyConnection]:
        if self._mem_con is not None:
            yield self._mem_con
        else:
            con = duckdb.connect(self.db_path)
            try:
                yield con
            finally:
                con.close()

    def _init_schema(self) -> None:
        with self.connect() as con:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS extrato_bancario (
                    id_extrato VARCHAR PRIMARY KEY,
                    arquivo_origem VARCHAR NOT NULL,
                    banco_codigo VARCHAR NOT NULL,
                    banco_nome VARCHAR NOT NULL,
                    conta VARCHAR NOT NULL,
                    data_lancamento VARCHAR NOT NULL,
                    tipo_movimento VARCHAR NOT NULL,
                    natureza_operacao VARCHAR NOT NULL,
                    descricao_original VARCHAR NOT NULL,
                    descricao_normalizada VARCHAR NOT NULL,
                    documento VARCHAR NOT NULL,
                    valor DOUBLE NOT NULL,
                    valor_absoluto DOUBLE NOT NULL,
                    valor_nominal DOUBLE,
                    valor_liquido DOUBLE,
                    prazo_dias DOUBLE,
                    data_vencimento_original VARCHAR,
                    importado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS lancamentos_omie (
                    id_omie VARCHAR PRIMARY KEY,
                    arquivo_origem VARCHAR NOT NULL,
                    origem_relatorio VARCHAR NOT NULL,
                    banco_nome VARCHAR NOT NULL,
                    conta_corrente VARCHAR NOT NULL,
                    data_lancamento VARCHAR NOT NULL,
                    data_vencimento VARCHAR,
                    status_titulo VARCHAR NOT NULL,
                    tipo_lancamento VARCHAR NOT NULL,
                    categoria VARCHAR NOT NULL,
                    grupo_contabil VARCHAR NOT NULL,
                    cliente_fornecedor VARCHAR NOT NULL,
                    documento VARCHAR NOT NULL,
                    descricao_original VARCHAR NOT NULL,
                    descricao_normalizada VARCHAR NOT NULL,
                    valor DOUBLE NOT NULL,
                    valor_absoluto DOUBLE NOT NULL,
                    valor_nominal DOUBLE,
                    juros_multa DOUBLE NOT NULL DEFAULT 0.0,
                    desconto_tarifa DOUBLE NOT NULL DEFAULT 0.0,
                    contrato_ref VARCHAR,
                    importado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS parametros_bancos (
                    banco_nome VARCHAR PRIMARY KEY,
                    banco_codigo VARCHAR NOT NULL,
                    tarifa_boleto_contratada DOUBLE NOT NULL DEFAULT 0.0,
                    taxa_antecipacao_mensal DOUBLE NOT NULL DEFAULT 0.0,
                    modalidade_antecipacao VARCHAR NOT NULL DEFAULT 'COMPOSTO',
                    iof_tarifa_lote DOUBLE NOT NULL DEFAULT 0.0,
                    tolerancia_dias_conciliacao INTEGER NOT NULL DEFAULT 2
                );

                CREATE TABLE IF NOT EXISTS contratos_emprestimo (
                    id_contrato VARCHAR PRIMARY KEY,
                    nome_contrato VARCHAR NOT NULL,
                    banco_nome VARCHAR NOT NULL,
                    sistema_amortizacao VARCHAR NOT NULL,
                    valor_principal DOUBLE NOT NULL,
                    taxa_juros_mensal DOUBLE NOT NULL,
                    prazo_meses INTEGER NOT NULL,
                    data_primeira_parcela VARCHAR NOT NULL,
                    palavra_chave_omie VARCHAR NOT NULL,
                    ativo BOOLEAN NOT NULL DEFAULT TRUE
                );

                CREATE TABLE IF NOT EXISTS catalogo_regras (
                    codigo VARCHAR PRIMARY KEY,
                    nome VARCHAR NOT NULL,
                    descricao VARCHAR NOT NULL,
                    categoria VARCHAR NOT NULL,
                    severidade VARCHAR NOT NULL,
                    tipo_regra VARCHAR NOT NULL,
                    condicao_sql VARCHAR,
                    ativa BOOLEAN NOT NULL DEFAULT TRUE,
                    criada_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS pares_conciliados (
                    id_extrato VARCHAR NOT NULL,
                    id_omie VARCHAR NOT NULL,
                    metodo_pareamento VARCHAR NOT NULL,
                    diferenca_valor DOUBLE NOT NULL,
                    dias_defasagem INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS historico_execucoes (
                    execucao_id VARCHAR PRIMARY KEY,
                    executado_em VARCHAR NOT NULL,
                    total_extrato INTEGER NOT NULL,
                    total_omie INTEGER NOT NULL,
                    total_divergencias INTEGER NOT NULL,
                    alertas_criticos INTEGER NOT NULL,
                    valor_total_divergente DOUBLE NOT NULL,
                    aderencia_erp_pct DOUBLE NOT NULL
                );

                CREATE TABLE IF NOT EXISTS divergencias_auditoria (
                    id_divergencia VARCHAR PRIMARY KEY,
                    execucao_id VARCHAR NOT NULL,
                    data_ocorrencia VARCHAR NOT NULL,
                    codigo_regra VARCHAR NOT NULL,
                    nome_regra VARCHAR NOT NULL,
                    categoria VARCHAR NOT NULL,
                    severidade VARCHAR NOT NULL,
                    banco VARCHAR NOT NULL,
                    valor_esperado DOUBLE NOT NULL,
                    valor_realizado DOUBLE NOT NULL,
                    valor_divergente DOUBLE NOT NULL,
                    resumo VARCHAR NOT NULL,
                    memoria_calculo VARCHAR NOT NULL,
                    id_extrato VARCHAR,
                    id_omie VARCHAR,
                    detalhe_json VARCHAR NOT NULL
                );

                CREATE TABLE IF NOT EXISTS historico_arquivos (
                    id_arquivo VARCHAR PRIMARY KEY,
                    hash_sha256 VARCHAR NOT NULL,
                    nome_arquivo VARCHAR NOT NULL,
                    tipo_fonte VARCHAR NOT NULL,
                    banco_nome VARCHAR NOT NULL,
                    conta VARCHAR NOT NULL,
                    dt_inicio VARCHAR,
                    dt_fim VARCHAR,
                    qtd_registros INTEGER NOT NULL,
                    soma_movimentos DOUBLE NOT NULL,
                    saldo_final_ofx DOUBLE,
                    importado_em VARCHAR NOT NULL
                );

                CREATE TABLE IF NOT EXISTS tratativas_divergencias (
                    assinatura_ocorrencia VARCHAR PRIMARY KEY,
                    status_tratativa VARCHAR NOT NULL DEFAULT 'Pendente',
                    justificativa VARCHAR NOT NULL DEFAULT '',
                    responsavel VARCHAR NOT NULL DEFAULT 'Analista Financeiro',
                    atualizado_em VARCHAR NOT NULL
                );

                CREATE TABLE IF NOT EXISTS vinculos_manuais (
                    id_extrato VARCHAR NOT NULL,
                    id_omie VARCHAR NOT NULL,
                    observacao VARCHAR NOT NULL DEFAULT '',
                    criado_em VARCHAR NOT NULL,
                    PRIMARY KEY (id_extrato, id_omie)
                );
                """
            )
            self._create_analytical_views(con)
            self._seed_defaults(con)

    def _create_analytical_views(self, con: duckdb.DuckDBPyConnection) -> None:
        con.execute(
            """
            CREATE OR REPLACE VIEW extrato AS
            SELECT
                id_extrato AS id,
                id_extrato,
                arquivo_origem,
                banco_codigo,
                banco_nome AS banco,
                banco_nome,
                conta,
                CAST(data_lancamento AS DATE) AS data,
                data_lancamento,
                tipo_movimento AS tipo,
                natureza_operacao AS natureza,
                documento,
                descricao_original,
                descricao_normalizada AS descricao,
                valor,
                valor_absoluto,
                valor_nominal,
                valor_liquido,
                prazo_dias,
                data_vencimento_original
            FROM extrato_bancario;

            CREATE OR REPLACE VIEW omie AS
            SELECT
                id_omie AS id,
                id_omie,
                arquivo_origem,
                origem_relatorio,
                banco_nome AS banco,
                banco_nome,
                conta_corrente AS conta,
                CAST(data_lancamento AS DATE) AS data,
                data_lancamento,
                data_vencimento,
                status_titulo AS status,
                tipo_lancamento AS tipo,
                categoria,
                grupo_contabil,
                cliente_fornecedor,
                documento,
                descricao_original,
                descricao_normalizada AS descricao,
                valor,
                valor_absoluto,
                valor_nominal,
                juros_multa,
                desconto_tarifa,
                contrato_ref
            FROM lancamentos_omie;

            CREATE OR REPLACE VIEW conciliacao AS
            SELECT
                p.metodo_pareamento,
                p.diferenca_valor,
                p.dias_defasagem,
                e.id_extrato,
                e.banco_nome AS banco,
                e.data_lancamento AS data_extrato,
                e.descricao_normalizada AS descricao_extrato,
                e.valor AS valor_extrato,
                o.id_omie,
                o.data_lancamento AS data_omie,
                o.categoria AS categoria_omie,
                o.descricao_normalizada AS descricao_omie,
                o.valor AS valor_omie
            FROM pares_conciliados p
            JOIN extrato_bancario e ON e.id_extrato = p.id_extrato
            JOIN lancamentos_omie o ON o.id_omie = p.id_omie;
            """
        )

    def _seed_defaults(self, con: duckdb.DuckDBPyConnection) -> None:
        rules_count = con.execute("SELECT COUNT(*) FROM catalogo_regras").fetchone()[0]
        if rules_count == 0:
            for r in DEFAULT_AUDIT_RULES:
                con.execute(
                    """
                    INSERT INTO catalogo_regras
                    (codigo, nome, descricao, categoria, severidade, tipo_regra, condicao_sql, ativa)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        r["codigo"],
                        r["nome"],
                        r["descricao"],
                        r["categoria"],
                        r["severidade"],
                        r["tipo_regra"],
                        r["condicao_sql"],
                        r["ativa"],
                    ],
                )
            # Reidrata regras customizadas salvas em JSON caso existam
            if self.rules_json_path.exists():
                try:
                    saved_custom = json.loads(self.rules_json_path.read_text(encoding="utf-8"))
                    for r in saved_custom:
                        con.execute(
                            """
                            INSERT OR REPLACE INTO catalogo_regras
                            (codigo, nome, descricao, categoria, severidade, tipo_regra, condicao_sql, ativa)
                            VALUES (?, ?, ?, ?, ?, 'CUSTOM', ?, ?)
                            """,
                            [
                                r["codigo"],
                                r["nome"],
                                r["descricao"],
                                r["categoria"],
                                r["severidade"],
                                r["condicao_sql"],
                                bool(r.get("ativa", True)),
                            ],
                        )
                except Exception:
                    pass

        banks_count = con.execute("SELECT COUNT(*) FROM parametros_bancos").fetchone()[0]
        if banks_count == 0:
            for b in DEFAULT_BANK_PARAMS:
                con.execute(
                    """
                    INSERT INTO parametros_bancos
                    (banco_codigo, banco_nome, tarifa_boleto_contratada, taxa_antecipacao_mensal,
                     modalidade_antecipacao, iof_tarifa_lote, tolerancia_dias_conciliacao)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        b["banco_codigo"],
                        b["banco_nome"],
                        b["tarifa_boleto_contratada"],
                        b["taxa_antecipacao_mensal"],
                        b["modalidade_antecipacao"],
                        b["iof_tarifa_lote"],
                        b["tolerancia_dias_conciliacao"],
                    ],
                )

        loans_count = con.execute("SELECT COUNT(*) FROM contratos_emprestimo").fetchone()[0]
        if loans_count == 0:
            for c in DEFAULT_LOAN_CONTRACTS:
                con.execute(
                    """
                    INSERT INTO contratos_emprestimo
                    (id_contrato, nome_contrato, banco_nome, sistema_amortizacao, valor_principal,
                     taxa_juros_mensal, prazo_meses, data_primeira_parcela, palavra_chave_omie, ativo)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        c["id_contrato"],
                        c["nome_contrato"],
                        c["banco_nome"],
                        c["sistema_amortizacao"],
                        c["valor_principal"],
                        c["taxa_juros_mensal"],
                        c["prazo_meses"],
                        c["data_primeira_parcela"],
                        c["palavra_chave_omie"],
                        c["ativo"],
                    ],
                )

    def upsert_extrato(self, df: pd.DataFrame) -> int:
        validated = validate_extrato_df(df)
        if validated.empty:
            return 0
        validated = validated.drop_duplicates(subset=["id_extrato"], keep="last")
        cols_sql = ", ".join(EXTRATO_COLUMNS)
        with self.connect() as con:
            con.register("tmp_extrato_in", validated)
            con.execute(
                f"""
                INSERT OR REPLACE INTO extrato_bancario ({cols_sql})
                SELECT {cols_sql} FROM tmp_extrato_in
                """
            )
            con.unregister("tmp_extrato_in")
            # Garante que todo banco recém-importado tenha linha de parâmetros cadastrada
            con.execute(
                """
                INSERT OR IGNORE INTO parametros_bancos
                (banco_nome, banco_codigo, tarifa_boleto_contratada, taxa_antecipacao_mensal,
                 modalidade_antecipacao, iof_tarifa_lote, tolerancia_dias_conciliacao)
                SELECT DISTINCT
                    banco_nome,
                    FIRST(banco_codigo),
                    2.50,
                    1.90,
                    'COMPOSTO',
                    0.0,
                    2
                FROM extrato_bancario
                WHERE banco_nome NOT IN (SELECT banco_nome FROM parametros_bancos)
                GROUP BY banco_nome
                """
            )
        return len(validated)

    def upsert_omie(self, df: pd.DataFrame) -> int:
        validated = validate_omie_df(df)
        if validated.empty:
            return 0
        validated = validated.drop_duplicates(subset=["id_omie"], keep="last")
        cols_sql = ", ".join(OMIE_COLUMNS)
        with self.connect() as con:
            con.register("tmp_omie_in", validated)
            con.execute(
                f"""
                INSERT OR REPLACE INTO lancamentos_omie ({cols_sql})
                SELECT {cols_sql} FROM tmp_omie_in
                """
            )
            con.unregister("tmp_omie_in")
        return len(validated)

    def clear_movements(self, target: str = "ALL") -> None:
        with self.connect() as con:
            if target in ("ALL", "EXTRATO"):
                con.execute("DELETE FROM extrato_bancario")
                con.execute("DELETE FROM historico_arquivos WHERE tipo_fonte = 'EXTRATO'")
            if target in ("ALL", "OMIE"):
                con.execute("DELETE FROM lancamentos_omie")
                con.execute("DELETE FROM historico_arquivos WHERE tipo_fonte = 'OMIE'")
            con.execute("DELETE FROM pares_conciliados")
            if target == "ALL":
                con.execute("DELETE FROM divergencias_auditoria")
                con.execute("DELETE FROM historico_execucoes")
                con.execute("DELETE FROM vinculos_manuais")

    def register_imported_file(
        self,
        raw_bytes: bytes,
        nome_arquivo: str,
        tipo_fonte: str,
        df: pd.DataFrame,
        ofx_meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Registra lote importado com hash SHA-256, período coberto e saldo OFX."""
        import hashlib

        sha256 = hashlib.sha256(raw_bytes).hexdigest()
        id_arq = f"{tipo_fonte}-{sha256[:16]}"
        already_imported = False
        with self.connect() as con:
            dup = con.execute(
                "SELECT nome_arquivo, importado_em FROM historico_arquivos WHERE hash_sha256 = ? AND tipo_fonte = ?",
                [sha256, tipo_fonte],
            ).fetchone()
            if dup:
                already_imported = True

        if df.empty:
            return {"id_arquivo": id_arq, "duplicado": already_imported, "qtd": 0}

        banco = str(df["banco_nome"].iloc[0]) if "banco_nome" in df.columns else "Multi-Banco"
        conta_col = "conta" if "conta" in df.columns else ("conta_corrente" if "conta_corrente" in df.columns else None)
        conta = str(df[conta_col].iloc[0]) if conta_col else "PADRAO"
        dt_ini = str(ofx_meta.get("dt_inicio") or df["data_lancamento"].min()) if ofx_meta else str(df["data_lancamento"].min())
        dt_fim = str(ofx_meta.get("dt_fim") or df["data_lancamento"].max()) if ofx_meta else str(df["data_lancamento"].max())
        saldo_ofx = ofx_meta.get("saldo_final") if ofx_meta else None
        soma_mov = round(float(df["valor"].sum()), 2)
        now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        with self.connect() as con:
            con.execute(
                """
                INSERT OR REPLACE INTO historico_arquivos
                (id_arquivo, hash_sha256, nome_arquivo, tipo_fonte, banco_nome, conta,
                 dt_inicio, dt_fim, qtd_registros, soma_movimentos, saldo_final_ofx, importado_em)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    id_arq,
                    sha256,
                    nome_arquivo,
                    tipo_fonte,
                    banco,
                    conta,
                    dt_ini,
                    dt_fim,
                    int(len(df)),
                    soma_mov,
                    float(saldo_ofx) if saldo_ofx is not None else None,
                    now_iso,
                ],
            )
        return {"id_arquivo": id_arq, "duplicado": already_imported, "qtd": len(df), "hash": sha256[:10]}

    def get_imported_files_df(self) -> pd.DataFrame:
        with self.connect() as con:
            return con.execute(
                """
                SELECT id_arquivo, nome_arquivo, tipo_fonte, banco_nome, conta,
                       dt_inicio, dt_fim, qtd_registros, soma_movimentos, saldo_final_ofx,
                       SUBSTR(hash_sha256, 1, 10) AS hash_curto, importado_em
                FROM historico_arquivos
                ORDER BY importado_em DESC
                """
            ).df()

    def delete_imported_file(self, id_arquivo: str) -> int:
        """Exclui apenas os lançamentos pertencentes a um arquivo específico enviado."""
        with self.connect() as con:
            row = con.execute(
                "SELECT nome_arquivo, tipo_fonte FROM historico_arquivos WHERE id_arquivo = ?",
                [id_arquivo],
            ).fetchone()
            if not row:
                return 0
            nome_arquivo, tipo_fonte = row[0], row[1]
            if tipo_fonte == "EXTRATO":
                deleted = con.execute(
                    "SELECT COUNT(*) FROM extrato_bancario WHERE arquivo_origem = ?",
                    [nome_arquivo],
                ).fetchone()[0]
                con.execute("DELETE FROM extrato_bancario WHERE arquivo_origem = ?", [nome_arquivo])
            else:
                deleted = con.execute(
                    "SELECT COUNT(*) FROM lancamentos_omie WHERE arquivo_origem = ?",
                    [nome_arquivo],
                ).fetchone()[0]
                con.execute("DELETE FROM lancamentos_omie WHERE arquivo_origem = ?", [nome_arquivo])
            con.execute("DELETE FROM historico_arquivos WHERE id_arquivo = ?", [id_arquivo])
            return int(deleted)

    def detect_statement_gaps(self) -> list[dict[str, Any]]:
        """Detecta buracos temporais (> 1 dia corrido) entre extratos importados da mesma conta/banco."""
        files_df = self.get_imported_files_df()
        ext_files = files_df[
            (files_df["tipo_fonte"] == "EXTRATO")
            & files_df["dt_inicio"].notna()
            & files_df["dt_fim"].notna()
        ].copy()
        if len(ext_files) < 2:
            return []

        gaps: list[dict[str, Any]] = []
        for (banco, conta), grp in ext_files.groupby(["banco_nome", "conta"]):
            if len(grp) < 2:
                continue
            ordered = grp.sort_values("dt_inicio").reset_index(drop=True)
            max_end_so_far = pd.to_datetime(ordered.loc[0, "dt_fim"])
            prev_file = ordered.loc[0, "nome_arquivo"]
            for i in range(1, len(ordered)):
                cur_start = pd.to_datetime(ordered.loc[i, "dt_inicio"])
                cur_end = pd.to_datetime(ordered.loc[i, "dt_fim"])
                cur_file = ordered.loc[i, "nome_arquivo"]
                missing_days = (cur_start - max_end_so_far).days - 1
                if missing_days >= 1:
                    gap_ini = (max_end_so_far + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
                    gap_fim = (cur_start - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
                    gaps.append(
                        {
                            "banco_nome": banco,
                            "conta": conta,
                            "arquivo_anterior": prev_file,
                            "arquivo_seguinte": cur_file,
                            "gap_inicio": gap_ini,
                            "gap_fim": gap_fim,
                            "dias_faltantes": int(missing_days),
                        }
                    )
                if cur_end > max_end_so_far:
                    max_end_so_far = cur_end
                    prev_file = cur_file
        return gaps

    def get_available_periods(self) -> list[str]:
        """Retorna lista ordenada de competências AAAA-MM presentes nos extratos ou no Omie."""
        with self.connect() as con:
            rows = con.execute(
                """
                SELECT DISTINCT comp FROM (
                    SELECT SUBSTR(data_lancamento, 1, 7) AS comp FROM extrato_bancario
                    UNION
                    SELECT SUBSTR(data_lancamento, 1, 7) AS comp FROM lancamentos_omie
                )
                WHERE comp IS NOT NULL AND LENGTH(comp) = 7
                ORDER BY comp DESC
                """
            ).fetchall()
        return [r[0] for r in rows]

    def get_extrato_df(self, competencia: str | None = None) -> pd.DataFrame:
        with self.connect() as con:
            if competencia and competencia != "TODAS":
                return con.execute(
                    f"""
                    SELECT {', '.join(EXTRATO_COLUMNS)}
                    FROM extrato_bancario
                    WHERE SUBSTR(data_lancamento, 1, 7) = ?
                    ORDER BY data_lancamento DESC, id_extrato
                    """,
                    [competencia],
                ).df()
            return con.execute(
                f"SELECT {', '.join(EXTRATO_COLUMNS)} FROM extrato_bancario ORDER BY data_lancamento DESC, id_extrato"
            ).df()

    def get_omie_df(self, competencia: str | None = None) -> pd.DataFrame:
        with self.connect() as con:
            if competencia and competencia != "TODAS":
                return con.execute(
                    f"""
                    SELECT {', '.join(OMIE_COLUMNS)}
                    FROM lancamentos_omie
                    WHERE SUBSTR(data_lancamento, 1, 7) = ?
                    ORDER BY data_lancamento DESC, id_omie
                    """,
                    [competencia],
                ).df()
            return con.execute(
                f"SELECT {', '.join(OMIE_COLUMNS)} FROM lancamentos_omie ORDER BY data_lancamento DESC, id_omie"
            ).df()

    @staticmethod
    def build_issue_signature(
        codigo_regra: str,
        data_ocorrencia: str,
        banco: str,
        id_extrato: str | None,
        id_omie: str | None,
        resumo: str,
    ) -> str:
        import hashlib

        raw = f"{codigo_regra}|{data_ocorrencia}|{banco}|{id_extrato or ''}|{id_omie or ''}|{resumo[:60]}"
        return f"SIG-{hashlib.sha1(raw.encode('utf-8')).hexdigest()[:16]}"

    def save_issue_workflow(
        self,
        assinatura: str,
        status_tratativa: str,
        justificativa: str = "",
        responsavel: str = "Analista Financeiro",
    ) -> None:
        now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self.connect() as con:
            con.execute(
                """
                INSERT OR REPLACE INTO tratativas_divergencias
                (assinatura_ocorrencia, status_tratativa, justificativa, responsavel, atualizado_em)
                VALUES (?, ?, ?, ?, ?)
                """,
                [assinatura, status_tratativa, justificativa.strip(), responsavel.strip(), now_iso],
            )

    def get_workflow_map(self) -> dict[str, dict[str, str]]:
        with self.connect() as con:
            df = con.execute(
                """
                SELECT assinatura_ocorrencia, status_tratativa, justificativa, responsavel, atualizado_em
                FROM tratativas_divergencias
                """
            ).df()
        return {r["assinatura_ocorrencia"]: r.to_dict() for _, r in df.iterrows()}

    def add_manual_link(self, id_extrato: str, id_omie: str, observacao: str = "") -> None:
        now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self.connect() as con:
            con.execute(
                """
                INSERT OR REPLACE INTO vinculos_manuais (id_extrato, id_omie, observacao, criado_em)
                VALUES (?, ?, ?, ?)
                """,
                [id_extrato, id_omie, observacao.strip(), now_iso],
            )

    def get_manual_links_df(self) -> pd.DataFrame:
        with self.connect() as con:
            return con.execute(
                "SELECT id_extrato, id_omie, observacao, criado_em FROM vinculos_manuais ORDER BY criado_em DESC"
            ).df()

    def delete_manual_link(self, id_extrato: str, id_omie: str) -> None:
        with self.connect() as con:
            con.execute(
                "DELETE FROM vinculos_manuais WHERE id_extrato = ? AND id_omie = ?",
                [id_extrato, id_omie],
            )

    def get_bank_params_df(self) -> pd.DataFrame:
        with self.connect() as con:
            return con.execute(
                """
                SELECT banco_nome, banco_codigo, tarifa_boleto_contratada, taxa_antecipacao_mensal,
                       modalidade_antecipacao, iof_tarifa_lote, tolerancia_dias_conciliacao
                FROM parametros_bancos
                ORDER BY banco_nome
                """
            ).df()

    def save_bank_params_df(self, df: pd.DataFrame) -> None:
        clean = df.copy()
        clean = clean.dropna(subset=["banco_nome"])
        clean["banco_nome"] = clean["banco_nome"].astype(str).str.strip()
        clean = clean[clean["banco_nome"] != ""]
        clean["banco_codigo"] = clean["banco_codigo"].fillna("000").astype(str).str.strip()
        clean["tarifa_boleto_contratada"] = pd.to_numeric(
            clean["tarifa_boleto_contratada"], errors="coerce"
        ).fillna(0.0)
        clean["taxa_antecipacao_mensal"] = pd.to_numeric(
            clean["taxa_antecipacao_mensal"], errors="coerce"
        ).fillna(0.0)
        clean["modalidade_antecipacao"] = (
            clean["modalidade_antecipacao"]
            .fillna("COMPOSTO")
            .astype(str)
            .str.upper()
            .where(lambda s: s.isin(["COMPOSTO", "SIMPLES"]), "COMPOSTO")
        )
        clean["iof_tarifa_lote"] = pd.to_numeric(
            clean["iof_tarifa_lote"], errors="coerce"
        ).fillna(0.0)
        clean["tolerancia_dias_conciliacao"] = (
            pd.to_numeric(clean["tolerancia_dias_conciliacao"], errors="coerce")
            .fillna(2)
            .astype(int)
        )
        with self.connect() as con:
            con.execute("DELETE FROM parametros_bancos")
            if not clean.empty:
                con.register("tmp_bancos", clean)
                con.execute(
                    """
                    INSERT INTO parametros_bancos
                    (banco_nome, banco_codigo, tarifa_boleto_contratada, taxa_antecipacao_mensal,
                     modalidade_antecipacao, iof_tarifa_lote, tolerancia_dias_conciliacao)
                    SELECT banco_nome, banco_codigo, tarifa_boleto_contratada, taxa_antecipacao_mensal,
                           modalidade_antecipacao, iof_tarifa_lote, tolerancia_dias_conciliacao
                    FROM tmp_bancos
                    """
                )
                con.unregister("tmp_bancos")

    def get_loan_contracts_df(self) -> pd.DataFrame:
        with self.connect() as con:
            return con.execute(
                """
                SELECT id_contrato, nome_contrato, banco_nome, sistema_amortizacao,
                       valor_principal, taxa_juros_mensal, prazo_meses,
                       data_primeira_parcela, palavra_chave_omie, ativo
                FROM contratos_emprestimo
                ORDER BY id_contrato
                """
            ).df()

    def upsert_loan_contract(self, contract: dict[str, Any]) -> None:
        with self.connect() as con:
            con.execute(
                """
                INSERT OR REPLACE INTO contratos_emprestimo
                (id_contrato, nome_contrato, banco_nome, sistema_amortizacao, valor_principal,
                 taxa_juros_mensal, prazo_meses, data_primeira_parcela, palavra_chave_omie, ativo)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    str(contract["id_contrato"]).strip(),
                    str(contract["nome_contrato"]).strip(),
                    str(contract["banco_nome"]).strip(),
                    str(contract["sistema_amortizacao"]).strip().upper(),
                    float(contract["valor_principal"]),
                    float(contract["taxa_juros_mensal"]),
                    int(contract["prazo_meses"]),
                    str(contract["data_primeira_parcela"]).strip(),
                    str(contract["palavra_chave_omie"]).strip(),
                    bool(contract.get("ativo", True)),
                ],
            )

    def delete_loan_contract(self, id_contrato: str) -> None:
        with self.connect() as con:
            con.execute("DELETE FROM contratos_emprestimo WHERE id_contrato = ?", [id_contrato])

    def get_rules_df(self) -> pd.DataFrame:
        with self.connect() as con:
            return con.execute(
                """
                SELECT codigo, nome, descricao, categoria, severidade, tipo_regra, condicao_sql, ativa
                FROM catalogo_regras
                ORDER BY CASE WHEN tipo_regra = 'NATIVA' THEN 0 ELSE 1 END, codigo
                """
            ).df()

    def update_rule_status(self, codigo: str, ativa: bool, severidade: str | None = None) -> None:
        with self.connect() as con:
            if severidade:
                con.execute(
                    "UPDATE catalogo_regras SET ativa = ?, severidade = ? WHERE codigo = ?",
                    [bool(ativa), severidade, codigo],
                )
            else:
                con.execute(
                    "UPDATE catalogo_regras SET ativa = ? WHERE codigo = ?",
                    [bool(ativa), codigo],
                )
        self._sync_custom_rules_json()

    def add_custom_rule(
        self,
        nome: str,
        descricao: str,
        condicao_sql: str,
        severidade: str,
        categoria: str,
        codigo: str | None = None,
    ) -> str:
        with self.connect() as con:
            if not codigo:
                existing = con.execute(
                    "SELECT codigo FROM catalogo_regras WHERE tipo_regra = 'CUSTOM'"
                ).fetchall()
                nums = []
                for (c,) in existing:
                    tail = str(c).replace("CUS-", "")
                    if tail.isdigit():
                        nums.append(int(tail))
                next_num = (max(nums) + 1) if nums else 1
                codigo = f"CUS-{next_num:02d}"

            con.execute(
                """
                INSERT OR REPLACE INTO catalogo_regras
                (codigo, nome, descricao, categoria, severidade, tipo_regra, condicao_sql, ativa)
                VALUES (?, ?, ?, ?, ?, 'CUSTOM', ?, TRUE)
                """,
                [codigo, nome.strip(), descricao.strip(), categoria.strip(), severidade, condicao_sql.strip()],
            )
        self._sync_custom_rules_json()
        return codigo

    def delete_custom_rule(self, codigo: str) -> None:
        with self.connect() as con:
            con.execute(
                "DELETE FROM catalogo_regras WHERE codigo = ? AND tipo_regra = 'CUSTOM'",
                [codigo],
            )
        self._sync_custom_rules_json()

    def _sync_custom_rules_json(self) -> None:
        with self.connect() as con:
            custom_df = con.execute(
                """
                SELECT codigo, nome, descricao, categoria, severidade, condicao_sql, ativa
                FROM catalogo_regras
                WHERE tipo_regra = 'CUSTOM'
                ORDER BY codigo
                """
            ).df()
        records = custom_df.to_dict(orient="records")
        self.rules_json_path.write_text(
            json.dumps(records, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def save_audit_execution(
        self,
        execucao_id: str,
        pairs_df: pd.DataFrame,
        divergencias_df: pd.DataFrame,
        metrics: dict[str, Any],
    ) -> None:
        validated_div = validate_divergencia_df(divergencias_df)
        cols_div = ", ".join(DIVERGENCIA_COLUMNS)
        now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self.connect() as con:
            con.execute("DELETE FROM pares_conciliados")
            if not pairs_df.empty:
                con.register("tmp_pairs", pairs_df)
                con.execute(
                    """
                    INSERT INTO pares_conciliados
                    (id_extrato, id_omie, metodo_pareamento, diferenca_valor, dias_defasagem)
                    SELECT id_extrato, id_omie, metodo_pareamento, diferenca_valor, dias_defasagem
                    FROM tmp_pairs
                    """
                )
                con.unregister("tmp_pairs")

            con.execute("DELETE FROM divergencias_auditoria")
            if not validated_div.empty:
                con.register("tmp_div", validated_div)
                con.execute(
                    f"""
                    INSERT INTO divergencias_auditoria ({cols_div})
                    SELECT {cols_div} FROM tmp_div
                    """
                )
                con.unregister("tmp_div")

            con.execute(
                """
                INSERT OR REPLACE INTO historico_execucoes
                (execucao_id, executado_em, total_extrato, total_omie, total_divergencias,
                 alertas_criticos, valor_total_divergente, aderencia_erp_pct)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    execucao_id,
                    now_iso,
                    int(metrics["total_extrato"]),
                    int(metrics["total_omie"]),
                    int(metrics["total_divergencias"]),
                    int(metrics["alertas_criticos"]),
                    float(metrics["valor_total_divergente"]),
                    float(metrics["aderencia_erp_pct"]),
                ],
            )

    def get_latest_audit_results(self) -> tuple[dict[str, Any] | None, pd.DataFrame]:
        with self.connect() as con:
            row = con.execute(
                """
                SELECT execucao_id, executado_em, total_extrato, total_omie,
                       total_divergencias, alertas_criticos, valor_total_divergente, aderencia_erp_pct
                FROM historico_execucoes
                ORDER BY executado_em DESC
                LIMIT 1
                """
            ).fetchone()
            if not row:
                return None, pd.DataFrame(columns=DIVERGENCIA_COLUMNS)
            summary = {
                "execucao_id": row[0],
                "executado_em": row[1],
                "total_extrato": row[2],
                "total_omie": row[3],
                "total_divergencias": row[4],
                "alertas_criticos": row[5],
                "valor_total_divergente": row[6],
                "aderencia_erp_pct": row[7],
            }
            divs = con.execute(
                f"""
                SELECT {', '.join(DIVERGENCIA_COLUMNS)}
                FROM divergencias_auditoria
                ORDER BY
                    CASE severidade
                        WHEN 'Crítico' THEN 0
                        WHEN 'Alerta' THEN 1
                        ELSE 2
                    END,
                    valor_divergente DESC,
                    data_ocorrencia DESC
                """
            ).df()
            return summary, divs
