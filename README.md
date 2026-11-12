# TaskSync

TaskSync is a Python script that keeps tasks in step across three places: a shared team board in Notion, a private "Sync DB" in Notion, and one TickTick project. It syncs tasks only (title, status, priority, due date, description), and it runs when you start it. `python -m tasksync.main` does one pass and exits. There is no daemon and no webhook, so scheduling means cron.

```
team board (Notion, shared)  <->  Sync DB (Notion, private)  <->  TickTick project
     status, title, priority,          title, due date, priority,
     due date flow down                completion, description
     status flows back up
```

| Link | Direction | Fields |
|---|---|---|
| Team board to Sync DB | board to Sync DB | title, status, priority, due date, for tasks assigned to you |
| Sync DB to team board | Sync DB to board | status (and page text in Notion-only mode); creates board tasks for Sync DB rows that have no link |
| Sync DB and TickTick | both ways | title, due date, description, priority (Notion wins), completion |

Python 3.10 or newer (the dependency lock was resolved for 3.14). About 2,400 lines of Python (icons.py is 230 of them) and 24 offline tests.

## How it works

### Data model

Each Sync DB row is the join record. Two text properties hold the foreign keys: `Kanban Link` (the team board page ID) and `TickTick ID`. The rows are parsed into a `NotionTask`, and TickTick payloads into a `TickTickTask`. Notion's `Assigned To` people property is matched by display name (`NOTION_USER_NAME`), client side, because Notion cannot filter databases by person name.

| Field | Notion | TickTick |
|---|---|---|
| title | `Name` | `title` |
| status | `Status`, 8 fixed names | `status`: 0 open, 2 done |
| priority | `Priority`: fire, high, normal, low | 5, 5, 3, 1 (back: 5 to high, 3 to normal, 1 to low, 0 to normal) |
| due date | `Due Date` | `dueDate`, cut to `YYYY-MM-DD` |
| description | `Description` text (page body on the board) | `content` |

TickTick has two states and Notion has eight. The engine never flattens Notion's: only "Done" and "Cancelled/No need" count as completed, and a TickTick edit never overwrites a non-done Notion status.

### Change detection

There is no stored state or change log. Every run reads all three databases in full (paginated), compares field by field, and writes only where values differ. The one timestamp comparison is the conflict decision below. `fire` and `high` both map to TickTick 5, so priorities are compared in TickTick terms and do not trigger a write every run.

### Conflict resolution

The engine decides conflicts only for Sync DB rows linked to a TickTick task (`_sync_linked_task` in `tasksync/sync/engine.py`). `CONFLICT_STRATEGY` picks the winning side:

1. `notion_priority`: the Sync DB row wins.
2. `ticktick_priority`: the TickTick task wins.
3. `last_write` (default and fallback for any other value): compare Notion `last_edited_time` with TickTick `modifiedTime`. Notion wins only if strictly newer, so a tie goes to TickTick. Naive timestamps are read as UTC.

Then these rules apply:

- The winner is chosen once per task, not per field. If the title or due date differs, the winner's title, due date and description are all copied.
- Priority ignores the strategy. It always goes from Notion to TickTick, and TickTick priority edits on linked tasks are never read back.
- Completion is one way per side. A Notion "Done" or "Cancelled/No need" completes the TickTick task when Notion wins. A TickTick completion sets Notion to "Done" when TickTick wins. Nothing reopens a task.
- A linked task that disappears from the TickTick project is treated as completed. TickTick's project endpoint returns only open tasks.

Worked example with `last_write`. The Sync DB row "Buy beans" has status "Investigation". Someone retitles it "Buy fresh beans" in Notion at 09:00, then retitles the TickTick task "Buy decaf" at 09:30, and raises the Notion priority to fire at 09:00. On the next run TickTick is newer, so Notion's title becomes "Buy decaf" and its description and due date are replaced by TickTick's. The status stays "Investigation". The priority goes to TickTick as 5 anyway. `tests/test_sync.py` covers each of these steps.

