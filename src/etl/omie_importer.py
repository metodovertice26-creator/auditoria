"""Importador de relatórios exportados do ERP Omie (CSV e XLSX)."""

from __future__ import annotations

import csv
import io
from typing import Any

import pandas as pd

from src.etl.normalizer import (
    classify_omie_accounting_group,
    clean_description,
    make_deterministic_id,
    parse_date_iso,
    parse_money_br,
    resolve_bank_identity,
    strip_accents,
)
from src.schemas import validate_omie_df

OMIE_HEADER_ALIASES: dict[str, tuple[str, ...]] = {
    "id_omie": ("codigo lancamento", "id lancamento", "codigo omie", "id", "cod lancamento", "codigo titulo"),
    "data_lancamento": (
        "data baixa",
        "data da baixa",
        "data pagamento",
        "data de pagamento",
        "data movimento",
        "data lancamento",
        "dt movimento",
        "data conciliacao",
        "data credito",
        "data",
    ),
    "data_vencimento": ("data vencimento", "data de vencimento", "vencimento", "dt vencimento"),
    "status_titulo": ("situacao", "status", "status titulo", "estado"),
    "tipo_lancamento": ("tipo", "natureza", "entrada saida", "tipo lancamento", "operacao"),
    "banco_nome": ("banco", "conta corrente", "instituicao", "conta bancaria"),
    "conta_corrente": ("nro conta", "numero conta", "agencia conta", "cod conta corrente"),
    "categoria": ("categoria", "plano de contas", "classificacao", "conta contabil", "categoria omie"),
    "cliente_fornecedor": (
        "cliente fornecedor",
        "cliente",
        "fornecedor",
        "razao social",
        "nome fantasia",
        "favorecido",
        "sacado",
    ),
    "documento": (
        "documento",
        "numero documento",
        "num documento",
        "nr doc",
        "nosso numero",
        "nota fiscal",
        "nf",
        "parcela",
    ),
    "descricao": ("observacoes", "historico", "descricao", "detalhes", "complemento", "observacao"),
    "valor": (
        "valor pago",
        "valor recebido",
        "valor liquido",
        "valor movimento",
        "valor lancamento",
        "valor",
        "valor r$",
    ),
    "valor_nominal": ("valor nominal", "valor original", "valor titulo", "valor do titulo", "valor bruto"),
    "juros_multa": ("juros", "juros multa", "multa juros", "encargos", "valor juros"),
    "desconto_tarifa": ("desconto", "tarifa", "desconto tarifa", "abatimento"),
    "contrato_ref": ("contrato", "id contrato", "nro contrato", "numero contrato", "referencia contrato"),
}


def _norm_col(col_name: Any) -> str:
    txt = strip_accents(str(col_name or "")).lower().strip()
    for ch in ("_", "-", "/", "(", ")", ".", ":"):
        txt = txt.replace(ch, " ")
    return " ".join(txt.split())


def _match_omie_columns(columns: list[Any]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    norm_map = {str(c): _norm_col(c) for c in columns}
    for canonical, aliases in OMIE_HEADER_ALIASES.items():
        for orig_col, ncol in norm_map.items():
            if ncol in aliases and orig_col not in mapping.values():
                mapping[canonical] = orig_col
                break
        if canonical in mapping:
            continue
        for orig_col, ncol in norm_map.items():
            if any(ncol.startswith(a) or f" {a}" in f" {ncol}" for a in aliases) and orig_col not in mapping.values():
                mapping[canonical] = orig_col
                break
    return mapping


def _read_omie_table(raw_bytes: bytes, filename: str) -> pd.DataFrame:
    lower = filename.lower()
    if lower.endswith((".xlsx", ".xls")):
        raw_df = pd.read_excel(io.BytesIO(raw_bytes), header=None)
        for idx in range(min(18, len(raw_df))):
            candidate_cols = [str(v) for v in raw_df.iloc[idx].tolist()]
            matched = _match_omie_columns(candidate_cols)
            if ("data_lancamento" in matched or "data_vencimento" in matched) and (
                "valor" in matched or "valor_nominal" in matched
            ):
                df = raw_df.iloc[idx + 1 :].copy()
                df.columns = candidate_cols
                return df.reset_index(drop=True)
        return pd.read_excel(io.BytesIO(raw_bytes))

    text = None
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            text = raw_bytes.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        text = raw_bytes.decode("latin-1", errors="replace")

    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return pd.DataFrame()

    sample = "\n".join(lines[:15])
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=";,|\t")
        sep = dialect.delimiter
    except Exception:
        sep = ";" if sample.count(";") >= sample.count(",") else ","

    header_idx = 0
    for idx, line in enumerate(lines[:15]):
        parts = [p.strip().strip('"') for p in line.split(sep)]
        matched = _match_omie_columns(parts)
        if ("data_lancamento" in matched or "data_vencimento" in matched) and (
            "valor" in matched or "valor_nominal" in matched
        ):
            header_idx = idx
            break

    return pd.read_csv(io.StringIO("\n".join(lines[header_idx:])), sep=sep, dtype=str)


