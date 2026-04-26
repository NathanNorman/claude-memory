#!/usr/bin/env python3
"""
Polling fallback for webhook-driven codebase indexing.

Checks tracked repositories via `git ls-remote` and enqueues index jobs
when the remote HEAD SHA differs from the last indexed SHA. Designed as
a one-shot cron-compatible script.

Usage:
    python3 src/poll_repos.py

Configuration:
    - JSON file: ~/.claude-memory/webhook-config.json
      {"tracked_repos": [{"name": "my-repo", "url": "https://..."}]}
    - Env var: TRACKED_REPOS="name1=url1,name2=url2"
"""

import json
import logging
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from job_queue import JobQueue

log = logging.getLogger('webhook-pipeline')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [poll-repos] %(levelname)s %(message)s',
    stream=sys.stderr,
)

HOME = Path.home()
MEMORY_DIR = HOME / '.claude-memory'
DB_PATH = MEMORY_DIR / 'index' / 'memory.db'
CONFIG_PATH = MEMORY_DIR / 'webhook-config.json'

NULL_SHA = '0' * 40


def load_tracked_repos() -> list[dict]:
    """Load tracked repos from env var or config file.

    Returns list of {"name": str, "url": str} dicts.
    Env var format: TRACKED_REPOS="name1=url1,name2=url2"
    """
    # Env var takes precedence
    env_val = os.environ.get('TRACKED_REPOS')
    if env_val:
        repos = []
        for entry in env_val.split(','):
            entry = entry.strip()
            if '=' not in entry:
                continue
            name, url = entry.split('=', 1)
            repos.append({'name': name.strip(), 'url': url.strip()})
        return repos

    # Fall back to config file
    if CONFIG_PATH.exists():
        try:
            config = json.loads(CONFIG_PATH.read_text())
            return config.get('tracked_repos', [])
        except (json.JSONDecodeError, KeyError) as e:
            log.error('Failed to parse config file %s: %s', CONFIG_PATH, e)
            return []

    log.warning('No tracked repos configured (no env var, no config file at %s)', CONFIG_PATH)
    return []


def git_ls_remote_head(clone_url: str) -> str | None:
    """Get the HEAD SHA from a remote repo via git ls-remote.

    Returns the SHA string, or None if the command fails.
    """
    try:
        result = subprocess.run(
            ['git', 'ls-remote', clone_url, 'HEAD'],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            log.error('git ls-remote failed for %s: %s', clone_url, result.stderr.strip())
            return None
        # Output format: "<sha>\tHEAD"
        for line in result.stdout.strip().split('\n'):
            if line and '\t' in line:
                sha, ref = line.split('\t', 1)
                if ref == 'HEAD':
                    return sha.strip()
        return None
    except subprocess.TimeoutExpired:
        log.error('git ls-remote timed out for %s', clone_url)
        return None


def get_last_indexed_sha(codebase_name: str) -> str | None:
    """Get the most recent content_hash from codebase_meta as a proxy for last indexed state.

    Since we don't store the git SHA directly in codebase_meta, we look for
    the most recent indexed_at timestamp to determine if the codebase exists.
    Returns None if the codebase has never been indexed.
    """
    try:
        conn = sqlite3.connect(str(DB_PATH), timeout=5.0)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            'SELECT indexed_at FROM codebase_meta WHERE codebase = ? '
            'ORDER BY indexed_at DESC LIMIT 1',
            (codebase_name,),
        ).fetchone()
        conn.close()
        return row['indexed_at'] if row else None
    except Exception as e:
        log.error('Failed to query codebase_meta for %s: %s', codebase_name, e)
        return None


def get_stored_sha(codebase_name: str) -> str | None:
    """Get the last known remote SHA from the index_jobs table.

    Looks at the most recent completed job's after_sha for this repo.
    """
    try:
        conn = sqlite3.connect(str(DB_PATH), timeout=5.0)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT after_sha FROM index_jobs WHERE repo_name = ? AND status = 'done' "
            'ORDER BY completed_at DESC LIMIT 1',
            (codebase_name,),
        ).fetchone()
        conn.close()
        return row['after_sha'] if row else None
    except Exception as e:
        log.debug('No stored SHA for %s: %s', codebase_name, e)
        return None


def poll_all() -> int:
    """Check all tracked repos for SHA drift and enqueue jobs.

    Returns the number of jobs enqueued.
    """
    repos = load_tracked_repos()
    if not repos:
        log.info('No tracked repos to poll')
        return 0

    queue = JobQueue()
    jobs_enqueued = 0

    for repo in repos:
        name = repo.get('name', '')
        url = repo.get('url', '')
        if not name or not url:
            log.warning('Skipping invalid repo entry: %s', repo)
            continue

        remote_sha = git_ls_remote_head(url)
        if not remote_sha:
            log.warning('Could not get remote SHA for %s', name)
            continue

        # Check if we have a stored SHA from previous jobs
        stored_sha = get_stored_sha(name)

        if stored_sha and stored_sha == remote_sha:
            log.debug('No drift for %s (SHA=%s)', name, remote_sha[:8])
            continue

        # Check if the codebase has ever been indexed
        last_indexed = get_last_indexed_sha(name)

        if stored_sha:
            before_sha = stored_sha
        elif last_indexed:
            # Codebase exists but no job history -- use null SHA to trigger full re-index
            before_sha = NULL_SHA
        else:
            # Never indexed -- full index
            before_sha = NULL_SHA

        log.info(
            'SHA drift detected for %s: %s -> %s',
            name,
            before_sha[:8] if before_sha != NULL_SHA else '(new)',
            remote_sha[:8],
        )
        queue.enqueue_job(
            repo_name=name,
            clone_url=url,
            before_sha=before_sha,
            after_sha=remote_sha,
            ref='refs/heads/main',
        )
        jobs_enqueued += 1

    log.info('Polling complete: %d jobs enqueued out of %d repos checked', jobs_enqueued, len(repos))
    return jobs_enqueued


def main():
    """Entry point for cron execution."""
    try:
        poll_all()
    except Exception as e:
        log.error('Poll failed: %s', e, exc_info=True)
        sys.exit(1)


if __name__ == '__main__':
    main()