### Duplicates and loops

- A row without a `TickTick ID` is created in TickTick, and the new ID is written back to the row immediately. If creation fails, no link is written (tested), so the next run retries instead of storing a false link.
- Rows without a `Kanban Link` are matched to board tasks by case-insensitive title and linked, each board task at most once, instead of being duplicated.
- Sync DB rows with no `Kanban Link` are created on the board (assigned to you if your user ID can be found) and linked.
- Loops are avoided by idempotence. After a sync, the compared fields are equal, so the next run writes nothing (tested: `test_unchanged_tasks_produce_no_writes`). In Notion-only mode the writeback runs before the pull, so a Sync DB edit is not overwritten by the stale board value.

## Authentication

Notion uses an internal integration token (`NOTION_TOKEN`) sent as a bearer header with Notion-Version 2022-06-28. Share both databases with the integration. There is no Notion OAuth.

TickTick uses OAuth 2 authorization code. `python -m tasksync.auth_ticktick` asks for your client ID and secret, opens the authorize URL (scope `tasks:read tasks:write`, redirect `http://localhost:8080/callback`), and catches the callback with a one-shot local Flask server (120 second timeout). It exchanges the code at the token endpoint with HTTP Basic auth, then prints the tokens, your project IDs and the `.env` lines. If Flask is missing or the flow fails, it falls back to pasting the code by hand.

The helper does not store tokens. You copy them into `.env`, which is git-ignored. On a 401 the client refreshes the access token once and retries. After a run in which the tokens changed, TaskSync writes the new access token, refresh token and refresh time to `<DATA_DIR>/ticktick_tokens.json` and logs one line with the path. The file is written atomically (temp file, then replace) and gets mode 0600 on POSIX. At startup, tokens from that file take precedence over `TICKTICK_ACCESS_TOKEN` and `TICKTICK_REFRESH_TOKEN`. Before the first refresh the file does not exist and the `.env` values are used. If the file is unreadable or malformed, the run logs a warning that names the file and falls back to the `.env` values. No log line contains a token, and a failed refresh logs only the HTTP status. The data directory and the token file are git-ignored.

## Setup

1. Create a virtual environment named `venv` (the cron wrapper expects that name) and install the hashed lock file:
   ```bash
   python3 -m venv venv && source venv/bin/activate
   python -m pip install --require-hashes --only-binary=:all: -r requirements.txt
   ```
   `requirements.in` lists the six direct dependencies. Regenerate the lock with `uv pip compile requirements.in --upgrade --universal --generate-hashes --python-version 3.14 -o requirements.txt`.
2. Create the two Notion databases (properties below) and share both with your integration.
3. Register a TickTick app at developer.ticktick.com, then run the OAuth helper.
4. `cp .env.example .env` and fill it in.
5. Check the connections, then sync:
   ```bash
   python -m tasksync.main --test     # reads from both APIs, writes nothing
   python -m tasksync.main            # one full sync
   python -m tasksync.main --notion   # Notion only: board and Sync DB
   python -m tasksync.main --env path/to/.env
   ```
   Without complete TickTick settings the run falls back to Notion-only mode. The exit code is 0 on success and 1 on failure.
6. To schedule, point cron at the wrapper: `*/5 * * * * /opt/tasksync/scripts/run_sync.sh >> /var/log/tasksync.log 2>&1`.

Notion properties the code reads and writes:

- Team board: `Name` (title), `Status` (status type), `Priority` (select), `Due Date` (date), `Assigned To` (people).
- Sync DB: `Name`, `Status` (select), `Priority` (select), `Due Date`, `Description` (text), `Kanban Link` (text), `TickTick ID` (text), `Last Synced` (date).
- Status options are hard-coded: Not started, Blocker, Investigation, Working on, Finishing/testing, Done, Cancelled/No need, In progress. Priority options are fire, high, normal, low. Any other value is silently left out of a write. To use other names, edit `VALID_STATUSES` in `notion_client.py` and `COMPLETED_STATUSES` and `NOTION_STATUSES` in `ticktick_client.py`.

