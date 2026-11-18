"""Guards for Streamlit elements that require the blocked pyarrow DLL."""

from __future__ import annotations

from pathlib import Path

import frontend.ui as ui

FRONTEND_ROOT = Path(__file__).resolve().parents[2] / "frontend"


def test_frontend_avoids_arrow_backed_tables() -> None:
    offenders: list[str] = []
    for path in FRONTEND_ROOT.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        for element in ("st.dataframe", "st.table", "st.data_editor"):
            if element in source:
                offenders.append(f"{path.relative_to(FRONTEND_ROOT)}: {element}")
    assert not offenders, offenders


def test_admin_page_uses_markdown_tables() -> None:
    source = (FRONTEND_ROOT / "pages" / "1_Admin_Vision.py").read_text(encoding="utf-8")
    assert source.count("render_table(") >= 2


def test_api_base_url_prefers_environment(monkeypatch) -> None:
    monkeypatch.setenv("API_BASE_URL", "http://api.example")
    assert ui.api_base_url() == "http://api.example"


def test_api_base_url_defaults_without_secrets(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("API_BASE_URL", raising=False)
    monkeypatch.setattr(ui, "SECRETS_PATHS", (tmp_path / "missing-secrets.toml",))
    assert ui.api_base_url() == ui.DEFAULT_API_BASE_URL
