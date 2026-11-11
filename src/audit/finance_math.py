"""Especificações matemáticas de auditoria: Boletos, Antecipações (VP) e Empréstimos (Price/SAC)."""

from __future__ import annotations

import calendar
from datetime import date, datetime
from typing import Any

import pandas as pd


def add_months(base_date: date, months: int) -> date:
    """Soma meses preservando o dia de vencimento com ajuste de fim de mês."""
    month_idx = base_date.month - 1 + months
    year = base_date.year + month_idx // 12
    month = month_idx % 12 + 1
    max_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(base_date.day, max_day))


def calcular_divergencia_boleto(
    valor_nominal: float,
    valor_liquido: float,
    tarifa_contratada: float,
    quantidade_boletos: int = 1,
) -> dict[str, float | bool]:
    """
    AUD-03:
    Tarifa Cobrada = Valor Nominal (Extrato) - Valor Líquido Creditado (Extrato)
    Se Tarifa Cobrada > Tarifa Contratada, retorna o delta cobrado a maior.
    """
    qtd = max(1, int(quantidade_boletos))
    tarifa_cobrada_total = round(max(0.0, float(valor_nominal) - float(valor_liquido)), 2)
    tarifa_contratada_total = round(float(tarifa_contratada) * qtd, 2)
    delta_maior = round(tarifa_cobrada_total - tarifa_contratada_total, 2)
    divergente = delta_maior > 0.01
    return {
        "tarifa_cobrada": tarifa_cobrada_total,
        "tarifa_contratada": tarifa_contratada_total,
        "tarifa_unitaria_cobrada": round(tarifa_cobrada_total / qtd, 2),
        "delta_indevido": max(0.0, delta_maior),
        "divergente": divergente,
    }


def calcular_vp_antecipacao(
    valor_nominal: float,
    valor_liquido_creditado: float,
    prazo_dias: float,
    taxa_mensal_pct: float,
    modalidade: str = "COMPOSTO",
    iof_tarifa_lote: float = 0.0,
    tolerancia_reais: float = 0.10,
) -> dict[str, Any]:
    """
    AUD-04:
    Recalcula o Valor Presente (VP) teórico do lote antecipado (Composto ou Comercial Simples)
    e compara com o valor líquido creditado pelo banco (tolerância de R$ 0,10).
    """
    vn = float(valor_nominal)
    vl = float(valor_liquido_creditado)
    n = max(1.0, float(prazo_dias))
    i_m = float(taxa_mensal_pct) / 100.0
    modo = str(modalidade or "COMPOSTO").upper().strip()
    custos_fixos = float(iof_tarifa_lote or 0.0)

    if modo == "SIMPLES":
        taxa_diaria = i_m / 30.0
        fator_desconto = max(0.0, 1.0 - (taxa_diaria * n))
        vp_bruto = vn * fator_desconto
    else:
        modo = "COMPOSTO"
        taxa_diaria = (1.0 + i_m) ** (1.0 / 30.0) - 1.0
        vp_bruto = vn / ((1.0 + taxa_diaria) ** n)

    vp_teorico = round(vp_bruto - custos_fixos, 2)
    desconto_teorico = round(vn - vp_teorico, 2)
    desconto_cobrado = round(vn - vl, 2)
    diferenca_vp = round(vp_teorico - vl, 2)

    # Estima a taxa mensal efetivamente praticada pelo banco para evidenciar spread oculto
    vl_ajustado = vl + custos_fixos
    if 0 < vl_ajustado < vn:
        if modo == "SIMPLES":
            i_d_efetiva = (1.0 - (vl_ajustado / vn)) / n
            taxa_efetiva_mensal_pct = round(i_d_efetiva * 30.0 * 100.0, 4)
        else:
            i_d_efetiva = (vn / vl_ajustado) ** (1.0 / n) - 1.0
            taxa_efetiva_mensal_pct = round(((1.0 + i_d_efetiva) ** 30.0 - 1.0) * 100.0, 4)
    else:
        taxa_efetiva_mensal_pct = 0.0

    spread_oculto_pp = round(taxa_efetiva_mensal_pct - float(taxa_mensal_pct), 4)
    divergente = abs(diferenca_vp) > tolerancia_reais

    return {
        "modalidade": modo,
        "valor_nominal": round(vn, 2),
        "valor_liquido_creditado": round(vl, 2),
        "prazo_dias": n,
        "taxa_contratada_mensal_pct": round(float(taxa_mensal_pct), 4),
        "taxa_efetiva_mensal_pct": taxa_efetiva_mensal_pct,
        "spread_oculto_pp": spread_oculto_pp,
        "vp_teorico": vp_teorico,
        "desconto_teorico": desconto_teorico,
        "desconto_cobrado": desconto_cobrado,
        "diferenca_vp": diferenca_vp,
        "valor_divergente": round(abs(diferenca_vp), 2),
        "desconto_a_maior": diferenca_vp > tolerancia_reais,
        "divergente": divergente,
    }


