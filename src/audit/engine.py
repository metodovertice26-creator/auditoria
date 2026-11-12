"""Motor principal de cruzamento, execução das regras AUD-01..AUD-05 e SQL dinâmico."""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

import pandas as pd

from src.audit.finance_math import (
    calcular_divergencia_boleto,
    calcular_vp_antecipacao,
    gerar_tabela_emprestimo,
)
from src.database import VerticeDB
from src.etl.normalizer import clean_description, make_deterministic_id
from src.schemas import DIVERGENCIA_COLUMNS, validate_divergencia_df

FORBIDDEN_SQL_PATTERNS = re.compile(
    r"\b(DROP|DELETE|INSERT|UPDATE|ALTER|CREATE|TRUNCATE|REPLACE|ATTACH|DETACH|COPY|EXPORT|IMPORT|PRAGMA|INSTALL|LOAD|CALL|READ_CSV|READ_PARQUET|READ_JSON)\b",
    flags=re.IGNORECASE,
)


def validate_custom_sql_safety(sql_text: str) -> tuple[bool, str]:
    """Garante que a regra SQL customizada seja estritamente somente leitura."""
    cleaned = (sql_text or "").strip()
    if not cleaned:
        return False, "A condição SQL não pode estar vazia."
    if ";" in cleaned.rstrip(";"):
        return False, "Múltiplas instruções SQL separadas por ';' não são permitidas."
    m = FORBIDDEN_SQL_PATTERNS.search(cleaned)
    if m:
        return False, f"Comando SQL bloqueado por política de segurança: '{m.group(1).upper()}'."
    return True, "OK"


def _build_custom_rule_query(condicao_sql: str) -> str:
    """Constrói a query analítica no DuckDB a partir de uma expressão WHERE ou SELECT completo."""
    expr = condicao_sql.strip().rstrip(";")
    if expr.upper().startswith("SELECT "):
        return expr

    uses_e = bool(re.search(r"\be\.", expr, flags=re.IGNORECASE))
    uses_o = bool(re.search(r"\bo\.", expr, flags=re.IGNORECASE))

    if uses_e and uses_o:
        return f"""
            WITH omie_por_extrato AS (
                SELECT
                    p.id_extrato AS _ext_key,
                    FIRST(o.id_omie) AS id,
                    FIRST(o.id_omie) AS id_omie,
                    FIRST(o.arquivo_origem) AS arquivo_origem,
                    FIRST(o.origem_relatorio) AS origem_relatorio,
                    FIRST(o.banco) AS banco,
                    FIRST(o.banco_nome) AS banco_nome,
                    FIRST(o.conta) AS conta,
                    FIRST(o.data) AS data,
                    FIRST(o.data_lancamento) AS data_lancamento,
                    FIRST(o.data_vencimento) AS data_vencimento,
                    FIRST(o.status) AS status,
                    FIRST(o.tipo) AS tipo,
                    FIRST(o.categoria) AS categoria,
                    FIRST(o.grupo_contabil) AS grupo_contabil,
                    FIRST(o.cliente_fornecedor) AS cliente_fornecedor,
                    FIRST(o.documento) AS documento,
                    FIRST(o.descricao_original) AS descricao_original,
                    STRING_AGG(o.descricao, ' + ') AS descricao,
                    ROUND(SUM(o.valor), 2) AS valor,
                    ROUND(SUM(o.valor_absoluto), 2) AS valor_absoluto,
                    ROUND(SUM(o.valor_nominal), 2) AS valor_nominal,
                    ROUND(SUM(o.juros_multa), 2) AS juros_multa,
                    ROUND(SUM(o.desconto_tarifa), 2) AS desconto_tarifa,
                    FIRST(o.contrato_ref) AS contrato_ref
                FROM pares_conciliados p
                JOIN omie o ON o.id_omie = p.id_omie
                GROUP BY p.id_extrato
            )
            SELECT
                e.id_extrato AS id_extrato,
                o.id_omie AS id_omie,
                COALESCE(e.data_lancamento, o.data_lancamento) AS data_ocorrencia,
                COALESCE(e.banco_nome, o.banco_nome, 'Multi-Banco') AS banco,
                COALESCE(o.valor, 0.0) AS valor_esperado,
                COALESCE(e.valor, 0.0) AS valor_realizado,
                ABS(COALESCE(e.valor, 0.0) - COALESCE(o.valor, 0.0)) AS valor_divergente,
                e.descricao AS descricao_extrato,
                o.descricao AS descricao_omie
            FROM extrato e
            LEFT JOIN omie_por_extrato o ON o._ext_key = e.id_extrato
            WHERE {expr}
        """
    if uses_e:
        return f"""
            SELECT
                e.id_extrato AS id_extrato,
                NULL AS id_omie,
                e.data_lancamento AS data_ocorrencia,
                e.banco_nome AS banco,
                0.0 AS valor_esperado,
                e.valor AS valor_realizado,
                e.valor_absoluto AS valor_divergente,
                e.descricao AS descricao_extrato,
                NULL AS descricao_omie
            FROM extrato e
            WHERE {expr}
        """
    if uses_o:
        return f"""
            SELECT
                NULL AS id_extrato,
                o.id_omie AS id_omie,
                o.data_lancamento AS data_ocorrencia,
                o.banco_nome AS banco,
                o.valor AS valor_esperado,
                0.0 AS valor_realizado,
                o.valor_absoluto AS valor_divergente,
                NULL AS descricao_extrato,
                o.descricao AS descricao_omie
            FROM omie o
            WHERE {expr}
        """
    return f"""
        SELECT
            id_extrato,
            id_omie,
            COALESCE(data_extrato, data_omie) AS data_ocorrencia,
            banco,
            valor_omie AS valor_esperado,
            valor_extrato AS valor_realizado,
            ABS(diferenca_valor) AS valor_divergente,
            descricao_extrato,
            descricao_omie
        FROM conciliacao
        WHERE {expr}
    """


