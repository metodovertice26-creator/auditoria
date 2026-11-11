"""Conector modularizado para a API REST JSON do ERP Omie."""

from __future__ import annotations

from typing import Any

import pandas as pd
import requests

from src.etl.normalizer import (
    classify_omie_accounting_group,
    clean_description,
    make_deterministic_id,
    parse_date_iso,
    parse_money_br,
    resolve_bank_identity,
)
from src.schemas import validate_omie_df

OMIE_BASE_URL = "https://app.omie.com.br/api/v1"


class OmieApiClient:
    """
    Cliente REST para sincronização direta com os endpoints financeiros do Omie:
    - /financas/extrato/ (ListarExtrato)
    - /financas/mf/ (ListarMovimentos)
    """

    def __init__(self, app_key: str, app_secret: str, timeout: int = 30) -> None:
        self.app_key = app_key.strip()
        self.app_secret = app_secret.strip()
        self.timeout = timeout

    def _post(self, path: str, call: str, param: dict[str, Any]) -> dict[str, Any]:
        if not self.app_key or not self.app_secret:
            raise ValueError("Credenciais Omie (APP_KEY e APP_SECRET) são obrigatórias.")
        payload = {
            "call": call,
            "app_key": self.app_key,
            "app_secret": self.app_secret,
            "param": [param],
        }
        resp = requests.post(f"{OMIE_BASE_URL}{path}", json=payload, timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict) and "faultstring" in data:
            raise RuntimeError(f"Erro API Omie: {data['faultstring']}")
        return data

    def testar_conexao(self) -> list[dict[str, Any]]:
        """Lista contas correntes ativas para validar credenciais da API Omie."""
        data = self._post(
            "/geral/contacorrente/",
            "ListarContasCorrentes",
            {"pagina": 1, "registros_por_pagina": 50, "apenas_importado_api": "N"},
        )
        return data.get("ListarContasCorrentes", [])

    def importar_movimentos_financeiros(
        self,
        data_inicio_br: str,
        data_fim_br: str,
        max_paginas: int = 10,
    ) -> pd.DataFrame:
        """Busca movimentos financeiros paginados na API Omie e converte para LancamentoOmieSchema."""
        rows: list[dict[str, Any]] = []
        for pagina in range(1, max_paginas + 1):
            data = self._post(
                "/financas/mf/",
                "ListarMovimentos",
                {
                    "nPagina": pagina,
                    "nRegPorPagina": 200,
                    "dDtPagtoDe": data_inicio_br,
                    "dDtPagtoAte": data_fim_br,
                },
            )
            movimentos = data.get("movimentos", [])
            if not movimentos:
                break

            for mov in movimentos:
                det = mov.get("detalhes", {})
                res = mov.get("resumo", {})
                dt_iso = parse_date_iso(det.get("dDtPagamento") or det.get("dDtPrevisao"))
                if not dt_iso:
                    continue
                valor_liq = parse_money_br(res.get("nValPago") or det.get("nValorTitulo")) or 0.0
                if abs(valor_liq) < 0.001:
                    continue
                natureza = str(det.get("cNatureza", "R")).upper()
                valor = abs(valor_liq) if natureza == "R" else -abs(valor_liq)
                categoria = str(det.get("cCodCateg", "Sem Categoria"))
                desc_orig = str(det.get("cObservacao") or det.get("cNumTitulo") or "MOVIMENTO API OMIE")
                desc_norm = clean_description(desc_orig)
                _, banco_nome = resolve_bank_identity(bank_hint=str(det.get("nCodCC", "")))
                doc = str(det.get("cNumDocFiscal") or det.get("cNumTitulo") or "API")
                cod_lanc = str(det.get("nCodTitulo") or make_deterministic_id("API", dt_iso, valor, doc))

                rows.append(
                    {
                        "id_omie": f"API-{cod_lanc}",
                        "arquivo_origem": "API_OMIE_MF",
                        "origem_relatorio": "API_OMIE",
                        "banco_nome": banco_nome,
                        "conta_corrente": str(det.get("nCodCC", "CC-API")),
                        "data_lancamento": dt_iso,
                        "data_vencimento": parse_date_iso(det.get("dDtVenc")),
                        "status_titulo": "BAIXADO" if det.get("dDtPagamento") else "ABERTO",
                        "tipo_lancamento": "ENTRADA" if valor >= 0 else "SAIDA",
                        "categoria": categoria,
                        "grupo_contabil": classify_omie_accounting_group(categoria, desc_orig),
                        "cliente_fornecedor": str(det.get("nCodCliente", "API Omie")),
                        "documento": doc,
                        "descricao_original": desc_orig,
                        "descricao_normalizada": desc_norm,
                        "valor": round(valor, 2),
                        "valor_absoluto": round(abs(valor), 2),
                        "valor_nominal": abs(parse_money_br(det.get("nValorTitulo")) or abs(valor)),
                        "juros_multa": abs(parse_money_br(res.get("nJuros")) or 0.0),
                        "desconto_tarifa": abs(parse_money_br(res.get("nDesconto")) or 0.0),
                        "contrato_ref": str(det.get("cNumContrato") or "") or None,
                    }
                )

            total_paginas = int(data.get("nTotPaginas", 1))
            if pagina >= total_paginas:
                break

        return validate_omie_df(pd.DataFrame(rows))
