"""Operations: unanswered questions, evaluation sets and health."""

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
    is_admin,
    render_sidebar,
    render_table,
    require_login,
)

st.set_page_config(page_title="Quality and operations", layout="wide")

user = require_login()
render_sidebar(user)
client = get_client()

st.title("Quality and operations")
st.caption(
    "Questions the assistant could not answer, retrieval evaluation sets and "
    "live service counters."
)

if not is_admin(user):
    st.warning("This page is restricted to administrators.")
    st.stop()

health_tab, questions_tab, eval_tab = st.tabs(
    ["Health", "Unanswered questions", "Evaluations"]
)

with health_tab:
    try:
        health = client.operations_health()
    except ApiError as exc:
        st.error(str(exc))
        health = {}
    counters = health.get("counts") or {}
    if counters:
        render_table(
            [
                "Documents",
                "Ready versions",
                "Running jobs",
                "Failed jobs",
                "Missing embeddings",
                "Open conflicts",
                "Open questions",
                "Requests (1h)",
                "Errors (1h)",
            ],
            [
                [
                    counters.get("documents", 0),
                    counters.get("ready_versions", 0),
                    counters.get("running_jobs", 0),
                    counters.get("failed_jobs", 0),
                    counters.get("missing_embeddings", 0),
                    counters.get("open_conflicts", 0),
                    counters.get("open_questions", 0),
                    counters.get("requests_last_hour", 0),
                    counters.get("errors_last_hour", 0),
                ]
            ],
        )
    recent_jobs = health.get("recent_jobs") or []
    if recent_jobs:
        st.markdown("**Recent ingestion jobs**")
        render_table(
            ["Job", "Status", "Stage", "Progress", "Error"],
            [
                [
                    str(item.get("id"))[:8],
                    item.get("status"),
                    item.get("stage"),
                    f"{item.get('progress')}%",
                    ((item.get("error_details") or {}).get("message") or "")[:80],
                ]
                for item in recent_jobs
            ],
        )
    with st.expander("Raw health payload"):
        st.json(health)

with questions_tab:
    status_filter = st.selectbox(
        "Status", ["open", "in_review", "resolved", "ignored"]
    )
    try:
        questions = client.unanswered_questions(status=status_filter).get("items", [])
    except ApiError as exc:
        st.error(str(exc))
        questions = []

    if not questions:
        st.success("No unanswered questions with this status.")
    else:
        render_table(
            ["Question", "Language", "Reason", "Session", "Created"],
            [
                [
                    item.get("question"),
                    item.get("language"),
                    item.get("reason"),
                    item.get("session_title") or "-",
                    item.get("created_at"),
                ]
                for item in questions
            ],
        )
        ids = [str(item["id"]) for item in questions]
        selected = st.selectbox(
            "Resolve a question", range(len(ids)), format_func=lambda i: questions[i].get("question", "")[:80]
        )
        resolution = st.text_area("Resolution", height=90)
        resolve_col, ignore_col = st.columns(2)
        with resolve_col:
            if st.button(
                "Mark resolved", use_container_width=True, disabled=not resolution.strip()
            ):
                try:
                    client.resolve_unanswered(ids[selected], "resolved", resolution.strip())
                except ApiError as exc:
                    st.error(str(exc))
                else:
                    st.success("Question resolved.")
                    st.rerun()
        with ignore_col:
            if st.button(
                "Ignore question", use_container_width=True, disabled=not resolution.strip()
            ):
                try:
                    client.resolve_unanswered(ids[selected], "ignored", resolution.strip())
                except ApiError as exc:
                    st.error(str(exc))
                else:
                    st.success("Question ignored.")
                    st.rerun()

with eval_tab:
    with st.form("eval_case_form"):
        name = st.text_input("Evaluation name")
        question = st.text_area("Question", height=80)
        keywords = st.text_input("Expected keywords (comma separated)")
        expected = st.text_area("Expected answer (optional)", height=80)
        language = st.text_input("Language", value="en")
        submitted = st.form_submit_button("Add evaluation case", use_container_width=True)

    if submitted:
        if not name.strip() or not question.strip():
            st.warning("Name and question are required.")
        else:
            keyword_list = [
                item.strip() for item in keywords.split(",") if item.strip()
            ]
            try:
                client.add_eval_case(
                    name.strip(),
                    question.strip(),
                    expected_keywords=keyword_list or None,
                    expected_answer=expected.strip() or None,
                    language=language.strip() or None,
                )
            except ApiError as exc:
                st.error(str(exc))
            else:
                st.success("Evaluation case added.")
                st.rerun()

    try:
        cases = client.eval_cases().get("items", [])
    except ApiError as exc:
        st.error(str(exc))
        cases = []

    if cases:
        st.markdown("**Evaluation set**")
        render_table(
            ["Name", "Question", "Keywords", "Language", "Active"],
            [
                [
                    item.get("name"),
                    (item.get("question") or "")[:80],
                    ", ".join(item.get("expected_keywords") or []),
                    item.get("language"),
                    "yes" if item.get("active") else "no",
                ]
                for item in cases
            ],
        )
        if st.button("Run evaluation", use_container_width=False):
            with st.spinner("Running evaluation set..."):
                try:
                    result = client.run_evals(limit=50)
                except ApiError as exc:
                    st.error(str(exc))
                else:
                    st.success(
                        f"Passed {result.get('passed')} of {result.get('cases')} "
                        f"cases ({result.get('pass_rate')})."
                    )
                    st.json(result)

    try:
        results = client.eval_results(limit=100).get("items", [])
    except ApiError as exc:
        st.error(str(exc))
        results = []

    if results:
        st.markdown("**Recent results**")
        render_table(
            [
                "Case",
                "Question",
                "Passed",
                "Keyword recall",
                "Document match",
                "Citation valid",
                "Latency (ms)",
            ],
            [
                [
                    item.get("name"),
                    (item.get("question") or "")[:60],
                    "yes" if item.get("passed") else "no",
                    item.get("keyword_recall"),
                    "yes" if item.get("document_match") else "no",
                    "yes" if item.get("citation_valid") else "no",
                    item.get("latency_ms"),
                ]
                for item in results
            ],
        )