def preview_custom_sql_rule(db: VerticeDB, condicao_sql: str, limit: int = 25) -> tuple[bool, str, pd.DataFrame]:
    """Executa um preview seguro da regra SQL customizada no DuckDB antes de salvá-la."""
    ok, msg = validate_custom_sql_safety(condicao_sql)
    if not ok:
        return False, msg, pd.DataFrame()
    query = _build_custom_rule_query(condicao_sql)
    try:
        with db.connect() as con:
            df = con.execute(f"SELECT * FROM ({query}) sub LIMIT {int(limit)}").df()
        return True, f"Condição SQL válida! {len(df)} registro(s) retornado(s) na amostra.", df
    except Exception as exc:
        return False, f"Erro ao executar condição SQL no DuckDB: {exc}", pd.DataFrame()


def _token_similarity(text_a: str, text_b: str) -> float:
    """Similaridade de tokens relevantes (0..1) ignorando palavras genéricas bancárias."""
    stop = {
        "DE", "DO", "DA", "EM", "NO", "NA", "PIX", "TED", "DOC", "RECEBIDO", "ENVIADO",
        "CLIENTE", "FORNECEDOR", "LTDA", "SA", "PAGAMENTO", "RECEBIMENTO", "BOLETO",
        "LIQUIDACAO", "COBRANCA", "BAIXA", "TITULO", "SEM", "DESCRICAO", "LANCAMENTO",
    }
    toks_a = {t for t in re.findall(r"[A-Z0-9]{3,}", clean_description(text_a)) if t not in stop}
    toks_b = {t for t in re.findall(r"[A-Z0-9]{3,}", clean_description(text_b)) if t not in stop}
    if not toks_a or not toks_b:
        return 0.0
    inter = len(toks_a & toks_b)
    return round(inter / max(1, min(len(toks_a), len(toks_b))), 3)


def _find_subset_sum(values: list[tuple[str, float]], target: float, tol: float = 0.08, max_k: int = 5) -> list[str] | None:
    """
    Encontra subconjunto de 2 até max_k títulos (limitado aos 14 candidatos mais próximos)
    cuja soma bate exatamente com o lote bancário (1:N).
    """
    from itertools import combinations

    pool = values[:14]
    if len(pool) < 2:
        return None
    upper_k = min(max_k, len(pool))
    for k in range(2, upper_k + 1):
        for comb in combinations(pool, k):
            total = sum(v for _, v in comb)
            if abs(total - target) <= tol:
                return [cid for cid, _ in comb]
    return None


def build_sql_from_visual_rule(campo: str, operador: str, valor_ref: float | str) -> str:
    """Converte parâmetros do Assistente Visual (Zero-SQL) em cláusula SQL válida para DuckDB."""
    field_map = {
        "Diferença absoluta |Extrato - Omie| (R$)": "ABS(e.valor - o.valor)",
        "Valor absoluto no Extrato (R$)": "e.valor_absoluto",
        "Valor no Extrato com sinal (R$)": "e.valor",
        "Valor absoluto no Omie (R$)": "o.valor_absoluto",
        "Juros / Multa pagos no Omie (R$)": "o.juros_multa",
        "Desconto / Tarifa no Omie (R$)": "o.desconto_tarifa",
        "Descrição do Extrato (texto)": "e.descricao",
        "Categoria do Omie (texto)": "o.categoria",
        "Cliente / Fornecedor no Omie (texto)": "o.cliente_fornecedor",
    }
    sql_col = field_map.get(campo, "e.valor_absoluto")
    op_map = {
        "Maior que (>)": ">",
        "Maior ou igual (>=)": ">=",
        "Menor que (<)": "<",
        "Igual a (=)": "=",
        "Diferente de (!=)": "!=",
        "Contém texto": "ILIKE",
    }
    sql_op = op_map.get(operador, ">")

    if sql_op == "ILIKE" or sql_col in ("e.descricao", "o.categoria", "o.cliente_fornecedor"):
        safe_txt = str(valor_ref).replace("'", "''").strip()
        if sql_op == "ILIKE":
            return f"{sql_col} ILIKE '%{safe_txt}%'"
        return f"UPPER({sql_col}) {sql_op} UPPER('{safe_txt}')"

    num_val = float(valor_ref)
    if "e.valor - o.valor" in sql_col:
        return f"{sql_col} {sql_op} {num_val:.2f} AND o.id_omie IS NOT NULL"
    return f"{sql_col} {sql_op} {num_val:.2f}"


def _compatible_banks(bank_e: str, bank_o: str) -> bool:
    if not bank_e or not bank_o:
        return True
    if "Não Identificado" in bank_e or "Não Identificado" in bank_o:
        return True
    ne = clean_description(bank_e)
    no = clean_description(bank_o)
    if ne == no or ne in no or no in ne:
        return True
    return False


