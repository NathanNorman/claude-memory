#!/Users/nathan.norman/.pyenv/versions/3.12.11/bin/python3
"""LLM client helpers for the iterative summary refinement system."""

import os
import random
import subprocess
import sys
import time


def call_llm(prompt: str, model: str = "haiku", timeout: int = 90) -> str:
    """Invoke the claude CLI with the given prompt and return the response.

    Strips CLAUDECODE from env to prevent nested-session rejection.
    """
    env = os.environ.copy()
    env.pop("CLAUDECODE", None)

    result = subprocess.run(
        ["claude", "-p", prompt, "--model", model, "--tools", "", "--no-session-persistence"],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"claude CLI failed (exit {result.returncode}): {result.stderr.strip()}"
        )

    return result.stdout.strip()


def retry_with_backoff(func, max_retries: int = 3, initial_delay: float = 2.0):
    """Call func() with retry logic and exponential backoff for rate limits.

    Rate-limit errors (429 or 'rate limit' in message) get exponential
    backoff with jitter. Other errors get fixed-delay retries.
    After max_retries exhausted, re-raises the last exception.
    """
    last_exc = None

    for attempt in range(max_retries):
        try:
            return func()
        except Exception as exc:
            last_exc = exc
            err_str = str(exc).lower()
            is_rate_limit = "429" in err_str or "rate limit" in err_str

            remaining = max_retries - attempt - 1
            if remaining == 0:
                break

            if is_rate_limit:
                delay = initial_delay * (2 ** attempt) + random.uniform(0, 1)
                print(
                    f"[summary-llm] Rate limit hit, retrying in {delay:.1f}s "
                    f"(attempt {attempt + 1}/{max_retries})",
                    file=sys.stderr,
                )
            else:
                delay = initial_delay + random.uniform(0, 0.5)
                print(
                    f"[summary-llm] Error: {exc}, retrying in {delay:.1f}s "
                    f"(attempt {attempt + 1}/{max_retries})",
                    file=sys.stderr,
                )

            time.sleep(delay)

    raise last_exc
