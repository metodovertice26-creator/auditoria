"""Parser universal de arquivos OFX (1.x SGML e 2.x XML) para qualquer banco brasileiro."""

from __future__ import annotations

import io
import re
from typing import Any

import pandas as pd
from ofxparse import OfxParser

from src.etl.normalizer import (
    classify_bank_operation,
    clean_description,
    extract_memo_financial_hints,
    make_deterministic_id,
    parse_date_iso,
    parse_money_br,
    resolve_bank_identity,
)
from src.schemas import validate_extrato_df


def _decode_ofx_bytes(raw_bytes: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return raw_bytes.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw_bytes.decode("latin-1", errors="replace")


def _extract_tag(block: str, tag: str) -> str | None:
    pattern = rf"<{tag}>\s*([^<\r\n]+)"
    m = re.search(pattern, block, flags=re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return None


def _sanitize_ofx_for_ofxparse(text: str) -> str:
    """Corrige variações comuns de bancos brasileiros que quebram parsers OFX estritos."""
    sanitized = text
    # Normaliza valores com vírgula decimal em <TRNAMT> e <BALAMT>
    sanitized = re.sub(
        r"(<(?:TRNAMT|BALAMT)>\s*[-+]?\d+),(\d+)",
        r"\1.\2",
        sanitized,
        flags=re.IGNORECASE,
    )
    # Remove brackets de timezone não padronizados em <DTPOSTED>, <DTSTART>, <DTEND>, <DTASOF>
    sanitized = re.sub(
        r"(<DT(?:POSTED|START|END|ASOF|SERVER)>\s*\d{8,14})(?:\.\d+)?\s*\[[^\]]*\]",
        r"\1",
        sanitized,
        flags=re.IGNORECASE,
    )
    return sanitized


def _parse_via_stmttrn_regex(text: str, filename: str, bank_override: str | None) -> list[dict[str, Any]]:
    """
    Extrator direto de blocos <STMTTRN> compatível com OFX 1.x (sem tags de fechamento)
    e OFX 2.x (XML), garantindo leitura universal mesmo em arquivos com cabeçalho fora do padrão.
    """
    bank_id = _extract_tag(text, "BANKID")
    org_name = _extract_tag(text, "ORG")
    acct_id = _extract_tag(text, "ACCTID") or "CONTA-PADRAO"

    banco_codigo, banco_nome = resolve_bank_identity(
        bank_code=bank_id,
        bank_hint=bank_override or org_name or filename,
    )
    if bank_override and bank_override.strip():
        _, override_name = resolve_bank_identity(bank_hint=bank_override)
        banco_nome = override_name if override_name != "Banco Não Identificado" else bank_override.strip()

    # Captura cada transação <STMTTRN> ... </STMTTRN> ou até o próximo <STMTTRN> / </BANKTRANLIST>
    pattern = re.compile(
        r"<STMTTRN>(.*?)(?=</STMTTRN>|<STMTTRN>|</BANKTRANLIST>)",
        flags=re.IGNORECASE | re.DOTALL,
    )
    rows: list[dict[str, Any]] = []
    for idx, match in enumerate(pattern.finditer(text), start=1):
        block = match.group(1)
        dt_raw = _extract_tag(block, "DTPOSTED") or _extract_tag(block, "DTUSER")
        amt_raw = _extract_tag(block, "TRNAMT")
        fitid = _extract_tag(block, "FITID")
        checknum = _extract_tag(block, "CHECKNUM")
        refnum = _extract_tag(block, "REFNUM")
        name_tag = _extract_tag(block, "NAME") or ""
        memo_tag = _extract_tag(block, "MEMO") or ""
        trntype = (_extract_tag(block, "TRNTYPE") or "").upper()

        dt_iso = parse_date_iso(dt_raw)
        valor = parse_money_br(amt_raw)
        if dt_iso is None or valor is None:
            continue

        # Ajusta sinal caso TRNTYPE seja DEBIT mas banco tenha exportado sem sinal (raro, mas ocorre)
        if trntype in ("DEBIT", "FEE", "SRVCHG", "PAYMENT") and valor > 0 and "-" in str(amt_raw):
            valor = -abs(valor)

        # Combina NAME + MEMO sem duplicar texto repetido
        parts = [p.strip() for p in (name_tag, memo_tag) if p and p.strip()]
        if len(parts) == 2 and parts[0].upper() in parts[1].upper():
            desc_orig = parts[1]
        elif len(parts) == 2 and parts[1].upper() in parts[0].upper():
            desc_orig = parts[0]
        else:
            desc_orig = " - ".join(parts) if parts else (trntype or "LANCAMENTO OFX")

        desc_norm = clean_description(desc_orig)
        doc = fitid or refnum or checknum or f"SEQ-{idx:04d}"
        natureza = classify_bank_operation(desc_norm, valor)
        hints = extract_memo_financial_hints(desc_norm)

        valor_liquido = abs(valor) if valor > 0 else None
        valor_nominal = hints["valor_nominal"]

        # ID determinístico para evitar duplicidade ao reimportar o mesmo OFX
        id_extrato = make_deterministic_id(
            "OFX",
            banco_codigo,
            acct_id,
            dt_iso,
            f"{valor:.2f}",
            fitid or desc_norm,
            checknum or "",
        )

        rows.append(
            {
                "id_extrato": id_extrato,
                "arquivo_origem": filename,
                "banco_codigo": banco_codigo,
                "banco_nome": banco_nome,
                "conta": str(acct_id).strip(),
                "data_lancamento": dt_iso,
                "tipo_movimento": "CREDITO" if valor >= 0 else "DEBITO",
                "natureza_operacao": natureza,
                "descricao_original": desc_orig,
                "descricao_normalizada": desc_norm,
                "documento": str(doc).strip(),
                "valor": round(valor, 2),
                "valor_absoluto": round(abs(valor), 2),
                "valor_nominal": valor_nominal,
                "valor_liquido": valor_liquido,
                "prazo_dias": hints["prazo_dias"],
                "data_vencimento_original": hints["data_vencimento_original"],
            }
        )
    return rows


def extract_ofx_statement_metadata(raw_bytes: bytes, filename: str = "extrato.ofx") -> dict[str, Any]:
    """Extrai metadados oficiais de cabeçalho/rodapé do OFX (<DTSTART>, <DTEND>, <BALAMT>, <ACCTID>)."""
    text = _decode_ofx_bytes(raw_bytes)
    bank_id = _extract_tag(text, "BANKID")
    org_name = _extract_tag(text, "ORG")
    acct_id = _extract_tag(text, "ACCTID") or "CONTA-PADRAO"
    dt_start = parse_date_iso(_extract_tag(text, "DTSTART"))
    dt_end = parse_date_iso(_extract_tag(text, "DTEND"))
    bal_amt = parse_money_br(_extract_tag(text, "BALAMT"))
    dt_asof = parse_date_iso(_extract_tag(text, "DTASOF"))
    banco_codigo, banco_nome = resolve_bank_identity(bank_code=bank_id, bank_hint=org_name or filename)
    return {
        "banco_codigo": banco_codigo,
        "banco_nome": banco_nome,
        "conta": str(acct_id).strip(),
        "dt_inicio": dt_start,
        "dt_fim": dt_end,
        "saldo_final": bal_amt,
        "data_saldo": dt_asof,
    }


def parse_ofx_bytes(
    raw_bytes: bytes,
    filename: str = "extrato.ofx",
    bank_override: str | None = None,
) -> pd.DataFrame:
    """
    Lê um arquivo OFX de qualquer banco e retorna DataFrame validado pelo Pandera.
    Utiliza extração estruturada híbrida (ofxparse + fallback SGML/XML universal).
    """
    text = _decode_ofx_bytes(raw_bytes)
    rows = _parse_via_stmttrn_regex(text, filename=filename, bank_override=bank_override)

    if not rows:
        # Tenta via OfxParser puro caso a árvore tenha tags diferenciadas
        sanitized = _sanitize_ofx_for_ofxparse(text)
        ofx = OfxParser.parse(io.BytesIO(sanitized.encode("utf-8")))
        accounts = getattr(ofx, "accounts", None) or ([ofx.account] if getattr(ofx, "account", None) else [])
        for acc in accounts:
            inst = getattr(acc, "institution", None)
            org_name = getattr(inst, "organization", None) if inst else None
            routing = getattr(acc, "routing_number", None)
            acct_id = getattr(acc, "account_id", None) or getattr(acc, "number", None) or "CONTA-PADRAO"
            banco_codigo, banco_nome = resolve_bank_identity(
                bank_code=routing,
                bank_hint=bank_override or org_name or filename,
            )
            stmt = getattr(acc, "statement", None)
            txns = getattr(stmt, "transactions", []) if stmt else []
            for idx, trn in enumerate(txns, start=1):
                dt_iso = parse_date_iso(getattr(trn, "date", None))
                valor = parse_money_br(getattr(trn, "amount", None))
                if dt_iso is None or valor is None:
                    continue
                payee = str(getattr(trn, "payee", "") or "").strip()
                memo = str(getattr(trn, "memo", "") or "").strip()
                desc_orig = " - ".join(p for p in (payee, memo) if p) or "LANCAMENTO OFX"
                desc_norm = clean_description(desc_orig)
                doc = str(getattr(trn, "id", "") or getattr(trn, "checknum", "") or f"SEQ-{idx:04d}")
                hints = extract_memo_financial_hints(desc_norm)
                id_extrato = make_deterministic_id("OFX", banco_codigo, acct_id, dt_iso, f"{valor:.2f}", doc)
                rows.append(
                    {
                        "id_extrato": id_extrato,
                        "arquivo_origem": filename,
                        "banco_codigo": banco_codigo,
                        "banco_nome": banco_nome,
                        "conta": str(acct_id),
                        "data_lancamento": dt_iso,
                        "tipo_movimento": "CREDITO" if valor >= 0 else "DEBITO",
                        "natureza_operacao": classify_bank_operation(desc_norm, valor),
                        "descricao_original": desc_orig,
                        "descricao_normalizada": desc_norm,
                        "documento": doc,
                        "valor": round(valor, 2),
                        "valor_absoluto": round(abs(valor), 2),
                        "valor_nominal": hints["valor_nominal"],
                        "valor_liquido": abs(valor) if valor > 0 else None,
                        "prazo_dias": hints["prazo_dias"],
                        "data_vencimento_original": hints["data_vencimento_original"],
                    }
                )

    df = pd.DataFrame(rows)
    return validate_extrato_df(df)
