"""
Bare git mirror management for webhook-driven codebase indexing.

Manages bare clones of tracked repositories. Uses `git clone --bare` for
initial cloning and `git fetch` for updates. Provides diff and file-read
operations directly from the bare repo (no working copy needed).
"""

import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

log = logging.getLogger('webhook-pipeline')

MIRROR_DIR = Path(
    os.environ.get('MIRROR_DIR', str(Path.home() / '.claude-memory' / 'mirrors'))
)


def _mirror_path(repo_name: str) -> Path:
    """Return the path for a repo's bare mirror."""
    return MIRROR_DIR / f'{repo_name}.git'


def _run_git(args: list[str], cwd: str | None = None, timeout: int = 120) -> subprocess.CompletedProcess:
    """Run a git command and return the result."""
    cmd = ['git'] + args
    log.debug('Running: %s (cwd=%s)', ' '.join(cmd), cwd)
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=cwd,
        timeout=timeout,
    )
    if result.returncode != 0:
        log.debug('git stderr: %s', result.stderr.strip())
    return result


def ensure_mirror(repo_name: str, clone_url: str) -> Path:
    """Ensure a bare mirror exists for the given repo.

    If no mirror exists, creates one via `git clone --bare`.
    If a mirror already exists, runs `git fetch` to update all branches.
    Returns the path to the mirror directory.
    """
    mirror = _mirror_path(repo_name)

    if mirror.is_dir():
        # Mirror exists -- fetch updates
        log.info('Fetching updates for mirror: %s', repo_name)
        result = _run_git(
            ['fetch', 'origin', '+refs/heads/*:refs/heads/*'],
            cwd=str(mirror),
        )
        if result.returncode != 0:
            raise RuntimeError(
                f'git fetch failed for {repo_name}: {result.stderr.strip()}'
            )
        return mirror

    # Create new bare clone
    MIRROR_DIR.mkdir(parents=True, exist_ok=True)
    log.info('Creating bare mirror for %s at %s', repo_name, mirror)
    result = _run_git(
        ['clone', '--bare', clone_url, str(mirror)],
        timeout=300,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f'git clone --bare failed for {repo_name}: {result.stderr.strip()}'
        )
    return mirror


def git_diff_files(mirror_path: Path, before_sha: str, after_sha: str) -> list[tuple[str, str]]:
    """Return a list of (status, filepath) tuples for changes between two SHAs.

    Status codes: A (added), M (modified), D (deleted), R (renamed), etc.
    Raises RuntimeError if the diff fails (e.g., unrelated SHAs from a force push).
    """
    result = _run_git(
        ['diff', '--name-status', f'{before_sha}..{after_sha}'],
        cwd=str(mirror_path),
    )
    if result.returncode != 0:
        raise RuntimeError(
            f'git diff failed ({before_sha}..{after_sha}): {result.stderr.strip()}'
        )

    changes = []
    for line in result.stdout.strip().split('\n'):
        if not line:
            continue
        parts = line.split('\t', 1)
        if len(parts) == 2:
            status, filepath = parts
            # Handle rename status (R100\toldname\tnewname)
            if status.startswith('R'):
                rename_parts = filepath.split('\t')
                if len(rename_parts) == 2:
                    # Treat rename as delete old + add new
                    changes.append(('D', rename_parts[0]))
                    changes.append(('A', rename_parts[1]))
                continue
            changes.append((status[0], filepath))  # Take first char (M, A, D, etc.)
    return changes


def git_show_file(mirror_path: Path, sha: str, filepath: str) -> str:
    """Return file content at a specific SHA as a string.

    Raises RuntimeError if the file does not exist at the given SHA.
    """
    result = _run_git(
        ['show', f'{sha}:{filepath}'],
        cwd=str(mirror_path),
    )
    if result.returncode != 0:
        raise RuntimeError(
            f'git show failed for {filepath} at {sha}: {result.stderr.strip()}'
        )
    return result.stdout


def git_ls_tree(mirror_path: Path, sha: str) -> list[str]:
    """List all file paths at a specific SHA (for full re-index).

    Returns a list of file paths (relative to repo root).
    """
    result = _run_git(
        ['ls-tree', '-r', '--name-only', sha],
        cwd=str(mirror_path),
    )
    if result.returncode != 0:
        raise RuntimeError(
            f'git ls-tree failed at {sha}: {result.stderr.strip()}'
        )
    return [line for line in result.stdout.strip().split('\n') if line]


def get_head_sha(mirror_path: Path, ref: str = 'HEAD') -> str:
    """Get the SHA of HEAD (or a specific ref) in the mirror."""
    result = _run_git(
        ['rev-parse', ref],
        cwd=str(mirror_path),
    )
    if result.returncode != 0:
        raise RuntimeError(
            f'git rev-parse {ref} failed: {result.stderr.strip()}'
        )
    return result.stdout.strip()


def cleanup_old_mirrors(max_age_days: int = 30) -> list[str]:
    """Remove mirrors not fetched within max_age_days.

    Uses the mtime of the mirror's packed-refs or HEAD file as a proxy
    for last-fetch time. Returns list of removed repo names.
    """
    if not MIRROR_DIR.is_dir():
        return []

    cutoff = time.time() - (max_age_days * 86400)
    removed = []

    for entry in MIRROR_DIR.iterdir():
        if not entry.is_dir() or not entry.name.endswith('.git'):
            continue

        # Check mtime of packed-refs or HEAD as proxy for last fetch
        indicator = entry / 'packed-refs'
        if not indicator.exists():
            indicator = entry / 'HEAD'
        if not indicator.exists():
            continue

        if indicator.stat().st_mtime < cutoff:
            repo_name = entry.name[:-4]  # strip .git
            log.info('Removing stale mirror: %s (not fetched in %d days)', repo_name, max_age_days)
            shutil.rmtree(entry)
            removed.append(repo_name)

    return removed
