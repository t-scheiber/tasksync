"""Core sync engine for TaskSync."""

from datetime import datetime, timezone
from dataclasses import dataclass

from ..clients.notion_client import NotionClient, NotionTask
from ..clients.ticktick_client import TickTickClient, TickTickTask
from ..logger import setup_logger

logger = setup_logger(__name__)


@dataclass
class SyncStats:
    """Statistics from a sync operation."""
    notion_to_sync: dict  # team board -> Sync DB stats
    sync_to_ticktick_created: int
    sync_to_ticktick_updated: int
    ticktick_to_sync_created: int
    ticktick_to_sync_updated: int
    writeback_to_team_board: int
    errors: int


class SyncEngine:
    """Orchestrates sync between Notion and TickTick."""
    
    def __init__(
        self,
        notion_client: NotionClient,
        ticktick_client: TickTickClient | None,
        conflict_strategy: str = "last_write",
    ):
        """Initialize sync engine.
        
        Args:
            notion_client: Configured Notion client.
            ticktick_client: Configured TickTick client (optional).
            conflict_strategy: How to resolve conflicts (last_write, notion_priority, ticktick_priority).
        """
        self.notion = notion_client
        self.ticktick = ticktick_client
        self.conflict_strategy = conflict_strategy
    
    def _now_iso(self) -> str:
        """Get current time in ISO format."""
        return datetime.now(timezone.utc).isoformat()
    
    def sync_notion_only(self) -> dict:
        """Run Notion-only sync (team board <-> Sync DB).
        
        Two-way sync:
        1. Write back status changes from Sync DB -> team board (first, so changes aren't overwritten)
        2. Pull updates from team board -> Sync DB
        3. Link unlinked tasks by title match
        
        Use this when TickTick is not configured.
        
        Returns:
            Sync statistics dictionary.
        """
        logger.info("=" * 50)
        logger.info("Starting Notion-only sync (two-way)")
        logger.info("=" * 50)
        
        # Step 1: Write back changes from Sync DB to team board FIRST
        # (before pulling from team board, so user changes aren't lost)
        logger.info("Step 1: Writing back changes from Sync DB -> team board")
        writeback_count = self._writeback_to_team_board_notion_only()
        
        # Step 2: Pull updates from team board to Sync DB
        logger.info("Step 2: Syncing team board -> Sync DB")
        stats = self.notion.sync_from_team_board_to_sync_db()
        stats["writeback"] = writeback_count
        
        # Step 3: Link unlinked tasks by title match
        logger.info("Step 3: Linking unlinked tasks by title match")
        self._link_unlinked_tasks_notion_only()
        
        logger.info("=" * 50)
        logger.info("Notion sync complete")
        logger.info("=" * 50)
        
        return stats
    
    def _link_unlinked_tasks_notion_only(self) -> None:
        """Link Sync DB tasks to team board tasks by title match (Notion-only mode)."""
        sync_tasks = self.notion.get_tasks_from_sync_db()
        team_tasks = self.notion.get_my_tasks_from_team_board()
        
        # Build lookup by title (lowercase)
        team_by_title = {t.title.strip().lower(): t for t in team_tasks}
        
        for sync_task in sync_tasks:
            if sync_task.kanban_link:
                continue  # Already linked
            
            title_lower = sync_task.title.strip().lower()
            if title_lower in team_by_title:
                team_task = team_by_title[title_lower]
                logger.info(f"Linking to Kanban by title: {sync_task.title}")
                self.notion.update_task_in_sync_db(sync_task.id, {
                    "kanban_link": team_task.id,
                })
    
    def _writeback_to_team_board_notion_only(self) -> int:
        """Write status and content changes from Sync DB back to team board.
        
        Returns:
            Number of tasks updated in team board.
        """
        writeback_count = 0
        
        sync_tasks = self.notion.get_tasks_from_sync_db()
        team_tasks = self.notion.get_my_tasks_from_team_board()
        
        # Create lookup for team board tasks
        team_by_id = {t.id: t for t in team_tasks}
        
        for sync_task in sync_tasks:
            if not sync_task.kanban_link:
                # Task created locally in Sync DB, not from Kanban - skip writeback
                continue
            
            if sync_task.kanban_link not in team_by_id:
                # Kanban task no longer exists or not assigned to user
                continue
            
            team_task = team_by_id[sync_task.kanban_link]
            updated = False
            
            # Check if status changed in Sync DB
            if sync_task.status != team_task.status:
                logger.info(f"Writeback status to team board: {sync_task.title} ({team_task.status} -> {sync_task.status})")
                self.notion.update_task_in_team_board(sync_task.kanban_link, {
                    "status": sync_task.status,
                })
                updated = True
            
            # Check if description (from property or page content) should sync to team board
            # Sync DB has Description property, team board uses page body
            sync_description = sync_task.description  # From Description property
            if not sync_description:
                # Fall back to page content if no property
                sync_description = self.notion.get_page_content(sync_task.id)
            
            team_content = self.notion.get_page_content(sync_task.kanban_link)
            
            if sync_description and sync_description.strip() != team_content.strip():
                logger.info(f"Writeback description to team board: {sync_task.title}")
                self.notion.set_page_content(sync_task.kanban_link, sync_description)
                updated = True
            
            if updated:
                writeback_count += 1
        
        logger.info(f"Writeback complete: {writeback_count} tasks updated in team board")
        
        # Also create tasks in team board that only exist in Sync DB (no Kanban Link)
        created_count = self._create_in_team_board_from_sync_db(sync_tasks)
        
        return writeback_count + created_count
    
    def _create_in_team_board_from_sync_db(self, sync_tasks: list) -> int:
        """Create tasks in team board from Sync DB tasks that have no Kanban Link.
        
        Returns:
            Number of tasks created in team board.
        """
        created_count = 0
        
        for sync_task in sync_tasks:
            if sync_task.kanban_link:
                # Already linked, skip
                continue
            
            # Create in team board
            logger.info(f"Creating in team board from Sync DB: {sync_task.title}")
            kanban_id = self.notion.create_task_in_team_board(sync_task)
            
            if kanban_id:
                # Update Sync DB with the Kanban Link
                self.notion.update_task_in_sync_db(sync_task.id, {
                    "kanban_link": kanban_id,
                })
                created_count += 1
                logger.info(f"Linked: {sync_task.title} -> {kanban_id}")
        
        if created_count:
            logger.info(f"Created {created_count} tasks in team board from Sync DB")
        
        return created_count
    
    def sync_all(self) -> SyncStats:
        """Run full sync between team board, Sync DB, and TickTick.
        
        Sync flow:
        1. team board -> Sync DB (mirror your tasks)
        2. Sync DB -> TickTick (create new tasks in TickTick, sync existing)
        3. TickTick -> Sync DB (sync changes from TickTick, create missing)
        4. Sync DB -> team board (writeback status changes, create missing tasks)
        5. Link unlinked tasks by title match
        
        Returns:
            SyncStats with operation counts.
        """
        if not self.ticktick:
            raise ValueError("TickTick client not configured. Use sync_notion_only() instead.")
        
        logger.info("=" * 50)
        logger.info("Starting full sync")
        logger.info("=" * 50)
        
        stats = SyncStats(
            notion_to_sync={},
            sync_to_ticktick_created=0,
            sync_to_ticktick_updated=0,
            ticktick_to_sync_created=0,
            ticktick_to_sync_updated=0,
            writeback_to_team_board=0,
            errors=0,
        )
        
        try:
            # Step 1: team board -> Sync DB
            logger.info("Step 1: Syncing team board -> Sync DB")
            stats.notion_to_sync = self.notion.sync_from_team_board_to_sync_db()
            
            # Step 2 & 3: Bidirectional sync between Sync DB and TickTick
            logger.info("Step 2: Syncing Sync DB <-> TickTick")
            self._sync_with_ticktick(stats)
            
            # Step 4: Create tasks in team board from Sync DB (for tasks without Kanban Link)
            logger.info("Step 3: Creating missing tasks in team board from Sync DB")
            sync_tasks = self.notion.get_tasks_from_sync_db()
            created_in_team_board = self._create_in_team_board_from_sync_db(sync_tasks)
            
            # Step 5: Writeback status changes to team board
            logger.info("Step 4: Writing back status changes to team board")
            self._writeback_to_team_board(stats)
            
            # Step 6: Link any remaining unlinked tasks by title
            logger.info("Step 5: Linking unlinked tasks by title match")
            self._link_unlinked_tasks()
            
            # Step 7: Assign icons to tasks without one
            logger.info("Step 6: Assigning icons to tasks without icons")
            icons_updated = self.notion.assign_icons_to_team_tasks()
            
        except Exception as e:
            logger.error(f"Sync error: {e}")
            stats.errors += 1
            raise
        
        logger.info("=" * 50)
        logger.info("Full sync complete")
        logger.info(f"Stats: {stats}")
        logger.info("=" * 50)
        
        return stats
    
    def _sync_with_ticktick(self, stats: SyncStats) -> None:
        """Bidirectional sync between Sync DB and TickTick."""
        # Get tasks from both sides
        sync_tasks = self.notion.get_tasks_from_sync_db()
        ticktick_tasks = self.ticktick.get_tasks()
        
        # Create lookups
        sync_by_ticktick_id = {t.ticktick_id: t for t in sync_tasks if t.ticktick_id}
        ticktick_by_id = {t.id: t for t in ticktick_tasks}
        
        # Process Sync DB tasks -> TickTick
        for sync_task in sync_tasks:
            if sync_task.ticktick_id:
                # Task already linked - check for updates
                if sync_task.ticktick_id in ticktick_by_id:
                    tt_task = ticktick_by_id[sync_task.ticktick_id]
                    self._sync_linked_task(sync_task, tt_task, stats)
                else:
                    # Task has TickTick ID but not found in TickTick
                    # This means it was completed in TickTick (completed tasks disappear from API)
                    if sync_task.status != "Done":
                        logger.info(f"Task completed in TickTick (missing from API): {sync_task.title}")
                        self.notion.update_task_in_sync_db(sync_task.id, {
                            "status": "Done",
                            "last_synced": self._now_iso(),
                        })
                        stats.ticktick_to_sync_updated += 1
            else:
                # New task - create in TickTick
                self._create_in_ticktick(sync_task, stats)
        
        # Process TickTick tasks -> Sync DB (tasks created in TickTick)
        for tt_task in ticktick_tasks:
            if tt_task.id not in [t.ticktick_id for t in sync_tasks if t.ticktick_id]:
                # Task exists in TickTick but not linked in Sync DB
                # This means it was created directly in TickTick
                self._create_in_sync_db_from_ticktick(tt_task, stats)
    
    def _sync_linked_task(
        self,
        sync_task: NotionTask,
        tt_task: TickTickTask,
        stats: SyncStats,
    ) -> None:
        """Sync a task that exists in both systems.
        
        Status sync logic:
        - TickTick only has done/not-done, so we preserve Notion's granular statuses
        - If TickTick task is marked done, set Notion to "Done"
        - If Notion task is marked done/cancelled, mark TickTick as complete
        - For non-done tasks, Notion's status is preserved (not overwritten by TickTick)
        """
        # Determine which side has newer changes
        sync_time = sync_task.last_edited
        tt_time = tt_task.modified_time
        
        # Make both timezone-aware for comparison
        if sync_time.tzinfo is None:
            sync_time = sync_time.replace(tzinfo=timezone.utc)
        if tt_time.tzinfo is None:
            tt_time = tt_time.replace(tzinfo=timezone.utc)
        
        # Apply conflict strategy
        if self.conflict_strategy == "notion_priority":
            source = "notion"
        elif self.conflict_strategy == "ticktick_priority":
            source = "ticktick"
        else:  # last_write
            source = "notion" if sync_time > tt_time else "ticktick"
        
        notion_priority = sync_task.priority
        tt_priority_as_notion = TickTickClient.ticktick_priority_to_notion(tt_task.priority)
        
        # Compare priorities by their TickTick equivalent (fire and high both = 5)
        notion_priority_as_tt = TickTickClient.notion_priority_to_ticktick(notion_priority)
        priorities_match = notion_priority_as_tt == tt_task.priority
        
        # Check for differences (excluding status - handled separately)
        has_content_differences = (
            sync_task.title != tt_task.title or
            not priorities_match or
            sync_task.due_date != tt_task.due_date
        )
        
        # Check status differences
        notion_is_done = TickTickClient.is_completed_status(sync_task.status)
        ticktick_is_done = TickTickClient.ticktick_is_done(tt_task)
        status_differs = notion_is_done != ticktick_is_done
        
        if not has_content_differences and not status_differs:
            return
        
        # Priority always syncs from Notion to TickTick (Notion is source of truth)
        # Other fields use the conflict strategy
        if not priorities_match:
            try:
                logger.info(f"Syncing priority to TickTick: {sync_task.title} ({sync_task.priority})")
                self.ticktick.update_task(tt_task.id, {
                    "priority": TickTickClient.notion_priority_to_ticktick(sync_task.priority),
                })
            except Exception as e:
                logger.warning(f"Failed to update TickTick priority (API issue): {e}")
        
        if source == "notion":
            # Update TickTick from Notion (title, description, due date)
            title_or_date_differs = (
                sync_task.title != tt_task.title or
                sync_task.due_date != tt_task.due_date
            )
            if title_or_date_differs:
                logger.info(f"Updating TickTick from Notion: {sync_task.title}")
                updates = {
                    "title": sync_task.title,
                    "content": sync_task.description,
                    "due_date": sync_task.due_date,
                }
                self.ticktick.update_task(tt_task.id, updates)
            
            # Handle completion status: Notion done -> TickTick complete
            if notion_is_done and not ticktick_is_done:
                logger.info(f"Completing in TickTick: {sync_task.title}")
                self.ticktick.complete_task(tt_task.id)
            
            if title_or_date_differs or status_differs or not priorities_match:
                stats.sync_to_ticktick_updated += 1
        else:
            # Update Notion from TickTick (title, description, due date)
            updates = {}
            
            title_or_date_differs = (
                sync_task.title != tt_task.title or
                sync_task.due_date != tt_task.due_date
            )
            if title_or_date_differs:
                logger.info(f"Updating Notion from TickTick: {tt_task.title}")
                updates = {
                    "title": tt_task.title,
                    "due_date": tt_task.due_date,
                    "description": tt_task.content,
                    "last_synced": self._now_iso(),
                }
            
            # Only update status if TickTick task is completed
            # This preserves Notion's granular statuses for non-done tasks
            if ticktick_is_done and not notion_is_done:
                logger.info(f"Marking done in Notion from TickTick: {tt_task.title}")
                updates["status"] = "Done"
            
            if updates:
                self.notion.update_task_in_sync_db(sync_task.id, updates)
                stats.ticktick_to_sync_updated += 1
    
    def _create_in_ticktick(self, sync_task: NotionTask, stats: SyncStats) -> None:
        """Create a new task in TickTick from Sync DB."""
        logger.info(f"Creating in TickTick: {sync_task.title}")
        
        # Get priority (defaults to medium/3 if not set)
        priority = TickTickClient.notion_priority_to_ticktick(sync_task.priority)
        
        tt_task = self.ticktick.create_task(
            title=sync_task.title,
            content=sync_task.description,
            due_date=sync_task.due_date,
            priority=priority,
        )
        
        # Update Sync DB with TickTick ID and ensure priority is set
        updates = {
            "ticktick_id": tt_task.id,
            "last_synced": self._now_iso(),
        }
        # If Notion had no priority, set it to normal
        if not sync_task.priority:
            updates["priority"] = "normal"
        
        self.notion.update_task_in_sync_db(sync_task.id, updates)
        
        # If task is completed in Notion, complete it in TickTick
        if TickTickClient.is_completed_status(sync_task.status):
            self.ticktick.complete_task(tt_task.id)
        
        stats.sync_to_ticktick_created += 1
    
    def _create_in_sync_db_from_ticktick(
        self,
        tt_task: TickTickTask,
        stats: SyncStats,
    ) -> None:
        """Create a task in Sync DB from TickTick (for tasks created in TickTick).
        
        Note: These won't have a Kanban Link since they didn't originate from team board.
        """
        logger.info(f"Creating in Sync DB from TickTick: {tt_task.title}")
        
        # Determine status: Done if TickTick completed, otherwise default to "Working on"
        status = "Done" if TickTickClient.ticktick_is_done(tt_task) else "Working on"
        
        # Get priority (defaults to "normal" if TickTick has 0/none)
        priority = TickTickClient.ticktick_priority_to_notion(tt_task.priority)
        
        # Create a NotionTask to use with the client
        task = NotionTask(
            id="",  # Will be assigned
            title=tt_task.title,
            status=status,
            priority=priority,
            due_date=tt_task.due_date,
            description=tt_task.content,
            kanban_link="",  # Not from Kanban
            ticktick_id=tt_task.id,
            last_edited=tt_task.modified_time,
        )
        
        # Create in Sync DB (no kanban_id since it's from TickTick)
        created = self.notion.create_task_in_sync_db(task, "")
        
        # Update with TickTick ID
        self.notion.update_task_in_sync_db(created.id, {
            "ticktick_id": tt_task.id,
            "last_synced": self._now_iso(),
        })
        
        # If TickTick task had no priority, update it to medium (3)
        if tt_task.priority == 0:
            logger.info(f"Setting TickTick priority to medium: {tt_task.title}")
            self.ticktick.update_task(tt_task.id, {"priority": 3})
        
        stats.ticktick_to_sync_created += 1
    
    def _writeback_to_team_board(self, stats: SyncStats) -> None:
        """Write status changes back to team board Kanban."""
        sync_tasks = self.notion.get_tasks_from_sync_db()
        team_tasks = self.notion.get_my_tasks_from_team_board()
        
        # Create lookup for team board tasks
        team_by_id = {t.id: t for t in team_tasks}
        
        for sync_task in sync_tasks:
            if not sync_task.kanban_link:
                # Not from Kanban, skip
                continue
            
            if sync_task.kanban_link not in team_by_id:
                # Kanban task no longer exists or not assigned to user
                continue
            
            team_task = team_by_id[sync_task.kanban_link]
            
            # Check if status changed
            if sync_task.status != team_task.status:
                logger.info(f"Writeback to team board: {sync_task.title} -> {sync_task.status}")
                self.notion.update_task_in_team_board(sync_task.kanban_link, {
                    "status": sync_task.status,
                })
                stats.writeback_to_team_board += 1
    
    def _link_unlinked_tasks(self) -> None:
        """Link tasks that exist in multiple systems but aren't linked yet.
        
        Matches by title (case-insensitive) and updates links.
        """
        sync_tasks = self.notion.get_tasks_from_sync_db()
        team_tasks = self.notion.get_my_tasks_from_team_board()
        ticktick_tasks = self.ticktick.get_tasks() if self.ticktick else []
        
        # Build lookups by title (lowercase)
        team_by_title = {t.title.strip().lower(): t for t in team_tasks}
        ticktick_by_title = {t.title.strip().lower(): t for t in ticktick_tasks}
        
        for sync_task in sync_tasks:
            title_lower = sync_task.title.strip().lower()
            updates = {}
            
            # Check if missing Kanban Link but title matches team board task
            if not sync_task.kanban_link and title_lower in team_by_title:
                team_task = team_by_title[title_lower]
                updates["kanban_link"] = team_task.id
                logger.info(f"Linking to Kanban by title: {sync_task.title}")
            
            # Check if missing TickTick ID but title matches TickTick task
            if not sync_task.ticktick_id and title_lower in ticktick_by_title:
                tt_task = ticktick_by_title[title_lower]
                updates["ticktick_id"] = tt_task.id
                logger.info(f"Linking to TickTick by title: {sync_task.title}")
            
            # Apply updates if any
            if updates:
                self.notion.update_task_in_sync_db(sync_task.id, updates)
