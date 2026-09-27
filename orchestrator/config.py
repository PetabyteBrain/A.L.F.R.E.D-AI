"""Central configuration, read once from the environment (.env via docker compose)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _bool(name: str, default: bool = False) -> bool:
    raw = _env(name)
    if not raw:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    # --- Claude ---
    anthropic_api_key: str = field(default_factory=lambda: _env("ANTHROPIC_API_KEY"))
    claude_model: str = field(default_factory=lambda: _env("CLAUDE_MODEL", "claude-haiku-4-5"))
    claude_max_tokens: int = field(default_factory=lambda: int(_env("CLAUDE_MAX_TOKENS", "2048")))
    history_turns: int = field(default_factory=lambda: int(_env("AGENT_HISTORY_TURNS", "40")))
    assistant_name: str = field(default_factory=lambda: _env("ASSISTANT_NAME", "Alfred"))
    timezone: str = field(default_factory=lambda: _env("TZ", "Europe/Zurich"))

    # --- Telegram ---
    telegram_bot_token: str = field(default_factory=lambda: _env("TELEGRAM_BOT_TOKEN"))
    telegram_session_idle_minutes: int = field(
        default_factory=lambda: int(_env("TELEGRAM_SESSION_IDLE_MINUTES", "30"))
    )

    # --- Twilio ---
    twilio_account_sid: str = field(default_factory=lambda: _env("TWILIO_ACCOUNT_SID"))
    twilio_auth_token: str = field(default_factory=lambda: _env("TWILIO_AUTH_TOKEN"))
    twilio_phone_number: str = field(default_factory=lambda: _env("TWILIO_PHONE_NUMBER"))
    twilio_validate_signature: bool = field(default_factory=lambda: _bool("TWILIO_VALIDATE_SIGNATURE", True))

    # --- ElevenLabs ---
    elevenlabs_api_key: str = field(default_factory=lambda: _env("ELEVENLABS_API_KEY"))
    elevenlabs_voice_id: str = field(default_factory=lambda: _env("ELEVENLABS_VOICE_ID"))
    elevenlabs_tts_model: str = field(default_factory=lambda: _env("ELEVENLABS_TTS_MODEL", "eleven_flash_v2_5"))
    elevenlabs_stt_model: str = field(default_factory=lambda: _env("ELEVENLABS_STT_MODEL", "scribe_v2_realtime"))
    stt_language: str = field(default_factory=lambda: _env("STT_LANGUAGE"))  # empty = auto-detect
    stt_tts_provider: str = field(default_factory=lambda: _env("STT_TTS_PROVIDER", "elevenlabs"))

    # --- n8n ---
    n8n_base_url: str = field(default_factory=lambda: _env("N8N_BASE_URL").rstrip("/"))
    n8n_api_key: str = field(default_factory=lambda: _env("N8N_API_KEY"))
    n8n_callback_secret: str = field(default_factory=lambda: _env("N8N_CALLBACK_SECRET"))

    # --- Dashboard ---
    session_secret: str = field(default_factory=lambda: _env("DASHBOARD_SESSION_SECRET"))
    cookie_secure: bool = field(default_factory=lambda: _bool("DASHBOARD_COOKIE_SECURE", True))
    session_idle_minutes: int = field(default_factory=lambda: int(_env("DASHBOARD_SESSION_IDLE_MINUTES", "60")))
    session_max_hours: int = field(default_factory=lambda: int(_env("DASHBOARD_SESSION_MAX_HOURS", "12")))
    # Only requests arriving *from* these IPs may set the client IP via CF-Connecting-IP /
    # X-Forwarded-For (the reverse proxy over Tailscale). Everyone else is taken at face value.
    trusted_proxy_ips: frozenset = field(default_factory=lambda: frozenset(
        ip.strip() for ip in _env("TRUSTED_PROXY_IPS", "100.64.0.2").split(",") if ip.strip()))

    # --- Admin bootstrap (seeded into the registry on startup) ---
    admin_display_name: str = field(default_factory=lambda: _env("ADMIN_DISPLAY_NAME", "Admin"))
    admin_telegram_id: str = field(default_factory=lambda: _env("ADMIN_TELEGRAM_ID"))
    admin_phone: str = field(default_factory=lambda: _env("ADMIN_PHONE"))
    admin_email: str = field(default_factory=lambda: _env("ADMIN_EMAIL"))
    admin_dashboard_username: str = field(default_factory=lambda: _env("DASHBOARD_ADMIN_USERNAME", "admin"))
    admin_password_hash: str = field(default_factory=lambda: _env("DASHBOARD_ADMIN_PASSWORD_HASH"))

    # --- Paths / networking ---
    public_base_url: str = field(default_factory=lambda: _env("PUBLIC_BASE_URL").rstrip("/"))
    storage_root: Path = field(default_factory=lambda: Path(_env("STORAGE_ROOT", "/data/storage")))
    db_path: Path = field(default_factory=lambda: Path(_env("DB_PATH", "/data/alfred.db")))
    tool_registry_path: Path = field(
        default_factory=lambda: Path(
            _env("TOOL_REGISTRY_PATH", str(Path(__file__).parent / "tools" / "registry.yaml"))
        )
    )
    log_level: str = field(default_factory=lambda: _env("LOG_LEVEL", "INFO"))

    @property
    def public_ws_base(self) -> str:
        return self.public_base_url.replace("https://", "wss://", 1).replace("http://", "ws://", 1)

    # Feature toggles: a channel only starts if its keys are present.
    @property
    def telegram_enabled(self) -> bool:
        return bool(self.telegram_bot_token)

    @property
    def twilio_enabled(self) -> bool:
        return bool(self.twilio_account_sid and self.twilio_auth_token and self.twilio_phone_number)

    @property
    def elevenlabs_enabled(self) -> bool:
        return bool(self.elevenlabs_api_key and self.elevenlabs_voice_id)

    @property
    def n8n_enabled(self) -> bool:
        return bool(self.n8n_base_url and self.n8n_api_key)


settings = Settings()
