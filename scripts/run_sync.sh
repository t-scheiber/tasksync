#!/bin/bash
# TaskSync - Cron wrapper script
# Add to crontab: */5 * * * * /opt/tasksync/scripts/run_sync.sh >> /var/log/tasksync.log 2>&1

set -e

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
VENV_DIR="$PROJECT_DIR/venv"

# Change to project directory
cd "$PROJECT_DIR"

# Activate virtual environment
source "$VENV_DIR/bin/activate"

# Run sync
python -m tasksync.main

# Deactivate (optional, script ends anyway)
deactivate 2>/dev/null || true
