"""The checkpoint serializer, locked to FORGE's own payload types.

LangGraph's default msgpack serializer will reconstruct *any* type it finds named in
a checkpoint. Its own docstring is blunt about what that means: "if an attacker can
write directly to your checkpoint database, they may be able to trigger code
execution when data is deserialized." FORGE's checkpoint database is a file on disk
(``data/checkpoints.sqlite``) holding whole graph states across restarts, so that is
not a hypothetical for a project whose thesis is that untrusted code gets contained.

This module runs the serializer in **strict** mode — the built-in safe types only —
and adds back exactly the payloads FORGE puts into state: everything in
``forge.models`` plus ``Budget``. A checkpoint naming any other type deserialises to
plain data instead of being instantiated.

Two things fall out of it beyond the security posture:

* the "Deserializing unregistered type ... will be blocked in a future version"
  warnings go away, because nothing is unregistered any more;
* D5's restart proof (C4) and D9's ``interrupt()``/``Command(resume=...)`` keep
  working when that future version lands, rather than breaking on upgrade.

Scoping the allowlist by *module* rather than by a hand-listed class is deliberate:
a model added to ``forge.models`` later is covered automatically, so nobody has to
remember to come back here — while a type from anywhere else still is not.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from enum import StrEnum
from pathlib import Path

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from pydantic import BaseModel

import forge.models as models
from forge.core.state import Budget


def _forge_payload_types() -> list[type]:
    """Every typed value FORGE puts into graph state."""
    types = [
        value
        for value in vars(models).values()
        if isinstance(value, type)
        and issubclass(value, BaseModel | StrEnum)
        and value.__module__ == models.__name__
    ]
    return [*types, Budget]


def forge_serde() -> JsonPlusSerializer:
    """A strict serializer that knows FORGE's payloads and nothing else's."""
    strict = JsonPlusSerializer(allowed_msgpack_modules=None)
    return strict.with_msgpack_allowlist(_forge_payload_types())


@asynccontextmanager
async def sqlite_checkpointer(path: str | Path):
    """The SQLite checkpointer FORGE ships (descope §1), on the strict serializer.

    ``from_conn_string`` takes no ``serde``, so it is set on the instance — the
    saver reads ``self.serde`` per call, so assigning it before first use is enough.
    """
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    async with AsyncSqliteSaver.from_conn_string(str(path)) as saver:
        saver.serde = forge_serde()
        yield saver
