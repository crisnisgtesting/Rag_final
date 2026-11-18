"""Administrator: vision provider selection and connectivity testing."""

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

st.set_page_config(page_title="Vision provider settings", layout="wide")

user = require_login()
render_sidebar(user)

if not is_admin(user):
    st.error("Administrator access is required for this page.")
    st.stop()

st.title("Vision provider settings")
st.caption(
    "Select the multimodal model used to read manual pages. "
    "Switching providers never falls back automatically."
)

client = get_client()

try:
    config = client.vision_configuration()
except ApiError as exc:
    st.error(str(exc))
    st.stop()

providers = config.get("providers", [])
active = config.get("active_provider")
source = config.get("source")
environment_default = config.get("environment_default")

st.markdown(
    f"**Active provider:** `{active}`  \n"
    f"**Selection source:** `{source}`  \n"
    f"**Environment default:** `{environment_default}`"
)

if providers:
    render_table(
        ["Provider", "Model", "Credentials", "Endpoint", "Active"],
        [
            [
                item.get("provider"),
                item.get("model") or "—",
                "configured" if item.get("credential_configured") else "missing",
                item.get("endpoint") or "—",
                "yes" if item.get("is_active") else "",
            ]
            for item in providers
        ],
    )

st.divider()
st.subheader("Change provider")

names = [item.get("provider") for item in providers] or [
    "qwen",
    "gemini",
    "databricks",
]
default_index = names.index(active) if active in names else 0

with st.form("provider_form"):
    selected = st.selectbox("Vision provider", names, index=default_index)
    save = st.form_submit_button("Save provider", use_container_width=True)

if save:
    try:
        updated = client.set_vision_provider(str(selected))
    except ApiError as exc:
        st.error(str(exc))
    else:
        st.success(f"Active vision provider is now '{updated.get('active_provider')}'.")
        st.rerun()

st.divider()
st.subheader("Test connectivity")

with st.form("test_form"):
    test_target = st.selectbox(
        "Provider to test",
        names,
        index=names.index(active) if active in names else 0,
        key="test_target",
    )
    run_test = st.form_submit_button("Run connectivity test", use_container_width=True)

if run_test:
    with st.spinner(f"Testing {test_target}..."):
        try:
            health = client.test_vision_provider(str(test_target))
        except ApiError as exc:
            st.error(str(exc))
        else:
            status_text = health.get("status")
            if health.get("reachable"):
                st.success(f"{test_target}: {status_text}")
            else:
                st.warning(f"{test_target}: {status_text}")
            st.json(health)
            st.caption(
                "Exact provider/model for each processing run is recorded in the database. "
                "No automatic fallback is performed."
            )

st.divider()
audit_items: list[dict] = []
with st.expander("Recent audit events"):
    try:
        log = client.audit_log(limit=50)
        audit_items = log.get("items", [])
        if audit_items:
            render_table(
                ["Time", "Action", "Actor", "Entity", "Request ID"],
                [
                    [
                        str(item.get("created_at") or "")[:19].replace("T", " "),
                        item.get("action"),
                        item.get("actor_user_id") or "—",
                        " ".join(
                            str(part)
                            for part in (item.get("entity_type"), item.get("entity_id"))
                            if part
                        )
                        or "—",
                        item.get("request_id") or "—",
                    ]
                    for item in audit_items
                ],
            )
        else:
            st.caption("No audit events recorded yet.")
    except ApiError as exc:
        st.error(str(exc))

if audit_items:
    with st.expander("Raw audit data"):
        st.json(audit_items)
