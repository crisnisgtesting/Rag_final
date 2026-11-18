"""Grounded chat over approved FMM documents."""

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
    render_sidebar,
    render_table,
    require_login,
)
from frontend.voice import (  # noqa: E402
    audio_fingerprint,
    merge_transcript,
    transcribe_recording,
)

st.set_page_config(page_title="Ask the FMM assistant", layout="wide")

user = require_login()
render_sidebar(user)
client = get_client()

st.title("Ask the FMM assistant")
st.caption(
    "Answers are generated only from approved documents and include source markers."
)

try:
    sessions = client.chat_sessions(limit=50).get("items", [])
except ApiError as exc:
    st.error(str(exc))
    sessions = []

session_ids = [str(item["id"]) for item in sessions]
session_titles = {
    str(item["id"]): item.get("title") or str(item["id"])[:8] for item in sessions
}
current_session = st.session_state.get("chat_session_id")
if current_session not in session_ids:
    current_session = None

with st.sidebar:
    st.divider()
    st.subheader("Conversations")
    options = ["New chat", *session_ids]
    default_index = 0 if current_session is None else session_ids.index(current_session) + 1
    choice = st.selectbox(
        "Open conversation",
        options,
        index=default_index,
        format_func=lambda value: (
            "New chat" if value == "New chat" else session_titles.get(value, value)
        ),
    )
    if choice == "New chat":
        st.session_state["chat_session_id"] = None
    else:
        st.session_state["chat_session_id"] = choice
    if st.button("Start new chat", use_container_width=True):
        st.session_state["chat_session_id"] = None
        st.rerun()

session_id = st.session_state.get("chat_session_id")
history: list[dict[str, Any]] = []
if session_id:
    try:
        detail = client.chat_session(session_id)
    except ApiError as exc:
        st.error(str(exc))
        st.session_state["chat_session_id"] = None
    else:
        history = detail.get("messages", [])
        st.subheader(detail.get("session", {}).get("title") or "Conversation")

if not history:
    st.info(
        "Ask about a procedure, warning, field name or rule from the approved FMM "
        "documents. The assistant will say when the documents do not contain the answer."
    )

for message in history:
    role = message.get("role")
    content = message.get("content") or ""
    if role == "user":
        st.markdown(f"**You:** {content}")
        continue
    st.markdown("**Assistant:**")
    st.markdown(content)
    citations = message.get("citations") or []
    if citations:
        render_table(
            ["Source", "Document", "Version", "Pages", "Section/Heading", "Score"],
            [
                [
                    f"[{item.get('marker')}]",
                    item.get("title"),
                    item.get("version_number"),
                    (
                        item.get("page_start")
                        if item.get("page_start") == item.get("page_end")
                        else f"{item.get('page_start')}-{item.get('page_end')}"
                    ),
                    item.get("heading") or item.get("section_number") or "-",
                    item.get("score"),
                ]
                for item in citations
            ],
        )
        with st.expander("Citations and retrieved chunks"):
            st.json(citations)
    if message.get("retrieval"):
        with st.expander("Retrieval details"):
            st.json(message["retrieval"])
    response_id = message.get("response_id")
    if response_id:
        feedback_col, status_col = st.columns([1, 2])
        with feedback_col:
            up_col, down_col = st.columns(2)
            with up_col:
                if st.button("Helpful", key=f"up_{message.get('id')}"):
                    try:
                        client.submit_feedback(str(response_id), 1)
                    except ApiError as exc:
                        st.error(str(exc))
                    else:
                        st.success("Feedback recorded.")
            with down_col:
                if st.button("Not helpful", key=f"down_{message.get('id')}"):
                    try:
                        client.submit_feedback(str(response_id), -1)
                    except ApiError as exc:
                        st.error(str(exc))
                    else:
                        st.success("Feedback recorded.")
        with status_col:
            st.caption(
                f"Language: {message.get('language') or 'en'} | "
                f"Citation check: {message.get('citation_status') or 'unchecked'}"
            )
    st.divider()
try:
    documents = [
        item
        for item in client.list_documents().get("items", [])
        if item.get("status") == "active"
    ]
except ApiError:
    documents = []

document_options = {str(item["id"]): item.get("title") for item in documents}

st.subheader("Ask a question")

