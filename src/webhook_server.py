"""
FastAPI webhook receiver for GitHub push events.

Accepts push webhooks, verifies HMAC-SHA256 signatures, filters by branch,
and enqueues index jobs. Runs the background index worker in a dedicated thread.

Single-process architecture: FastAPI (uvicorn async) + worker thread.
"""

import hashlib
import hmac
import logging
import os
import subprocess
import sys
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

# Add src/ to path
sys.path.insert(0, str(Path(__file__).parent))

from job_queue import JobQueue
from index_worker import worker_loop

log = logging.getLogger('webhook-pipeline')

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [webhook-pipeline] %(levelname)s %(message)s',
    stream=sys.stderr,
)

# Global state
_queue: Optional[JobQueue] = None
_worker_thread: Optional[threading.Thread] = None
_stop_event = threading.Event()


def _get_webhook_secret() -> Optional[str]:
    """Retrieve the webhook secret.

    Tries macOS Keychain first, then falls back to WEBHOOK_SECRET env var.
    """
    # Try macOS Keychain
    try:
        result = subprocess.run(
            [
                'security', 'find-generic-password',
                '-a', os.getlogin(),
                '-s', 'webhook-github-secret',
                '-w',
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass

    # Fall back to environment variable
    return os.environ.get('WEBHOOK_SECRET')


def verify_signature(payload: bytes, signature: str, secret: str) -> bool:
    """Verify HMAC-SHA256 signature from GitHub."""
    if not signature.startswith('sha256='):
        return False
    expected = 'sha256=' + hmac.new(
        secret.encode(), payload, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(signature, expected)


# Branch filter: only index main/master
ALLOWED_REFS = {'refs/heads/main', 'refs/heads/master'}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown lifecycle for the FastAPI app."""
    global _queue, _worker_thread, _stop_event

    _stop_event.clear()
    _queue = JobQueue()

    # Start worker thread
    _worker_thread = threading.Thread(
        target=worker_loop,
        kwargs={
            'queue': _queue,
            'stop_event': _stop_event,
        },
        daemon=True,
        name='index-worker',
    )
    _worker_thread.start()
    log.info('Webhook server started, worker thread running')

    yield

    # Shutdown
    log.info('Shutting down worker thread...')
    _stop_event.set()
    if _worker_thread and _worker_thread.is_alive():
        _worker_thread.join(timeout=30)
    log.info('Webhook server stopped')


app = FastAPI(title='claude-memory webhook receiver', lifespan=lifespan)


@app.get('/health')
async def health():
    """Health check endpoint."""
    return {'status': 'ok'}


@app.post('/webhook')
async def webhook(
    request: Request,
    x_hub_signature_256: Optional[str] = Header(None),
    x_github_event: Optional[str] = Header(None),
):
    """Handle GitHub webhook push events.

    1. Verify HMAC-SHA256 signature
    2. Filter by event type (only process 'push')
    3. Filter by branch (only main/master)
    4. Enqueue index job
    5. Return 202 Accepted
    """
    # Read raw body for signature verification
    body = await request.body()

    # Verify signature
    secret = _get_webhook_secret()
    if secret:
        if not x_hub_signature_256:
            raise HTTPException(status_code=401, detail='Missing signature header')
        if not verify_signature(body, x_hub_signature_256, secret):
            raise HTTPException(status_code=401, detail='Invalid signature')
    else:
        log.warning('No webhook secret configured -- skipping signature verification')

    # Check event type
    event_type = x_github_event or ''
    if event_type != 'push':
        return JSONResponse(
            status_code=200,
            content={'message': f'Ignored event type: {event_type}'},
        )

    # Parse payload
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail='Invalid JSON payload')

    ref = payload.get('ref', '')
    before_sha = payload.get('before', '')
    after_sha = payload.get('after', '')
    repo = payload.get('repository', {})
    repo_name = repo.get('full_name', '') or repo.get('name', '')
    clone_url = repo.get('clone_url', '') or repo.get('ssh_url', '')

    # Branch filter
    if ref not in ALLOWED_REFS:
        return JSONResponse(
            status_code=200,
            content={'message': f'Skipped branch: {ref}'},
        )

    if not repo_name or not clone_url:
        raise HTTPException(status_code=400, detail='Missing repository info in payload')

    # Normalize repo_name: use the last component (e.g., "owner/repo" -> "repo")
    # or keep full_name for uniqueness
    safe_name = repo_name.replace('/', '-')

    # Enqueue job
    job_id = _queue.enqueue_job(
        repo_name=safe_name,
        clone_url=clone_url,
        before_sha=before_sha,
        after_sha=after_sha,
        ref=ref,
    )

    return JSONResponse(
        status_code=202,
        content={
            'message': 'Job enqueued',
            'job_id': job_id,
            'repo': safe_name,
        },
    )