def gerar_tabela_emprestimo(
    valor_principal: float,
    taxa_juros_mensal_pct: float,
    prazo_meses: int,
    sistema_amortizacao: str = "PRICE",
    data_primeira_parcela: str = "2026-01-15",
) -> pd.DataFrame:
    """
    AUD-05:
    Gera o cronograma completo de amortização teórica nos sistemas PRICE ou SAC.
    """
    principal = float(valor_principal)
    n_meses = max(1, int(prazo_meses))
    i = float(taxa_juros_mensal_pct) / 100.0
    sistema = str(sistema_amortizacao or "PRICE").upper().strip()
    dt_base = datetime.strptime(data_primeira_parcela, "%Y-%m-%d").date()

    saldo = principal
    rows: list[dict[str, Any]] = []

    if sistema == "SAC":
        amort_constante = principal / n_meses
        for k in range(1, n_meses + 1):
            venc = add_months(dt_base, k - 1)
            juros = round(saldo * i, 2)
            amort = round(saldo, 2) if k == n_meses else round(amort_constante, 2)
            prestacao = round(amort + juros, 2)
            saldo_final = round(max(0.0, saldo - amort), 2)
            rows.append(
                {
                    "numero_parcela": k,
                    "competencia": venc.strftime("%Y-%m"),
                    "data_vencimento": venc.strftime("%Y-%m-%d"),
                    "sistema": "SAC",
                    "saldo_inicial": round(saldo, 2),
                    "prestacao_teorica": prestacao,
                    "amortizacao_teorica": amort,
                    "juros_teoricos": juros,
                    "saldo_final": saldo_final,
                }
            )
            saldo = saldo_final
    else:
        # Sistema Francês (Tabela PRICE)
        if i > 0:
            pmt = principal * (i * ((1.0 + i) ** n_meses)) / (((1.0 + i) ** n_meses) - 1.0)
        else:
            pmt = principal / n_meses
        pmt_round = round(pmt, 2)

        for k in range(1, n_meses + 1):
            venc = add_months(dt_base, k - 1)
            juros = round(saldo * i, 2)
            if k == n_meses:
                amort = round(saldo, 2)
                prestacao = round(amort + juros, 2)
            else:
                prestacao = pmt_round
                amort = round(prestacao - juros, 2)
            saldo_final = round(max(0.0, saldo - amort), 2)
            rows.append(
                {
                    "numero_parcela": k,
                    "competencia": venc.strftime("%Y-%m"),
                    "data_vencimento": venc.strftime("%Y-%m-%d"),
                    "sistema": "PRICE",
                    "saldo_inicial": round(saldo, 2),
                    "prestacao_teorica": prestacao,
                    "amortizacao_teorica": amort,
                    "juros_teoricos": juros,
                    "saldo_final": saldo_final,
                }
            )
            saldo = saldo_final

    return pd.DataFrame(rows)
