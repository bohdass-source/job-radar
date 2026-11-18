"""Settings from the environment (and an optional .env file) plus YAML config files."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

ROOT = Path(os.environ.get("JOB_RADAR_ROOT", Path(__file__).resolve().parents[2]))
CONFIG_DIR = ROOT / "config"
DOCS_SOURCES_DIR = ROOT / "docs" / "sources"
DATA_DIR = ROOT / "data"
PRIVATE_DIR = DATA_DIR / "private"
REPORTS_DIR = ROOT / "reports"


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader: KEY=VALUE lines, existing env vars win."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


class Settings(BaseModel):
    mongo_uri: str = "mongodb://localhost:27017"
    mongo_db: str = "job_radar"
    llm_url: str = ""
    llm_api_key: str = ""
    llm_model: str = "default"
    adzuna_app_id: str = ""
    adzuna_app_key: str = ""
    jooble_api_key: str = ""
    imap_host: str = ""
    imap_user: str = ""
    imap_password: str = ""
    imap_folder: str = "INBOX"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    digest_to: str = ""
    inbox_token: str = ""
    inbox_port: int = 8765
    inbox_bind: str = "127.0.0.1"


@lru_cache
def settings() -> Settings:
    _load_dotenv(ROOT / ".env")
    env = os.environ
    return Settings(
        mongo_uri=env.get("MONGO_URI", "mongodb://localhost:27017"),
        mongo_db=env.get("MONGO_DB", "job_radar"),
        llm_url=env.get("LLM_GATEWAY_URL", ""),
        llm_api_key=env.get("LLM_GATEWAY_API_KEY", ""),
        llm_model=env.get("LLM_GATEWAY_MODEL", "default"),
        adzuna_app_id=env.get("ADZUNA_APP_ID", ""),
        adzuna_app_key=env.get("ADZUNA_APP_KEY", ""),
        jooble_api_key=env.get("JOOBLE_API_KEY", ""),
        imap_host=env.get("IMAP_HOST", ""),
        imap_user=env.get("IMAP_USER", ""),
        imap_password=env.get("IMAP_PASSWORD", ""),
        imap_folder=env.get("IMAP_FOLDER", "INBOX"),
        smtp_host=env.get("SMTP_HOST", ""),
        smtp_port=int(env.get("SMTP_PORT", "587") or 587),
        smtp_user=env.get("SMTP_USER", ""),
        smtp_password=env.get("SMTP_PASSWORD", ""),
        digest_to=env.get("DIGEST_TO", ""),
        inbox_token=env.get("INBOX_TOKEN", ""),
        inbox_port=int(env.get("INBOX_PORT", "8765") or 8765),
        inbox_bind=env.get("INBOX_BIND", "127.0.0.1"),
    )


def _deep_merge(base: Any, override: Any) -> Any:
    if isinstance(base, dict) and isinstance(override, dict):
        out = dict(base)
        for key, value in override.items():
            out[key] = _deep_merge(base.get(key), value)
        return out
    return override


def load_yaml(name: str) -> Any:
    """Load config/<name>; if config/<stem>.local.yaml exists (git-ignored), deep-merge it on top.
    The local file holds machine-specific decisions such as enabling a source."""
    path = CONFIG_DIR / name
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    local = path.with_name(f"{path.stem}.local{path.suffix}")
    if local.exists():
        with local.open(encoding="utf-8") as fh:
            data = _deep_merge(data, yaml.safe_load(fh) or {})
    return data


def private_path(*parts: str) -> Path:
    path = PRIVATE_DIR.joinpath(*parts)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path
