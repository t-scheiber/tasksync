"""Offline regression coverage for sync decisions that can overwrite user data."""
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

from tasksync.clients.notion_client import NotionClient, NotionTask
from tasksync.clients.ticktick_client import TickTickClient, TickTickTask
from tasksync.sync.engine import SyncEngine, SyncStats


class SyncDecisions(unittest.TestCase):
    def setUp(self):
        # Catch accidental real API calls, even outside the CI network sandbox.
        self.network = patch('socket.socket.connect', side_effect=AssertionError('Network forbidden in tests'))
        self.network.start()
        self.addCleanup(self.network.stop)
        self.notion = Mock(spec=NotionClient)
        self.ticktick = Mock(spec=TickTickClient)
        self.engine = SyncEngine(self.notion, self.ticktick)
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.task = NotionTask('notion-1', 'Buy beans', 'Working on', 'normal', None, '', '', 'tick-1', now)
        self.remote = TickTickTask('tick-1', 'Buy beans', 0, 3, None, '', 'project-1', now, [])
        self.stats = SyncStats({}, 0, 0, 0, 0, 0, 0)

    def sync(self, task=None, remote=None):
        self.engine._sync_linked_task(task or self.task, remote or self.remote, self.stats)

    def test_unchanged_tasks_produce_no_writes(self):
        self.sync()
        self.assertEqual(self.notion.mock_calls, [])
        self.assertEqual(self.ticktick.mock_calls, [])

    def test_latest_title_wins_with_naive_and_aware_timestamps(self):
        task = replace(self.task, title='Buy fresh beans', last_edited=datetime(2026, 1, 2))
        self.sync(task=task)
        self.ticktick.update_task.assert_called_once_with('tick-1', {
            'title': 'Buy fresh beans', 'content': '', 'due_date': None,
        })
        self.notion.update_task_in_sync_db.assert_not_called()

    def test_ticktick_title_change_preserves_granular_notion_status(self):
        remote = replace(self.remote, title='Buy decaf', modified_time=self.remote.modified_time + timedelta(hours=1))
        self.sync(task=replace(self.task, status='Investigation'), remote=remote)
        task_id, changes = self.notion.update_task_in_sync_db.call_args.args
        self.assertEqual(task_id, 'notion-1')
        self.assertEqual(changes['title'], 'Buy decaf')
        self.assertNotIn('status', changes)
        self.assertNotIn('priority', changes)

    def test_notion_priority_wins_even_when_ticktick_content_is_newer(self):
        self.sync(task=replace(self.task, priority='fire'))
        self.ticktick.update_task.assert_called_once_with('tick-1', {'priority': 5})
        self.notion.update_task_in_sync_db.assert_not_called()

    def test_equivalent_fire_and_high_priorities_do_not_repeat_writes(self):
        self.sync(task=replace(self.task, priority='fire'), remote=replace(self.remote, priority=5))
        self.assertEqual(self.ticktick.mock_calls, [])
        self.assertEqual(self.notion.mock_calls, [])

    def test_explicit_notion_conflict_strategy_wins_over_newer_remote(self):
        self.engine.conflict_strategy = 'notion_priority'
        remote = replace(self.remote, title='Remote edit', modified_time=self.remote.modified_time + timedelta(days=1))
        self.sync(remote=remote)
        self.assertEqual(self.ticktick.update_task.call_args.args[1]['title'], self.task.title)
        self.notion.update_task_in_sync_db.assert_not_called()

    def test_explicit_ticktick_conflict_strategy_wins_over_newer_notion(self):
        self.engine.conflict_strategy = 'ticktick_priority'
        task = replace(self.task, title='Local edit', last_edited=self.task.last_edited + timedelta(days=1))
        self.sync(task=task)
        self.assertEqual(self.notion.update_task_in_sync_db.call_args.args[1]['title'], self.remote.title)
        self.ticktick.update_task.assert_not_called()

    def test_completed_remote_marks_notion_done(self):
        self.sync(remote=replace(self.remote, status=2))
        self.notion.update_task_in_sync_db.assert_called_once_with('notion-1', {'status': 'Done'})
        self.assertEqual(self.stats.ticktick_to_sync_updated, 1)

    def test_done_or_cancelled_notion_completes_remote(self):
        self.engine.conflict_strategy = 'notion_priority'
        for status in ('Done', 'Cancelled/No need'):
            with self.subTest(status=status):
                self.ticktick.reset_mock()
                self.sync(task=replace(self.task, status=status))
                self.ticktick.complete_task.assert_called_once_with('tick-1')

    def test_remote_creation_failure_does_not_write_a_false_link(self):
        self.ticktick.create_task.side_effect = RuntimeError('Service unavailable')
        with self.assertRaises(RuntimeError):
            self.engine._create_in_ticktick(replace(self.task, ticktick_id=''), self.stats)
        self.notion.update_task_in_sync_db.assert_not_called()
        self.assertEqual(self.stats.sync_to_ticktick_created, 0)

    def test_created_remote_is_linked_before_following_sync(self):
        self.ticktick.create_task.return_value = self.remote
        self.engine._create_in_ticktick(replace(self.task, ticktick_id='', priority=''), self.stats)
        changes = self.notion.update_task_in_sync_db.call_args.args[1]
        self.assertEqual(changes['ticktick_id'], 'tick-1')
        self.assertEqual(changes['priority'], 'normal')
        self.assertIn('last_synced', changes)
        self.assertEqual(self.stats.sync_to_ticktick_created, 1)


class TickTickPayload(unittest.TestCase):
    def test_parse_preserves_utc_date_and_completed_state(self):
        client = object.__new__(TickTickClient)
        task = client._parse_task({
            'id': 'task-1', 'title': 'Buy beans', 'status': 2,
            'modifiedTime': '2026-01-01T12:00:00Z',
            'dueDate': '2026-01-03T00:00:00Z', 'tags': None,
        })
        self.assertTrue(task.is_completed)
        self.assertEqual(task.due_date, '2026-01-03')
        self.assertEqual(task.modified_time.utcoffset(), timedelta(0))
        self.assertEqual(task.tags, [])

    def test_partial_update_preserves_current_title_and_project(self):
        client = object.__new__(TickTickClient)
        client.project_id = 'default-project'
        client._request = Mock(side_effect=[
            {'id': 'task-1', 'title': 'Existing title', 'projectId': 'existing-project'},
            {'id': 'task-1', 'title': 'Existing title'},
        ])
        client.update_task('task-1', {'priority': 5})
        client._request.assert_called_with('POST', '/task/task-1', {
            'id': 'task-1', 'title': 'Existing title', 'projectId': 'existing-project', 'priority': 5,
        })


if __name__ == '__main__':
    unittest.main()
