"""LLM runtime built on the Claude Agent SDK.

One code path serves both providers because Ollama (>= 0.14) exposes an
Anthropic-compatible /v1/messages endpoint: switching between the local model
and Claude is only a matter of base URL / auth token / model name, injected
into the SDK's subprocess environment. No application code changes.

The SDK is used as a *runtime* here (tools disabled, one turn, our own system
prompt, no filesystem settings). Retrieval, routing and skills are orchestrated
by our code so that grounding does not depend on a small local model's
tool-calling reliability (see docs/architecture.md, "Agent design")."""
import asyncio
import tempfile
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol

import httpx
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    CLIConnectionError,
    CLINotFoundError,
    ProcessError,
    ResultMessage,
    TextBlock,
    query,
)

from app.core.config import Settings, get_settings
from app.core.errors import ProviderMisconfigured, ProviderTimeout, ProviderUnavailable
from app.core.logging import get_logger

log = get_logger("llm")

try:  # StreamEvent carries raw API deltas when include_partial_messages=True
    from claude_agent_sdk import StreamEvent
except ImportError:  # pragma: no cover - older SDKs
    StreamEvent = None  # type: ignore[assignment]


class LLMClient(Protocol):
    provider: str
    model: str

    def stream(self, system: str, prompt: str) -> AsyncIterator[str]: ...


@dataclass
class ProviderStatus:
    name: str
    kind: str  # local | cloud
    model: str
    configured: bool
    available: bool
    detail: str
    is_default: bool = False


def provider_env(provider: str, cfg: Settings) -> tuple[dict[str, str], str]:
    """Return (subprocess env, model name) for a provider."""
    base_env = {
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        "DISABLE_TELEMETRY": "1",
        "DISABLE_AUTOUPDATER": "1",
        "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
    }
    if provider == "ollama":
        model = cfg.ollama_model
        base_env |= {
            "ANTHROPIC_BASE_URL": cfg.ollama_base_url.rstrip("/"),
            "ANTHROPIC_AUTH_TOKEN": "ollama",
            "ANTHROPIC_API_KEY": "",
            # Claude Code issues small background calls with its "haiku" alias; pin every alias to the local model
            "ANTHROPIC_DEFAULT_HAIKU_MODEL": model,
            "ANTHROPIC_DEFAULT_SONNET_MODEL": model,
            "ANTHROPIC_DEFAULT_OPUS_MODEL": model,
            "ANTHROPIC_SMALL_FAST_MODEL": model,
        }
        return base_env, model
    if provider == "anthropic":
        if not cfg.anthropic_api_key:
            raise ProviderMisconfigured(
                "ANTHROPIC_API_KEY is not set. Add it to .env (see .env.example) or switch to the local Ollama provider.",
                details={"provider": "anthropic", "missing": ["ANTHROPIC_API_KEY"]},
            )
        model = cfg.anthropic_model
        base_env |= {
            "ANTHROPIC_API_KEY": cfg.anthropic_api_key,
            "ANTHROPIC_DEFAULT_HAIKU_MODEL": model,
        }
        if cfg.anthropic_base_url:
            base_env["ANTHROPIC_BASE_URL"] = cfg.anthropic_base_url
        return base_env, model
    raise ProviderMisconfigured(f"Unknown provider '{provider}'", details={"provider": provider})


