"""Flatten chat-model output content to plain text.

Providers disagree on the shape of ``AIMessage.content``. Gemini 2.5 and most
OpenAI models return a plain ``str``; Gemini 3.x returns a list of typed content
blocks — ``{"type": "text", "text": ...}`` interleaved with reasoning/signature
blocks that carry no answer text. Code that did ``reply.content.strip()`` crashes
on the list form, and a bare ``str(content)`` would splice the block repr —
thought-signatures and all — into the answer. Concatenate the text blocks and
drop the rest.
"""

from __future__ import annotations


def content_to_text(content: object) -> str:
    """The plain-text content of a message, whatever shape the provider returned."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
        return "".join(parts)
    return str(content)
