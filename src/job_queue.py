"""
SQLite-backed job queue for webhook-driven codebase indexing.

Provides enqueue, claim, mark_done, and mark_failed operations with
deduplication of pending jobs for the same repo. Uses WAL mode and
BEGIN IMMEDIATE for safe concurrent access from webhook + worker threads.
"""

import logging
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

log = logging.getLogger('webhook-pipeline')

HOME = Path.home()
MEMORY_DIR = HOME / '.claude-memory'
DB_PATH = MEMORY_DIR / 'index' / 'memory.db'


@dataclass
class IndexJob:
    """A single index job from the queue."""
    id: int
    repo_name: str
    clone_url: str
    before_sha: str
    after_sha: str
    ref: str
    status: str
    error: Optional[str]
    created_at: float
    started_at: Optional[float]
    completed_at: Optional[float]
    timing: Optional[str] = None  # JSON with per-stage timestamps


class JobQueue:
    """SQLite-backed job queue with deduplication and atomic claim."""

    def __init__(self, db_path: str | Path | None = None):
        self.db_path = str(db_path or DB_PATH)
        self._ensure_table()

    def _get_conn(self) -> sqlite3.Connection:
        """Get a new connection (connections should not be shared across threads)."""
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.execute('PRAGMA busy_timeout = 5000')
        conn.execute('PRAGMA journal_mode = WAL')
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_table(self) -> None:
        """Create the index_jobs table if it doesn't exist."""
        conn = self._get_conn()
        try:
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
            # Idempotent migration: add timing column if table already exists
            try:
                conn.execute('ALTER TABLE index_jobs ADD COLUMN timing TEXT')
            except sqlite3.OperationalError:
                pass  # Column already exists
            conn.execute('''
                CREATE INDEX IF NOT EXISTS idx_jobs_status_created
                ON index_jobs (status, created_at)
            ''')
            conn.commit()
        finally:
            conn.close()

    def enqueue_job(
        self,
        repo_name: str,
        clone_url: str,
        before_sha: str,
        after_sha: str,
        ref: str,
    ) -> int:
        """Enqueue an index job with deduplication.

        If a pending job already exists for the same repo_name, updates
        its after_sha to the new value (coalescing rapid pushes).
        Returns the job ID.
        """
        conn = self._get_conn()
        try:
            # Check for existing pending job for this repo
            existing = conn.execute(
                "SELECT id FROM index_jobs WHERE repo_name = ? AND status = 'pending'",
                (repo_name,),
            ).fetchone()

            if existing:
                # Coalesce: update the existing job's after_sha
                conn.execute(
                    'UPDATE index_jobs SET after_sha = ?, clone_url = ? WHERE id = ?',
                    (after_sha, clone_url, existing['id']),
                )
                conn.commit()
                log.info(
                    'Coalesced job for %s (updated after_sha to %s, job_id=%d)',
                    repo_name, after_sha[:8], existing['id'],
                )
                return existing['id']

            # Insert new job
            now = time.time()
            cursor = conn.execute(
                'INSERT INTO index_jobs (repo_name, clone_url, before_sha, after_sha, ref, status, created_at) '
                'VALUES (?, ?, ?, ?, ?, ?, ?)',
                (repo_name, clone_url, before_sha, after_sha, ref, 'pending', now),
            )
            conn.commit()
            job_id = cursor.lastrowid
            log.info(
                'Enqueued job for %s (%s..%s, ref=%s, job_id=%d)',
                repo_name, before_sha[:8], after_sha[:8], ref, job_id,
            )
            return job_id
        finally:
            conn.close()

    def claim_next_job(self) -> Optional[IndexJob]:
        """Atomically claim the oldest pending job.

        Uses BEGIN IMMEDIATE to prevent race conditions.
        Returns the claimed job, or None if no pending jobs exist.
        """
        conn = self._get_conn()
        try:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute(
                "SELECT * FROM index_jobs WHERE status = 'pending' "
                'ORDER BY created_at ASC LIMIT 1',
            ).fetchone()

            if not row:
                conn.execute('COMMIT')
                return None

            now = time.time()
            conn.execute(
                "UPDATE index_jobs SET status = 'processing', started_at = ? WHERE id = ?",
                (now, row['id']),
            )
            conn.execute('COMMIT')

            return IndexJob(
                id=row['id'],
                repo_name=row['repo_name'],
                clone_url=row['clone_url'],
                before_sha=row['before_sha'],
                after_sha=row['after_sha'],
                ref=row['ref'],
                status='processing',
                error=None,
                created_at=row['created_at'],
                started_at=now,
                completed_at=None,
            )
        except Exception:
            try:
                conn.execute('ROLLBACK')
            except Exception:
                pass
            raise
        finally:
            conn.close()

    def mark_done(self, job_id: int, timing: Optional[str] = None) -> None:
        """Mark a job as successfully completed with optional timing data."""
        conn = self._get_conn()
        try:
            now = time.time()
            conn.execute(
                "UPDATE index_jobs SET status = 'done', completed_at = ?, timing = ? WHERE id = ?",
                (now, timing, job_id),
            )
            conn.commit()
            log.info('Job %d marked done', job_id)
        finally:
            conn.close()

    def mark_failed(self, job_id: int, error: str) -> None:
        """Mark a job as failed with an error message."""
        conn = self._get_conn()
        try:
            now = time.time()
            conn.execute(
                "UPDATE index_jobs SET status = 'failed', error = ?, completed_at = ? WHERE id = ?",
                (error, now, job_id),
            )
            conn.commit()
            log.warning('Job %d marked failed: %s', job_id, error)
        finally:
            conn.close()

    def get_pipeline_health(self) -> dict:
        """Get pipeline health metrics for the last hour.

        Returns: jobs_last_hour, avg_latency_ms, p95_latency_ms, queue_depth.
        """
        conn = self._get_conn()
        try:
            now = time.time()
            one_hour_ago = now - 3600

            # Jobs completed in last hour
            done_rows = conn.execute(
                "SELECT started_at, completed_at FROM index_jobs "
                "WHERE status = 'done' AND completed_at >= ?",
                (one_hour_ago,),
            ).fetchall()

            latencies = []
            for row in done_rows:
                if row['started_at'] and row['completed_at']:
                    latency_ms = (row['completed_at'] - row['started_at']) * 1000
                    latencies.append(latency_ms)

            # Queue depth
            pending = conn.execute(
                "SELECT COUNT(*) as cnt FROM index_jobs WHERE status = 'pending'"
            ).fetchone()['cnt']

            result = {
                'jobs_last_hour': len(done_rows),
                'queue_depth': pending,
            }

            if latencies:
                latencies.sort()
                result['avg_latency_ms'] = round(sum(latencies) / len(latencies), 1)
                p95_idx = int(len(latencies) * 0.95)
                result['p95_latency_ms'] = round(latencies[min(p95_idx, len(latencies) - 1)], 1)
            else:
                result['avg_latency_ms'] = 0
                result['p95_latency_ms'] = 0

            return result
        finally:
            conn.close()
