"""Knowledge review: workflows, steps, rules, visuals and conflicts."""

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

st.set_page_config(page_title="Knowledge", layout="wide")

user = require_login()
render_sidebar(user)
admin = is_admin(user)
client = get_client()

st.title("Knowledge organization")
st.caption(
    "Structured knowledge derived from approved document content: workflows, "
    "steps, rules, visual references and detected conflicts."
)

try:
    summary = client.knowledge_summary()
except ApiError as exc:
    st.error(str(exc))
    summary = {}

if summary:
    render_table(
        [
            "Entities",
            "Relationships",
            "Workflows",
            "Steps",
            "Visuals",
            "Rules",
            "Page Markdown",
            "Open Conflicts",
        ],
        [
            [
                summary.get("entities", 0),
                summary.get("relationships", 0),
                summary.get("workflows", 0),
                summary.get("steps", 0),
                summary.get("visuals", 0),
                summary.get("rules", 0),
                summary.get("page_markdown", 0),
                summary.get("open_conflicts", 0),
            ]
        ],
    )

workflows_tab, conflicts_tab = st.tabs(["Workflows", "Conflicts"])

with workflows_tab:
    status_filter = st.selectbox(
        "Status", ["all", "proposed", "approved", "rejected", "superseded"]
    )
    try:
        workflows = client.knowledge_workflows(
            status=None if status_filter == "all" else status_filter, limit=200
        ).get("items", [])
    except ApiError as exc:
        st.error(str(exc))
        workflows = []

    if not workflows:
        st.info("No workflows are available yet. Ingest and approve a document first.")
    else:
        render_table(
            [
                "Workflow",
                "Document",
                "Status",
                "Pages",
                "Steps",
                "Visuals",
                "Rules",
                "Confidence",
            ],
            [
                [
                    item.get("title"),
                    item.get("filename"),
                    item.get("status"),
                    (
                        f"{item.get('source_page_start')}-{item.get('source_page_end')}"
                        if item.get("source_page_start")
                        else "-"
                    ),
                    item.get("step_count", 0),
                    item.get("visual_count", 0),
                    item.get("rule_count", 0),
                    item.get("confidence"),
                ]
                for item in workflows
            ],
        )
        labels = [
            f"{item.get('title')} ({item.get('filename')})" for item in workflows
        ]
        index = st.selectbox(
            "Inspect a workflow", range(len(labels)), format_func=lambda i: labels[i]
        )
        workflow_id = str(workflows[index]["id"])
        try:
            detail = client.knowledge_workflow(workflow_id)
        except ApiError as exc:
            st.error(str(exc))
            detail = {}

        workflow = detail.get("workflow") or {}
        steps = detail.get("steps") or []
        rules = detail.get("rules") or []
        visuals = detail.get("visuals") or []

        st.markdown(f"**Workflow:** {workflow.get('title')}")
        if workflow.get("summary"):
            st.markdown(f"**Summary:** {workflow.get('summary')}")

        if steps:
            st.markdown("**Steps**")
            render_table(
                ["#", "Action", "Description", "Page", "Status"],
                [
                    [
                        item.get("step_number"),
                        item.get("action"),
                        (item.get("description") or "")[:160],
                        item.get("source_page_number"),
                        item.get("status"),
                    ]
                    for item in steps
                ],
            )
        if rules:
            st.markdown("**Rules and warnings**")
            render_table(
                ["Type", "Rule", "Page", "Status"],
                [
                    [
                        item.get("rule_type"),
                        item.get("rule_text"),
                        item.get("source_page_number"),
                        item.get("status"),
                    ]
                    for item in rules
                ],
            )
        if visuals:
            st.markdown("**Visual references**")
            render_table(
                ["Element", "Label", "Action", "Callout", "Page"],
                [
                    [
                        item.get("element_type"),
                        item.get("label"),
                        item.get("action"),
                        item.get("callout_number"),
                        item.get("page_number"),
                    ]
                    for item in visuals
                ],
            )

        if admin:
            approve_col, reject_col = st.columns(2)
            with approve_col:
                if st.button(
                    "Approve workflow",
                    use_container_width=True,
                    disabled=workflow.get("status") == "approved",
                ):
                    try:
                        client.set_knowledge_status(workflow_id, "approved")
                    except ApiError as exc:
                        st.error(str(exc))
                    else:
                        st.success("Workflow approved.")
                        st.rerun()
            with reject_col:
                if st.button(
                    "Reject workflow",
                    use_container_width=True,
                    disabled=workflow.get("status") == "rejected",
                ):
                    try:
                        client.set_knowledge_status(workflow_id, "rejected")
                    except ApiError as exc:
                        st.error(str(exc))
                    else:
                        st.warning("Workflow rejected.")
                        st.rerun()

with conflicts_tab:
    try:
        conflicts = client.knowledge_conflicts(status="open", limit=200).get("items", [])
    except ApiError as exc:
        st.error(str(exc))
        conflicts = []

    if not conflicts:
        st.success("No open conflicts.")
    else:
        render_table(
            ["Type", "Description", "Detected by", "Created"],
            [
                [
                    item.get("conflict_type"),
                    item.get("description"),
                    item.get("detected_by"),
                    item.get("created_at"),
                ]
                for item in conflicts
            ],
        )
        if admin:
            conflict_ids = [str(item["id"]) for item in conflicts]
            selected = st.selectbox(
                "Resolve a conflict", range(len(conflict_ids)), format_func=lambda i: conflicts[i].get("conflict_type", "")
            )
            resolution = st.text_area("Resolution note", height=90)
            action_col, ignore_col = st.columns(2)
            with action_col:
                if st.button(
                    "Mark resolved",
                    use_container_width=True,
                    disabled=not resolution.strip(),
                ):
                    try:
                        client.resolve_conflict(
                            conflict_ids[selected], "resolved", resolution.strip()
                        )
                    except ApiError as exc:
                        st.error(str(exc))
                    else:
                        st.success("Conflict resolved.")
                        st.rerun()
            with ignore_col:
                if st.button(
                    "Ignore conflict",
                    use_container_width=True,
                    disabled=not resolution.strip(),
                ):
                    try:
                        client.resolve_conflict(
                            conflict_ids[selected], "ignored", resolution.strip()
                        )
                    except ApiError as exc:
                        st.error(str(exc))
                    else:
                        st.success("Conflict ignored.")
                        st.rerun()
