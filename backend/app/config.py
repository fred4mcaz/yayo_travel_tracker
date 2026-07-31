"""Runtime configuration, read from environment / deploy/.env.

Nothing secret has a usable default. If a secret is missing the app should fail
loudly at boot rather than silently run in an insecure state.
"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="YAYO_", env_file=".env", extra="ignore")

    # --- identity -----------------------------------------------------------
    # rp_id is the WebAuthn Relying Party ID. Passkeys are cryptographically
    # bound to it, so changing this invalidates every registered passkey.
    site_origin: str = "http://localhost:8000"
    rp_id: str = "localhost"
    rp_name: str = "Yayo travel"

    # --- storage ------------------------------------------------------------
    # var_dir holds everything mutable: the database, backups, stored email.
    # It is a mounted volume in production and gitignored locally.
    var_dir: Path = Path("var")
    data_dir: Path = Path("data")

    # --- integrations (stage 8; unset until then) ---------------------------
    imap_host: str = "imap.gmail.com"
    imap_port: int = 993
    imap_user: str = ""
    imap_app_password: str = ""
    anthropic_api_key: str = ""
    email_ingest_enabled: bool = False

    # --- behaviour ----------------------------------------------------------
    session_days: int = 90
    backup_keep_days: int = 30
    log_level: str = "INFO"

    @property
    def db_path(self) -> Path:
        return self.var_dir / "travel.db"

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.db_path}"

    @property
    def backup_dir(self) -> Path:
        return self.var_dir / "backups"

    @property
    def is_production(self) -> bool:
        return self.site_origin.startswith("https://")


@lru_cache
def get_settings() -> Settings:
    return Settings()