def _reconciliar_extrato_omie(
    extrato_df: pd.DataFrame,
    omie_df: pd.DataFrame,
    bank_tol_days: dict[str, int],
    manual_links_df: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, set[str], set[str]]:
    """
    Realiza o pareamento determinístico e inteligente:
    0. Vínculos manuais confirmados pelo analista
    1. Documento exato + sentido financeiro + janela de data
    2. Split contábil N:1 (parcelas de empréstimo Passivo + Juros)
    3. Valor exato 1:1 desempatado por similaridade textual (Fuzzy Token Score) e menor defasagem
    4. Lotes bancários 1:N (Subset Sum de 2 a 5 títulos baixados no mesmo banco/janela)
    5. Fuzzy Match assistido (mesmo valor aproximado c/ pequena tarifa + alta similaridade de nome/cliente)
    """
    pair_cols = ["id_extrato", "id_omie", "metodo_pareamento", "diferenca_valor", "dias_defasagem"]
    if extrato_df.empty or omie_df.empty:
        return pd.DataFrame(columns=pair_cols), set(), set()

    ext = extrato_df.copy()
    ext["_dt"] = pd.to_datetime(ext["data_lancamento"])
    ext["_doc_clean"] = ext["documento"].astype(str).str.upper().str.replace(r"[^A-Z0-9]", "", regex=True)

    omi_settled = omie_df[omie_df["status_titulo"].isin(["BAIXADO", "CONCILIADO"])].copy()
    if omi_settled.empty:
        return pd.DataFrame(columns=pair_cols), set(), set()

    omi_settled["_dt"] = pd.to_datetime(omi_settled["data_lancamento"])
    omi_settled["_doc_clean"] = (
        omi_settled["documento"].astype(str).str.upper().str.replace(r"[^A-Z0-9]", "", regex=True)
    )

    matched_ext: set[str] = set()
    matched_omi: set[str] = set()
    pairs: list[dict[str, Any]] = []

    # Passo 0: Vínculos manuais definidos pelo analista financeiro
    if manual_links_df is not None and not manual_links_df.empty:
        ext_idx = ext.set_index("id_extrato")
        omi_idx = omi_settled.set_index("id_omie")
        for _, mlink in manual_links_df.iterrows():
            eid = str(mlink["id_extrato"])
            oid = str(mlink["id_omie"])
            if eid in ext_idx.index and oid in omi_idx.index:
                erow = ext_idx.loc[eid]
                orow = omi_idx.loc[oid]
                matched_ext.add(eid)
                matched_omi.add(oid)
                pairs.append(
                    {
                        "id_extrato": eid,
                        "id_omie": oid,
                        "metodo_pareamento": "VINCULO_MANUAL_ANALISTA",
                        "diferenca_valor": round(float(erow["valor"]) - float(orow["valor"]), 2),
                        "dias_defasagem": int((erow["_dt"] - orow["_dt"]).days),
                    }
                )

    # Passo 1: Match por Documento + Mesmo sentido financeiro + Data próxima
    for _, erow in ext[~ext["id_extrato"].isin(matched_ext)].iterrows():
        eid = erow["id_extrato"]
        edoc = erow["_doc_clean"]
        if not edoc or edoc.startswith(("SEQ", "CSV", "OMIEDOC")) or len(edoc) < 3:
            continue
        tol_days = bank_tol_days.get(erow["banco_nome"], 2)

        candidates = omi_settled[
            (~omi_settled["id_omie"].isin(matched_omi))
            & (omi_settled["_doc_clean"] == edoc)
        ]
        for _, orow in candidates.iterrows():
            if not _compatible_banks(erow["banco_nome"], orow["banco_nome"]):
                continue
            if (erow["valor"] >= 0) != (orow["valor"] >= 0):
                continue
            days_diff = abs((erow["_dt"] - orow["_dt"]).days)
            if days_diff > max(tol_days, 5):
                continue
            val_diff = round(float(erow["valor"]) - float(orow["valor"]), 2)
            if abs(val_diff) <= 50.0 or erow["natureza_operacao"] in ("BOLETO_LIQUIDACAO", "ANTECIPACAO_RECEBIVEIS"):
                matched_ext.add(eid)
                matched_omi.add(orow["id_omie"])
                pairs.append(
                    {
                        "id_extrato": eid,
                        "id_omie": orow["id_omie"],
                        "metodo_pareamento": "DOCUMENTO_DIRETO",
                        "diferenca_valor": val_diff,
                        "dias_defasagem": int((erow["_dt"] - orow["_dt"]).days),
                    }
                )
                break

    # Passo 2: Match N:1 para parcelas de empréstimo desmembradas no Omie (Passivo + Juros = Débito Único no Banco)
    unmatched_ext_loans = ext[
        (~ext["id_extrato"].isin(matched_ext)) & (ext["valor"] < 0)
    ]
    for _, erow in unmatched_ext_loans.iterrows():
        eid = erow["id_extrato"]
        tol_days = bank_tol_days.get(erow["banco_nome"], 2)
        same_day_outflows = omi_settled[
            (~omi_settled["id_omie"].isin(matched_omi))
            & (omi_settled["valor"] < 0)
            & ((omi_settled["_dt"] - erow["_dt"]).abs().dt.days <= tol_days)
        ]
        if len(same_day_outflows) < 2:
            continue

        for grp_col in ("contrato_ref", "documento"):
            for grp_val, grp_df in same_day_outflows.groupby(grp_col, dropna=True):
                if not grp_val or len(grp_df) < 2:
                    continue
                if not all(_compatible_banks(erow["banco_nome"], b) for b in grp_df["banco_nome"]):
                    continue
                soma_omie = round(float(grp_df["valor"].sum()), 2)
                if abs(float(erow["valor"]) - soma_omie) <= 0.15:
                    matched_ext.add(eid)
                    for _, orow in grp_df.iterrows():
                        matched_omi.add(orow["id_omie"])
                        pairs.append(
                            {
                                "id_extrato": eid,
                                "id_omie": orow["id_omie"],
                                "metodo_pareamento": "AGRUPADO_SPLIT_CONTABIL",
                                "diferenca_valor": round(float(erow["valor"]) - soma_omie, 2),
                                "dias_defasagem": int((erow["_dt"] - orow["_dt"]).days),
                            }
                        )
                    break
            if eid in matched_ext:
                break

    # Passo 3: Match 1:1 por Valor Exato (tolerância R$ 0,05) + Desempate por Similaridade Textual (Fuzzy) + Data
    for _, erow in ext[~ext["id_extrato"].isin(matched_ext)].iterrows():
        eid = erow["id_extrato"]
        tol_days = bank_tol_days.get(erow["banco_nome"], 2)
        candidates = omi_settled[~omi_settled["id_omie"].isin(matched_omi)].copy()
        if candidates.empty:
            break
        candidates["_days"] = (candidates["_dt"] - erow["_dt"]).abs().dt.days
        candidates["_vdiff"] = (candidates["valor"] - erow["valor"]).abs()
        candidates = candidates[(candidates["_days"] <= tol_days) & (candidates["_vdiff"] <= 0.05)]
        if candidates.empty:
            continue
        candidates = candidates[
            candidates["banco_nome"].apply(lambda b: _compatible_banks(erow["banco_nome"], b))
        ]
        if candidates.empty:
            continue
        candidates["_sim"] = candidates.apply(
            lambda r: _token_similarity(
                erow["descricao_normalizada"],
                f"{r['cliente_fornecedor']} {r['descricao_normalizada']}",
            ),
            axis=1,
        )
        best = candidates.sort_values(by=["_vdiff", "_sim", "_days"], ascending=[True, False, True]).iloc[0]
        matched_ext.add(eid)
        matched_omi.add(best["id_omie"])
        pairs.append(
            {
                "id_extrato": eid,
                "id_omie": best["id_omie"],
                "metodo_pareamento": "VALOR_DATA_TOLERANCIA",
                "diferenca_valor": round(float(erow["valor"]) - float(best["valor"]), 2),
                "dias_defasagem": int((erow["_dt"] - best["_dt"]).days),
            }
        )

    # Passo 4: Subset Sum 1:N — 1 lançamento consolidado no extrato = soma exata de 2..5 baixas no Omie
    for _, erow in ext[~ext["id_extrato"].isin(matched_ext)].iterrows():
        eid = erow["id_extrato"]
        tol_days = bank_tol_days.get(erow["banco_nome"], 2)
        same_sign = omi_settled[
            (~omi_settled["id_omie"].isin(matched_omi))
            & ((omi_settled["valor"] >= 0) == (erow["valor"] >= 0))
            & ((omi_settled["_dt"] - erow["_dt"]).abs().dt.days <= tol_days)
            & (omi_settled["valor_absoluto"] < erow["valor_absoluto"])
        ].copy()
        if len(same_sign) < 2:
            continue
        same_sign = same_sign[
            same_sign["banco_nome"].apply(lambda b: _compatible_banks(erow["banco_nome"], b))
        ]
        if len(same_sign) < 2:
            continue
        same_sign["_days"] = (same_sign["_dt"] - erow["_dt"]).abs().dt.days
        same_sign = same_sign.sort_values(["_days", "valor_absoluto"], ascending=[True, False])
        val_tuples = [(str(r["id_omie"]), float(r["valor"])) for _, r in same_sign.iterrows()]
        subset_ids = _find_subset_sum(val_tuples, target=float(erow["valor"]), tol=0.08, max_k=5)
        if subset_ids:
            matched_ext.add(eid)
            sub_df = same_sign[same_sign["id_omie"].isin(subset_ids)]
            soma_sub = round(float(sub_df["valor"].sum()), 2)
            for _, orow in sub_df.iterrows():
                matched_omi.add(str(orow["id_omie"]))
                pairs.append(
                    {
                        "id_extrato": eid,
                        "id_omie": str(orow["id_omie"]),
                        "metodo_pareamento": f"LOTE_1_PARA_{len(subset_ids)}_SOMA_EXATA",
                        "diferenca_valor": round(float(erow["valor"]) - soma_sub, 2),
                        "dias_defasagem": int((erow["_dt"] - orow["_dt"]).days),
                    }
                )

    return pd.DataFrame(pairs, columns=pair_cols), matched_ext, matched_omi


