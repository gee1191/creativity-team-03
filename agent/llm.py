"""Thin wrapper around the Claude API: one structured (JSON) answer per call."""
from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import TypeVar

import anthropic
from pydantic import BaseModel

MODEL = os.environ.get("CERTIVITY_MODEL", "claude-opus-5-5")
T = TypeVar("T", bound=BaseModel)

_client: anthropic.Anthropic | None = None


KEY_FILE = Path(__file__).resolve().parent.parent / "api_key.txt"


def client() -> anthropic.Anthropic:
    """Uses ANTHROPIC_API_KEY if set, otherwise the key saved in api_key.txt next to app.py."""
    global _client
    if _client is None:
        key = os.environ.get("ANTHROPIC_API_KEY")
        if not key and KEY_FILE.exists():
            key = KEY_FILE.read_text().strip()
        _client = anthropic.Anthropic(api_key=key, max_retries=6) if key else anthropic.Anthropic(max_retries=6)
    return _client


def image_block(path: Path) -> dict:
    media = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}[path.suffix.lower()]
    data = base64.standard_b64encode(path.read_bytes()).decode()
    return {"type": "image", "source": {"type": "base64", "media_type": media, "data": data}}


def ask(system: str, content: list[dict] | str, schema: type[T], *, effort: str = "medium",
        max_tokens: int = 32000, cache: bool = False) -> T:
    """Ask Claude and get back a validated instance of `schema`. cache=True caches a long system prompt that many
    calls share, so repeating it costs a tenth."""
    if isinstance(content, str):
        content = [{"type": "text", "text": content}]
    with client().messages.stream(
        model=MODEL,
        max_tokens=max_tokens,
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}] if cache else system,
        messages=[{"role": "user", "content": content}],
        output_config={"effort": effort},
        output_format=schema,
    ) as stream:
        response = stream.get_final_message()
    if response.stop_reason == "refusal":
        raise RuntimeError("the model declined this request")
    if response.parsed_output is None:
        raise RuntimeError(f"no structured answer (stop_reason={response.stop_reason})")
    return response.parsed_output
