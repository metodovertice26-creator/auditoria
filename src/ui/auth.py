"""Autenticação simples via st.secrets / variável de ambiente para uso em nuvem."""

from __future__ import annotations

import hmac
import os

import streamlit as st

_AUTH_KEY = "_vertice_authenticated"


def _get_configured_password() -> str:
    env_pw = os.environ.get("APP_PASSWORD", "").strip()
    if env_pw:
        return env_pw
    try:
        return str(st.secrets.get("APP_PASSWORD", "")).strip()
    except Exception:
        return ""


def is_auth_enabled() -> bool:
    return bool(_get_configured_password())


def require_auth() -> None:
    """Exige senha quando APP_PASSWORD estiver configurada; libera acesso se não houver senha."""
    expected = _get_configured_password()
    if not expected:
        return

    if st.session_state.get(_AUTH_KEY):
        return

    _, col, _ = st.columns([1, 1.4, 1])
    with col:
        st.space("large")
        with st.container(border=True):
            st.html(
                '<span class="vertice-pill-gold">Governança Financeira</span>'
                '<div class="vertice-cinzel-sidebar" style="margin-top:0.5rem;">Auditoria Vértice</div>'
            )
            st.caption("Acesso restrito. Informe a senha da equipe para continuar.")
            with st.form("form_vertice_login", border=False):
                pwd = st.text_input("Senha de acesso", type="password", key="inp_vertice_password")
                submitted = st.form_submit_button("Entrar", type="primary", width="stretch")
                if submitted:
                    if hmac.compare_digest(pwd or "", expected):
                        st.session_state[_AUTH_KEY] = True
                        st.rerun()
                    else:
                        st.error("Senha incorreta.")
    st.stop()


def render_logout_button() -> None:
    """Exibe botão de logout na sidebar quando a autenticação está ativa."""
    if is_auth_enabled() and st.session_state.get(_AUTH_KEY):
        if st.button("Sair", icon=":material/logout:", width="stretch", key="btn_vertice_logout"):
            st.session_state[_AUTH_KEY] = False
            st.rerun()
