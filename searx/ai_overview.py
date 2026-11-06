# SPDX-License-Identifier: AGPL-3.0-or-later
"""AI overview of search results via Google Gemini, OpenAI, Anthropic Claude or OpenCode (Zen / Go)."""

import json
import time
import uuid
from collections.abc import Iterator

from searx.network import get, stream

PROVIDERS = {
    "google": "gemini-2.5-flash",
    "openai": "gpt-5-mini",
    "claude": "claude-haiku-5-5",
    "opencode": "",
    "opencode_go": "hy3",
}
NATIVE = {
    "google": ("generate-content", "https://generativelanguage.googleapis.com/v1beta"),
    "openai": ("chat-completions", "https://api.openai.com/v1"),
    "claude": ("messages", "https://api.anthropic.com/v1"),
}
OPENCODE = {"opencode": "https://opencode.ai/zen/v1", "opencode_go": "https://opencode.ai/zen/go/v1"}
_routes: dict[str, dict[str, tuple[str, str, str]]] = {}
EFFORT = {
    "chat-completions": lambda e: {"reasoning_effort": e},
    "responses": lambda e: {"reasoning": {"effort": e}},
    "messages": lambda e: {"output_config": {"effort": e}},
    "generate-content": lambda e: {"generationConfig": {"thinkingConfig": {"thinkingLevel": e}}},
}
DELTA = {
    "chat-completions": lambda d: (d.get("choices") or [{}])[0].get("delta", {}).get("content"),
    "messages": lambda d: d["delta"].get("text") if d.get("type") == "content_block_delta" else None,
    "responses": lambda d: d.get("delta") if d.get("type") == "response.output_text.delta" else None,
    "generate-content": lambda d: "".join(
        p.get("text", "") for c in d.get("candidates", [])[:1] for p in c.get("content", {}).get("parts", []) if not p.get("thought")
    ),
}


def build_prompt(query: str, results: list[dict[str, str]]) -> str:
    sources = "\n".join(f"[{i}] {r.get('title', '')} ({r.get('url', '')}): {r.get('content', '')[:300]}" for i, r in enumerate(results[:5], 1))
    return (
        "Write a concise, factual overview (max 120 words) answering the search query using the sources. "
        f"Cite sources as [n]. Plain text only.\n\nQuery: {query}\n\nSources:\n{sources}"
    )


def _lowest(efforts: list[str] | None) -> str:
    return next((e for e in ("none", "minimal", "low") if e in (efforts or [])), "")


def _route(provider: str, model: str) -> tuple[str, str, str]:
    if provider in NATIVE:
        return (*NATIVE[provider], "")
    if model not in _routes.get(provider, {}):
        data = get(OPENCODE[provider] + "/models", timeout=10).json()["data"]
        _routes[provider] = {
            m["id"]: (*next((k, v["base_url"]) for k, v in m["api"].items()), _lowest(m.get("capabilities", {}).get("reasoning", {}).get("effort")))
            for m in data
        }
    if model not in _routes[provider]:
        raise ValueError(f"unknown {provider} model: {model}")
    return _routes[provider][model]


def overview(provider: str, model: str, key: str, query: str, results: list[dict[str, str]]) -> Iterator[str]:
    model = model or PROVIDERS[provider]
    if not (key and model):
        raise ValueError("AI overview needs an API key and model")
    api, base, effort = _route(provider, model)
    prompt = build_prompt(query, results)

    headers = {"content-type": "application/json"}
    if provider.startswith("opencode"):
        headers |= {"authorization": f"Bearer {key}", "x-opencode-session": str(uuid.uuid4()), "user-agent": "searxng-ai-overview/1.0"}

    if api == "generate-content":
        url, body = f"{base}/models/{model}:streamGenerateContent?alt=sse", {"contents": [{"parts": [{"text": prompt}]}]}
        headers["x-goog-api-key"] = key
    elif api == "messages":
        url, body = f"{base}/messages", {"model": model, "max_tokens": 1024, "stream": True, "messages": [{"role": "user", "content": prompt}]}
        headers |= {"x-api-key": key, "anthropic-version": "2023-06-01"}
    elif api == "responses":
        url, body = f"{base}/responses", {"model": model, "input": prompt, "stream": True}
        headers.setdefault("authorization", f"Bearer {key}")
    else:
        url, body = f"{base}/chat/completions", {"model": model, "stream": True, "messages": [{"role": "user", "content": prompt}]}
        headers.setdefault("authorization", f"Bearer {key}")

    if effort:
        body |= EFFORT[api](effort)

    for wait in (1, 3, 0):
        res, chunks = stream("POST", url, content=json.dumps(body), headers=headers, timeout=60)
        if res.status_code < 400:
            break
        error = b"".join(chunks)[:300].decode(errors="replace")
        res.close()
        if res.status_code not in (429, 503, 529) or not wait:
            raise ValueError(f"{provider} HTTP {res.status_code}: {error}")
        time.sleep(wait)

    try:
        buf = b""
        for chunk in chunks:
            *lines, buf = (buf + chunk).split(b"\n")
            for line in lines:
                data = line[5:].strip() if line.startswith(b"data:") else b""
                if data and data != b"[DONE]" and (text := DELTA[api](json.loads(data))):
                    yield text
    finally:
        res.close()
