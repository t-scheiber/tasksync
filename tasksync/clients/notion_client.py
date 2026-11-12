"""Notion API client for TaskSync."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any
import httpx
from notion_client import Client

from ..logger import setup_logger

logger = setup_logger(__name__)

# Notion API version
NOTION_VERSION = "2022-06-28"


@dataclass
class NotionTask:
    """Represents a task from Notion."""
    id: str
    title: str
    status: str
    priority: str
    due_date: str | None
    description: str
    kanban_link: str  # ID of the original team board task (if synced)
    ticktick_id: str  # ID of the linked TickTick task
    last_edited: datetime
    
    def to_dict(self) -> dict:
        """Convert to dictionary for comparison."""
        return {
            "title": self.title,
            "status": self.status,
            "priority": self.priority,
            "due_date": self.due_date,
            "description": self.description,
        }


class NotionClient:
    """Client for interacting with Notion API."""
    
    # Status names used on the team board and in the sync database
    VALID_STATUSES = [
        "Not started",
        "Blocker",
        "Investigation",
        "Working on",
        "Finishing/testing",
        "Done",
        "Cancelled/No need",
        "In progress",
    ]
    
    VALID_PRIORITIES = ["fire", "high", "normal", "low"]
    
    def __init__(self, token: str, team_db_id: str, sync_db_id: str, user_name: str):
        """Initialize Notion client.
        
        Args:
            token: Notion integration token.
            team_db_id: Team board database ID.
            sync_db_id: Tasks Sync DB database ID.
            user_name: Name to filter tasks by in team board.
        """
        self.token = token
        self.client = Client(auth=token)
        self.team_db_id = team_db_id
        self.sync_db_id = sync_db_id
        self.user_name = user_name
        self._user_id: str | None = None  # Cached user ID for assignment
        self._http_client = httpx.Client(
            headers={
                "Authorization": f"Bearer {token}",
                "Notion-Version": NOTION_VERSION,
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )
    
    def _get_user_id(self) -> str | None:
        """Get the Notion user ID for the configured user_name.
        
        Caches the result for subsequent calls.
        
        Returns:
            Notion user ID or None if not found.
        """
        if self._user_id:
            return self._user_id
        
        # Search for user ID in existing team board tasks
        # (we can't list all workspace users without being a workspace integration)
        try:
            response = self._query_database(
                database_id=self.team_db_id,
                page_size=100,
            )
            
            for page in response.get("results", []):
                props = page.get("properties", {})
                assigned_prop = props.get("Assigned To", {})
                people = assigned_prop.get("people", [])
                
                for person in people:
                    if person.get("name") == self.user_name:
                        self._user_id = person.get("id")
                        logger.info(f"Found user ID for {self.user_name}: {self._user_id}")
                        return self._user_id
            
            logger.warning(f"Could not find user ID for {self.user_name}")
            return None
            
        except Exception as e:
            logger.error(f"Error looking up user ID: {e}")
            return None
    
    def get_page_content(self, page_id: str) -> str:
        """Get the text content from a page's body (blocks).
        
        Args:
            page_id: Notion page ID.
            
        Returns:
            Plain text content from the page body.
        """
        url = f"https://api.notion.com/v1/blocks/{page_id}/children"
        response = self._http_client.get(url)
        response.raise_for_status()
        data = response.json()
        
        # Extract text from blocks
        content_parts = []
        for block in data.get("results", []):
            block_type = block.get("type", "")
            block_data = block.get(block_type, {})
            
            # Handle different block types that contain text
            rich_text = block_data.get("rich_text", [])
            for text_obj in rich_text:
                plain_text = text_obj.get("plain_text", "")
                if plain_text:
                    content_parts.append(plain_text)
            
            # Add newline between blocks
            if rich_text:
                content_parts.append("\n")
        
        return "".join(content_parts).strip()
    
    def set_page_content(self, page_id: str, content: str) -> None:
        """Set the content of a page's body (replaces existing content).
        
        Args:
            page_id: Notion page ID.
            content: Plain text content to set.
        """
        if not content:
            return
        
        # First, get existing blocks to delete them
        url = f"https://api.notion.com/v1/blocks/{page_id}/children"
        response = self._http_client.get(url)
        response.raise_for_status()
        existing_blocks = response.json().get("results", [])
        
        # Delete existing blocks
        for block in existing_blocks:
            block_id = block.get("id")
            if block_id:
                delete_url = f"https://api.notion.com/v1/blocks/{block_id}"
                self._http_client.delete(delete_url)
        
        # Add new content as paragraph blocks
        # Split content into paragraphs
        paragraphs = content.split("\n")
        children = []
        for para in paragraphs:
            if para.strip():  # Skip empty lines
                children.append({
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [{"type": "text", "text": {"content": para}}]
                    }
                })
        
        if children:
            self._http_client.patch(url, json={"children": children})
    
    def _query_database(self, database_id: str, start_cursor: str | None = None, page_size: int = 100) -> dict:
        """Query a database using direct HTTP request.
        
        The notion-client SDK v2.7 doesn't have databases.query, so we use httpx directly.
        """
        url = f"https://api.notion.com/v1/databases/{database_id}/query"
        body: dict[str, Any] = {"page_size": page_size}
        if start_cursor:
            body["start_cursor"] = start_cursor
        
        response = self._http_client.post(url, json=body)
        response.raise_for_status()
        return response.json()
        
    def _extract_title(self, properties: dict) -> str:
        """Extract title from Notion properties."""
        title_prop = properties.get("Name", {})
        title_arr = title_prop.get("title", [])
        if title_arr:
            return title_arr[0].get("plain_text", "")
        return ""
    
    def _extract_select(self, properties: dict, prop_name: str) -> str:
        """Extract select or status value from Notion properties."""
        prop = properties.get(prop_name, {})
        # Handle both 'select' and 'status' property types
        select = prop.get("select") or prop.get("status")
        if select:
            return select.get("name", "")
        return ""
    
    def _extract_date(self, properties: dict, prop_name: str) -> str | None:
        """Extract date from Notion properties."""
        prop = properties.get(prop_name, {})
        date = prop.get("date")
        if date:
            return date.get("start")
        return None
    
    def _extract_text(self, properties: dict, prop_name: str) -> str:
        """Extract rich text from Notion properties."""
        prop = properties.get(prop_name, {})
        rich_text = prop.get("rich_text", [])
        if rich_text:
            return rich_text[0].get("plain_text", "")
        return ""
    
    def _extract_person_names(self, properties: dict, prop_name: str = "Assigned To") -> list[str]:
        """Extract person names from Notion properties."""
        prop = properties.get(prop_name, {})
        people = prop.get("people", [])
        names = []
        for person in people:
            name = person.get("name", "")
            if name:
                names.append(name)
        return names
    
    def _parse_task(self, page: dict, is_sync_db: bool = False) -> NotionTask:
        """Parse a Notion page into a NotionTask.
        
        Args:
            page: Notion page object.
            is_sync_db: Whether this is from the sync DB (has Kanban Link, TickTick ID).
        """
        props = page.get("properties", {})
        
        # Handle different property names between databases
        kanban_link = ""
        ticktick_id = ""
        
        if is_sync_db:
            kanban_link = self._extract_text(props, "Kanban Link")
            ticktick_id = self._extract_text(props, "TickTick ID")
        
        last_edited_str = page.get("last_edited_time", "")
        last_edited = datetime.fromisoformat(last_edited_str.replace("Z", "+00:00")) if last_edited_str else datetime.now()
        
        return NotionTask(
            id=page["id"],
            title=self._extract_title(props),
            status=self._extract_select(props, "Status"),
            priority=self._extract_select(props, "Priority"),
            due_date=self._extract_date(props, "Due Date"),
            description=self._extract_text(props, "Description"),
            kanban_link=kanban_link,
            ticktick_id=ticktick_id,
            last_edited=last_edited,
        )
    
    def get_my_tasks_from_team_board(self) -> list[NotionTask]:
        """Get all tasks assigned to user from team board database.
        
        Returns:
            List of NotionTask objects for tasks assigned to the configured user.
        """
        logger.info(f"Fetching tasks from team board for user: {self.user_name}")
        
        tasks = []
        has_more = True
        start_cursor = None
        
        while has_more:
            # Query with filter for assignee
            # Note: Notion API doesn't support direct person name filter,
            # so we fetch all and filter client-side
            response = self._query_database(
                database_id=self.team_db_id,
                start_cursor=start_cursor,
                page_size=100,
            )
            
            for page in response.get("results", []):
                props = page.get("properties", {})
                assignees = self._extract_person_names(props, "Assigned To")
                
                # Check if our user is assigned
                if self.user_name in assignees:
                    task = self._parse_task(page, is_sync_db=False)
                    tasks.append(task)
            
            has_more = response.get("has_more", False)
            start_cursor = response.get("next_cursor")
        
        logger.info(f"Found {len(tasks)} tasks assigned to {self.user_name}")
        return tasks
    
    def get_tasks_from_sync_db(self) -> list[NotionTask]:
        """Get all tasks from the sync database.
        
        Returns:
            List of NotionTask objects from the sync database.
        """
        logger.info("Fetching tasks from Tasks Sync DB")
        
        tasks = []
        has_more = True
        start_cursor = None
        
        while has_more:
            response = self._query_database(
                database_id=self.sync_db_id,
                start_cursor=start_cursor,
                page_size=100,
            )
            
            for page in response.get("results", []):
                task = self._parse_task(page, is_sync_db=True)
                tasks.append(task)
            
            has_more = response.get("has_more", False)
            start_cursor = response.get("next_cursor")
        
        logger.info(f"Found {len(tasks)} tasks in sync DB")
        return tasks
    
    def create_task_in_sync_db(self, task: NotionTask, kanban_id: str) -> NotionTask:
        """Create a new task in the sync database.
        
        Args:
            task: Task data to create.
            kanban_id: ID of the source task in team board.
            
        Returns:
            Created NotionTask with new ID.
        """
        logger.info(f"Creating task in sync DB: {task.title}")
        
        properties: dict[str, Any] = {
            "Name": {"title": [{"text": {"content": task.title}}]},
            "Kanban Link": {"rich_text": [{"text": {"content": kanban_id}}]},
        }
        
        # Add status if valid
        if task.status in self.VALID_STATUSES:
            properties["Status"] = {"select": {"name": task.status}}
        
        # Add priority if valid
        if task.priority in self.VALID_PRIORITIES:
            properties["Priority"] = {"select": {"name": task.priority}}
        
        # Add due date if present
        if task.due_date:
            properties["Due Date"] = {"date": {"start": task.due_date}}
        
        # Add description if present
        if task.description:
            properties["Description"] = {"rich_text": [{"text": {"content": task.description}}]}
        
        response = self.client.pages.create(
            parent={"database_id": self.sync_db_id},
            properties=properties,
        )
        
        return self._parse_task(response, is_sync_db=True)
    
    def update_task_in_sync_db(self, task_id: str, updates: dict) -> NotionTask:
        """Update a task in the sync database.
        
        Args:
            task_id: Notion page ID of the task.
            updates: Dictionary of property updates.
            
        Returns:
            Updated NotionTask.
        """
        logger.debug(f"Updating task {task_id} in sync DB")
        
        properties: dict[str, Any] = {}
        
        if "title" in updates:
            properties["Name"] = {"title": [{"text": {"content": updates["title"]}}]}
        
        if "status" in updates and updates["status"] in self.VALID_STATUSES:
            properties["Status"] = {"select": {"name": updates["status"]}}
        
        if "priority" in updates and updates["priority"] in self.VALID_PRIORITIES:
            properties["Priority"] = {"select": {"name": updates["priority"]}}
        
        if "due_date" in updates:
            if updates["due_date"]:
                properties["Due Date"] = {"date": {"start": updates["due_date"]}}
            else:
                properties["Due Date"] = {"date": None}
        
        if "description" in updates:
            properties["Description"] = {"rich_text": [{"text": {"content": updates["description"]}}]}
        
        if "ticktick_id" in updates:
            properties["TickTick ID"] = {"rich_text": [{"text": {"content": updates["ticktick_id"]}}]}
        
        if "kanban_link" in updates:
            properties["Kanban Link"] = {"rich_text": [{"text": {"content": updates["kanban_link"]}}]}
        
        if "last_synced" in updates:
            properties["Last Synced"] = {"date": {"start": updates["last_synced"]}}
        
        response = self.client.pages.update(
            page_id=task_id,
            properties=properties,
        )
        
        return self._parse_task(response, is_sync_db=True)
    
    def create_task_in_team_board(self, task: NotionTask) -> str | None:
        """Create a new task in team board database.
        
        Args:
            task: Task data from Sync DB.
            
        Returns:
            ID of created task, or None if failed.
        """
        logger.info(f"Creating task in team board: {task.title}")
        
        properties: dict[str, Any] = {
            "Name": {"title": [{"text": {"content": task.title}}]},
        }
        
        # Add status if valid (team board uses 'status' type)
        if task.status in self.VALID_STATUSES:
            properties["Status"] = {"status": {"name": task.status}}
        
        # Add priority if valid
        if task.priority in self.VALID_PRIORITIES:
            properties["Priority"] = {"select": {"name": task.priority}}
        
        # Add due date if present
        if task.due_date:
            properties["Due Date"] = {"date": {"start": task.due_date}}
        
        # Assign to the configured user
        user_id = self._get_user_id()
        if user_id:
            properties["Assigned To"] = {"people": [{"id": user_id}]}
            logger.info(f"Assigning task to {self.user_name}")
        
        try:
            response = self.client.pages.create(
                parent={"database_id": self.team_db_id},
                properties=properties,
            )
            
            created_id = response.get("id")
            
            # If task has description, add it to page body
            if task.description:
                self.set_page_content(created_id, task.description)
            
            logger.info(f"Created in team board: {task.title} ({created_id})")
            return created_id
            
        except Exception as e:
            logger.error(f"Failed to create in team board: {task.title} - {e}")
            return None
    
    def update_task_in_team_board(self, task_id: str, updates: dict) -> None:
        """Update a task in the team board database (writeback).
        
        Args:
            task_id: Notion page ID of the task in team board.
            updates: Dictionary of property updates (typically just status).
        """
        logger.info(f"Writing back to team board: {task_id}")
        
        properties: dict[str, Any] = {}
        
        # team board uses 'status' type (not 'select') for Status property
        if "status" in updates and updates["status"] in self.VALID_STATUSES:
            properties["Status"] = {"status": {"name": updates["status"]}}
        
        if properties:
            self.client.pages.update(
                page_id=task_id,
                properties=properties,
            )
    
    def sync_from_team_board_to_sync_db(self) -> dict:
        """Sync tasks from team board to sync database.
        
        This is a one-way sync that mirrors your assigned tasks
        from the team Kanban to your private sync database.
        
        Also links orphaned tasks: if a task exists in Sync DB without a Kanban Link
        and a matching task (by title) appears in team board, they get linked.
        
        Returns:
            Dictionary with sync statistics.
        """
        stats = {"created": 0, "updated": 0, "unchanged": 0, "linked": 0}
        
        # Get tasks from both databases
        team_tasks = self.get_my_tasks_from_team_board()
        sync_tasks = self.get_tasks_from_sync_db()
        
        # Create lookups
        sync_by_kanban = {t.kanban_link: t for t in sync_tasks if t.kanban_link}
        # Also create lookup for unlinked tasks by title (normalized)
        sync_unlinked_by_title = {
            t.title.strip().lower(): t 
            for t in sync_tasks 
            if not t.kanban_link
        }
        
        for team_task in team_tasks:
            kanban_id = team_task.id
            
            if kanban_id in sync_by_kanban:
                # Task exists in sync DB with link - check if update needed
                sync_task = sync_by_kanban[kanban_id]
                
                # Compare relevant fields
                needs_update = (
                    sync_task.title != team_task.title or
                    sync_task.status != team_task.status or
                    sync_task.priority != team_task.priority or
                    sync_task.due_date != team_task.due_date
                )
                
                if needs_update:
                    self.update_task_in_sync_db(sync_task.id, {
                        "title": team_task.title,
                        "status": team_task.status,
                        "priority": team_task.priority,
                        "due_date": team_task.due_date,
                    })
                    stats["updated"] += 1
                    logger.info(f"Updated: {team_task.title}")
                else:
                    stats["unchanged"] += 1
            else:
                # Check if there's an unlinked task with matching title
                title_key = team_task.title.strip().lower()
                if title_key in sync_unlinked_by_title:
                    # Found matching unlinked task - link it!
                    sync_task = sync_unlinked_by_title[title_key]
                    logger.info(f"Linking existing task to Kanban: {team_task.title}")
                    self.update_task_in_sync_db(sync_task.id, {
                        "kanban_link": kanban_id,
                        "status": team_task.status,
                        "priority": team_task.priority,
                        "due_date": team_task.due_date,
                    })
                    stats["linked"] += 1
                    # Remove from unlinked lookup to prevent double-matching
                    del sync_unlinked_by_title[title_key]
                else:
                    # Task doesn't exist in sync DB - create it
                    self.create_task_in_sync_db(team_task, kanban_id)
                    stats["created"] += 1
                    logger.info(f"Created: {team_task.title}")
        
        logger.info(f"Team board -> Sync DB: {stats['created']} created, {stats['updated']} updated, {stats['linked']} linked, {stats['unchanged']} unchanged")
        return stats
    
    def set_page_icon(self, page_id: str, emoji: str) -> None:
        """Set the icon for a Notion page.
        
        Args:
            page_id: Notion page ID.
            emoji: Emoji string to use as the icon.
        """
        self.client.pages.update(
            page_id=page_id,
            icon={"type": "emoji", "emoji": emoji},
        )
    
    def get_page_icon(self, page: dict) -> str | None:
        """Get the icon from a Notion page response.
        
        Args:
            page: Notion page object from API response.
            
        Returns:
            Emoji string if icon exists, None otherwise.
        """
        icon = page.get("icon")
        if icon and icon.get("type") == "emoji":
            return icon.get("emoji")
        return None
    
    def assign_icons_to_team_tasks(self) -> int:
        """Assign icons to team board tasks that don't have one.
        
        Uses keyword-based matching to assign appropriate icons.
        
        Returns:
            Number of tasks updated with icons.
        """
        from ..icons import get_icon_for_title
        
        logger.info("Checking team board tasks for missing icons")
        
        # Query all pages - we need the icon field which isn't in the standard query
        url = f"https://api.notion.com/v1/databases/{self.team_db_id}/query"
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        }
        
        updated = 0
        has_more = True
        start_cursor = None
        
        while has_more:
            body: dict[str, Any] = {"page_size": 100}
            if start_cursor:
                body["start_cursor"] = start_cursor
            
            response = httpx.post(url, headers=headers, json=body, timeout=60.0)
            response.raise_for_status()
            data = response.json()
            
            for page in data.get("results", []):
                current_icon = self.get_page_icon(page)
                
                # Skip if already has an icon
                if current_icon:
                    continue
                
                # Get title
                title = self._extract_title(page.get("properties", {}))
                if not title:
                    continue
                
                # Get suggested icon
                icon = get_icon_for_title(title)
                
                # Set the icon
                try:
                    self.set_page_icon(page["id"], icon)
                    logger.info(f"Set icon {icon} for: {title[:50]}")
                    updated += 1
                except Exception as e:
                    logger.warning(f"Failed to set icon for {title}: {e}")
            
            has_more = data.get("has_more", False)
            start_cursor = data.get("next_cursor")
        
        logger.info(f"Updated {updated} tasks with icons")
        return updated
