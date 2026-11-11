"""Serviço de auditoria: execução do motor, consolidação do workflow de tratativas e KPIs executivos."""

from __future__ import annotations

import json
from typing import Any

import pandas as pd
from pydantic import BaseModel

from src.audit.engine import executar_auditoria_completa
from src.database import VerticeDB

STATUS_EM_ABERTO = ("Pendente", "Em Contestação")


class KpisAuditoria(BaseModel):
    """Indicadores executivos derivados da última execução e do workflow de tratativas."""

    total: int
    pendentes: int
    resolvidos: int
    criticos_pendentes: int
    valor_em_aberto: float


def _assinatura_do_json(detalhe: Any) -> str | None:
    try:
        return json.loads(detalhe).get("assinatura_ocorrencia")
    except (TypeError, ValueError, AttributeError):
        return None


def enriquecer_com_workflow(div_df: pd.DataFrame, wf_map: dict[str, dict[str, str]]) -> pd.DataFrame:
    """Anexa assinatura, status de tratativa e justificativa a cada divergência."""
    out = div_df.copy()
    if out.empty:
        for col in ("assinatura_ocorrencia", "status_tratativa", "justificativa"):
            out[col] = pd.Series(dtype="str")
        return out

    sigs = out["detalhe_json"].map(_assinatura_do_json)
    faltantes = sigs.isna()
    if faltantes.any():
        sigs.loc[faltantes] = out.loc[faltantes].apply(
            lambda r: VerticeDB.build_issue_signature(
                str(r["codigo_regra"]),
                str(r["data_ocorrencia"]),
                str(r["banco"]),
                r["id_extrato"] if pd.notna(r["id_extrato"]) else None,
                r["id_omie"] if pd.notna(r["id_omie"]) else None,
                str(r["resumo"]),
            ),
            axis=1,
        )
    out["assinatura_ocorrencia"] = sigs.astype(str)
    out["status_tratativa"] = sigs.map(lambda s: wf_map.get(s, {}).get("status_tratativa", "Pendente"))
    out["justificativa"] = sigs.map(lambda s: wf_map.get(s, {}).get("justificativa", ""))
    return out


def carregar_divergencias(db: VerticeDB, competencia: str | None) -> tuple[dict[str, Any] | None, pd.DataFrame]:
    """Retorna o resumo da última execução e as divergências enriquecidas, filtradas pela competência."""
    summary, raw = db.get_latest_audit_results()
    divs = enriquecer_com_workflow(raw, db.get_workflow_map())
    if competencia and not divs.empty:
        divs = divs[divs["data_ocorrencia"].astype(str).str.startswith(competencia)].reset_index(drop=True)
    return summary, divs


def calcular_kpis(divs: pd.DataFrame) -> KpisAuditoria:
    if divs.empty:
        return KpisAuditoria(total=0, pendentes=0, resolvidos=0, criticos_pendentes=0, valor_em_aberto=0.0)
    abertos = divs[divs["status_tratativa"].isin(STATUS_EM_ABERTO)]
    return KpisAuditoria(
        total=len(divs),
        pendentes=len(abertos),
        resolvidos=len(divs) - len(abertos),
        criticos_pendentes=int((abertos["severidade"] == "Crítico").sum()),
        valor_em_aberto=float(abertos["valor_divergente"].sum()),
    )


def executar_auditoria(db: VerticeDB, competencia: str | None) -> dict[str, Any]:
    """Executa o motor completo e devolve as métricas da execução."""
    metrics, _ = executar_auditoria_completa(db, competencia=competencia)
    return metrics
