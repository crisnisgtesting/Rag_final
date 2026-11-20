"""System status details."""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from frontend.api_client import ApiError  # noqa: E402
from frontend.ui import api_base_url, get_client, render_sidebar, require_login  # noqa: E402

st.set_page_config(page_title="System status", layout="wide")

user = require_login()
render_sidebar(user)

st.title("System status")
st.caption(f"API base URL: {api_base_url()}")

client = get_client()

if st.button("Refresh", use_container_width=False):
    st.rerun()

try:
    health = client.health_ready()
    st.subheader("Readiness")
    st.json(health)
    if health.get("status") == "ready":
        st.success("Databricks SQL is available.")
    else:
        st.warning("One or more dependencies are not ready.")
except ApiError as exc:
    st.error(str(exc))

st.subheader("Voice input")
try:
    voice = client.voice_capability()
except ApiError as exc:
    st.error(str(exc))
else:
    st.json(voice)
    if voice.get("available"):
        st.success(
            f"Microphone dictation is enabled (local model '{voice.get('model')}')."
        )
    else:
        st.warning(
            "Microphone dictation is unavailable. Install faster-whisper from "
            "requirements-ml.txt and restart the API."
        )

st.subheader("Current account")
st.json(user)
