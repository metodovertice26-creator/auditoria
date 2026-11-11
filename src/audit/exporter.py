"""Exportação auditável da Matriz de Divergências e Dossiê de Contestação Bancária."""

from __future__ import annotations

import io
from datetime import datetime
from typing import Any

import pandas as pd

EXPORT_RENAME = {
    "status_tratativa": "Status Tratativa",
    "codigo_regra": "Código Regra",
    "nome_regra": "Regra de Auditoria",
    "severidade": "Severidade",
    "categoria": "Categoria",
    "data_ocorrencia": "Data Ocorrência",
    "banco": "Banco",
    "valor_esperado": "Valor Esperado / Contratado (R$)",
    "valor_realizado": "Valor Realizado / Extrato (R$)",
    "valor_divergente": "Impacto Divergente (R$)",
    "resumo": "Resumo da Exceção",
    "memoria_calculo": "Memória de Cálculo Auditável",
    "justificativa": "Parecer / Tratativa do Analista",
    "id_extrato": "ID Extrato",
    "id_omie": "ID Omie",
}


def _fmt_brl(val: float | int) -> str:
    s = f"{abs(float(val)):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"-R$ {s}" if float(val) < 0 else f"R$ {s}"


def gerar_csv_auditoria(div_df: pd.DataFrame) -> bytes:
    """Exporta CSV com BOM UTF-8 e separador ';' para abertura direta no Excel em PT-BR."""
    cols = [c for c in EXPORT_RENAME if c in div_df.columns]
    export_df = (
        div_df[cols].rename(columns=EXPORT_RENAME)
        if not div_df.empty
        else pd.DataFrame(columns=list(EXPORT_RENAME.values()))
    )
    return export_df.to_csv(index=False, sep=";", decimal=",").encode("utf-8-sig")


def gerar_dossie_contestacao_bancaria(
    div_df: pd.DataFrame,
    banco_alvo: str | None = None,
) -> tuple[str, pd.DataFrame]:
    """
    Gera o Dossiê Técnico de Contestação e Ressarcimento Bancário para cobranças
    acima do contratado (AUD-03 Tarifas de Boletos e AUD-04 Desconto de Antecipação).
    Retorna (texto_carta_markdown, df_itens_contestaveis).
    """
    if div_df.empty:
        return "Nenhuma divergência bancária contestável encontrada.", pd.DataFrame()

    claims = div_df[div_df["codigo_regra"].isin(["AUD-03", "AUD-04", "AUD-05"])].copy()
    if "status_tratativa" in claims.columns:
        claims = claims[~claims["status_tratativa"].isin(["Ignorado / Aceito", "Ajustado no Omie"])]
    if banco_alvo and banco_alvo != "Todos os bancos":
        claims = claims[claims["banco"] == banco_alvo]

    if claims.empty:
        return "Não há cobranças bancárias pendentes de contestação para o filtro selecionado.", pd.DataFrame()

    data_hoje = datetime.now().strftime("%d/%m/%Y")
    total_ressarcir = float(claims["valor_divergente"].sum())
    bancos_str = ", ".join(sorted(claims["banco"].unique()))

    linhas_md = [
        f"# DOSSIÊ DE CONTESTAÇÃO E SOLICITAÇÃO DE ESTORNO BANCÁRIO",
        f"**Emitido por:** Controladoria & Auditoria Financeira Vértice  ",
        f"**Data de Emissão:** {data_hoje}  ",
        f"**Instituição(ões):** {bancos_str}  ",
        f"**Total Apurado para Ressarcimento / Ajuste:** **{_fmt_brl(total_ressarcir)}**",
        "",
        "---",
        "",
        "Prezada Gerência de Relacionamento Empresarial,",
        "",
        "Em procedimento rotineiro de conciliação e auditoria financeira dos extratos bancários frente às condições comerciais pactuadas em contrato, identificamos divergências na cobrança de tarifas de liquidação de cobrança, taxas efetivas de antecipação de recebíveis e/ou encargos contratuais, conforme memória analítica detalhada abaixo:",
        "",
    ]

    for idx, (_, row) in enumerate(claims.iterrows(), start=1):
        linhas_md.append(
            f"### {idx}. [{row['codigo_regra']}] {row['banco']} — Data: {row['data_ocorrencia']}"
        )
        linhas_md.append(f"- **Ocorrência:** {row['resumo']}")
        linhas_md.append(
            f"- **Parâmetro Contratado / Esperado:** {_fmt_brl(row['valor_esperado'])} | "
            f"**Valor Efetivamente Praticado:** {_fmt_brl(row['valor_realizado'])}"
        )
        linhas_md.append(f"- **Diferença a Estornar (Delta):** **{_fmt_brl(row['valor_divergente'])}**")
        linhas_md.append(f"- **Memória de Cálculo:** {row['memoria_calculo']}")
        linhas_md.append("")

    linhas_md.extend(
        [
            "---",
            f"Solicitamos a gentileza de analisar os apontamentos acima e proceder com o **estorno/crédito em conta corrente no montante total de {_fmt_brl(total_ressarcir)}**, bem como a parametrização sistêmica da conta para evitar novas cobranças em desacordo nas próximas liquidações.",
            "",
            "Atenciosamente,  ",
            "**Departamento Financeiro / Controladoria**",
        ]
    )

    return "\n".join(linhas_md), claims.reset_index(drop=True)


