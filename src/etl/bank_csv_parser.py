"""Parser resiliente para extratos bancários em CSV e XLSX de múltiplos bancos."""

from __future__ import annotations

import csv
import io
from typing import Any

import pandas as pd

from src.etl.normalizer import (
    classify_bank_operation,
    clean_description,
    extract_memo_financial_hints,
    make_deterministic_id,
    parse_date_iso,
    parse_money_br,
    resolve_bank_identity,
    strip_accents,
)
from src.schemas import validate_extrato_df

HEADER_ALIASES: dict[str, tuple[str, ...]] = {
    "data": ("data", "dt", "data lancamento", "data movimento", "data mov", "dt lancamento", "dt balancete"),
    "descricao": ("descricao", "historico", "lancamento", "detalhe", "complemento", "memo", "favorecido"),
    "valor": ("valor", "valor r$", "valor (r$)", "vlr", "valor lancamento", "valor movimentacao", "montante"),
    "credito": ("credito", "entrada", "creditos", "valor credito"),
    "debito": ("debito", "saida", "debitos", "valor debito"),
    "documento": ("documento", "doc", "nr doc", "num doc", "fitid", "nsu", "autenticacao", "nosso numero"),
    "banco": ("banco", "instituicao", "nome banco", "cod banco", "codigo banco"),
    "conta": ("conta", "conta corrente", "agencia conta", "nro conta"),
    "valor_nominal": ("valor nominal", "vlr nominal", "valor bruto", "nominal", "valor titulo"),
    "valor_liquido": ("valor liquido", "vlr liquido", "liquido creditado", "liquido"),
    "prazo_dias": ("prazo", "prazo dias", "dias antecipacao", "dias"),
    "data_vencimento_original": ("vencimento original", "data vencimento", "dt vencimento", "vencimento"),
}


def _norm_col(col_name: Any) -> str:
    txt = strip_accents(str(col_name or "")).lower().strip()
    for ch in ("_", "-", "/", "(", ")", ".", ":"):
        txt = txt.replace(ch, " ")
    return " ".join(txt.split())


def _match_columns(columns: list[Any]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    norm_map = {str(c): _norm_col(c) for c in columns}
    for canonical, aliases in HEADER_ALIASES.items():
        # Match exato primeiro
        for orig_col, ncol in norm_map.items():
            if ncol in aliases and orig_col not in mapping.values():
                mapping[canonical] = orig_col
                break
        if canonical in mapping:
            continue
        # Match por substring controlada
        for orig_col, ncol in norm_map.items():
            if any(ncol.startswith(a) or f" {a}" in f" {ncol}" for a in aliases) and orig_col not in mapping.values():
                mapping[canonical] = orig_col
                break
    return mapping


def _read_tabular_bytes(raw_bytes: bytes, filename: str) -> pd.DataFrame:
    lower_name = filename.lower()
    if lower_name.endswith((".xlsx", ".xls")):
        raw_df = pd.read_excel(io.BytesIO(raw_bytes), header=None)
        for idx in range(min(15, len(raw_df))):
            candidate_cols = [str(v) for v in raw_df.iloc[idx].tolist()]
            matched = _match_columns(candidate_cols)
            if "data" in matched and ("valor" in matched or "credito" in matched or "valor_liquido" in matched):
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

    # Detecta delimitador e linha real de cabeçalho (ignorando linhas iniciais de resumo bancário)
    sample = "\n".join(lines[:15])
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=";,|\t")
        sep = dialect.delimiter
    except Exception:
        sep = ";" if sample.count(";") >= sample.count(",") else ","

    header_idx = 0
    for idx, line in enumerate(lines[:15]):
        parts = [p.strip().strip('"') for p in line.split(sep)]
        matched = _match_columns(parts)
        if "data" in matched and ("valor" in matched or "credito" in matched or "valor_liquido" in matched):
            header_idx = idx
            break

    cleaned_text = "\n".join(lines[header_idx:])
    return pd.read_csv(io.StringIO(cleaned_text), sep=sep, dtype=str)


