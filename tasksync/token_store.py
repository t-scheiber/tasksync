"""Persist refreshed TickTick tokens in the data directory."""

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

TOKEN_FILE_NAME = "ticktick_tokens.json"


def token_path(data_dir: Path) -> Path:
    """Return the path of the token file inside the data directory."""
    return Path(data_dir) / TOKEN_FILE_NAME


def load_tokens(data_dir: Path) -> tuple[str, str] | None:
    """Read saved tokens.

    Returns:
        (access_token, refresh_token), or None if the file is missing,
        unreadable or malformed. A missing file is normal and logs nothing.
        For an unreadable or malformed file the warning names the file only.
    """
    path = token_path(data_dir)
    if not path.exists():
        # No refresh has happened yet, so the environment tokens are current.
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        access = data["access_token"]
        refresh = data["refresh_token"]
        if not (isinstance(access, str) and isinstance(refresh, str) and access and refresh):
            raise ValueError("invalid token fields")
    except Exception as e:
        # Log the exception type only, never its message.
        logger.warning(
            f"Could not read tokens from {path} ({type(e).__name__}), "
            "using tokens from the environment"
        )
        return None
    return access, refresh


def save_tokens(data_dir: Path, access_token: str, refresh_token: str) -> Path:
    """Write tokens atomically and restrict the file to its owner where possible."""
    path = token_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "refreshed_at": datetime.now(timezone.utc).isoformat(),
    }
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".ticktick_tokens.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        if os.name == "posix":
            os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return path
