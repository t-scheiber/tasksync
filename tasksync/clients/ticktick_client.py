"""TickTick API client for TaskSync."""

import base64
from dataclasses import dataclass
from datetime import datetime
from typing import Any
import requests

from ..logger import setup_logger

logger = setup_logger(__name__)


@dataclass
class TickTickTask:
    """Represents a task from TickTick."""
    id: str
    title: str
    status: int  # 0 = active, 2 = completed
    priority: int  # 0 = none, 1 = low, 3 = medium, 5 = high
    due_date: str | None  # ISO format
    content: str  # Description/notes
    project_id: str
    modified_time: datetime
    tags: list[str]  # Tags/labels for status tracking
    
    @property
    def is_completed(self) -> bool:
        return self.status == 2
    
    def to_dict(self) -> dict:
        """Convert to dictionary for comparison."""
        return {
            "title": self.title,
            "status": self.status,
            "priority": self.priority,
            "due_date": self.due_date,
            "content": self.content,
            "tags": self.tags,
        }


class TickTickClient:
    """Client for interacting with TickTick API."""
    
    BASE_URL = "https://api.ticktick.com/open/v1"
    AUTH_URL = "https://ticktick.com/oauth"
    
    # Priority mapping: Notion -> TickTick
    # Default (empty/none) -> 3 (medium in TickTick)
    PRIORITY_TO_TICKTICK = {
        "fire": 5,   # Fire = high priority
        "high": 5,
        "normal": 3,
        "low": 1,
        "": 3,  # Default to medium
    }
    
    # Priority mapping: TickTick -> Notion
    # Default (0/none) -> "normal" in Notion
    PRIORITY_FROM_TICKTICK = {
        5: "high",
        3: "normal",
        1: "low",
        0: "normal",  # Default to normal (medium in TickTick)
    }
    
    # Notion statuses that map to TickTick completed
    COMPLETED_STATUSES = {"Done", "Cancelled/No need"}
    
    # All valid Notion statuses
    NOTION_STATUSES = [
        "Not started",
        "Blocker",
        "Investigation",
        "Working on",
        "Finishing/testing",
        "Done",
        "Cancelled/No need",
        "In progress",
    ]
    
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        access_token: str,
        refresh_token: str,
        project_id: str,
    ):
        """Initialize TickTick client.
        
        Args:
            client_id: OAuth client ID.
            client_secret: OAuth client secret.
            access_token: Current access token.
            refresh_token: Refresh token for getting new access tokens.
            project_id: Project/list ID to sync with.
        """
        self.client_id = client_id
        self.client_secret = client_secret
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.project_id = project_id
        self._session = requests.Session()
    
    def _get_auth_header(self) -> dict:
        """Get authorization header."""
        return {"Authorization": f"Bearer {self.access_token}"}
    
    def _request(
        self,
        method: str,
        endpoint: str,
        data: dict | None = None,
        retry_on_401: bool = True,
    ) -> dict | list | None:
        """Make an API request with automatic token refresh.
        
        Args:
            method: HTTP method (GET, POST, PUT, DELETE).
            endpoint: API endpoint (without base URL).
            data: Request body data.
            retry_on_401: Whether to retry after refreshing token on 401.
            
        Returns:
            Response JSON or None.
        """
        url = f"{self.BASE_URL}{endpoint}"
        headers = self._get_auth_header()
        headers["Content-Type"] = "application/json"
        
        response = self._session.request(
            method=method,
            url=url,
            headers=headers,
            json=data,
        )
        
        if response.status_code == 401 and retry_on_401:
            logger.warning("Access token expired, refreshing...")
            if self._refresh_access_token():
                return self._request(method, endpoint, data, retry_on_401=False)
            else:
                raise Exception("Failed to refresh access token")
        
        if response.status_code >= 400:
            logger.error(f"API error {response.status_code}")
            response.raise_for_status()
        
        if response.text:
            return response.json()
        return None
    
    def _refresh_access_token(self) -> bool:
        """Refresh the access token using refresh token.
        
        Returns:
            True if successful, False otherwise.
        """
        # Create Basic auth header
        credentials = f"{self.client_id}:{self.client_secret}"
        encoded = base64.b64encode(credentials.encode()).decode()
        
        response = requests.post(
            f"{self.AUTH_URL}/token",
            headers={
                "Authorization": f"Basic {encoded}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={
                "grant_type": "refresh_token",
                "refresh_token": self.refresh_token,
            },
        )
        
        if response.status_code == 200:
            data = response.json()
            self.access_token = data["access_token"]
            if "refresh_token" in data:
                self.refresh_token = data["refresh_token"]
            logger.info("Access token refreshed successfully")
            return True
        
        logger.error(f"Failed to refresh token: HTTP {response.status_code}")
        return False
    
    def get_tokens(self) -> tuple[str, str]:
        """Get current tokens (useful for saving after refresh).
        
        Returns:
            Tuple of (access_token, refresh_token).
        """
        return self.access_token, self.refresh_token
    
    def _parse_task(self, data: dict) -> TickTickTask:
        """Parse API response into TickTickTask."""
        # Parse modified time
        modified_str = data.get("modifiedTime", "")
        if modified_str:
            # TickTick uses ISO format with timezone
            try:
                modified_time = datetime.fromisoformat(modified_str.replace("Z", "+00:00"))
            except ValueError:
                modified_time = datetime.now()
        else:
            modified_time = datetime.now()
        
        # Parse due date
        due_date = None
        if data.get("dueDate"):
            due_date = data["dueDate"][:10]  # Just the date part
        
        # Parse tags
        tags = data.get("tags", []) or []
        
        return TickTickTask(
            id=data["id"],
            title=data.get("title", ""),
            status=data.get("status", 0),
            priority=data.get("priority", 0),
            due_date=due_date,
            content=data.get("content", ""),
            project_id=data.get("projectId", ""),
            modified_time=modified_time,
            tags=tags,
        )
    
    def get_projects(self) -> list[dict]:
        """Get all projects/lists.
        
        Returns:
            List of project dictionaries.
        """
        response = self._request("GET", "/project")
        return response if response else []
    
    def get_tasks(self, project_id: str | None = None) -> list[TickTickTask]:
        """Get all tasks from a project.
        
        Args:
            project_id: Project ID. If None, uses configured project.
            
        Returns:
            List of TickTickTask objects.
        """
        pid = project_id or self.project_id
        logger.info(f"Fetching tasks from TickTick project: {pid}")
        
        response = self._request("GET", f"/project/{pid}/data")
        
        if not response:
            return []
        
        tasks = []
        for task_data in response.get("tasks", []):
            task = self._parse_task(task_data)
            tasks.append(task)
        
        logger.info(f"Found {len(tasks)} tasks in TickTick")
        return tasks
    
    def create_task(
        self,
        title: str,
        project_id: str | None = None,
        content: str = "",
        due_date: str | None = None,
        priority: int = 0,
        tags: list[str] | None = None,
    ) -> TickTickTask:
        """Create a new task.
        
        Args:
            title: Task title.
            project_id: Project ID. If None, uses configured project.
            content: Task description/notes.
            due_date: Due date in ISO format (YYYY-MM-DD).
            priority: Priority (0=none, 1=low, 3=medium, 5=high).
            tags: List of tags/labels.
            
        Returns:
            Created TickTickTask.
        """
        pid = project_id or self.project_id
        logger.info(f"Creating task in TickTick: {title}")
        
        data: dict[str, Any] = {
            "title": title,
            "projectId": pid,
        }
        
        if content:
            data["content"] = content
        
        if due_date:
            data["dueDate"] = due_date
        
        if priority:
            data["priority"] = priority
        
        if tags:
            data["tags"] = tags
        
        response = self._request("POST", "/task", data)
        return self._parse_task(response)
    
    def update_task(self, task_id: str, updates: dict) -> TickTickTask:
        """Update an existing task.
        
        Args:
            task_id: Task ID to update.
            updates: Dictionary of fields to update.
            
        Returns:
            Updated TickTickTask.
        """
        logger.debug(f"Updating TickTick task: {task_id}")
        
        # First get the current task to merge updates
        current = self._request("GET", f"/task/{task_id}")
        
        if not current:
            raise Exception(f"Task {task_id} not found")
        
        # Merge updates
        data = {
            "id": task_id,
            "projectId": current.get("projectId", self.project_id),
        }
        
        if "title" in updates:
            data["title"] = updates["title"]
        else:
            data["title"] = current.get("title", "")
        
        if "content" in updates:
            data["content"] = updates["content"]
        
        if "due_date" in updates:
            data["dueDate"] = updates["due_date"]
        
        if "priority" in updates:
            data["priority"] = updates["priority"]
        
        if "tags" in updates:
            data["tags"] = updates["tags"]
        
        response = self._request("POST", f"/task/{task_id}", data)
        return self._parse_task(response)
    
    def complete_task(self, task_id: str, project_id: str | None = None) -> None:
        """Mark a task as complete.
        
        Args:
            task_id: Task ID to complete.
            project_id: Project ID. If None, uses configured project.
        """
        pid = project_id or self.project_id
        logger.info(f"Completing TickTick task: {task_id}")
        self._request("POST", f"/project/{pid}/task/{task_id}/complete")
    
    def delete_task(self, task_id: str, project_id: str | None = None) -> None:
        """Delete a task.
        
        Args:
            task_id: Task ID to delete.
            project_id: Project ID. If None, uses configured project.
        """
        pid = project_id or self.project_id
        logger.warning(f"Deleting TickTick task: {task_id}")
        self._request("DELETE", f"/project/{pid}/task/{task_id}")
    
    @classmethod
    def notion_priority_to_ticktick(cls, notion_priority: str) -> int:
        """Convert Notion priority to TickTick priority."""
        return cls.PRIORITY_TO_TICKTICK.get(notion_priority.lower(), 0)
    
    @classmethod
    def ticktick_priority_to_notion(cls, ticktick_priority: int) -> str:
        """Convert TickTick priority to Notion priority."""
        return cls.PRIORITY_FROM_TICKTICK.get(ticktick_priority, "normal")
    
    @classmethod
    def is_completed_status(cls, notion_status: str) -> bool:
        """Check if a Notion status maps to TickTick completed."""
        return notion_status in cls.COMPLETED_STATUSES
    
    @classmethod
    def ticktick_is_done(cls, tt_task: "TickTickTask") -> bool:
        """Check if TickTick task is completed."""
        return tt_task.status == 2
