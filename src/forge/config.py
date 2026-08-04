"""Central settings, loaded once from the environment / .env."""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class CacheMode(StrEnum):
    """How aggressively external calls are allowed to touch the network."""

    REPLAY = "replay"
    """Disk only. A missing fixture raises. This is what the live demo runs."""

    AUTO = "auto"
    """Read through: serve from disk, otherwise call for real and record."""

    REFRESH = "refresh"
    """Always call for real, overwriting whatever was recorded."""


class LLMProvider(StrEnum):
    GEMINI = "gemini"
    GROQ = "groq"
    OLLAMA = "ollama"


class SandboxBackend(StrEnum):
    """Which executor to use. Distinct from ``models.Isolation``, which records
    what actually ran — ``AUTO`` is a preference and can never be an answer."""

    AUTO = "auto"
    """Container when the Docker socket answers, the weaker fallback otherwise."""
    DOCKER = "docker"
    """Require the container. A missing socket is an error, not a silent downgrade."""
    SUBPROCESS = "subprocess"
    """Force the fallback. Development and CI only — see docs/limitations.md §1."""


class LLMRole(StrEnum):
    """Which model tier a call should use — cahier 12.2.

    Splitting these is the main lever on free-tier quota. One end-to-end
    change_request run is ~29 calls / ~300k input tokens, and ~18 of those carry
    a 15-20k-token ContextPack. Keeping routing and guardrail classification off
    the expensive tier is what makes a day of development affordable.
    """

    ROUTER = "router"
    """Supervisor routing, intent + guardrail classification. Frequent, cheap."""

    REASONER = "reasoner"
    """Planner and Reviewer. Strong reasoning, token-fat, called sparingly."""

    CODER = "coder"
    """Editor and test generation. Strong code model."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    cache_mode: CacheMode = CacheMode.AUTO
    fixtures_dir: Path = PROJECT_ROOT / "data" / "fixtures"

    # --- models ---------------------------------------------------------
    llm_provider: LLMProvider = LLMProvider.GEMINI

    google_api_key: str = ""
    # Repointed off the 2.5 tier: as of 2026-07 Google 404s gemini-2.5-flash and
    # -flash-lite for keys created after their cutover ("no longer available to new
    # users"), and 2.0-flash-lite is 429-throttled. The 3.5 tier serves cleanly.
    # Verify in the AI Studio dashboard the week of the demo — this list churns.
    gemini_router_model: str = "gemini-3.5-flash-lite"
    # The reasoner name is NOT free to change: it is part of the fixture cache key,
    # so the recorded completions replay only under the name they were recorded with.
    # Every grounded-answer fixture in data/fixtures/ was recorded against
    # `gemini-flash-latest`, and shipping `gemini-3.5-flash` here made `CACHE_MODE=
    # replay forge ask` raise FixtureMiss on a fresh clone — the offline demo, dead,
    # with the repository looking perfectly clean. `tests/test_llm_cache.py` now
    # asserts the agreement. Keeping the coder on a second id is deliberate: two
    # model ids are two quota pools.
    gemini_reasoner_model: str = "gemini-flash-latest"
    gemini_coder_model: str = "gemini-3.5-flash"

    groq_api_key: str = ""
    groq_router_model: str = "llama-3.3-70b-versatile"
    groq_reasoner_model: str = "llama-3.3-70b-versatile"
    groq_coder_model: str = "llama-3.3-70b-versatile"

    # Offline profile. Scoped to the grounded-Q&A path only — a local 7B does
    # not drive the full repair loop. See docs/descope-v1.md §6.
    ollama_base_url: str = "http://localhost:11434"
    ollama_router_model: str = "qwen2.5-coder:7b"
    ollama_reasoner_model: str = "qwen2.5-coder:7b"
    ollama_coder_model: str = "qwen2.5-coder:7b"

    # --- retrieval ------------------------------------------------------
    # FROZEN on D4's ablation, no longer provisional (docs/evaluation.md D4). With
    # the full pipeline (AST + hybrid + prefer-impl + parent expansion) MiniLM
    # reaches Recall@10 0.905 vs BGE-M3's 0.857 — it WINS the primary metric, so the
    # D3 "MiniLM is too weak" worry was a weak pipeline, not a weak embedder. BGE-M3
    # ranks a little higher (nDCG@10 0.706 vs 0.652) but costs 37x the index time
    # (742 s vs 20 s — breaks the §14/J2 2 s reindex) and 11x the query latency on
    # this CPU. Kept configurable for a GPU box where BGE's nDCG edge is free.
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    # Server mode when set (the compose default), embedded local mode otherwise.
    # The escape hatch matters: embedded Qdrant frees ~300 MB-1 GB of RAM on a
    # machine that only has ~5 GB to give.
    qdrant_url: str = ""
    qdrant_path: Path = PROJECT_ROOT / "data" / "qdrant"
    # Built and measured in the eval harness, shipped off. D4's ablation settled it
    # on numbers, not a priori: on this CPU the cross-encoder takes p95 from 14 ms
    # to 2589 ms AND *lowers* nDCG@10 (0.596 -> 0.547) on code, because ms-marco is
    # a general web-search reranker. See docs/evaluation.md D4 and descope-v1.md §3.
    rerank_enabled: bool = False
    rerank_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    retrieval_top_k: int = 8
    # D4 ablation lever: demote (never drop) test chunks below implementation after
    # fusion. BM25 ranks test_* by query-word overlap above the code they exercise;
    # this took hybrid Recall@10 0.750 -> 0.869 for ~35 ms. Wired into the live
    # grounded-answer path at D5, when the offline forge-ask fixture is re-recorded.
    prefer_implementation: bool = True
    # O7 / limitations.md §8: add the definitions a retrieved chunk *calls*, one hop.
    # Built and measured, shipped off — the same posture as the reranker above and for
    # a blunter reason: it changes the pack for every query, the pack is embedded in
    # the prompt, and the prompt keys the replay fixtures that are the offline demo.
    # Flipping it on is a re-record, so it is a decision with a cost, not a default.
    retrieval_expand_calls: bool = False

    # --- workspace ------------------------------------------------------
    # The repository FORGE operates on. Every write is confined to a git
    # worktree beneath workspace_root — enforced in the policy engine, not here.
    target_repo: Path = PROJECT_ROOT / "data" / "target"
    workspace_root: Path = PROJECT_ROOT / "data" / "workspaces"
    checkpoint_db: Path = PROJECT_ROOT / "data" / "checkpoints.sqlite"
    # D15b — the only directories under which the UI may select a target repository,
    # os.pathsep-separated. Empty means "the parent of target_repo", which keeps the
    # default reach to the one directory the deployment already exposes.
    #
    # This is a security boundary, not a convenience. `target_repo` is the confinement
    # root for the read_file/list_files tools (tools/registry._root_for), so a browser
    # that could set it to an arbitrary path would be choosing what the sandbox may
    # read. §8.3 asserts there is no code path from an untrusted string to a
    # permission; this list is what keeps that true — the browser selects from a
    # server-side enumeration and never supplies a path that is used as given.
    repo_roots: str = ""
    # Where the UI's drop zone writes. Deliberately *inside* the default repo root, so
    # an uploaded document set becomes a selectable repository with no extra config —
    # drop files, pick the folder, rebuild. Uploads are never written anywhere else:
    # the route takes the basename only, so a filename cannot steer the destination.
    upload_dir: Path = PROJECT_ROOT / "data" / "uploads"

    # --- sandbox (cahier 8.3) -------------------------------------------
    # These caps are quoted verbatim on the security slide and asserted in
    # tests/test_config.py, so drift is a test failure rather than a false claim.
    sandbox_image: str = "forge-sandbox:latest"
    sandbox_timeout_s: int = 60
    sandbox_memory_mb: int = 512
    sandbox_cpus: float = 1.0
    sandbox_pids_limit: int = 128
    sandbox_max_output_bytes: int = 64_000
    # AUTO, not DOCKER, so a clean machine without a Docker socket still runs — but
    # the downgrade is never silent: every ExecutionReport carries the Isolation it
    # actually got, and the fallback's gaps are written down in limitations.md §1.
    sandbox_backend: SandboxBackend = SandboxBackend.AUTO

    # --- budget guard (cahier 4, A0) ------------------------------------
    max_iterations_per_step: int = 3
    max_llm_calls_per_run: int = 40
    max_tokens_per_run: int = 400_000
    max_wall_clock_s: int = 900

    @property
    def offline(self) -> bool:
        """True when nothing in this process is permitted to hit the network."""
        return self.cache_mode is CacheMode.REPLAY

    def model_name(self, role: LLMRole) -> str:
        """The concrete model id for a role under the active provider."""
        return getattr(self, f"{self.llm_provider.value}_{role.value}_model")

    def secret_values(self) -> list[str]:
        """Every configured credential, for scrubbing before anything is written.

        Derived from the field names rather than a hand-maintained list, so a
        credential added later cannot silently start leaking into fixtures.
        """
        return [
            value
            for name, value in self
            if name.endswith(("_api_key", "_token", "_secret", "_password"))
            and isinstance(value, str)
            and value
        ]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
