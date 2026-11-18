"""Search debugging: hybrid retrieval with filters and knowledge matches."""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from frontend.api_client import ApiError  # noqa: E402
from frontend.ui import (  # noqa: E402
    get_client,
    render_sidebar,
    render_table,
    require_login,
)

st.set_page_config(page_title="Search", layout="wide")

user = require_login()
render_sidebar(user)
client = get_client()

st.title("Search")
st.caption(
    "Hybrid dense plus lexical retrieval over approved documents, with "
    "knowledge-aware boosting from reviewed workflows, entities and rules."
)

try:
    documents = client.list_documents().get("items", [])
except ApiError as exc:
    st.error(str(exc))
    documents = []

document_options = {
    str(item["id"]): item.get("title") for item in documents if item.get("status") == "active"
}

with st.form("search_form"):
    query = st.text_input(
        "Query",
        placeholder="Example: how is an attendance group updated?",
    )
    left, right = st.columns([1, 2])
    with left:
        top_k = st.slider("Results", 1, 30, 8)
        document_types = st.multiselect(
            "Document type",
            options=["pdf", "docx", "markdown"],
            default=[],
        )
        content_types = st.multiselect(
            "Content type",
            options=["procedure", "reference", "table", "unknown"],
            default=[],
        )
    with right:
        selected_documents = st.multiselect(
            "Limit to documents",
            options=list(document_options),
            format_func=lambda value: document_options.get(value, value),
            disabled=not document_options,
        )
    submitted = st.form_submit_button("Search", use_container_width=True)

if submitted:
    if not query.strip():
        st.warning("Enter a query.")
    else:
        with st.spinner("Retrieving..."):
            try:
                result = client.search(
                    query.strip(),
                    top_k=top_k,
                    document_ids=selected_documents or None,
                    document_types=document_types or None,
                    content_types=content_types or None,
                )
            except ApiError as exc:
                st.error(str(exc))
            else:
                items = result.get("items", [])
                if not items:
                    st.info("No approved content matched this query.")
                else:
                    render_table(
                        [
                            "Score",
                            "Dense",
                            "Lexical",
                            "Document",
                            "Pages",
                            "Section",
                            "Heading",
                            "Knowledge boost",
                        ],
                        [
                            [
                                round(item.get("score", 0), 6),
                                round(item.get("dense_score", 0), 4),
                                round(item.get("lexical_score", 0), 4),
                                item.get("filename"),
                                (
                                    f"{item.get('page_start')}-{item.get('page_end')}"
                                    if item.get("page_start") != item.get("page_end")
                                    else item.get("page_start")
                                ),
                                item.get("section_number") or "-",
                                item.get("heading") or "-",
                                (item.get("metadata") or {}).get("knowledge_boost", "-"),
                            ]
                            for item in items
                        ],
                    )
                    with st.expander("Chunk content"):
                        for item in items:
                            st.markdown(f"**{item.get('filename')}** (p{item.get('page_start')})")
                            st.text((item.get("content") or "")[:1500])
