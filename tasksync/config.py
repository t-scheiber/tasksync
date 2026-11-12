"""Configuration loader for TaskSync."""

import os
from pathlib import Path
from dataclasses import dataclass
from dotenv import load_dotenv

from .token_store import load_tokens


@dataclass
class NotionConfig:
    """Notion API configuration."""
    token: str
    team_db_id: str
    sync_db_id: str
    user_name: str


@dataclass
class TickTickConfig:
    """TickTick API configuration."""
    client_id: str
    client_secret: str
    access_token: str
    refresh_token: str
    project_id: str


@dataclass
class SyncConfig:
    """Sync behavior configuration."""
    conflict_strategy: str  # last_write, notion_priority, ticktick_priority
    log_level: str
    data_dir: Path


@dataclass
class Config:
    """Main configuration container."""
    notion: NotionConfig
    ticktick: TickTickConfig
    sync: SyncConfig


def load_config(env_path: str | None = None) -> Config:
    """Load configuration from environment variables.
    
    Args:
        env_path: Optional path to .env file. If None, looks in current directory.
        
    Returns:
        Config object with all settings.
        
    Raises:
        ValueError: If required configuration is missing.
    """
    # Load .env file if it exists
    if env_path:
        load_dotenv(env_path)
    else:
        load_dotenv()
    
    # Notion config (required for basic operation)
    notion_token = os.getenv("NOTION_TOKEN", "")
    if not notion_token:
        raise ValueError("NOTION_TOKEN is required")
    
    notion = NotionConfig(
        token=notion_token,
        team_db_id=os.getenv("NOTION_TEAM_DB_ID", ""),
        sync_db_id=os.getenv("NOTION_SYNC_DB_ID", ""),
        user_name=os.getenv("NOTION_USER_NAME", ""),
    )
    
    # TickTick config (optional - can run Notion-only sync)
    ticktick = TickTickConfig(
        client_id=os.getenv("TICKTICK_CLIENT_ID", ""),
        client_secret=os.getenv("TICKTICK_CLIENT_SECRET", ""),
        access_token=os.getenv("TICKTICK_ACCESS_TOKEN", ""),
        refresh_token=os.getenv("TICKTICK_REFRESH_TOKEN", ""),
        project_id=os.getenv("TICKTICK_PROJECT_ID", ""),
    )
    
    # Sync settings
    data_dir = Path(os.getenv("DATA_DIR", "./data"))
    data_dir.mkdir(parents=True, exist_ok=True)
    
    sync = SyncConfig(
        conflict_strategy=os.getenv("CONFLICT_STRATEGY", "last_write"),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        data_dir=data_dir,
    )
    
    # Tokens saved after a refresh take precedence over the environment
    saved = load_tokens(data_dir)
    if saved:
        ticktick.access_token, ticktick.refresh_token = saved
    
    return Config(notion=notion, ticktick=ticktick, sync=sync)


def is_ticktick_configured(config: Config) -> bool:
    """Check if TickTick is fully configured."""
    tt = config.ticktick
    return bool(tt.client_id and tt.access_token and tt.project_id)
