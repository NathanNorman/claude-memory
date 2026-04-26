#!/Users/nathan.norman/.pyenv/versions/3.12.11/bin/python3
"""Tests for webhook pipeline timing instrumentation and latency metrics."""

import json
import sqlite3
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))
sys.path.insert(0, str(Path(__file__).parent))



class TestPipelineTimer(unittest.TestCase):
    """Test PipelineTimer — per-stage timing instrumentation."""

    def test_single_stage(self):
        from index_worker import PipelineTimer
        timer = PipelineTimer()
        timer.start('fetch')
        time.sleep(0.01)
        timer.stop()
        self.assertIn('fetch', timer.stages)
        self.assertGreater(timer.stages['fetch'], 0)

    def test_multi_stage(self):
        from index_worker import PipelineTimer
        timer = PipelineTimer()
        timer.start('fetch')
        time.sleep(0.01)
        timer.stop()
        timer.start('embed')
        time.sleep(0.01)
        timer.stop()
        self.assertIn('fetch', timer.stages)
        self.assertIn('embed', timer.stages)
        self.assertEqual(len(timer.stages), 2)

    def test_to_json_valid(self):
        from index_worker import PipelineTimer
        timer = PipelineTimer()
        timer.start('fetch')
        time.sleep(0.01)
        timer.stop()
        j = timer.to_json()
        parsed = json.loads(j)
        self.assertIsInstance(parsed, dict)
        self.assertIn('fetch', parsed)

    def test_summary_identifies_slowest(self):
        from index_worker import PipelineTimer
        timer = PipelineTimer()
        # Manually set stages with known values
        timer.stages = {'fast': 10.0, 'slow': 500.0, 'medium': 100.0}
        summary = timer.summary()
        self.assertIn('slow', summary)
        self.assertIn('500', summary)

    def test_empty_timer(self):
        from index_worker import PipelineTimer
        timer = PipelineTimer()
        j = timer.to_json()
        self.assertEqual(j, '{}')
        summary = timer.summary()
        self.assertIn('0', summary)


