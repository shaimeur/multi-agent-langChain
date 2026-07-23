"""Answer node — grounded generation from the pack (cahier §6.6).

Reuses ``rag.answer.ground_answer`` — the same numbered-snippets-in, verified
``[n]``-citations-out contract as the direct ``forge ask`` path — so the graph and
the direct service ground answers identically. The node appends the answer to the
conversation (so multi-turn memory grows) and reports its spend to the budget guard.
"""

from __future__ import annotations

from collections.abc import Callable

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage

from forge.core.agents.base import est_tokens, get_budget, get_pack, latest_user_text
from forge.core.state import ForgeState
from forge.models import ContextPack
from forge.rag.answer import ground_answer


def make_answer_node(*, llm: BaseChatModel, repo_name: str = "the") -> Callable[[ForgeState], dict]:
    def answer_node(state: ForgeState) -> dict:
        question = latest_user_text(state)
        pack = get_pack(state) or ContextPack()
        result = ground_answer(question, pack, llm=llm, repo_name=repo_name)

        spent = est_tokens("".join(c.raw for c in pack.chunks)) if pack.chunks else 0
        budget = get_budget(state).spend(calls=1, tokens=spent)
        return {
            "answer": result,
            "messages": [AIMessage(content=result.answer)],
            "budget": budget,
        }

    return answer_node
