from __future__ import annotations

import sys
from pathlib import Path
from typing import Literal

from pydantic import SecretStr, ValidationError
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

DEFAULT_CONFIG_PATH = Path.home() / ".config" / "notes-pipeline" / "config.toml"


class Config(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    anthropic_api_key: SecretStr
    model: str = "claude-opus-5"
    library_root: Path
    db_path: Path
    transcriber: Literal["whisper", "apple", "remote"] = "whisper"
    stt_bin: Path
    whisper_bin: Path = Path("whisper-cli")
    whisper_model: Path
    whisper_threads: int = 8
    remote_url: str | None = None

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        toml_settings = TomlConfigSettingsSource(settings_cls, toml_file=DEFAULT_CONFIG_PATH)
        # config.toml overrides plain env vars, per PLAN.md; explicit kwargs
        # (used by tests) still win over both.
        return (init_settings, toml_settings, env_settings, file_secret_settings)


class ConfigError(SystemExit):
    """Raised (as a clean SystemExit) when configuration cannot be loaded."""


def load_config(**overrides: object) -> Config:
    """Load Config, turning a missing/invalid value into one clear sentence
    instead of a pydantic traceback."""
    try:
        return Config(**overrides)
    except ValidationError as exc:
        missing = [str(err["loc"][0]) for err in exc.errors() if err["type"] == "missing"]
        if missing:
            fields = ", ".join(missing)
            print(
                f"Missing required configuration: {fields}. "
                f"Set them as environment variables or in {DEFAULT_CONFIG_PATH}.",
                file=sys.stderr,
            )
        else:
            problems = "; ".join(f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in exc.errors())
            print(f"Invalid configuration: {problems}.", file=sys.stderr)
        raise ConfigError(1) from None
