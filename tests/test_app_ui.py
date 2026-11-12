"""Testes headless da interface multipágina (AppTest): cada página renderiza sem exceção, vazia e com dados."""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

APP_PATH = Path(__file__).resolve().parent.parent / "app.py"

PAGES = [
    "app_pages/painel.py",
    "app_pages/extratos.py",
    "app_pages/omie.py",
    "app_pages/base_normalizada.py",
    "app_pages/excecoes.py",
    "app_pages/fechamento.py",
    "app_pages/vinculos.py",
    "app_pages/regras.py",
    "app_pages/parametros_bancarios.py",
    "app_pages/contratos.py",
    "app_pages/manutencao.py",
]


def _new_app(tmp_path: Path) -> AppTest:
    at = AppTest.from_file(str(APP_PATH), default_timeout=60)
    at.session_state["_vertice_db_path"] = str(tmp_path / "ui_test.duckdb")
    return at


def _load_demo(at: AppTest) -> None:
    at.switch_page("app_pages/manutencao.py").run()
    assert at.button(key="btn_load_demo").disabled  # protegido até confirmar
    at.checkbox(key="chk_confirm_manutencao").check().run()
    at.button(key="btn_load_demo").click().run()
    assert not at.exception


@pytest.mark.parametrize("page", PAGES)
def test_pagina_renderiza_com_base_vazia(tmp_path: Path, page: str) -> None:
    at = _new_app(tmp_path)
    at.run()
    at.switch_page(page).run()
    assert not at.exception, f"Falha em {page}: {at.exception}"


def test_fluxo_demo_execucao_e_todas_as_paginas(tmp_path: Path) -> None:
    at = _new_app(tmp_path)
    at.run()
    assert not at.exception

    _load_demo(at)

    at.switch_page("app_pages/painel.py").run()
    at.button(key="btn_executar_auditoria_completa").click().run()
    assert not at.exception
    assert len(at.metric) >= 4

    for page in PAGES:
        at.switch_page(page).run()
        assert not at.exception, f"Falha em {page} com dados: {at.exception}"
