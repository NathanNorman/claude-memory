#!/Users/nathan.norman/.pyenv/versions/3.12.11/bin/python3
"""Summarize-judge-refine loop for session summaries."""

import sys
import time

from summary_prompts import (
    SUMMARIZER_SYSTEM,
    JUDGE_SYSTEM,
    JUDGE_USER_TEMPLATE,
    REFINER_SYSTEM,
    REFINER_USER_TEMPLATE,
    parse_judge_response,
)
from summary_llm import call_llm, retry_with_backoff


def generate_summary(transcript: str, model: str = "haiku") -> str:
    """Generate an initial summary from a transcript."""
    prompt = SUMMARIZER_SYSTEM + "\n\n---\n\n" + transcript
    return retry_with_backoff(lambda: call_llm(prompt, model))


def judge_summary(
    transcript: str, summary: str, model: str = "haiku"
) -> tuple[float, str]:
    """Judge a summary against its source transcript. Returns (score, feedback)."""
    user_content = JUDGE_USER_TEMPLATE.format(transcript=transcript, summary=summary)
    prompt = JUDGE_SYSTEM + "\n\n---\n\n" + user_content
    response = retry_with_backoff(lambda: call_llm(prompt, model))
    return parse_judge_response(response)


def refine_summary(
    transcript: str, summary: str, feedback: str, model: str = "haiku"
) -> str:
    """Refine a summary based on judge feedback."""
    user_content = REFINER_USER_TEMPLATE.format(
        transcript=transcript, summary=summary, feedback=feedback
    )
    prompt = REFINER_SYSTEM + "\n\n---\n\n" + user_content
    return retry_with_backoff(lambda: call_llm(prompt, model))


def summarize_with_refinement(
    transcript: str,
    model: str = "haiku",
    threshold: float = 8.0,
    max_iter: int = 2,
) -> dict:
    """Orchestrate the full summarize-judge-refine loop.

    Returns dict with keys: summary, score, iterations, refined, elapsed_seconds.
    """
    start = time.time()

    summary = generate_summary(transcript, model)
    score, feedback = judge_summary(transcript, summary, model)
    iterations = 1
    refined = False

    if score < threshold and max_iter > 1:
        for _ in range(max_iter - 1):
            summary = refine_summary(transcript, summary, feedback, model)
            refined = True
            score, feedback = judge_summary(transcript, summary, model)
            iterations += 1
            if score >= threshold:
                break

    elapsed = time.time() - start

    return {
        "summary": summary,
        "score": score,
        "iterations": iterations,
        "refined": refined,
        "elapsed_seconds": elapsed,
    }
