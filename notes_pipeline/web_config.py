from __future__ import annotations

import sys
from pathlib import Path

from pydantic import SecretStr, ValidationError, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# M13 (§14) — settings specific to the hosted multi-tenant service. Kept
# separate from `config.Config` (the personal, single-user CLI/MCP config
# from M0) rather than added to it: `Config.anthropic_api_key` etc. are
# required fields with no defaults, and the personal CLI's own acceptance
# criterion (M0: "notes --help works... a missing API key produces one
# clear sentence") must keep working with none of these hosted-only values
# set. `deploy/docker-compose.yml`'s `api` service still loads a personal
# `Config` too (`pipeline.run_build` takes one) — it just gets a placeholder
# `ANTHROPIC_API_KEY` there, since every hosted build overrides it per-call
# with the uploading user's own key (see `pipeline.run_build`'s `api_key`).

# The one identity every request runs as when AUTH_ENABLED=false. Shared
# (by convention, not by import — the TS side has its own copy in
# web/src/lib/authz.ts) so a locally-run `web` and `api` agree on who "the
# logged-in user" is without either service needing real Google credentials.
DEV_BYPASS_EMAIL = "dev-local@notes-pipeline.local"


class WebConfig(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    # Local-testing escape hatch: AUTH_ENABLED=false skips Google OAuth
    # (`/mcp`) and the internal_auth signature check (`/api/*`) entirely,
    # treating every request as DEV_BYPASS_EMAIL. Defaults to True — a
    # forgotten env var must fail closed, not open. Never set this false
    # anywhere but a local dev machine; nothing in this service enforces
    # that for you.
    auth_enabled: bool = True

    google_client_id: str | None = None
    google_client_secret: SecretStr | None = None
    # Public URL friends' MCP clients see, e.g. "https://notes.example.com/mcp".
    # Also the value GoogleProvider registers as the OAuth redirect base.
    mcp_base_url: str | None = None
    # Locks FastMCP's DNS-rebinding guard (`http_app(allowed_hosts=...)`) to
    # the real domain, per M13's acceptance criteria — never "*".
    allowed_host: str | None = None

    # AES-256-GCM master key for `crypto.encrypt_key`/`decrypt_key`,
    # base64-encoded 32 bytes. Generate with `crypto.generate_master_key()`.
    # Required even with auth disabled — locally-stored Anthropic keys still
    # get encrypted at rest; that's not an auth concern.
    key_master_key: str

    # Shared secret for `internal_auth` tokens between `web` and `api`.
    # Unused when auth_enabled=False (require_internal_user short-circuits
    # before ever checking a token), but still required: harmless to set,
    # and keeps the required-field set stable across the auth_enabled flip.
    internal_api_secret: str

    admin_email: str

    uploads_root: Path = Path("/data/uploads")
    db_path: Path = Path("/data/notes-db/notes.db")

    # §14.1: "a small per-user pending-job cap so one person can't
    # monopolize the only GPU."
    max_pending_jobs_per_user: int = 3

    @model_validator(mode="after")
    def _require_google_creds_when_auth_enabled(self) -> "WebConfig":
        if not self.auth_enabled:
            return self
        missing = [
            name
            for name, value in (
                ("google_client_id", self.google_client_id),
                ("google_client_secret", self.google_client_secret),
                ("mcp_base_url", self.mcp_base_url),
                ("allowed_host", self.allowed_host),
            )
            if not value
        ]
        if missing:
            raise ValueError(
                f"Required when AUTH_ENABLED is true (the default): {', '.join(missing)}. "
                "Set AUTH_ENABLED=false instead if this is local testing."
            )
        return self


class WebConfigError(SystemExit):
    """Raised (as a clean SystemExit) when hosted-service config can't be loaded."""


def load_web_config(**overrides: object) -> WebConfig:
    try:
        return WebConfig(**overrides)
    except ValidationError as exc:
        for err in exc.errors():
            print(f"Invalid hosted-service configuration: {err['msg']}", file=sys.stderr)
        raise WebConfigError(1) from None
