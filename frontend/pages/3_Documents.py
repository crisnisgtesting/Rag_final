"""Document registry, ingestion queue and approval controls."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import streamlit as st

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from frontend.api_client import ApiError  # noqa: E402
from frontend.ui import (  # noqa: E402
    get_client,
    is_admin,
    render_sidebar,
    render_table,
    require_login,
)

st.set_page_config(page_title="Documents", layout="wide")

user = require_login()
render_sidebar(user)
admin = is_admin(user)
client = get_client()

st.title("Documents")
st.caption(
    "Imported versions stay in draft until an administrator approves them. "
    "Only approved, active versions are used by search and chat."
)

if admin:
    with st.expander("Add documents", expanded=False):
        scan_col, upload_col = st.columns(2)
        with scan_col:
            st.markdown("**Scan the inbox**")
            st.caption(
                "Registers every PDF, DOCX and Markdown file in `data/documents/` "
                "and skips duplicates."
            )
            if st.button("Scan inbox", use_container_width=True):
                try:
                    result = client.scan_documents()
                except ApiError as exc:
                    st.error(str(exc))
                else:
                    st.success(
                        f"Scanned {result.get('scanned', 0)} file(s); "
                        f"{result.get('registered', 0)} registered, "
                        f"{result.get('duplicates', 0)} duplicate(s)."
                    )
                    st.json(result)
        with upload_col:
            st.markdown("**Upload a document**")
            uploaded = st.file_uploader(
                "Choose a file", type=["pdf", "docx", "md", "markdown"]
            )
            if st.button(
                "Upload document", use_container_width=True, disabled=uploaded is None
            ):
                try:
                    result = client.upload_document(
                        uploaded.name, uploaded.getvalue()
                    )
                except ApiError as exc:
                    st.error(str(exc))
                else:
                    if result.get("status") == "duplicate":
                        st.info("This exact file is already registered.")
                    else:
                        st.success(f"Registered {result.get('title')}.")
                    st.json(result)
                    st.rerun()

try:
    documents = client.list_documents().get("items", [])
except ApiError as exc:
    st.error(str(exc))
    st.stop()

if not documents:
    st.info("No documents registered yet. Scan the inbox or upload a PDF.")
    st.stop()

st.subheader("Registered documents")
render_table(
    ["Title", "Status", "Latest version", "Approval", "Pages", "Chunks", "Latest job"],
    [
        [
            item.get("title"),
            item.get("status"),
            item.get("version_number") or "-",
            item.get("approval_status") or "-",
            item.get("page_count") or 0,
            item.get("chunk_count") or 0,
            (
                f"{item.get('latest_job_status')} / "
                f"{item.get('latest_job_stage')} / "
                f"{item.get('latest_job_progress')}%"
                if item.get("latest_job_status")
                else "-"
            ),
        ]
        for item in documents
    ],
)

labels = [
    f"{item.get('title')} [{item.get('status')}]" for item in documents
]
selected_index = st.selectbox(
    "Select a document", range(len(labels)), format_func=lambda i: labels[i]
)
selected_document_id = documents[selected_index]["id"]

try:
    detail = client.document_detail(selected_document_id)
except ApiError as exc:
    st.error(str(exc))
    st.stop()

versions: list[dict[str, Any]] = detail.get("versions", [])
if not versions:
    st.info("This document has no versions yet.")
    st.stop()

version_labels = [
    f"v{item.get('version_number')} - {item.get('status')} / {item.get('approval_status')}"
    for item in versions
]
version_index = st.selectbox(
    "Select a version", range(len(version_labels)), format_func=lambda i: version_labels[i]
)
version = versions[version_index]
version_id = str(version["id"])

st.markdown(
    f"**Version id:** `{version_id}`  \n"
    f"**Status:** `{version.get('status')}`  \n"
    f"**Approval:** `{version.get('approval_status')}`  \n"
    f"**Pages:** `{version.get('page_count') or 0}`  \n"
    f"**Chunks:** `{version.get('chunk_count') or 0}`"
)

if admin:
    st.divider()
    st.subheader("Ingestion and approval")
    can_ingest = not (
        version.get("status") == "ready"
        and version.get("approval_status") == "approved"
    )
    if can_ingest:
        with st.form("ingest_form"):
            provider = st.selectbox(
                "Vision provider",
                ["configured default", "gemini", "qwen", "databricks"],
            )
            vision_mode = st.selectbox("Vision mode", ["auto", "all", "none"])
            model = st.text_input(
                "Model override",
                placeholder="Leave blank to use the configured model",
            )
            submitted = st.form_submit_button(
                "Start ingestion", use_container_width=True
            )
        if submitted:
            try:
                result = client.ingest_version(
                    version_id,
                    provider=None if provider == "configured default" else provider,
                    model=model.strip() or None,
                    vision_mode=vision_mode,
                )
            except ApiError as exc:
                st.error(str(exc))
            else:
                st.success(f"Ingestion job queued: {result.get('job', {}).get('id')}")
                st.rerun()

    approve_col, reject_col = st.columns(2)
    with approve_col:
        if st.button(
            "Approve version",
            use_container_width=True,
            disabled=version.get("status") != "ready"
            or version.get("approval_status") == "approved",
        ):
            try:
                client.approve_version(version_id)
            except ApiError as exc:
                st.error(str(exc))
            else:
                st.success("Version approved and made active.")
                st.rerun()
    with reject_col:
        if st.button(
            "Reject version",
            use_container_width=True,
            disabled=version.get("approval_status") == "rejected",
        ):
            try:
                client.reject_version(version_id)
            except ApiError as exc:
                st.error(str(exc))
            else:
                st.warning("Version rejected.")
                st.rerun()

st.divider()
st.subheader("Knowledge organization")
try:
    knowledge = client.knowledge_summary(version_id)
except ApiError as exc:
    st.error(str(exc))
    knowledge = {}

counts = knowledge or {}
if counts:
    render_table(
        ["Entities", "Relationships", "Workflows", "Steps", "Visuals", "Rules", "Conflicts"],
        [
            [
                counts.get("entities", 0),
                counts.get("relationships", 0),
                counts.get("workflows", 0),
                counts.get("steps", 0),
                counts.get("visuals", 0),
                counts.get("rules", 0),
                counts.get("open_conflicts", 0),
            ]
        ],
    )
else:
    st.caption("No structured knowledge has been built for this version yet.")

if admin:
    rebuild_col, approve_col = st.columns(2)
    with rebuild_col:
        if st.button("Rebuild knowledge", use_container_width=True):
            try:
                result = client.rebuild_knowledge(version_id, approve=False)
            except ApiError as exc:
                st.error(str(exc))
            else:
                st.success(f"Rebuilt knowledge: {result.get('stats')}")
                st.rerun()
    with approve_col:
        if st.button(
            "Approve knowledge",
            use_container_width=True,
            disabled=not counts.get("workflows"),
        ):
            try:
                result = client.rebuild_knowledge(version_id, approve=True)
            except ApiError as exc:
                st.error(str(exc))
            else:
                st.success(f"Approved knowledge: {result.get('stats')}")
                st.rerun()

try:
    workflows = client.knowledge_workflows(version_id=version_id, limit=50).get("items", [])
except ApiError as exc:
    st.error(str(exc))
    workflows = []

if workflows:
    render_table(
        ["Workflow", "Status", "Pages", "Steps", "Confidence"],
        [
            [
                item.get("title"),
                item.get("status"),
                (
                    f"{item.get('source_page_start')}-{item.get('source_page_end')}"
                    if item.get("source_page_start")
                    else "-"
                ),
                item.get("step_count", 0),
                item.get("confidence"),
            ]
            for item in workflows
        ],
    )

try:
    jobs = [
        item
        for item in client.list_jobs(limit=200).get("items", [])
        if str(item.get("document_version_id")) == version_id
    ]
except ApiError as exc:
    st.error(str(exc))
    jobs = []

if jobs:
    st.divider()
    st.subheader("Ingestion jobs")
    render_table(
        ["Job", "Status", "Stage", "Progress", "Pages", "Chunks", "Error"],
        [
            [
                str(item.get("id"))[:8],
                item.get("status"),
                item.get("stage"),
                f"{item.get('progress')}%",
                f"{item.get('completed_pages')}/{item.get('total_pages')}",
                (item.get("stats") or {}).get("chunks", 0),
                ((item.get("error_details") or {}).get("message") or "")[:80],
            ]
            for item in jobs
        ],
    )
    latest = jobs[0]
    if admin and latest.get("status") in {"running", "queued"}:
        if st.button("Cancel latest job", use_container_width=False):
            try:
                client.cancel_job(str(latest["id"]))
            except ApiError as exc:
                st.error(str(exc))
            else:
                st.warning("Cancellation requested.")
                st.rerun()
    if admin and latest.get("status") in {"paused", "failed", "cancelled"}:
        if st.button("Resume latest job", use_container_width=False):
            try:
                client.resume_job(str(latest["id"]))
            except ApiError as exc:
                st.error(str(exc))
            else:
                st.success("Job resumed.")
                st.rerun()
    with st.expander("Latest job details"):
        try:
            st.json(client.job_detail(str(latest["id"])))
        except ApiError as exc:
            st.error(str(exc))

st.divider()
st.subheader("Processed content")
pages_tab, chunks_tab = st.tabs(["Pages", "Chunks"])

with pages_tab:
    try:
        pages = client.version_pages(version_id).get("items", [])
    except ApiError as exc:
        st.error(str(exc))
        pages = []
    if pages:
        render_table(
            ["Page", "Status", "Source", "Content", "Vision", "Review", "Summary"],
            [
                [
                    item.get("physical_page_number"),
                    item.get("processing_status"),
                    item.get("text_source"),
                    item.get("content_type"),
                    item.get("analysis_model") or "-",
                    "yes" if item.get("needs_review") else "",
                    (item.get("analysis_summary") or "")[:120],
                ]
                for item in pages
            ],
        )
        with st.expander("Raw page data"):
            st.json(pages)
    else:
        st.caption("No processed pages available yet.")

with chunks_tab:
    try:
        chunks = client.version_chunks(version_id).get("items", [])
    except ApiError as exc:
        st.error(str(exc))
        chunks = []
    if chunks:
        render_table(
            ["#", "Pages", "Section", "Heading", "Chars", "Embedded", "Excerpt"],
            [
                [
                    item.get("chunk_index"),
                    (
                        item.get("page_start")
                        if item.get("page_start") == item.get("page_end")
                        else f"{item.get('page_start')}-{item.get('page_end')}"
                    ),
                    item.get("section_number") or "-",
                    item.get("heading") or "-",
                    item.get("char_count"),
                    "yes" if item.get("has_embedding") else "no",
                    (item.get("content") or "")[:140].replace("\n", " "),
                ]
                for item in chunks
            ],
        )
        with st.expander("Raw chunk data"):
            st.json(chunks)
    else:
        st.caption("No chunks available yet.")

with st.expander("Raw document data"):
    st.json(detail)
