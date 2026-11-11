"""Teste headless da interface Streamlit (AppTest) para garantir zero exceções nas 3 abas."""

from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest


def test_streamlit_app_renders_and_executes_demo(tmp_path: Path) -> None:
    app_path = Path(__file__).resolve().parent.parent / "app.py"
    at = AppTest.from_file(str(app_path), default_timeout=30)
    at.session_state["_vertice_db_path"] = str(tmp_path / "ui_test.duckdb")
    at.run()
    assert not at.exception

    # Aciona carregamento de demonstração na sidebar (importa OFX + CSV + Omie e roda auditoria)
    at.button(key="btn_load_demo").click().run()
    assert not at.exception

    # Aciona botão único "Executar Auditoria Completa"
    at.button(key="btn_executar_auditoria_completa").click().run()
    assert not at.exception
    assert len(at.metric) >= 7
