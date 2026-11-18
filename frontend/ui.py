"""Shared Streamlit helpers: configuration, login and session state."""

from __future__ import annotations

import os
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

import streamlit as st

from frontend.api_client import ApiError, FmmApiClient

DEFAULT_API_BASE_URL = "http://127.0.0.1:8000"
SECRETS_PATHS = (
    Path.home() / ".streamlit" / "secrets.toml",
    Path.cwd() / ".streamlit" / "secrets.toml",
)


def _secrets_file_exists() -> bool:
    return any(path.is_file() for path in SECRETS_PATHS)


def api_base_url() -> str:
    configured = os.environ.get("API_BASE_URL")
    if configured:
        return configured
    if _secrets_file_exists():
        try:
            url = st.secrets.get("api_base_url")  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001 - secrets file is optional
            url = None
        if url:
            return str(url)
    return DEFAULT_API_BASE_URL


def render_table(headers: Sequence[str], rows: Iterable[Sequence[object]]) -> None:
    """Render a static table as Markdown, avoiding Streamlit's pyarrow tables."""

    def cell(value: object) -> str:
        text = "—" if value is None or value == "" else str(value)
        return text.replace("|", "\\|").replace("\r", " ").replace("\n", " ")

    header = "| " + " | ".join(cell(item) for item in headers) + " |"
    separator = "| " + " | ".join("---" for _ in headers) + " |"
    body = ["| " + " | ".join(cell(item) for item in row) + " |" for row in rows]
    st.markdown("\n".join([header, separator, *body]))


def get_client() -> FmmApiClient:
    return FmmApiClient(api_base_url(), token=st.session_state.get("token"))


def _render_login() -> None:
    st.subheader("Sign in")
    with st.form("login_form", clear_on_submit=False):
        username = st.text_input("Username", autocomplete="username")
        password = st.text_input(
            "Password", type="password", autocomplete="current-password"
        )
        submitted = st.form_submit_button("Sign in", use_container_width=True)

    if submitted:
        if not username or not password:
            st.error("Enter both username and password.")
            return
        client = FmmApiClient(api_base_url())
        try:
            data = client.login(username, password)
        except ApiError as exc:
            st.error(str(exc))
            return
        st.session_state["token"] = data.get("access_token")
        st.session_state["user"] = data.get("user")
        st.rerun()


def require_login() -> dict[str, Any]:
    """Ensure a user is authenticated; returns the user payload."""
    if not st.session_state.get("token"):
        _render_login()
        st.stop()

    user = st.session_state.get("user")
    if not user:
        try:
            user = get_client().me()
        except ApiError as exc:
            st.session_state.pop("token", None)
            st.session_state.pop("user", None)
            st.warning(f"Session expired or invalid: {exc}")
            _render_login()
            st.stop()
        st.session_state["user"] = user
    return user


def logout() -> None:
    st.session_state.pop("token", None)
    st.session_state.pop("user", None)
    st.rerun()


def is_admin(user: dict[str, Any]) -> bool:
    return "admin" in (user.get("roles") or [])


def render_sidebar(user: dict[str, Any] | None = None) -> None:
    with st.sidebar:
        st.title("FMM Assistant")
        if user:
            st.caption(f"Signed in as **{user.get('username')}**")
            st.caption("Roles: " + ", ".join(user.get("roles") or []))
            if st.button("Sign out", use_container_width=True):
                logout()
        st.divider()
        st.caption(
            "Ingestion, knowledge review, grounded chat, voice and evaluation"
        )


def role_badge(roles: list[str]) -> str:
    return ", ".join(roles) if roles else "user"
