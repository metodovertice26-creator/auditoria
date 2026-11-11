"""Funções puras de normalização de datas, valores monetários, bancos e descrições."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import date, datetime
from typing import Any

import pandas as pd

COMPE_BANKS: dict[str, str] = {
    "001": "Banco do Brasil",
    "033": "Santander",
    "077": "Banco Inter",
    "104": "Caixa Econômica Federal",
    "208": "BTG Pactual",
    "237": "Bradesco",
    "260": "Nubank",
    "336": "C6 Bank",
    "341": "Itaú Unibanco",
    "403": "Cora",
    "422": "Banco Safra",
    "748": "Sicredi",
    "756": "Sicoob",
}

BANK_KEYWORDS: list[tuple[str, str, str]] = [
    ("ITAU", "341", "Itaú Unibanco"),
    ("BRADESCO", "237", "Bradesco"),
    ("SANTANDER", "033", "Santander"),
    ("BANCO DO BRASIL", "001", "Banco do Brasil"),
    ("BB ", "001", "Banco do Brasil"),
    ("INTER", "077", "Banco Inter"),
    ("CORA", "403", "Cora"),
    ("CAIXA", "104", "Caixa Econômica Federal"),
    ("CEF", "104", "Caixa Econômica Federal"),
    ("BTG", "208", "BTG Pactual"),
    ("NUBANK", "260", "Nubank"),
    ("NU PAGAMENTOS", "260", "Nubank"),
    ("C6", "336", "C6 Bank"),
    ("SAFRA", "422", "Banco Safra"),
    ("SICREDI", "748", "Sicredi"),
    ("SICOOB", "756", "Sicoob"),
]


def strip_accents(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text or "")
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def clean_description(raw: Any) -> str:
    """Padroniza descrições removendo ruídos, múltiplos espaços e acentuação para matching."""
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return "SEM DESCRICAO"
    text = str(raw).strip()
    if not text:
        return "SEM DESCRICAO"
    text = re.sub(r"[\r\n\t]+", " ", text)
    text = strip_accents(text).upper()
    text = re.sub(r"\s+", " ", text).strip()
    return text or "SEM DESCRICAO"


def parse_date_iso(value: Any) -> str | None:
    """Converte diversos formatos de data (BR, ISO, OFX, Timestamp) para AAAA-MM-DD."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, pd.Timestamp):
        if pd.isna(value):
            return None
        return value.strftime("%Y-%m-%d")
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")

    raw = str(value).strip()
    if not raw or raw.lower() in ("nan", "none", "nat", "-"):
        return None

    # Formato OFX: 20261015120000[-3:BRT] ou 20261015
    m_ofx = re.match(r"^(\d{4})(\d{2})(\d{2})(?:\d{0,6})?(?:\.\d+)?(?:\[.*\])?$", raw)
    if m_ofx:
        y, m, d = int(m_ofx.group(1)), int(m_ofx.group(2)), int(m_ofx.group(3))
        try:
            return date(y, m, d).strftime("%Y-%m-%d")
        except ValueError:
            pass

    # Remove sufixo de hora se houver
    date_part = raw.split(" ")[0].split("T")[0]

    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%y", "%Y/%m/%d"):
        try:
            return datetime.strptime(date_part, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue

    parsed = pd.to_datetime(raw, dayfirst=True, errors="coerce")
    if pd.notna(parsed):
        return parsed.strftime("%Y-%m-%d")
    return None


def parse_money_br(value: Any) -> float | None:
    """Converte strings monetárias nos padrões BR (1.234,56 / D / C) e US (1,234.56) para float."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, (int, float)):
        return round(float(value), 2)

    raw = str(value).strip()
    if not raw or raw.lower() in ("nan", "none", "-", ""):
        return None

    sign = 1.0
    # Parênteses contábeis: (1.250,00)
    if raw.startswith("(") and raw.endswith(")"):
        sign = -1.0
        raw = raw[1:-1].strip()

    # Sufixos bancários D / C (Débito / Crédito)
    m_dc = re.search(r"\s*([DCdc])$", raw)
    if m_dc:
        if m_dc.group(1).upper() == "D":
            sign = -1.0
        raw = raw[: m_dc.start()].strip()

    raw = re.sub(r"[R$\s\u00a0]", "", raw)
    if raw.startswith("-"):
        sign *= -1.0
        raw = raw[1:]
    elif raw.startswith("+"):
        raw = raw[1:]

    if "," in raw and "." in raw:
        if raw.rfind(",") > raw.rfind("."):
            # Padrão BR: 1.234,56
            raw = raw.replace(".", "").replace(",", ".")
        else:
            # Padrão US: 1,234.56
            raw = raw.replace(",", "")
    elif "," in raw:
        # Vírgula como separador decimal: 1234,56
        parts = raw.split(",")
        if len(parts) == 2 and len(parts[1]) <= 4:
            raw = raw.replace(",", ".")
        else:
            raw = raw.replace(",", "")

    try:
        return round(sign * float(raw), 2)
    except ValueError:
        return None


def resolve_bank_identity(bank_code: str | None = None, bank_hint: str | None = None) -> tuple[str, str]:
    """Identifica código COMPE de 3 dígitos e nome padronizado do banco."""
    code_clean = ""
    if bank_code:
        digits = re.sub(r"\D", "", str(bank_code))
        if digits:
            code_clean = digits[-3:].zfill(3)
            if code_clean in COMPE_BANKS:
                return code_clean, COMPE_BANKS[code_clean]

    hint_norm = clean_description(bank_hint or "")
    for kw, code, name in BANK_KEYWORDS:
        if kw in hint_norm:
            return code, name

    if code_clean and code_clean != "000":
        return code_clean, f"Banco {code_clean}"

    if bank_hint and str(bank_hint).strip() and hint_norm != "SEM DESCRICAO":
        return "000", str(bank_hint).strip()

    return "000", "Banco Não Identificado"


def classify_bank_operation(desc_norm: str, valor: float) -> str:
    """Classifica semanticamente a linha do extrato para alimentar AUD-03, AUD-04 e AUD-05."""
    d = desc_norm.upper()

    # Tarifas de boleto (débitos)
    if valor < 0 and any(
        k in d
        for k in (
            "TAR BOLETO",
            "TARIFA BOLETO",
            "TAR LIQ COB",
            "TARIFA LIQ",
            "TARIFA COBRANCA",
            "TAR COB",
            "CUSTO BOLETO",
            "TAR REGISTRO COB",
        )
    ):
        return "TARIFA_BOLETO"

    # Antecipação de recebíveis (créditos de lotes)
    if valor > 0 and any(
        k in d
        for k in (
            "ANTECIP",
            "DESCONTO DUPL",
            "DESC DUPLICATA",
            "CESSAO RECEB",
            "OPERACAO DESCONTO",
            "LOTE ANTECIP",
        )
    ):
        return "ANTECIPACAO_RECEBIVEIS"

    # Liquidação de boletos (créditos)
    if valor > 0 and any(
        k in d
        for k in (
            "BOLETO",
            "LIQ COBRANCA",
            "LIQUIDACAO COB",
            "COB SIMPLES",
            "COBRANCA REGISTRADA",
            "CREDITO COBRANCA",
            "RECEBIMENTO TITULO",
        )
    ):
        return "BOLETO_LIQUIDACAO"

    # Parcela de empréstimo / financiamento (débitos)
    if valor < 0 and any(
        k in d
        for k in (
            "EMPREST",
            "CAP GIRO",
            "CAPITAL DE GIRO",
            "PRONAMPE",
            "FINANCIAMENTO",
            "PARCELA CONTRATO",
            "AMORTIZACAO",
        )
    ):
        return "EMPRESTIMO_PARCELA"

    if valor < 0 and any(k in d for k in ("TAR ", "TARIFA", "PACOTE SERV", "IOF", "MANUT CONTA")):
        return "TARIFA_BANCARIA"

    if any(k in d for k in ("PIX", "TED", "DOC", "TRANSF")):
        return "PIX_TED_DOC"

    return "OUTROS"


def extract_memo_financial_hints(desc_norm: str) -> dict[str, Any]:
    """
    Extrai metadados opcionais embutidos no histórico bancário quando disponíveis, ex.:
    'LIQ BOLETO VN 1500,00' ou 'ANTECIPACAO VN 50000,00 PRAZO 45D'.
    """
    hints: dict[str, Any] = {
        "valor_nominal": None,
        "prazo_dias": None,
        "data_vencimento_original": None,
    }
    m_vn = re.search(r"(?:NOMINAL|VN|BRUTO)[:=\s]+R?\$?\s*([\d\.,]+)", desc_norm)
    if m_vn:
        hints["valor_nominal"] = parse_money_br(m_vn.group(1))

    m_prazo = re.search(r"(?:PRAZO|DIAS)[:=\s]+(\d{1,4})\s*D?", desc_norm)
    if m_prazo:
        hints["prazo_dias"] = float(m_prazo.group(1))

    m_venc = re.search(r"(?:VENC|VCTO)[:=\s]+(\d{2}/\d{2}/\d{4}|\d{4}-\d{2}-\d{2})", desc_norm)
    if m_venc:
        hints["data_vencimento_original"] = parse_date_iso(m_venc.group(1))

    return hints


def classify_omie_accounting_group(categoria: str, descricao: str) -> str:
    """Classifica o grupo contábil/financeiro do lançamento Omie (essencial para AUD-05)."""
    combined = f"{clean_description(categoria)} {clean_description(descricao)}"
    if any(
        k in combined
        for k in (
            "PASSIVO",
            "AMORTIZACAO",
            "PRINCIPAL EMPREST",
            "BAIXA PRINCIPAL",
            "EMPRESTIMOS A PAGAR",
            "OBRIGACOES EMPREST",
        )
    ):
        return "PASSIVO_EMPRESTIMO"

    if any(
        k in combined
        for k in (
            "JUROS",
            "ENCARGO FINANCEIRO",
            "ENCARGOS FINANCEIROS",
            "DESPESA FINANCEIRA",
            "DESPESAS FINANCEIRAS",
        )
    ):
        return "DESPESA_JUROS"

    if any(k in combined for k in ("TARIFA", "TAXA BANCARIA", "DESPESA BANCARIA", "DESPESAS BANCARIAS", "IOF")):
        return "DESPESA_BANCARIA"

    if any(k in combined for k in ("RECEITA", "VENDA", "SERVICO", "FATURAMENTO", "COBRANCA CLIENTE")):
        return "RECEITA_OPERACIONAL"

    return "OPERACIONAL_OUTROS"


def make_deterministic_id(prefix: str, *parts: Any) -> str:
    payload = "|".join("" if p is None else str(p).strip() for p in parts)
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:14]
    return f"{prefix}-{digest}"
