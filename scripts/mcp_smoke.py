#!/usr/bin/env python
"""C6, second half — prove the MCP surface from *outside* the process.

The twin of ``scripts/sse_smoke.sh``. ``tests/test_mcp.py`` connects a client to the
server object in memory, which is the right unit test and is not evidence that
``forge mcp`` is launchable: it never spawns a process, never writes a byte of
JSON-RPC to a pipe, and would keep passing if the CLI command were broken or the
entry point misspelled.

So this one launches the real command as a subprocess, speaks the real protocol down
its stdin, and checks four things a client actually depends on:

1. the server initialises and names itself,
2. it advertises the §9 toolset with usable JSON schemas,
3. a call returns real repository content,
4. a path escape is refused over MCP, exactly as it is over LangChain.

Run it directly — it needs no server and no key:

    uv run python scripts/mcp_smoke.py       # -> "C6/MCP PASS", exit 0
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import mcp

ROOT = Path(__file__).resolve().parents[1]

GREEN, RED, DIM, OFF = "\033[32m", "\033[31m", "\033[2m", "\033[0m"
_failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    mark = f"{GREEN}ok{OFF}" if ok else f"{RED}FAIL{OFF}"
    print(f"  [{mark}] {label}" + (f" {DIM}{detail}{OFF}" if detail else ""))
    if not ok:
        _failures.append(label)


async def main() -> int:
    # Replay + a hashing embedder: this proves the transport and the toolset, and
    # must not depend on a key, a network or 90 MB of downloaded weights.
    env = {
        **os.environ,
        "CACHE_MODE": "replay",
        "EMBEDDING_MODEL": "hashing",
        "QDRANT_URL": "",
    }
    # The installed console script, not ``python -m``: it is the command an MCP
    # client's config file names, so it is the one worth proving launchable.
    forge = Path(sys.executable).parent / "forge"
    if not forge.exists():
        print(f"{RED}`forge` is not on the venv path — run `uv sync` first{OFF}")
        return 1
    params = mcp.StdioServerParameters(
        command=str(forge), args=["mcp", "--no-sandbox"], env=env, cwd=str(ROOT)
    )

    print(f"\n{DIM}spawning `forge mcp` and speaking MCP down its stdin{OFF}\n")
    async with mcp.Client(mcp.stdio_client(params)) as client:
        check(
            "the server initialises over stdio",
            client.server_info.name == "forge",
            f"name={client.server_info.name} version={client.server_info.version}",
        )

        listed = await client.list_tools()
        names = {t.name for t in listed.tools}
        check("it advertises the §9 knowledge tools", len(names) == 7, f"{len(names)} tools")
        check(
            "every tool carries a description and an object schema",
            all(t.description and t.input_schema.get("type") == "object" for t in listed.tools),
        )
        read_file = next((t for t in listed.tools if t.name == "read_file"), None)
        check(
            "a client can validate arguments before sending them",
            read_file is not None and read_file.input_schema.get("required") == ["path"],
        )

        found = await client.call_tool("find_definitions", {"symbol": "remove_quotes"})
        text = found.content[0].text if found.content else ""
        check(
            "a tool call returns real repository content",
            found.is_error is not True and "remove_quotes" in text,
            text.splitlines()[0] if text else "(empty)",
        )

        escaped = await client.call_tool("read_file", {"path": "../../../../etc/passwd"})
        refused = escaped.content[0].text if escaped.content else ""
        check(
            "a path escape is refused over MCP too",
            refused.startswith("refused:") and "root:x:" not in refused,
            refused.splitlines()[0] if refused else "(empty)",
        )

        unknown = await client.call_tool("exfiltrate_env", {})
        still_up = await client.list_tools()
        check(
            "an unknown tool is an error frame, not a dropped connection",
            unknown.is_error is True and bool(still_up.tools),
        )

    if _failures:
        print(f"\n{RED}C6/MCP FAIL{OFF} — {len(_failures)}: {', '.join(_failures)}\n")
        return 1
    print(f"\n{GREEN}C6/MCP PASS{OFF} — the toolset is reachable over MCP from another process\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
