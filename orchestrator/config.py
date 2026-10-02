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
    # Default quiet hours for everyone (HH:MM-HH:MM local, "off" to disable); editable on the dashboard.
    quiet_hours: str = field(default_factory=lambda: _env("QUIET_HOURS", "22:00-07:00"))
    # Pause (s) that counts as "the caller finished"; lower = snappier, too low cuts people off.
    stt_vad_silence_secs: float = field(default_factory=lambda: float(_env("STT_VAD_SILENCE_SECS", "0.5") or 0.5))
    stt_tts_provider: str = field(default_factory=lambda: _env("STT_TTS_PROVIDER", "elevenlabs"))
    # Jetson speech server (speech_server/): e.g. http://100.64.0.6:8770 + its bearer token.
    speech_url: str = field(default_factory=lambda: _env("SPEECH_URL"))
    speech_token: str = field(default_factory=lambda: _env("SPEECH_TOKEN"))

    # --- n8n ---
    n8n_base_url: str = field(default_factory=lambda: _env("N8N_BASE_URL").rstrip("/"))
    n8n_api_key: str = field(default_factory=lambda: _env("N8N_API_KEY"))
    n8n_callback_secret: str = field(default_factory=lambda: _env("N8N_CALLBACK_SECRET"))

    # --- Google Calendar (per-user OAuth) ---
    google_client_id: str = field(default_factory=lambda: _env("GOOGLE_CLIENT_ID"))
    google_client_secret: str = field(default_factory=lambda: _env("GOOGLE_CLIENT_SECRET"))
    # Fernet key that encrypts stored OAuth tokens. Generate:
    # python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    token_encryption_key: str = field(default_factory=lambda: _env("TOKEN_ENCRYPTION_KEY"))
    # Google Search Console ownership proof for the homepage (either or both):
    #  - HTML tag method: the `content` value of <meta name="google-site-verification" ...>
    #  - HTML file method: the file name Google gives you, e.g. google1a2b3c4d5e6f7a8b.html
    google_site_verification: str = field(default_factory=lambda: _env("GOOGLE_SITE_VERIFICATION"))
    google_site_verification_file: str = field(default_factory=lambda: _env("GOOGLE_SITE_VERIFICATION_FILE"))

    # --- Usage/cost estimates (USD). Claude prices are per million tokens. ---
    price_claude_input: float = field(default_factory=lambda: float(_env("PRICE_CLAUDE_INPUT_PER_MTOK", "1.00")))
    price_claude_output: float = field(default_factory=lambda: float(_env("PRICE_CLAUDE_OUTPUT_PER_MTOK", "5.00")))
    price_claude_cache_read: float = field(default_factory=lambda: float(_env("PRICE_CLAUDE_CACHE_READ_PER_MTOK", "0.10")))
    price_claude_cache_write: float = field(default_factory=lambda: float(_env("PRICE_CLAUDE_CACHE_WRITE_PER_MTOK", "1.25")))
    # ElevenLabs bills by plan; set these to your plan's effective rates to get cost estimates.
    price_web_search_per_1k: float = field(default_factory=lambda: float(_env("PRICE_WEB_SEARCH_PER_1K", "10.00")))
    # Homelab operator: key pinned to ~/bin/alfred-ops on each host (mounted read-only into the container)
    ops_key_path: str = field(default_factory=lambda: _env("OPS_KEY_PATH", "/run/secrets/alfred_ops"))
    ops_known_hosts: str = field(default_factory=lambda: _env("OPS_KNOWN_HOSTS", "/run/secrets/ops_known_hosts"))
    voicemail_detection: bool = field(default_factory=lambda: _bool("VOICEMAIL_DETECTION", True))
    # Unknown numbers: Alfred answers and takes a message for the admins (screening.py). Off = busy.
    call_screening: bool = field(default_factory=lambda: _bool("CALL_SCREENING", True))
    # Admins/cofounders can have Alfred call numbers outside the registry for a concrete task
    # (external_calls.py). Only numbers starting with these prefixes.
    external_calls: bool = field(default_factory=lambda: _bool("EXTERNAL_CALLS", True))
    external_call_prefixes: tuple = field(default_factory=lambda: tuple(
        p.strip() for p in _env("EXTERNAL_CALL_PREFIXES", "+41,+423").split(",") if p.strip()))
    web_search_enabled: bool = field(default_factory=lambda: _bool("WEB_SEARCH_ENABLED", True))
    price_tts_per_1k_chars: float = field(default_factory=lambda: float(_env("PRICE_ELEVENLABS_TTS_PER_1K_CHARS", "0")))
    price_stt_per_hour: float = field(default_factory=lambda: float(_env("PRICE_ELEVENLABS_STT_PER_HOUR", "0")))

    # --- Privacy / data ---
    # Call audio is deleted this many days after the call (transcripts stay until deleted). 0 = keep.
    recording_retention_days: int = field(default_factory=lambda: int(_env("RECORDING_RETENTION_DAYS", "90")))

    # --- Dashboard ---
    session_secret: str = field(default_factory=lambda: _env("DASHBOARD_SESSION_SECRET"))
    cookie_secure: bool = field(default_factory=lambda: _bool("DASHBOARD_COOKIE_SECURE", True))
    session_idle_minutes: int = field(default_factory=lambda: int(_env("DASHBOARD_SESSION_IDLE_MINUTES", "60")))
    session_max_hours: int = field(default_factory=lambda: int(_env("DASHBOARD_SESSION_MAX_HOURS", "12")))
    # Roles that must set up two-factor sign-in before they can use the dashboard ("" = nobody).
    mfa_required_roles: frozenset = field(default_factory=lambda: frozenset(
        r.strip() for r in _env("DASHBOARD_2FA_REQUIRED_ROLES", "admin").split(",") if r.strip()))
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
    def speech_enabled(self) -> bool:
        """Some speech provider (Jetson or ElevenLabs) is configured, so calls can work."""
        return self.elevenlabs_enabled or (self.stt_tts_provider == "jetson" and bool(self.speech_url))

    @property
    def google_enabled(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret and self.token_encryption_key
                    and self.public_base_url)

    @property
    def n8n_enabled(self) -> bool:
        return bool(self.n8n_base_url and self.n8n_api_key)


settings = Settings()