def parse_bank_tabular_bytes(
    raw_bytes: bytes,
    filename: str = "extrato.csv",
    bank_override: str | None = None,
) -> pd.DataFrame:
    """Converte CSV ou XLSX de extrato bancário no schema padronizado ExtratoBancarioSchema."""
    raw_df = _read_tabular_bytes(raw_bytes, filename)
    if raw_df.empty:
        return validate_extrato_df(pd.DataFrame())

    col_map = _match_columns(list(raw_df.columns))
    if "data" not in col_map:
        raise ValueError(
            f"Não foi possível localizar a coluna de Data no arquivo '{filename}'. Colunas encontradas: {list(raw_df.columns)}"
        )

    rows: list[dict[str, Any]] = []
    for idx, rec in raw_df.iterrows():
        dt_iso = parse_date_iso(rec.get(col_map["data"]))
        if not dt_iso:
            continue

        # Ignora linhas de SALDO ANTERIOR / SALDO DO DIA comuns em CSVs de bancos
        desc_orig = str(rec.get(col_map.get("descricao", ""), "") or "LANCAMENTO EXTRATO").strip()
        desc_norm = clean_description(desc_orig)
        if any(
            desc_norm.startswith(prefix)
            for prefix in ("SALDO ANTERIOR", "SALDO DO DIA", "SALDO FINAL", "S A L D O", "SALDO TOTAL")
        ):
            continue

        valor: float | None = None
        if "valor" in col_map:
            valor = parse_money_br(rec.get(col_map["valor"]))
        if valor is None and ("credito" in col_map or "debito" in col_map):
            cred = parse_money_br(rec.get(col_map.get("credito", ""))) or 0.0
            deb = parse_money_br(rec.get(col_map.get("debito", ""))) or 0.0
            if cred != 0.0 or deb != 0.0:
                valor = abs(cred) - abs(deb)
        if valor is None and "valor_liquido" in col_map:
            valor = parse_money_br(rec.get(col_map["valor_liquido"]))

        if valor is None or abs(valor) < 0.001:
            continue

        bank_cell = str(rec.get(col_map.get("banco", ""), "") or "").strip() if "banco" in col_map else ""
        banco_codigo, banco_nome = resolve_bank_identity(
            bank_code=bank_cell if bank_cell.isdigit() else None,
            bank_hint=bank_override or bank_cell or filename,
        )
        if bank_override and bank_override.strip():
            _, ov_name = resolve_bank_identity(bank_hint=bank_override)
            banco_nome = ov_name if ov_name != "Banco Não Identificado" else bank_override.strip()

        conta = (
            str(rec.get(col_map["conta"], "CONTA-CSV") or "CONTA-CSV").strip()
            if "conta" in col_map
            else "CONTA-CSV"
        )
        doc = (
            str(rec.get(col_map["documento"], "") or "").strip()
            if "documento" in col_map
            else ""
        )
        if not doc or doc.lower() == "nan":
            doc = f"CSV-{idx + 1:04d}"

        hints = extract_memo_financial_hints(desc_norm)
        valor_nominal = (
            parse_money_br(rec.get(col_map["valor_nominal"]))
            if "valor_nominal" in col_map
            else hints["valor_nominal"]
        )
        valor_liquido = (
            parse_money_br(rec.get(col_map["valor_liquido"]))
            if "valor_liquido" in col_map
            else (abs(valor) if valor > 0 else None)
        )
        prazo_raw = rec.get(col_map["prazo_dias"]) if "prazo_dias" in col_map else None
        prazo_dias = parse_money_br(prazo_raw) if prazo_raw is not None else hints["prazo_dias"]
        venc_orig = (
            parse_date_iso(rec.get(col_map["data_vencimento_original"]))
            if "data_vencimento_original" in col_map
            else hints["data_vencimento_original"]
        )

        natureza = classify_bank_operation(desc_norm, valor)
        # Se houver valor_nominal > valor_liquido e prazo_dias informado, pode ser antecipação ou boleto
        if natureza == "OUTROS" and valor > 0 and valor_nominal and valor_nominal > abs(valor):
            if prazo_dias and prazo_dias >= 3:
                natureza = "ANTECIPACAO_RECEBIVEIS"
            else:
                natureza = "BOLETO_LIQUIDACAO"

        id_extrato = make_deterministic_id(
            "CSV",
            banco_codigo,
            conta,
            dt_iso,
            f"{valor:.2f}",
            doc,
            desc_norm,
        )

        rows.append(
            {
                "id_extrato": id_extrato,
                "arquivo_origem": filename,
                "banco_codigo": banco_codigo,
                "banco_nome": banco_nome,
                "conta": conta,
                "data_lancamento": dt_iso,
                "tipo_movimento": "CREDITO" if valor >= 0 else "DEBITO",
                "natureza_operacao": natureza,
                "descricao_original": desc_orig,
                "descricao_normalizada": desc_norm,
                "documento": doc,
                "valor": round(valor, 2),
                "valor_absoluto": round(abs(valor), 2),
                "valor_nominal": valor_nominal,
                "valor_liquido": valor_liquido,
                "prazo_dias": prazo_dias,
                "data_vencimento_original": venc_orig,
            }
        )

    return validate_extrato_df(pd.DataFrame(rows))