class TestJobQueueTiming(unittest.TestCase):
    """Test JobQueue.mark_done() with timing data."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.tmp.close()
        self.db_path = self.tmp.name
        # Initialize with schema
        conn = sqlite3.connect(self.db_path)
        conn.execute('PRAGMA journal_mode = WAL')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS index_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                repo_name TEXT NOT NULL,
                clone_url TEXT NOT NULL,
                before_sha TEXT NOT NULL,
                after_sha TEXT NOT NULL,
                ref TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                error TEXT,
                created_at REAL NOT NULL,
                started_at REAL,
                completed_at REAL,
                timing TEXT
            )
        ''')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_jobs_status_created ON index_jobs(status, created_at)')
        conn.commit()
        conn.close()

    def tearDown(self):
        import os
        os.unlink(self.db_path)

    def test_mark_done_stores_timing(self):
        from job_queue import JobQueue
        queue = JobQueue(db_path=self.db_path)
        job_id = queue.enqueue_job('test-repo', 'https://example.com/repo.git', 'aaa', 'bbb', 'refs/heads/main')
        job = queue.claim_next_job()
        self.assertIsNotNone(job)

        timing_json = json.dumps({'fetch': 100.0, 'embed': 200.0})
        queue.mark_done(job.id, timing=timing_json)

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute('SELECT * FROM index_jobs WHERE id = ?', (job.id,)).fetchone()
        conn.close()

        self.assertEqual(row['status'], 'done')
        self.assertIsNotNone(row['timing'])
        self.assertEqual(json.loads(row['timing']), {'fetch': 100.0, 'embed': 200.0})

    def test_mark_done_without_timing(self):
        from job_queue import JobQueue
        queue = JobQueue(db_path=self.db_path)
        job_id = queue.enqueue_job('test-repo', 'https://example.com/repo.git', 'aaa', 'bbb', 'refs/heads/main')
        job = queue.claim_next_job()
        queue.mark_done(job.id)

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute('SELECT * FROM index_jobs WHERE id = ?', (job.id,)).fetchone()
        conn.close()

        self.assertEqual(row['status'], 'done')
        self.assertIsNone(row['timing'])


class TestPipelineHealth(unittest.TestCase):
    """Test get_pipeline_health() — metrics aggregation."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.tmp.close()
        self.db_path = self.tmp.name
        conn = sqlite3.connect(self.db_path)
        conn.execute('PRAGMA journal_mode = WAL')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS index_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                repo_name TEXT NOT NULL,
                clone_url TEXT NOT NULL,
                before_sha TEXT NOT NULL,
                after_sha TEXT NOT NULL,
                ref TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                error TEXT,
                created_at REAL NOT NULL,
                started_at REAL,
                completed_at REAL,
                timing TEXT
            )
        ''')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_jobs_status_created ON index_jobs(status, created_at)')
        conn.commit()
        conn.close()

    def tearDown(self):
        import os
        os.unlink(self.db_path)

    def test_empty_table_returns_zeroes(self):
        from job_queue import JobQueue
        queue = JobQueue(db_path=self.db_path)
        health = queue.get_pipeline_health()
        self.assertEqual(health['jobs_last_hour'], 0)
        self.assertEqual(health['avg_latency_ms'], 0)
        self.assertEqual(health['p95_latency_ms'], 0)
        self.assertEqual(health['queue_depth'], 0)

    def test_five_done_jobs_computes_metrics(self):
        from job_queue import JobQueue
        conn = sqlite3.connect(self.db_path)
        now = time.time()
        # Insert 5 completed jobs with known latencies
        latencies_seconds = [0.1, 0.2, 0.3, 0.4, 0.5]
        for i, lat in enumerate(latencies_seconds):
            started = now - 60 + i  # Within last hour
            completed = started + lat
            conn.execute(
                'INSERT INTO index_jobs (repo_name, clone_url, before_sha, after_sha, ref, '
                'status, created_at, started_at, completed_at) '
                'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
                (f'repo{i}', 'url', 'aaa', 'bbb', 'refs/heads/main',
                 'done', started - 1, started, completed),
            )
        conn.commit()
        conn.close()

        queue = JobQueue(db_path=self.db_path)
        health = queue.get_pipeline_health()
        self.assertEqual(health['jobs_last_hour'], 5)
        # Average latency: (100+200+300+400+500)/5 = 300ms
        self.assertAlmostEqual(health['avg_latency_ms'], 300.0, delta=5.0)
        # p95 should be close to the highest latency
        self.assertGreater(health['p95_latency_ms'], 0)

    def test_queue_depth_counts_pending(self):
        from job_queue import JobQueue
        queue = JobQueue(db_path=self.db_path)
        # Enqueue 3 jobs (all pending)
        for i in range(3):
            queue.enqueue_job(f'repo{i}', 'url', 'aaa', f'bbb{i}', 'refs/heads/main')

        health = queue.get_pipeline_health()
        self.assertEqual(health['queue_depth'], 3)

    def test_old_jobs_excluded(self):
        from job_queue import JobQueue
        conn = sqlite3.connect(self.db_path)
        # Insert a job completed 2 hours ago (outside the 1-hour window)
        old_time = time.time() - 7200
        conn.execute(
            'INSERT INTO index_jobs (repo_name, clone_url, before_sha, after_sha, ref, '
            'status, created_at, started_at, completed_at) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
            ('old-repo', 'url', 'aaa', 'bbb', 'refs/heads/main',
             'done', old_time - 1, old_time, old_time + 0.1),
        )
        conn.commit()
        conn.close()

        queue = JobQueue(db_path=self.db_path)
        health = queue.get_pipeline_health()
        self.assertEqual(health['jobs_last_hour'], 0)


if __name__ == '__main__':
    unittest.main()