def gerar_excel_auditoria(
    div_df: pd.DataFrame,
    summary: dict[str, Any] | None = None,
    extrato_df: pd.DataFrame | None = None,
    omie_df: pd.DataFrame | None = None,
) -> bytes:
    """Gera pasta de trabalho Excel (.xlsx) formatada com trilha completa e aba de Contestação Bancária."""
    buffer = io.BytesIO()
    cols = [c for c in EXPORT_RENAME if c in div_df.columns]
    matriz_export = (
        div_df[cols].rename(columns=EXPORT_RENAME)
        if not div_df.empty
        else pd.DataFrame(columns=list(EXPORT_RENAME.values()))
    )

    _, claims_df = gerar_dossie_contestacao_bancaria(div_df)

    kpis_rows = []
    if summary:
        kpis_rows = [
            {"Indicador Executivo": "Código da Execução", "Valor": summary.get("execucao_id", "-")},
            {"Indicador Executivo": "Data/Hora da Auditoria", "Valor": summary.get("executado_em", "-")},
            {"Indicador Executivo": "Valor Total Divergente (R$)", "Valor": summary.get("valor_total_divergente", 0.0)},
            {"Indicador Executivo": "Quantidade de Alertas Críticos", "Valor": summary.get("alertas_criticos", 0)},
            {"Indicador Executivo": "Total de Exceções Identificadas", "Valor": summary.get("total_divergencias", 0)},
            {"Indicador Executivo": "Percentual de Aderência do ERP (%)", "Valor": summary.get("aderencia_erp_pct", 100.0)},
            {"Indicador Executivo": "Lançamentos no Extrato Auditados", "Valor": summary.get("total_extrato", 0)},
            {"Indicador Executivo": "Lançamentos no Omie Carregados", "Valor": summary.get("total_omie", 0)},
        ]
    resumo_df = pd.DataFrame(kpis_rows)

    with pd.ExcelWriter(buffer, engine="xlsxwriter") as writer:
        workbook = writer.book
        header_fmt = workbook.add_format(
            {
                "bold": True,
                "bg_color": "#2C3531",
                "font_color": "#FFFFFF",
                "border": 1,
            }
        )
        money_fmt = workbook.add_format({"num_format": "R$ #,##0.00"})

        matriz_export.to_excel(writer, sheet_name="Matriz_Divergencias", index=False)
        ws_div = writer.sheets["Matriz_Divergencias"]
        for col_idx, col_name in enumerate(matriz_export.columns):
            ws_div.write(0, col_idx, col_name, header_fmt)
            width = 22 if "(R$)" in col_name else (46 if "Memória" in col_name or "Resumo" in col_name else 20)
            if "(R$)" in col_name:
                ws_div.set_column(col_idx, col_idx, width, money_fmt)
            else:
                ws_div.set_column(col_idx, col_idx, width)

        if not claims_df.empty:
            claim_cols = [c for c in EXPORT_RENAME if c in claims_df.columns]
            claims_export = claims_df[claim_cols].rename(columns=EXPORT_RENAME)
            claims_export.to_excel(writer, sheet_name="Contestacao_Bancaria", index=False)
            ws_cl = writer.sheets["Contestacao_Bancaria"]
            for col_idx, col_name in enumerate(claims_export.columns):
                ws_cl.write(0, col_idx, col_name, header_fmt)
                ws_cl.set_column(col_idx, col_idx, 26, money_fmt if "(R$)" in col_name else None)

        if not resumo_df.empty:
            resumo_df.to_excel(writer, sheet_name="Resumo_Executivo", index=False)
            ws_res = writer.sheets["Resumo_Executivo"]
            ws_res.write(0, 0, "Indicador Executivo", header_fmt)
            ws_res.write(0, 1, "Valor", header_fmt)
            ws_res.set_column(0, 0, 38)
            ws_res.set_column(1, 1, 26)

        if extrato_df is not None and not extrato_df.empty:
            extrato_df.to_excel(writer, sheet_name="Extrato_Normalizado", index=False)

        if omie_df is not None and not omie_df.empty:
            omie_df.to_excel(writer, sheet_name="Omie_Normalizado", index=False)

    return buffer.getvalue()