def _normalize_status(raw_status: Any) -> str:
    s = clean_description(raw_status)
    if any(k in s for k in ("CONCILIAD",)):
        return "CONCILIADO"
    if any(k in s for k in ("RECEBID", "PAGO", "LIQUIDAD", "BAIXAD", "QUITAD", "REALIZAD")):
        return "BAIXADO"
    if any(k in s for k in ("CANCELAD",)):
        return "CANCELADO"
    if any(k in s for k in ("VENCID", "ATRASAD")):
        return "ATRASADO"
    if any(k in s for k in ("A VENCER", "ABERTO", "PREVIST", "PENDENT")):
        return "ABERTO"
    return "BAIXADO"


def parse_omie_report_bytes(
    raw_bytes: bytes,
    filename: str = "relatorio_omie.xlsx",
    origem_relatorio: str = "MOVIMENTO_CC",
) -> pd.DataFrame:
    """Lê relatórios exportados do Omie (Movimento CC, Contas a Pagar/Receber, Baixas)."""
    raw_df = _read_omie_table(raw_bytes, filename)
    if raw_df.empty:
        return validate_omie_df(pd.DataFrame())

    col_map = _match_omie_columns(list(raw_df.columns))
    if "data_lancamento" not in col_map and "data_vencimento" not in col_map:
        raise ValueError(
            f"Não foi possível identificar coluna de Data no relatório Omie '{filename}'. Colunas: {list(raw_df.columns)}"
        )

    rows: list[dict[str, Any]] = []
    for idx, rec in raw_df.iterrows():
        dt_lanc = (
            parse_date_iso(rec.get(col_map["data_lancamento"]))
            if "data_lancamento" in col_map
            else None
        )
        dt_venc = (
            parse_date_iso(rec.get(col_map["data_vencimento"]))
            if "data_vencimento" in col_map
            else None
        )
        effective_date = dt_lanc or dt_venc
        if not effective_date:
            continue

        valor = parse_money_br(rec.get(col_map["valor"])) if "valor" in col_map else None
        valor_nominal = (
            parse_money_br(rec.get(col_map["valor_nominal"]))
            if "valor_nominal" in col_map
            else None
        )
        if valor is None:
            valor = valor_nominal
        if valor is None or abs(valor) < 0.001:
            continue

        categoria = (
            str(rec.get(col_map["categoria"], "Sem Categoria") or "Sem Categoria").strip()
            if "categoria" in col_map
            else "Sem Categoria"
        )
        cliente_forn = (
            str(rec.get(col_map["cliente_fornecedor"], "Não Informado") or "Não Informado").strip()
            if "cliente_fornecedor" in col_map
            else "Não Informado"
        )
        desc_orig = (
            str(rec.get(col_map["descricao"], "") or "").strip()
            if "descricao" in col_map
            else ""
        )
        if not desc_orig or desc_orig.lower() == "nan":
            desc_orig = f"{categoria} - {cliente_forn}"
        desc_norm = clean_description(desc_orig)

        # Determina ENTRADA vs SAIDA
        tipo_raw = clean_description(rec.get(col_map["tipo_lancamento"], "")) if "tipo_lancamento" in col_map else ""
        if any(k in tipo_raw for k in ("SAIDA", "PAGAR", "DEBITO", "DESPESA", "PAGAMENTO")):
            valor = -abs(valor)
            tipo_lancamento = "SAIDA"
        elif any(k in tipo_raw for k in ("ENTRADA", "RECEBER", "CREDITO", "RECEITA", "RECEBIMENTO")):
            valor = abs(valor)
            tipo_lancamento = "ENTRADA"
        elif origem_relatorio == "CONTAS_PAGAR" and valor > 0:
            valor = -abs(valor)
            tipo_lancamento = "SAIDA"
        elif origem_relatorio == "CONTAS_RECEBER" and valor < 0:
            valor = abs(valor)
            tipo_lancamento = "ENTRADA"
        else:
            tipo_lancamento = "ENTRADA" if valor >= 0 else "SAIDA"

        bank_raw = (
            str(rec.get(col_map["banco_nome"], "") or "").strip()
            if "banco_nome" in col_map
            else ""
        )
        _, banco_nome = resolve_bank_identity(bank_hint=bank_raw)
        if banco_nome == "Banco Não Identificado" and bank_raw and bank_raw.lower() != "nan":
            banco_nome = bank_raw

        conta_cc = (
            str(rec.get(col_map["conta_corrente"], bank_raw or "CC-OMIE") or "CC-OMIE").strip()
            if "conta_corrente" in col_map
            else (bank_raw or "CC-OMIE")
        )
        status_titulo = (
            _normalize_status(rec.get(col_map["status_titulo"]))
            if "status_titulo" in col_map
            else "BAIXADO"
        )
        doc = (
            str(rec.get(col_map["documento"], "") or "").strip()
            if "documento" in col_map
            else ""
        )
        if not doc or doc.lower() == "nan":
            doc = f"OMIE-DOC-{idx + 1:04d}"

        juros_multa = (
            abs(parse_money_br(rec.get(col_map["juros_multa"])) or 0.0)
            if "juros_multa" in col_map
            else 0.0
        )
        desconto_tarifa = (
            abs(parse_money_br(rec.get(col_map["desconto_tarifa"])) or 0.0)
            if "desconto_tarifa" in col_map
            else 0.0
        )
        contrato_ref = (
            str(rec.get(col_map["contrato_ref"], "") or "").strip()
            if "contrato_ref" in col_map
            else None
        )
        if contrato_ref and contrato_ref.lower() == "nan":
            contrato_ref = None

        raw_id = (
            str(rec.get(col_map["id_omie"], "") or "").strip()
            if "id_omie" in col_map
            else ""
        )
        if not raw_id or raw_id.lower() == "nan":
            raw_id = make_deterministic_id(
                "OMIE",
                banco_nome,
                effective_date,
                f"{valor:.2f}",
                doc,
                desc_norm,
                idx,
            )

        grupo_contabil = classify_omie_accounting_group(categoria, desc_orig)

        rows.append(
            {
                "id_omie": raw_id,
                "arquivo_origem": filename,
                "origem_relatorio": origem_relatorio,
                "banco_nome": banco_nome,
                "conta_corrente": conta_cc,
                "data_lancamento": effective_date,
                "data_vencimento": dt_venc,
                "status_titulo": status_titulo,
                "tipo_lancamento": tipo_lancamento,
                "categoria": categoria,
                "grupo_contabil": grupo_contabil,
                "cliente_fornecedor": cliente_forn,
                "documento": doc,
                "descricao_original": desc_orig,
                "descricao_normalizada": desc_norm,
                "valor": round(valor, 2),
                "valor_absoluto": round(abs(valor), 2),
                "valor_nominal": abs(valor_nominal) if valor_nominal is not None else abs(valor),
                "juros_multa": round(juros_multa, 2),
                "desconto_tarifa": round(desconto_tarifa, 2),
                "contrato_ref": contrato_ref,
            }
        )

    return validate_omie_df(pd.DataFrame(rows))