voice: dict[str, Any] = {}
try:
    voice = client.voice_capability()
except ApiError:
    voice = {}
voice_available = bool(voice.get("available"))
if voice:
    if voice_available:
        st.caption(
            f"Voice input ready (local transcription, model '{voice.get('model')}')."
        )
    else:
        st.warning(
            "Voice input is unavailable on the server. Install faster-whisper "
            "(requirements-ml.txt) to enable dictation, or type your question."
        )

recording = st.audio_input(
    "Speak your question",
    key="chat_mic",
    disabled=bool(voice) and not voice_available,
    help=(
        "Click the microphone, speak your question and press stop. The recognized "
        "text is placed in the question box, where you can edit it before asking."
    ),
)

if recording is not None:
    audio_bytes = recording.getvalue()
    fingerprint = audio_fingerprint(audio_bytes)
    if fingerprint != st.session_state.get("voice_fingerprint"):
        st.session_state["voice_fingerprint"] = fingerprint
        with st.spinner("Transcribing your question..."):
            try:
                transcript = transcribe_recording(client, audio_bytes)
            except ApiError as exc:
                st.error(
                    f"Could not transcribe the recording: {exc}. "
                    "You can still type the question."
                )
            else:
                if transcript:
                    st.session_state["voice_transcript"] = transcript
                else:
                    st.warning(
                        "No speech was detected in the recording. "
                        "You can still type the question."
                    )
        if st.session_state.get("voice_transcript"):
            st.rerun()

pending_transcript = st.session_state.pop("voice_transcript", None)
if pending_transcript:
    current = str(st.session_state.get("chat_question", "") or "")
    merged = merge_transcript(
        current, pending_transcript, st.session_state.get("voice_applied")
    )
    st.session_state["chat_question"] = merged
    st.session_state["voice_applied"] = merged

with st.form("chat_form", clear_on_submit=True):
    question = st.text_area(
        "Question",
        height=110,
        key="chat_question",
        placeholder="Example: What should I check before parting a hot axle train?",
    )
    left, right = st.columns([1, 2])
    with left:
        top_k = st.slider("Sources to retrieve", 1, 20, 8)
        stream_enabled = st.checkbox("Stream the answer", value=False)
        document_types = st.multiselect(
            "Document type", options=["pdf", "docx", "markdown"], default=[]
        )
    with right:
        selected_documents = st.multiselect(
            "Limit to documents (optional)",
            options=list(document_options),
            format_func=lambda value: document_options.get(value, value),
            disabled=not document_options,
        )
    submitted = st.form_submit_button("Ask", use_container_width=True)

if submitted:
    if not question.strip():
        st.warning("Enter a question.")
    elif stream_enabled:
        placeholder = st.empty()
        with st.spinner("Searching approved documents and composing an answer..."):
            try:
                events = client.chat_stream(
                    question.strip(),
                    session_id=session_id,
                    top_k=top_k,
                    document_ids=selected_documents or None,
                    document_types=document_types or None,
                )
            except ApiError as exc:
                st.error(str(exc))
            else:
                answer_parts: list[str] = []
                citations: list[dict[str, Any]] = []
                streamed_session = session_id
                for event in events:
                    name = event.get("event")
                    payload = event.get("data")
                    if name == "token" and isinstance(payload, dict):
                        answer_parts.append(str(payload.get("text") or ""))
                        placeholder.markdown("".join(answer_parts))
                    elif name == "meta" and isinstance(payload, dict):
                        streamed_session = str(payload.get("session_id") or streamed_session)
                    elif name == "citations" and isinstance(payload, list):
                        citations = payload
                    elif name == "error":
                        st.error(str(payload.get("message") if isinstance(payload, dict) else payload))
                if answer_parts:
                    st.session_state["chat_session_id"] = streamed_session
                    st.rerun()
    else:
        with st.spinner("Searching approved documents and composing an answer..."):
            try:
                result = client.chat(
                    question.strip(),
                    session_id=session_id,
                    top_k=top_k,
                    document_ids=selected_documents or None,
                    document_types=document_types or None,
                )
            except ApiError as exc:
                st.error(str(exc))
            else:
                st.session_state["chat_session_id"] = result.get("session_id")
                st.rerun()