def sugerir_candidatos_conciliacao(
    target_row: dict[str, Any],
    pool_df: pd.DataFrame,
    is_extrato_target: bool = True,
    top_n: int = 5,
) -> pd.DataFrame:
    """Sugere candidatos mais próximos (data, valor e nome/histórico) para vínculo manual assistido."""
    if pool_df.empty or not target_row:
        return pd.DataFrame()
    cand = pool_df.copy()
    t_dt = pd.to_datetime(target_row.get("data_lancamento"))
    t_val = float(target_row.get("valor", 0.0))
    t_desc = str(target_row.get("descricao_normalizada", ""))

    cand["dias_distancia"] = (pd.to_datetime(cand["data_lancamento"]) - t_dt).abs().dt.days
    cand["dif_valor_abs"] = (cand["valor"] - t_val).abs().round(2)
    if is_extrato_target:
        cand["similaridade"] = cand.apply(
            lambda r: _token_similarity(t_desc, f"{r.get('cliente_fornecedor', '')} {r.get('descricao_normalizada', '')}"),
            axis=1,
        )
    else:
        t_full = f"{target_row.get('cliente_fornecedor', '')} {t_desc}"
        cand["similaridade"] = cand["descricao_normalizada"].apply(lambda d: _token_similarity(t_full, str(d)))

    cand = cand.sort_values(
        by=["dif_valor_abs", "similaridade", "dias_distancia"],
        ascending=[True, False, True],
    )
    return cand.head(top_n).reset_index(drop=True)


