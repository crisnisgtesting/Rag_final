"""FMM Knowledge and Operational Assistant - Streamlit shell (Phase 2)."""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from frontend.api_client import ApiError  # noqa: E402
from frontend.ui import (  # noqa: E402
    get_client,
    is_admin,
    render_sidebar,
    require_login,
)

st.set_page_config(
    page_title="FMM Knowledge and Operational Assistant",
    layout="wide",
)

user = require_login()
render_sidebar(user)

st.title("FMM Knowledge and Operational Assistant")
st.caption("Internal technical assistant for the FMM machine")

left, right = st.columns([2, 1])

with left:
    st.markdown(
        f"""
Welcome, **{user.get("full_name") or user.get("username")}**.

Available today:

- Secure sign-in with role-based access (administrator / user)
- Document registration for PDF, DOCX and Markdown, with selective vision analysis
- Hybrid dense plus lexical retrieval over approved documents
- Grounded chat with page and section citations, streaming and voice input
- Reviewable knowledge: workflows, steps, rules, visual references and conflicts
- Response feedback, unanswered-question tracking and evaluation runs
"""
    )

    st.info(
        "Administrators can scan `data/documents/`, ingest documents and approve "
        "versions from the Documents page. Approved versions become available to "
        "search and chat."
    )

with right:
    st.subheader("System status")
    try:
        health = get_client().health_ready()
        if health.get("status") == "ready":
            st.success("API and database are ready")
        else:
            st.warning("API is up but dependencies are not ready")
        st.json(health)
    except ApiError as exc:
        st.error(str(exc))

st.divider()
st.subheader("Workspace")
st.page_link("pages/4_Chat.py", label="Ask the FMM assistant")
st.page_link("pages/5_Knowledge.py", label="Knowledge organization")
st.page_link("pages/7_Search.py", label="Search the corpus")

if is_admin(user):
    st.subheader("Administrator")
    st.page_link("pages/3_Documents.py", label="Documents and ingestion")
    st.page_link("pages/6_Quality.py", label="Quality and operations")
    st.page_link("pages/1_Admin_Vision.py", label="Vision provider settings")
    st.page_link("pages/2_System_Status.py", label="System status details")
else:
    st.caption("Administrator pages are hidden for this account.")