### Configuration

| Variable | Meaning |
|---|---|
| `NOTION_TOKEN` | Integration token. The only variable the program refuses to start without. |
| `NOTION_TEAM_DB_ID` | Database ID of the shared team board. |
| `NOTION_SYNC_DB_ID` | Database ID of your private Sync DB. |
| `NOTION_USER_NAME` | Your display name as it appears in `Assigned To`. |
| `TICKTICK_CLIENT_ID` | OAuth client ID. TickTick counts as configured when this, the access token and the project ID are set. |
| `TICKTICK_CLIENT_SECRET` | OAuth client secret, used for token refresh. |
| `TICKTICK_ACCESS_TOKEN` | Current access token. |
| `TICKTICK_REFRESH_TOKEN` | Refresh token. |
| `TICKTICK_PROJECT_ID` | The one TickTick project to sync. |
| `CONFLICT_STRATEGY` | `last_write`, `notion_priority` or `ticktick_priority`. Default `last_write`. |
| `LOG_LEVEL` | DEBUG, INFO, WARNING or ERROR. Default INFO. |
| `DATA_DIR` | Directory created at startup. Default `./data`. Holds `ticktick_tokens.json`, the refreshed TickTick tokens, which override the two token variables above. |

## Tests

```bash
python -m unittest discover -s tests -v
```

Twenty-four tests, no credentials, no network. One of them checks the 0600 file mode and is skipped on Windows. The sync tests patch `socket.socket.connect` to raise, and they use `Mock(spec=...)` clients. They cover the three conflict strategies, naive against aware timestamps, status preservation, the priority rule, completion in both directions, link-on-create and failed creation, TickTick payload parsing, partial updates, escaping in the OAuth error page, saving and loading refreshed tokens, file tokens overriding the environment, fallback on a malformed or missing token file, and a check that neither the save path nor a failed refresh logs a token. CI runs the same command on every push and pull request (`.github/workflows/tests.yml`).

Not covered: `NotionClient` (no tests at all), the full `sync_all` flow, title linking, the rest of config loading and the icon matcher.

## Limitations

- Full mode runs team board to Sync DB first. For rows with a `Kanban Link` that copies the board's title, status, priority and due date over the Sync DB row, so edits to those four fields in the Sync DB do not survive a run. A TickTick completion does reach the board, through the writeback step.
- Tasks created in TickTick are copied into the Sync DB and then created on the team board too, since they have no `Kanban Link`.
- "Cancelled/No need" completes the TickTick task. The task then drops out of the TickTick API response, and the next run sets the Notion status to "Done".
- Deletions are not synced, and a deleted TickTick task looks the same as a completed one.
- Title matching to TickTick runs after the creation steps. It only catches rows they skipped, so a first run against existing identical tasks can create duplicates.
- Creating the TickTick task and writing its ID back are two calls. If the second fails, the run aborts and the next run creates another copy.
- Notion's `last_edited_time` also moves when this tool writes (for example `Last Synced`), which biases `last_write` toward Notion.
- Description comparison is not part of change detection, so a description-only edit does not sync by itself.
- In Notion-only mode a differing description replaces every block on the board page with plain paragraphs. The board page text is read from the first page of blocks only.
- Full mode sets a keyword-based emoji icon (`tasksync/icons.py`) on every team board page without an icon, including pages assigned to other people.
- The user ID for assignment is found by scanning the first 100 board pages for your name. If none matches, new board tasks are unassigned.
- Only open tasks of one TickTick project are read. Subtasks, tags, reminders and repeats are not synced. Due dates are cut to the date, so times of day are lost.
- No retries or rate-limit handling for Notion, and no lock, so overlapping cron runs can collide.

I wrote TaskSync in 2026 to keep my own task boards in step. I no longer run it, so the repository is not maintained.
