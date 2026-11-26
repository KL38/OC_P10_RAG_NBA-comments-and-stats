"""Minimal Streamlit chat UI — thin client over the Pydantic AI agent.

Port of the prototype's ``MistralChat.py``. Since Phase 3 the call site routes
through the agent (``agent/agent.py``), which picks between the RAG tool
(match commentary) and the SQL tool (NBA stats) — the UI only renders the
typed ``RagAnswer``.

Run:
    uv run streamlit run streamlit_app.py
(Build the index first: ``uv run python scripts/build_index.py``,
 and load the DB: ``uv run python scripts/load_excel_to_db.py``.)
"""

from __future__ import annotations

import logging

import streamlit as st

from sportsee_rag.agent.agent import AgentDeps, Agent, ask_agent, build_agent
from sportsee_rag.config import get_settings
from sportsee_rag.models import AskRequest
from sportsee_rag.observability import setup_observability
from sportsee_rag.retrieval.vector_store import VectorStoreManager
from sportsee_rag.sql.sql_tool import SqlTool

setup_observability()
settings = get_settings()
logger = logging.getLogger(__name__)

st.set_page_config(page_title="NBA Analyst AI", page_icon="🏀")

_TOOL_LABELS = {
    "rag": "🔎 RAG (match commentary)",
    "sql": "🗃️ SQL (NBA stats)",
    "both": "🔎+🗃️ RAG + SQL",
    "none": "⚠️ no tool (model-only answer)",
}

# Clickable examples (short label -> full question), one per routing case
_EXAMPLES = {
    "🔎 What fans criticize the NBA for": (
        "What criticism do fans direct at the NBA regarding how the playoffs are promoted?"
    ),
    "🗃️ Combined points of the top 10 scorers": (
        "What is the total number of points scored by the league's top 10 scorers?"
    ),
    "🔎+🗃️ SGA: fan criticism and points scored": (
        "What do some fans criticize Shai Gilgeous-Alexander for, "
        "and how many points did he score this season?"
    ),
}


@st.cache_resource
def get_manager() -> VectorStoreManager:
    """Load the vector store once per session (heavy: FAISS + chunks).

    Pins the PDF-only index variant ("_pdf"): the app must run the reference
    configuration (enriched_v2 — flattened Excel excluded from text retrieval,
    numbers reachable only through SQL). The full-corpus default stays in
    place for the baseline/enriched evaluation runs.
    """
    settings = get_settings().model_copy(update={"index_variant": "_pdf"})
    return VectorStoreManager(settings=settings)


@st.cache_resource
def get_agent() -> Agent[AgentDeps, str]:
    """Build the routing agent once per session."""
    return build_agent()


@st.cache_resource
def get_sql_tool() -> SqlTool:
    """Open the SQL database handle once per session."""
    return SqlTool()


st.title("🏀 NBA Analyst AI")
st.caption(f"SportSee RAG + SQL assistant | model: {settings.chat_model}")

with st.sidebar:
    k = st.slider(
        "Retrieved chunks (k)", min_value=1, max_value=10, value=settings.search_k
    )

manager = get_manager()
if manager.index is None:
    st.error(
        "PDF-only vector index not found. Build it first:\n\n"
        "`uv run python scripts/build_index.py --pdf-only`"
    )
    st.stop()

if "messages" not in st.session_state:
    st.session_state.messages = [
        {
            "role": "assistant",
            "content": "Hi! Ask me anything about the NBA: match commentary "
            "(fan debates) or player statistics.",
        }
    ]

for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.write(message["content"])

prompt = st.chat_input("Ask your NBA question...")

# Examples are offered only while the chat holds just the welcome message
examples = st.empty()
if not prompt and len(st.session_state.messages) == 1:
    if selected := examples.pills("Example questions", list(_EXAMPLES)):
        prompt = _EXAMPLES[selected]

if prompt:
    examples.empty()  # hide the examples as soon as a question is sent
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.write(prompt)

    with st.chat_message("assistant"):
        try:
            with st.spinner("Routing + retrieval + generation..."):
                answer = ask_agent(
                    AskRequest(question=prompt, k=k),
                    agent=get_agent(),
                    manager=manager,
                    sql_tool=get_sql_tool(),
                )
            st.write(answer.answer)
            st.caption(f"Tool used: {_TOOL_LABELS[answer.used_tool]}")
            if answer.sources:
                with st.expander(f"Sources ({len(answer.sources)})"):
                    for source in dict.fromkeys(answer.sources):  # de-duplicated
                        st.markdown(f"- {source}")
            content = answer.answer
        except Exception:  # noqa: BLE001 - any backend failure: log details, show generic
            # Driver/SDK exceptions can embed the connection string or the
            # failing query — never surface them to the user.
            logger.exception("Agent run failed")
            content = "⚠️ Technical error. Please try again in a moment."
            st.error(content)

    st.session_state.messages.append({"role": "assistant", "content": content})
