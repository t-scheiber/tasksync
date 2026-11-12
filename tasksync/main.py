"""Main entry point for TaskSync.

Usage:
    python -m tasksync.main              # Run full sync
    python -m tasksync.main --notion     # Notion-only sync
    python -m tasksync.main --test       # Test connections only
"""

import argparse
import sys
from pathlib import Path

from .config import load_config, is_ticktick_configured
from .clients.notion_client import NotionClient
from .clients.ticktick_client import TickTickClient
from .sync.engine import SyncEngine
from .logger import setup_logger
from .token_store import save_tokens


def save_if_refreshed(ticktick, config, logger) -> None:
    """Save TickTick tokens to the data directory if a refresh changed them."""
    new_access, new_refresh = ticktick.get_tokens()
    if (new_access, new_refresh) == (config.ticktick.access_token, config.ticktick.refresh_token):
        return
    try:
        path = save_tokens(config.sync.data_dir, new_access, new_refresh)
    except OSError as e:
        logger.error(f"Could not save refreshed TickTick tokens ({type(e).__name__})")
        return
    config.ticktick.access_token, config.ticktick.refresh_token = new_access, new_refresh
    logger.info(f"TickTick tokens were refreshed and saved to {path}")


def test_connections(config) -> bool:
    """Test API connections without syncing."""
    logger = setup_logger("test", config.sync.log_level)
    success = True
    
    # Test Notion
    logger.info("Testing Notion connection...")
    try:
        notion = NotionClient(
            token=config.notion.token,
            team_db_id=config.notion.team_db_id,
            sync_db_id=config.notion.sync_db_id,
            user_name=config.notion.user_name,
        )
        
        # Test team board access
        tasks = notion.get_my_tasks_from_team_board()
        logger.info(f"✓ Team board: Found {len(tasks)} tasks assigned to {config.notion.user_name}")
        
        # Test Sync DB access
        sync_tasks = notion.get_tasks_from_sync_db()
        logger.info(f"✓ Sync DB: Found {len(sync_tasks)} tasks")
        
    except Exception as e:
        logger.error(f"✗ Notion connection failed: {e}")
        success = False
    
    # Test TickTick if configured
    if is_ticktick_configured(config):
        logger.info("Testing TickTick connection...")
        try:
            ticktick = TickTickClient(
                client_id=config.ticktick.client_id,
                client_secret=config.ticktick.client_secret,
                access_token=config.ticktick.access_token,
                refresh_token=config.ticktick.refresh_token,
                project_id=config.ticktick.project_id,
            )
            
            tasks = ticktick.get_tasks()
            logger.info(f"✓ TickTick: Found {len(tasks)} tasks in project")
            save_if_refreshed(ticktick, config, logger)
            
        except Exception as e:
            logger.error(f"✗ TickTick connection failed: {e}")
            success = False
    else:
        logger.info("TickTick not configured, skipping test")
    
    return success


def run_sync(config, notion_only: bool = False) -> int:
    """Run the sync operation.
    
    Args:
        config: Loaded configuration.
        notion_only: If True, only sync within Notion.
        
    Returns:
        Exit code (0 = success, 1 = error).
    """
    logger = setup_logger("tasksync", config.sync.log_level)
    ticktick = None
    
    try:
        # Initialize Notion client
        notion = NotionClient(
            token=config.notion.token,
            team_db_id=config.notion.team_db_id,
            sync_db_id=config.notion.sync_db_id,
            user_name=config.notion.user_name,
        )
        
        # Initialize TickTick client if configured and not notion_only
        if not notion_only and is_ticktick_configured(config):
            ticktick = TickTickClient(
                client_id=config.ticktick.client_id,
                client_secret=config.ticktick.client_secret,
                access_token=config.ticktick.access_token,
                refresh_token=config.ticktick.refresh_token,
                project_id=config.ticktick.project_id,
            )
        
        # Create sync engine
        engine = SyncEngine(
            notion_client=notion,
            ticktick_client=ticktick,
            conflict_strategy=config.sync.conflict_strategy,
        )
        
        # Run sync
        if ticktick and not notion_only:
            logger.info("Running full sync (Notion + TickTick)")
            stats = engine.sync_all()
            logger.info(f"Sync complete: {stats}")
        else:
            if not notion_only:
                logger.info("TickTick not configured, running Notion-only sync")
            else:
                logger.info("Running Notion-only sync")
            stats = engine.sync_notion_only()
            logger.info(f"Notion sync complete: {stats}")
        
        return 0
        
    except Exception as e:
        logger.error(f"Sync failed: {e}")
        return 1
    finally:
        # Save refreshed tokens even if the sync failed after the refresh
        if ticktick:
            save_if_refreshed(ticktick, config, logger)


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="TaskSync - Sync tasks between Notion and TickTick"
    )
    parser.add_argument(
        "--notion",
        action="store_true",
        help="Run Notion-only sync (team board -> Sync DB)",
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help="Test API connections without syncing",
    )
    parser.add_argument(
        "--env",
        type=str,
        help="Path to .env file",
    )
    
    args = parser.parse_args()
    
    # Load configuration
    try:
        config = load_config(args.env)
    except ValueError as e:
        print(f"Configuration error: {e}")
        print("Make sure you have a .env file with required settings.")
        print("See .env.example for template.")
        sys.exit(1)
    
    # Set up logging
    logger = setup_logger("tasksync", config.sync.log_level)
    
    if args.test:
        success = test_connections(config)
        sys.exit(0 if success else 1)
    
    # Run sync
    exit_code = run_sync(config, notion_only=args.notion)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
