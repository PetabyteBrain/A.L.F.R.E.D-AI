"""Which Claude API key (billing) and which model Alfred uses, switchable by admins on the Settings tab.

Keys come from .env only (ANTHROPIC_API_KEY = primary, ANTHROPIC_API_KEY_2 = secondary, optional labels
ANTHROPIC_KEY_LABEL / ANTHROPIC_KEY_2_LABEL); the dashboard only picks one and never sees a key. The choice
is stored in app_settings ("ai"): {"key", "model", "effort", "call_model"}.

- `model` answers chats, Telegram and the web helper; `call_model` answers phone calls (latency matters:
  Haiku by default). Small helper jobs (call summaries, briefing text, screening, search queries) stay on
  CLAUDE_MODEL (Haiku): cheap and quick, and they go to the selected key's bill like everything else.
- Haiku 4.5 takes no thinking/effort parameters. Sonnet 5.5 / Opus 5.5 / Fable 5.1 always think
  (adaptive); `effort` (low/medium/high) sets how much, and max_tokens leaves room for it.
- History is rebuilt from stored text each turn and only appended to within a turn, which is what
  their preserved-thinking check expects.
"""
from __future__ import annotations

import anthropic

from . import db
from .config import _env, settings

SETTING = "ai"
HAIKU = "claude-haiku-4-5"

# Prices per million tokens: input, output, cache read, cache write (5-minute TTL = 1.25 x input).
MODELS: dict[str, dict] = {
    "claude-haiku-4-5": {"label": "Claude Haiku 4.5", "tier": "Fastest and cheapest (current default)",
                         "price": (1.00, 5.00, 0.10, 1.25), "thinking": False},
    "claude-sonnet-5-5": {"label": "Claude Sonnet 5.5", "tier": "Balanced: smarter, a bit slower",
                          "price": (2.00, 10.00, 0.20, 2.50), "thinking": True},
    "claude-opus-5-5": {"label": "Claude Opus 5.5", "tier": "Very capable, slower",
                        "price": (4.00, 20.00, 0.20, 5.00), "thinking": True},
    "claude-fable-5-1": {"label": "Claude Fable 5.1", "tier": "Most capable, slowest and most expensive",
                         "price": (10.00, 50.00, 0.25, 12.50), "thinking": True},
}
EFFORTS = ("low", "medium", "high")
DEFAULTS = {"key": "primary", "model": HAIKU, "effort": "medium", "call_model": HAIKU}
_clients: dict[str, anthropic.AsyncAnthropic] = {}


class AiConfigError(ValueError):
    """Message is safe to show."""


def _keys() -> dict[str, dict]:
    return {
        "primary": {"key": settings.anthropic_api_key, "label": _env("ANTHROPIC_KEY_LABEL", "Primary")},
        "secondary": {"key": _env("ANTHROPIC_API_KEY_2"), "label": _env("ANTHROPIC_KEY_2_LABEL", "Secondary")},
    }


def current() -> dict:
    stored = db.get_setting(SETTING, {}) or {}
    out = {**DEFAULTS, **{k: v for k, v in stored.items() if k in DEFAULTS}}
    keys = _keys()
    if not keys.get(out["key"], {}).get("key"):        # chosen key removed from .env: fall back to primary
        out["key"] = "primary"
    for k in ("model", "call_model"):
        if out[k] not in MODELS:
            out[k] = HAIKU
    if out["effort"] not in EFFORTS:
        out["effort"] = DEFAULTS["effort"]
    return out


def key_name() -> str:
    return current()["key"]


def configured() -> bool:
    return bool(_keys()[key_name()]["key"])


def client() -> anthropic.AsyncAnthropic:
    """Async client for the selected key (one cached client per key)."""
    name = key_name()
    if name not in _clients:
        _clients[name] = anthropic.AsyncAnthropic(api_key=_keys()[name]["key"] or None, max_retries=3, timeout=120.0)
    return _clients[name]


def sync_client(timeout: float = 30) -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key=_keys()[key_name()]["key"] or None, max_retries=2, timeout=timeout)


def model_for(channel: str) -> str:
    c = current()
    return c["call_model"] if channel == "call" else c["model"]


def request_params(model: str, channel: str) -> dict:
    """max_tokens and effort for a conversation request on this model."""
    if not MODELS.get(model, {}).get("thinking"):
        return {"max_tokens": 400 if channel == "call" else settings.claude_max_tokens}
    # Thinking models: room for the reasoning plus the reply; calls think briefly.
    effort = "low" if channel == "call" else current()["effort"]
    return {"max_tokens": 4000 if channel == "call" else 16000, "output_config": {"effort": effort}}


def web_search_type(model: str) -> str:
    """Haiku 4.5 takes the basic web search; the newer models the dynamic-filtering variant."""
    return "web_search_20250305" if model == HAIKU else "web_search_20260209"


def price(model: str) -> tuple[float, float, float, float]:
    if model in MODELS:
        return MODELS[model]["price"]
    return (settings.price_claude_input, settings.price_claude_output,
            settings.price_claude_cache_read, settings.price_claude_cache_write)


def describe() -> dict:
    """For the Settings tab: options and the current choice (keys shown by label and last 4 characters only)."""
    keys = _keys()
    return {
        "current": current(),
        "keys": [{"id": k, "label": v["label"], "configured": bool(v["key"]),
                  "hint": f"…{v['key'][-4:]}" if v["key"] else "not set in .env"} for k, v in keys.items()],
        "models": [{"id": m, "label": v["label"], "tier": v["tier"], "price_in": v["price"][0],
                    "price_out": v["price"][1], "thinking": v["thinking"]} for m, v in MODELS.items()],
        "efforts": list(EFFORTS),
    }


def update(changes: dict, *, actor: str) -> dict:
    c = current()
    if "key" in changes:
        if changes["key"] not in _keys():
            raise AiConfigError("Unknown key.")
        if not _keys()[changes["key"]]["key"]:
            raise AiConfigError("That key isn't set in the .env on the server.")
    for k in ("model", "call_model"):
        if k in changes and changes[k] not in MODELS:
            raise AiConfigError("Unknown model.")
    if "effort" in changes and changes["effort"] not in EFFORTS:
        raise AiConfigError("Effort must be low, medium or high.")
    new = {**c, **{k: v for k, v in changes.items() if k in DEFAULTS}}
    db.set_setting(SETTING, new)
    diff = ", ".join(f"{k}: {c[k]} -> {new[k]}" for k in DEFAULTS if c[k] != new[k])
    if diff:
        db.log_activity("system", f"{actor} changed Alfred's AI settings ({diff})")
    return new
