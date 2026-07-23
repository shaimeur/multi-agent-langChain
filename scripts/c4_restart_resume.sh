#!/usr/bin/env bash
# C4 — short-term memory survives a process restart (cahier §7, acceptance gate C4).
#
# Runs two turns of one session in TWO SEPARATE `forge` processes. The second process
# shares nothing with the first except the SQLite checkpoint at CHECKPOINT_DB — so if
# it can continue the conversation, memory survived the "restart".
#
# The rigorous, network-free proof is `uv run pytest tests/test_graph.py -k restart`;
# this script is the same guarantee, end to end against a live model. It needs a
# configured LLM provider (a .env, or env vars). For the local profile:
#
#   LLM_PROVIDER=ollama OLLAMA_ROUTER_MODEL=mistral:latest \
#     OLLAMA_REASONER_MODEL=mistral:latest scripts/c4_restart_resume.sh
set -euo pipefail
cd "$(dirname "$0")/.."

SESSION="c4-$(date +%s)"
Q1="How is a SQL string split into individual statements?"
Q2="Where is that splitter wired into the parsing pipeline?"

echo "== turn 1 (process A) · session ${SESSION} =="
uv run forge ask --session "${SESSION}" "${Q1}"

echo
echo "== simulating a restart: a brand-new process resumes the same session =="
echo "== turn 2 (process B) · session ${SESSION} =="
uv run forge ask --session "${SESSION}" "${Q2}"

echo
echo "== verifying the checkpoint carried both turns across the restart =="
SESSION="${SESSION}" uv run python - <<'PY'
import asyncio
import os

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from forge.config import get_settings
from forge.core.graph import build_default_nodes, build_graph


async def main():
    settings = get_settings()
    session_id = os.environ["SESSION"]
    async with AsyncSqliteSaver.from_conn_string(str(settings.checkpoint_db)) as cp:
        graph = build_graph(build_default_nodes(settings), checkpointer=cp)
        snapshot = await graph.aget_state({"configurable": {"thread_id": session_id}})
    humans = [m for m in snapshot.values.get("messages", []) if isinstance(m, HumanMessage)]
    print(f"session {session_id}: {len(humans)} user turn(s) persisted in the checkpoint")
    assert len(humans) >= 2, "the restart lost the earlier turn"
    print("C4 PASS — the session survived the restart")


asyncio.run(main())
PY