class AgentSDKClient:
    def __init__(self, provider: str, cfg: Settings | None = None):
        self.cfg = cfg or get_settings()
        self.provider = provider
        self.env, self.model = provider_env(provider, self.cfg)

    def _options(self, system: str) -> ClaudeAgentOptions:
        return ClaudeAgentOptions(
            system_prompt=system,
            model=self.model,
            tools=[],  # no built-in tools: the model may only write text
            allowed_tools=[],
            max_turns=1,
            setting_sources=[],  # ignore any CLAUDE.md / user settings on the host
            include_partial_messages=True,
            cwd=tempfile.gettempdir(),
            env=self.env,
        )

    async def _raw_stream(self, system: str, prompt: str) -> AsyncIterator[str]:
        got_delta = False
        final_text = ""
        try:
            async for msg in query(prompt=prompt, options=self._options(system)):
                if StreamEvent is not None and isinstance(msg, StreamEvent):
                    ev = msg.event or {}
                    if ev.get("type") == "content_block_delta":
                        d = ev.get("delta", {})
                        if d.get("type") == "text_delta" and d.get("text"):
                            got_delta = True
                            yield d["text"]
                elif isinstance(msg, AssistantMessage):
                    final_text += "".join(b.text for b in msg.content if isinstance(b, TextBlock))
                elif isinstance(msg, ResultMessage):
                    if msg.is_error:
                        raise ProviderUnavailable(
                            f"{self.provider} returned an error: {(msg.result or 'unknown error')[:300]}",
                            details={"provider": self.provider, "model": self.model},
                        )
        except CLINotFoundError as exc:
            raise ProviderMisconfigured("Claude Agent SDK runtime (claude CLI) not found", details={"error": str(exc)}) from exc
        except (CLIConnectionError, ProcessError) as exc:
            raise ProviderUnavailable(
                f"Could not run the {self.provider} model: {str(exc)[:300]}",
                details={"provider": self.provider, "model": self.model},
            ) from exc
        if not got_delta and final_text:
            yield final_text  # non-streaming backends: emit the finished message once

    async def stream(self, system: str, prompt: str) -> AsyncIterator[str]:
        """Yield text deltas with a total deadline and an idle (no-token) deadline."""
        cfg = self.cfg
        t0 = time.monotonic()
        agen = self._raw_stream(system, prompt)
        it = agen.__aiter__()
        first = True
        try:
            while True:
                remaining = cfg.llm_timeout_s - (time.monotonic() - t0)
                if remaining <= 0:
                    raise TimeoutError("total")
                # first token on a cold local model can be slow (model load): allow the full budget
                idle = remaining if first else min(cfg.llm_idle_timeout_s, remaining)
                try:
                    chunk = await asyncio.wait_for(it.__anext__(), timeout=idle)
                except StopAsyncIteration:
                    return
                first = False
                yield chunk
        except (TimeoutError, asyncio.TimeoutError) as exc:
            log.error("llm_timeout", provider=self.provider, model=self.model, elapsed_s=round(time.monotonic() - t0, 1))
            raise ProviderTimeout(
                f"The {self.provider} model ({self.model}) timed out after {cfg.llm_timeout_s:.0f}s. "
                "Try a smaller model, raise LLM_TIMEOUT_SECONDS, or switch provider.",
                details={"provider": self.provider, "model": self.model},
            ) from exc
        finally:
            await agen.aclose()

    async def complete(self, system: str, prompt: str) -> str:
        return "".join([c async for c in self.stream(system, prompt)])


# ---------------------------------------------------------------------------
# Provider registry: availability probing + fallback
# ---------------------------------------------------------------------------
class ProviderRegistry:
    def __init__(self, cfg: Settings | None = None):
        self.cfg = cfg or get_settings()
        self._cache: dict[str, tuple[float, ProviderStatus]] = {}

    async def _probe_ollama(self) -> ProviderStatus:
        cfg = self.cfg
        base = ProviderStatus("ollama", "local", cfg.ollama_model, True, False, "")
        try:
            async with httpx.AsyncClient(timeout=3.0) as c:
                r = await c.get(f"{cfg.ollama_base_url.rstrip('/')}/api/tags")
                r.raise_for_status()
            names = {m.get("name", "") for m in r.json().get("models", [])}
            want = cfg.ollama_model
            ok = want in names or f"{want}:latest" in names or any(n.split(":")[0] == want for n in names if ":" not in want)
            base.available = ok
            base.detail = "ready" if ok else f"Ollama is running but model '{want}' is not pulled. Run: ollama pull {want}"
        except Exception as exc:  # noqa: BLE001
            base.detail = f"Ollama not reachable at {cfg.ollama_base_url} ({type(exc).__name__}). Start it with: ollama serve"
        return base

    def _probe_anthropic(self) -> ProviderStatus:
        cfg = self.cfg
        ok = bool(cfg.anthropic_api_key)
        return ProviderStatus(
            "anthropic", "cloud", cfg.anthropic_model, ok, ok,
            "ready" if ok else "ANTHROPIC_API_KEY is not set",
        )

    async def status(self, name: str, *, max_age: float = 8.0) -> ProviderStatus:
        now = time.monotonic()
        hit = self._cache.get(name)
        if hit and now - hit[0] < max_age:
            return hit[1]
        st = await self._probe_ollama() if name == "ollama" else self._probe_anthropic()
        st.is_default = name == self.cfg.llm_provider
        self._cache[name] = (now, st)
        return st

    async def all_status(self) -> list[ProviderStatus]:
        return [await self.status("ollama"), await self.status("anthropic")]

    def invalidate(self) -> None:
        self._cache.clear()

    async def resolve(self, requested: str | None) -> tuple[LLMClient, str | None]:
        """Pick the client to use. Returns (client, fallback_note). If the requested provider
        is unavailable and LLM_FALLBACK_PROVIDER is healthy we fall back and say so."""
        name = requested or self.cfg.llm_provider
        st = await self.status(name)
        if st.available:
            return AgentSDKClient(name, self.cfg), None
        fb = self.cfg.llm_fallback_provider
        if fb and fb != name:
            fst = await self.status(fb)
            if fst.available:
                note = f"{name} unavailable ({st.detail}); fell back to {fb}"
                log.warning("provider_fallback", requested=name, fallback=fb, reason=st.detail)
                return AgentSDKClient(fb, self.cfg), note
        cls = ProviderMisconfigured if not st.configured else ProviderUnavailable
        raise cls(st.detail, details={"provider": name, "model": st.model, "fallback": fb})