def executar_auditoria_completa(
    db: VerticeDB,
    competencia: str | None = None,
    banco: str | None = None,
) -> tuple[dict[str, Any], pd.DataFrame]:
    """
    Executa todas as regras ativas (AUD-01 a AUD-06 + Regras Customizadas SQL),
    salva a execução no DuckDB e retorna (resumo_kpis, df_divergencias).
    """
    execucao_id = f"EXEC-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    extrato_df = db.get_extrato_df(competencia=competencia, banco=banco)
    omie_df = db.get_omie_df(competencia=competencia, banco=banco)
    bank_params_df = db.get_bank_params_df()
    loans_df = db.get_loan_contracts_df()
    rules_df = db.get_rules_df()

    active_rules = {
        row["codigo"]: row.to_dict()
        for _, row in rules_df[rules_df["ativa"] == True].iterrows()  # noqa: E712
    }

    bank_params: dict[str, dict[str, Any]] = {
        row["banco_nome"]: row.to_dict() for _, row in bank_params_df.iterrows()
    }
    bank_tol_days = {
        bname: int(bcfg.get("tolerancia_dias_conciliacao", 2))
        for bname, bcfg in bank_params.items()
    }

    ext_lookup = {r["id_extrato"]: r.to_dict() for _, r in extrato_df.iterrows()}
    omi_lookup = {r["id_omie"]: r.to_dict() for _, r in omie_df.iterrows()}
    manual_links_df = db.get_manual_links_df()

    pairs_df, matched_ext_ids, matched_omi_ids = _reconciliar_extrato_omie(
        extrato_df, omie_df, bank_tol_days, manual_links_df=manual_links_df
    )

    # Mapa auxiliar id_extrato -> primeiro id_omie pareado
    ext_to_omi: dict[str, str] = {}
    for _, prow in pairs_df.iterrows():
        ext_to_omi.setdefault(prow["id_extrato"], prow["id_omie"])

    divergencias: list[dict[str, Any]] = []

    def _add_div(
        codigo_regra: str,
        data_ocorrencia: str,
        banco: str,
        valor_esperado: float,
        valor_realizado: float,
        valor_divergente: float,
        resumo: str,
        memoria_calculo: str,
        id_extrato: str | None = None,
        id_omie: str | None = None,
        extra_detail: dict[str, Any] | None = None,
        override_rule_meta: dict[str, Any] | None = None,
    ) -> None:
        meta = override_rule_meta or active_rules.get(codigo_regra, {})
        if not meta:
            return
        assinatura = VerticeDB.build_issue_signature(
            codigo_regra=codigo_regra,
            data_ocorrencia=data_ocorrencia,
            banco=banco or "Não Identificado",
            id_extrato=id_extrato,
            id_omie=id_omie,
            resumo=resumo,
        )
        detail_payload = {
            "assinatura_ocorrencia": assinatura,
            "extrato": ext_lookup.get(id_extrato) if id_extrato else None,
            "omie": omi_lookup.get(id_omie) if id_omie else None,
            "calculo": extra_detail or {},
        }
        div_id = make_deterministic_id(
            codigo_regra,
            execucao_id,
            data_ocorrencia,
            banco,
            id_extrato or "",
            id_omie or "",
            resumo,
        )
        divergencias.append(
            {
                "id_divergencia": div_id,
                "execucao_id": execucao_id,
                "data_ocorrencia": data_ocorrencia,
                "codigo_regra": codigo_regra,
                "nome_regra": meta["nome"],
                "categoria": meta["categoria"],
                "severidade": meta["severidade"],
                "banco": banco or "Não Identificado",
                "valor_esperado": round(float(valor_esperado), 2),
                "valor_realizado": round(float(valor_realizado), 2),
                "valor_divergente": round(abs(float(valor_divergente)), 2),
                "resumo": resumo,
                "memoria_calculo": memoria_calculo,
                "id_extrato": id_extrato,
                "id_omie": id_omie,
                "detalhe_json": json.dumps(detail_payload, ensure_ascii=False, default=str),
            }
        )

    # =========================================================================
    # AUD-01: Lançamentos no Omie sem correspondência no extrato (Órfãos)
    # =========================================================================
    if "AUD-01" in active_rules and not omie_df.empty:
        omie_baixados = omie_df[omie_df["status_titulo"].isin(["BAIXADO", "CONCILIADO"])]
        orfaos = omie_baixados[~omie_baixados["id_omie"].isin(matched_omi_ids)]
        for _, orow in orfaos.iterrows():
            _add_div(
                codigo_regra="AUD-01",
                data_ocorrencia=orow["data_lancamento"],
                banco=orow["banco_nome"],
                valor_esperado=float(orow["valor"]),
                valor_realizado=0.0,
                valor_divergente=float(orow["valor_absoluto"]),
                resumo=(
                    f"Baixa órfã no Omie sem reflexo no extrato: {orow['descricao_original']} "
                    f"(Doc: {orow['documento']})"
                ),
                memoria_calculo=(
                    f"Lançamento '{orow['id_omie']}' consta como {orow['status_titulo']} no Omie em "
                    f"{orow['data_lancamento']} no valor de R$ {orow['valor']:,.2f}, porém não há "
                    f"movimentação correspondente no extrato do banco {orow['banco_nome']}."
                ),
                id_extrato=None,
                id_omie=orow["id_omie"],
            )

    # =========================================================================
    # AUD-02: Lançamentos no extrato sem baixa no ERP (Fantasmas)
    # =========================================================================
    if "AUD-02" in active_rules and not extrato_df.empty:
        fantasmas = extrato_df[~extrato_df["id_extrato"].isin(matched_ext_ids)]
        for _, erow in fantasmas.iterrows():
            _add_div(
                codigo_regra="AUD-02",
                data_ocorrencia=erow["data_lancamento"],
                banco=erow["banco_nome"],
                valor_esperado=0.0,
                valor_realizado=float(erow["valor"]),
                valor_divergente=float(erow["valor_absoluto"]),
                resumo=(
                    f"Movimento bancário fantasma sem baixa no Omie: {erow['descricao_original']} "
                    f"(Doc: {erow['documento']})"
                ),
                memoria_calculo=(
                    f"Transação '{erow['documento']}' efetivada no extrato do {erow['banco_nome']} em "
                    f"{erow['data_lancamento']} (R$ {erow['valor']:,.2f}) não possui lançamento baixado "
                    f"correspondente no ERP Omie."
                ),
                id_extrato=erow["id_extrato"],
                id_omie=None,
            )

    # =========================================================================
    # AUD-03: Divergência em Tarifas de Boletos
    # =========================================================================
    if "AUD-03" in active_rules and not extrato_df.empty:
        for _, erow in extrato_df.iterrows():
            bcfg = bank_params.get(erow["banco_nome"], {})
            tarifa_contratada = float(bcfg.get("tarifa_boleto_contratada", 2.50))
            eid = erow["id_extrato"]
            oid = ext_to_omi.get(eid)
            orow_dict = omi_lookup.get(oid) if oid else None

            # Caso 1: Liquidação de boleto com Valor Nominal > Valor Líquido Creditado
            if erow["natureza_operacao"] == "BOLETO_LIQUIDACAO" and erow["valor"] > 0:
                vn = erow["valor_nominal"]
                if (vn is None or pd.isna(vn)) and orow_dict:
                    vn = orow_dict.get("valor_nominal") or orow_dict.get("valor_absoluto")
                vl = erow["valor_liquido"] if pd.notna(erow["valor_liquido"]) else abs(float(erow["valor"]))
                if vn is not None and pd.notna(vn) and float(vn) > float(vl) + 0.009:
                    calc = calcular_divergencia_boleto(
                        valor_nominal=float(vn),
                        valor_liquido=float(vl),
                        tarifa_contratada=tarifa_contratada,
                        quantidade_boletos=1,
                    )
                    if calc["divergente"]:
                        _add_div(
                            codigo_regra="AUD-03",
                            data_ocorrencia=erow["data_lancamento"],
                            banco=erow["banco_nome"],
                            valor_esperado=float(calc["tarifa_contratada"]),
                            valor_realizado=float(calc["tarifa_cobrada"]),
                            valor_divergente=float(calc["delta_indevido"]),
                            resumo=(
                                f"Tarifa de boleto acima do contrato ({erow['banco_nome']}): cobrado "
                                f"R$ {calc['tarifa_cobrada']:.2f} vs contratado R$ {calc['tarifa_contratada']:.2f}"
                            ),
                            memoria_calculo=(
                                f"Valor Nominal R$ {float(vn):,.2f} - Valor Líquido Creditado R$ {float(vl):,.2f} "
                                f"= Tarifa Cobrada R$ {calc['tarifa_cobrada']:.2f} | Tarifa Contratada "
                                f"({erow['banco_nome']}): R$ {calc['tarifa_contratada']:.2f} | "
                                f"Cobrança a maior (Delta): R$ {calc['delta_indevido']:.2f}."
                            ),
                            id_extrato=eid,
                            id_omie=oid,
                            extra_detail=calc,
                        )

            # Caso 2: Linha dedicada de débito de tarifa de boleto no extrato (OFX/CSV)
            elif erow["natureza_operacao"] == "TARIFA_BOLETO" and erow["valor"] < 0:
                m_qtd = re.search(r"(?:QTDE|QTD|LOTE)\s*[:=]?\s*(\d{1,4})", erow["descricao_normalizada"])
                qtd = int(m_qtd.group(1)) if m_qtd else 1
                tarifa_cobrada = abs(float(erow["valor"]))
                calc = calcular_divergencia_boleto(
                    valor_nominal=tarifa_cobrada,
                    valor_liquido=0.0,
                    tarifa_contratada=tarifa_contratada,
                    quantidade_boletos=qtd,
                )
                if calc["divergente"]:
                    _add_div(
                        codigo_regra="AUD-03",
                        data_ocorrencia=erow["data_lancamento"],
                        banco=erow["banco_nome"],
                        valor_esperado=float(calc["tarifa_contratada"]),
                        valor_realizado=float(calc["tarifa_cobrada"]),
                        valor_divergente=float(calc["delta_indevido"]),
                        resumo=(
                            f"Débito de tarifa de cobrança excedente ({qtd} boleto(s)): "
                            f"R$ {calc['tarifa_unitaria_cobrada']:.2f}/título vs R$ {tarifa_contratada:.2f} contratado"
                        ),
                        memoria_calculo=(
                            f"Débito de tarifa no extrato: R$ {calc['tarifa_cobrada']:.2f} para {qtd} título(s) "
                            f"(R$ {calc['tarifa_unitaria_cobrada']:.2f}/boleto) | Tarifa contratada "
                            f"{erow['banco_nome']}: R$ {calc['tarifa_contratada']:.2f} | "
                            f"Delta cobrado a maior: R$ {calc['delta_indevido']:.2f}."
                        ),
                        id_extrato=eid,
                        id_omie=oid,
                        extra_detail=calc,
                    )

    # =========================================================================
    # AUD-04: Divergência de Taxas de Antecipação de Recebíveis (VP)
    # =========================================================================
    if "AUD-04" in active_rules and not extrato_df.empty:
        antecipacoes = extrato_df[
            (extrato_df["natureza_operacao"] == "ANTECIPACAO_RECEBIVEIS") & (extrato_df["valor"] > 0)
        ]
        for _, erow in antecipacoes.iterrows():
            eid = erow["id_extrato"]
            oid = ext_to_omi.get(eid)
            orow_dict = omi_lookup.get(oid) if oid else None
            bcfg = bank_params.get(erow["banco_nome"], {})

            taxa_mensal = float(bcfg.get("taxa_antecipacao_mensal", 1.90))
            modalidade = str(bcfg.get("modalidade_antecipacao", "COMPOSTO"))
            iof_tarifa = float(bcfg.get("iof_tarifa_lote", 0.0))

            vn = erow["valor_nominal"] if pd.notna(erow["valor_nominal"]) else None
            if vn is None and orow_dict and pd.notna(orow_dict.get("valor_nominal")):
                vn = float(orow_dict["valor_nominal"])

            prazo = erow["prazo_dias"] if pd.notna(erow["prazo_dias"]) else None
            if prazo is None:
                venc_str = erow["data_vencimento_original"]
                if (not venc_str or pd.isna(venc_str)) and orow_dict:
                    venc_str = orow_dict.get("data_vencimento")
                if venc_str and pd.notna(venc_str):
                    d_cred = datetime.strptime(erow["data_lancamento"], "%Y-%m-%d").date()
                    d_venc = datetime.strptime(str(venc_str), "%Y-%m-%d").date()
                    diff_d = (d_venc - d_cred).days
                    if diff_d > 0:
                        prazo = float(diff_d)

            vl = float(erow["valor_liquido"]) if pd.notna(erow["valor_liquido"]) else abs(float(erow["valor"]))

            if vn is not None and prazo is not None and float(vn) > 0 and float(prazo) > 0:
                vp_res = calcular_vp_antecipacao(
                    valor_nominal=float(vn),
                    valor_liquido_creditado=vl,
                    prazo_dias=float(prazo),
                    taxa_mensal_pct=taxa_mensal,
                    modalidade=modalidade,
                    iof_tarifa_lote=iof_tarifa,
                    tolerancia_reais=0.10,
                )
                if vp_res["divergente"]:
                    _add_div(
                        codigo_regra="AUD-04",
                        data_ocorrencia=erow["data_lancamento"],
                        banco=erow["banco_nome"],
                        valor_esperado=float(vp_res["vp_teorico"]),
                        valor_realizado=vl,
                        valor_divergente=float(vp_res["valor_divergente"]),
                        resumo=(
                            f"Desconto Indevido de Antecipação ({erow['banco_nome']}): líquido creditado "
                            f"R$ {vl:,.2f} vs VP teórico R$ {vp_res['vp_teorico']:,.2f}"
                        ),
                        memoria_calculo=(
                            f"Lote Nominal R$ {float(vn):,.2f} por {int(prazo)} dias ({modalidade}) | "
                            f"Taxa contratada: {taxa_mensal:.2f}% a.m. -> VP Teórico: R$ {vp_res['vp_teorico']:,.2f} | "
                            f"Líquido Creditado: R$ {vl:,.2f} | Taxa efetiva praticada: "
                            f"{vp_res['taxa_efetiva_mensal_pct']:.2f}% a.m. (Spread oculto: "
                            f"{vp_res['spread_oculto_pp']:+.2f} p.p.) | Divergência: R$ {vp_res['diferenca_vp']:,.2f}."
                        ),
                        id_extrato=eid,
                        id_omie=oid,
                        extra_detail=vp_res,
                    )

    # =========================================================================
    # AUD-05: Conformidade de Empréstimos (Price / SAC + Alocação Contábil Omie)
    # =========================================================================
    if "AUD-05" in active_rules and not loans_df.empty and not omie_df.empty:
        active_loans = loans_df[loans_df["ativo"] == True]  # noqa: E712
        all_dates = pd.concat(
            [
                extrato_df["data_lancamento"] if not extrato_df.empty else pd.Series(dtype=str),
                omie_df["data_lancamento"] if not omie_df.empty else pd.Series(dtype=str),
            ]
        )
        min_month = str(all_dates.min())[:7] if not all_dates.empty else "0000-00"
        max_month = str(all_dates.max())[:7] if not all_dates.empty else "9999-99"

        for _, ctr in active_loans.iterrows():
            kw = clean_description(ctr["palavra_chave_omie"])
            cid = str(ctr["id_contrato"]).upper()

            schedule = gerar_tabela_emprestimo(
                valor_principal=float(ctr["valor_principal"]),
                taxa_juros_mensal_pct=float(ctr["taxa_juros_mensal"]),
                prazo_meses=int(ctr["prazo_meses"]),
                sistema_amortizacao=str(ctr["sistema_amortizacao"]),
                data_primeira_parcela=str(ctr["data_primeira_parcela"]),
            )

            # Filtra lançamentos do Omie vinculados ao contrato
            omie_ctr = omie_df[
                (omie_df["status_titulo"].isin(["BAIXADO", "CONCILIADO"]))
                & (omie_df["valor"] < 0)
                & (
                    omie_df["contrato_ref"].fillna("").astype(str).str.upper().str.contains(re.escape(cid), na=False)
                    | omie_df["contrato_ref"].fillna("").astype(str).str.upper().str.contains(re.escape(kw), na=False)
                    | omie_df["descricao_normalizada"].str.contains(re.escape(kw), na=False)
                    | omie_df["documento"].astype(str).str.upper().str.contains(re.escape(kw), na=False)
                )
            ].copy()

            if omie_ctr.empty:
                continue

            omie_ctr["_comp"] = omie_ctr["data_lancamento"].str.slice(0, 7)
            comps_relevantes = set(omie_ctr["_comp"].unique())

            for _, srow in schedule.iterrows():
                comp = srow["competencia"]
                if comp not in comps_relevantes and not (min_month <= comp <= max_month):
                    continue
                lanc_mes = omie_ctr[omie_ctr["_comp"] == comp]
                if lanc_mes.empty:
                    continue

                total_pago_omie = round(float(lanc_mes["valor_absoluto"].sum()), 2)
                principal_passivo = round(
                    float(
                        lanc_mes[lanc_mes["grupo_contabil"] == "PASSIVO_EMPRESTIMO"]["valor_absoluto"].sum()
                    ),
                    2,
                )
                juros_despesa = round(
                    float(
                        lanc_mes[lanc_mes["grupo_contabil"] == "DESPESA_JUROS"]["valor_absoluto"].sum()
                    ),
                    2,
                )
                # Caso o usuário tenha informado juros_multa embutido numa linha única de passivo
                if juros_despesa == 0.0 and lanc_mes["juros_multa"].sum() > 0:
                    juros_embutido = round(float(lanc_mes["juros_multa"].sum()), 2)
                    juros_despesa = juros_embutido
                    if principal_passivo >= total_pago_omie:
                        principal_passivo = round(total_pago_omie - juros_embutido, 2)

                pmt_teo = float(srow["prestacao_teorica"])
                amort_teo = float(srow["amortizacao_teorica"])
                juros_teo = float(srow["juros_teoricos"])

                diff_pmt = round(abs(total_pago_omie - pmt_teo), 2)
                diff_passivo = round(abs(principal_passivo - amort_teo), 2)
                diff_juros = round(abs(juros_despesa - juros_teo), 2)

                first_omie_id = str(lanc_mes.iloc[0]["id_omie"])
                paired_ext_rows = pairs_df[pairs_df["id_omie"].isin(lanc_mes["id_omie"])]
                linked_ext_id = str(paired_ext_rows.iloc[0]["id_extrato"]) if not paired_ext_rows.empty else None

                if diff_pmt > 0.15 or diff_passivo > 0.50 or diff_juros > 0.50:
                    motivos: list[str] = []
                    if diff_pmt > 0.15:
                        motivos.append(
                            f"Prestação paga R$ {total_pago_omie:,.2f} difere da parcela {srow['sistema']} "
                            f"teórica R$ {pmt_teo:,.2f} (Δ R$ {total_pago_omie - pmt_teo:+,.2f})"
                        )
                    if diff_passivo > 0.50 or diff_juros > 0.50:
                        motivos.append(
                            f"Alocação contábil incorreta: Passivo baixado R$ {principal_passivo:,.2f} "
                            f"(teórico R$ {amort_teo:,.2f}) | Juros em Despesa Financeira R$ {juros_despesa:,.2f} "
                            f"(teórico R$ {juros_teo:,.2f})"
                        )

                    impacto = max(diff_pmt, diff_passivo, diff_juros)
                    _add_div(
                        codigo_regra="AUD-05",
                        data_ocorrencia=str(lanc_mes.iloc[0]["data_lancamento"]),
                        banco=str(ctr["banco_nome"]),
                        valor_esperado=pmt_teo,
                        valor_realizado=total_pago_omie,
                        valor_divergente=impacto,
                        resumo=(
                            f"Divergência Contrato {ctr['nome_contrato']} (Parcela {int(srow['numero_parcela'])}/"
                            f"{int(ctr['prazo_meses'])} - {srow['sistema']})"
                        ),
                        memoria_calculo=" | ".join(motivos),
                        id_extrato=linked_ext_id,
                        id_omie=first_omie_id,
                        extra_detail={
                            "contrato": ctr.to_dict(),
                            "parcela_teorica": srow.to_dict(),
                            "realizado_omie": {
                                "total_pago": total_pago_omie,
                                "principal_passivo": principal_passivo,
                                "juros_despesa_financeira": juros_despesa,
                                "qtd_lancamentos_omie": len(lanc_mes),
                            },
                        },
                    )

    # =========================================================================
    # AUD-06: Fechamento Diário Divergente
    # =========================================================================
    if "AUD-06" in active_rules and not extrato_df.empty and not omie_df.empty:
        fechamento_dia = db.get_fechamento_diario_df(competencia=competencia, banco=banco)
        dias_divergentes = fechamento_dia[fechamento_dia["diferenca"].abs() > 0.05]
        for _, drow in dias_divergentes.iterrows():
            dt_dia = str(drow["data_lancamento"])
            banco_dia = str(drow["banco_nome"])
            dif_dia = float(drow["diferenca"])
            ext_dia_val = float(drow["mov_liquida_extrato"])
            omi_dia_val = float(drow["mov_liquida_omie"])
            _add_div(
                codigo_regra="AUD-06",
                data_ocorrencia=dt_dia,
                banco=banco_dia,
                valor_esperado=omi_dia_val,
                valor_realizado=ext_dia_val,
                valor_divergente=abs(dif_dia),
                resumo=(
                    f"Fechamento diário divergente ({dt_dia} - {banco_dia}): "
                    f"Extrato {ext_dia_val:,.2f} vs Omie {omi_dia_val:,.2f} (Delta: R$ {dif_dia:,.2f})"
                ),
                memoria_calculo=(
                    f"Na data {dt_dia}, a movimentação líquida total do extrato do {banco_dia} "
                    f"foi de R$ {ext_dia_val:,.2f} ({int(drow['qtd_extrato'])} lançamentos) contra "
                    f"R$ {omi_dia_val:,.2f} ({int(drow['qtd_omie'])} baixas) no Omie, gerando uma quebra "
                    f"líquida diária de R$ {dif_dia:,.2f}."
                ),
                id_extrato=None,
                id_omie=None,
                extra_detail={
                    "data": dt_dia,
                    "banco": banco_dia,
                    "mov_liquida_extrato": ext_dia_val,
                    "mov_liquida_omie": omi_dia_val,
                    "diferenca_dia": dif_dia,
                    "entradas_extrato": float(drow["entradas_extrato"]),
                    "entradas_omie": float(drow["entradas_omie"]),
                    "saidas_extrato": float(drow["saidas_extrato"]),
                    "saidas_omie": float(drow["saidas_omie"]),
                },
            )

    # =========================================================================
    # Regras Customizadas SQL (CUS-*) executadas direto no DuckDB
    # =========================================================================
    # Atualiza pares_conciliados antes de rodar as views SQL customizadas
    with db.connect() as con:
        con.execute("DELETE FROM pares_conciliados")
        if not pairs_df.empty:
            con.register("tmp_pairs_live", pairs_df)
            con.execute(
                """
                INSERT INTO pares_conciliados
                (id_extrato, id_omie, metodo_pareamento, diferenca_valor, dias_defasagem)
                SELECT id_extrato, id_omie, metodo_pareamento, diferenca_valor, dias_defasagem
                FROM tmp_pairs_live
                """
            )
            con.unregister("tmp_pairs_live")

        for codigo, rmeta in active_rules.items():
            if rmeta.get("tipo_regra") != "CUSTOM":
                continue
            sql_cond = str(rmeta.get("condicao_sql") or "").strip()
            safe, _ = validate_custom_sql_safety(sql_cond)
            if not safe:
                continue
            try:
                query = _build_custom_rule_query(sql_cond)
                custom_hits = con.execute(query).df()
            except Exception:
                continue

            for _, crow in custom_hits.iterrows():
                row_d = crow.to_dict()
                cid_ext = str(row_d.get("id_extrato")) if pd.notna(row_d.get("id_extrato")) else None
                cid_omi = str(row_d.get("id_omie")) if pd.notna(row_d.get("id_omie")) else None
                dt_oc = str(row_d.get("data_ocorrencia") or datetime.now().strftime("%Y-%m-%d"))[:10]
                banco_c = str(row_d.get("banco") or "Multi-Banco")
                v_esp = float(row_d.get("valor_esperado") or 0.0)
                v_rea = float(row_d.get("valor_realizado") or 0.0)
                v_div = float(row_d.get("valor_divergente") or abs(v_rea - v_esp))
                desc_ref = (
                    row_d.get("descricao_extrato")
                    or row_d.get("descricao_omie")
                    or f" violação detectada em {codigo}"
                )
                _add_div(
                    codigo_regra=codigo,
                    data_ocorrencia=dt_oc,
                    banco=banco_c,
                    valor_esperado=v_esp,
                    valor_realizado=v_rea,
                    valor_divergente=v_div,
                    resumo=f"{rmeta['nome']}: {desc_ref}",
                    memoria_calculo=f"Regra SQL Customizada [{codigo}] acionada pela condição: {sql_cond}",
                    id_extrato=cid_ext,
                    id_omie=cid_omi,
                    extra_detail={"linha_sql": row_d, "condicao_sql": sql_cond},
                    override_rule_meta=rmeta,
                )

    div_df = (
        validate_divergencia_df(pd.DataFrame(divergencias))
        if divergencias
        else pd.DataFrame(columns=DIVERGENCIA_COLUMNS)
    )

    # Cálculo dos KPIs Executivos
    total_extrato = len(extrato_df)
    omie_efetivos = (
        len(omie_df[omie_df["status_titulo"].isin(["BAIXADO", "CONCILIADO"])])
        if not omie_df.empty
        else 0
    )
    universo_auditado = max(1, total_extrato + omie_efetivos)

    ids_afetados = set()
    if not div_df.empty:
        ids_afetados.update(div_df["id_extrato"].dropna().astype(str).tolist())
        ids_afetados.update(div_df["id_omie"].dropna().astype(str).tolist())
        sem_id = len(div_df[div_df["id_extrato"].isna() & div_df["id_omie"].isna()])
    else:
        sem_id = 0

    itens_divergentes = min(universo_auditado, len(ids_afetados) + sem_id)
    aderencia_pct = (
        round(max(0.0, (1.0 - (itens_divergentes / universo_auditado)) * 100.0), 2)
        if (total_extrato + omie_efetivos) > 0
        else 100.0
    )

    valor_total_divergente = round(float(div_df["valor_divergente"].sum()), 2) if not div_df.empty else 0.0
    alertas_criticos = int((div_df["severidade"] == "Crítico").sum()) if not div_df.empty else 0

    metrics = {
        "execucao_id": execucao_id,
        "executado_em": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total_extrato": total_extrato,
        "total_omie": len(omie_df),
        "total_pares_conciliados": len(pairs_df),
        "total_divergencias": len(div_df),
        "alertas_criticos": alertas_criticos,
        "valor_total_divergente": valor_total_divergente,
        "aderencia_erp_pct": aderencia_pct,
    }

    db.save_audit_execution(
        execucao_id=execucao_id,
        pairs_df=pairs_df,
        divergencias_df=div_df,
        metrics=metrics,
    )
    return metrics, div_df
