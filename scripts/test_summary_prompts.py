#!/Users/nathan.norman/.pyenv/versions/3.12.11/bin/python3
"""Unit tests for summary_prompts.parse_judge_response()."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from summary_prompts import parse_judge_response


def test_valid_score_and_feedback():
    text = "SCORE: 8.5/10\nFEEDBACK: Good coverage of decisions."
    score, feedback = parse_judge_response(text)
    assert score == 8.5, f"Expected 8.5, got {score}"
    assert feedback == "Good coverage of decisions.", f"Got: {feedback}"


def test_integer_score():
    text = "SCORE: 7/10\nFEEDBACK: Missing file paths."
    score, feedback = parse_judge_response(text)
    assert score == 7.0, f"Expected 7.0, got {score}"
    assert "Missing file paths" in feedback


def test_case_insensitive():
    text = "score: 9/10\nfeedback: Excellent summary."
    score, feedback = parse_judge_response(text)
    assert score == 9.0, f"Expected 9.0, got {score}"
    assert "Excellent" in feedback


def test_missing_score_returns_zero():
    text = "This response has no score format at all."
    score, feedback = parse_judge_response(text)
    assert score == 0.0, f"Expected 0.0, got {score}"
    assert feedback == text.strip()


def test_missing_feedback_returns_full_text():
    text = "SCORE: 6/10\nSome other content without the feedback marker."
    score, feedback = parse_judge_response(text)
    assert score == 6.0, f"Expected 6.0, got {score}"
    assert feedback == text.strip()


def test_empty_response():
    score, feedback = parse_judge_response("")
    assert score == 0.0, f"Expected 0.0, got {score}"
    assert feedback == ""


def test_multiline_feedback():
    text = (
        "SCORE: 5/10\n"
        "FEEDBACK: Missing decisions about RRF vs weighted merge.\n"
        "Also missing file paths for indexer.ts changes.\n"
        "Structure is process-first, should lead with outcomes."
    )
    score, feedback = parse_judge_response(text)
    assert score == 5.0
    assert "RRF" in feedback
    assert "indexer.ts" in feedback
    assert "outcomes" in feedback


def test_perfect_score():
    text = "SCORE: 10/10\nFEEDBACK: Comprehensive coverage."
    score, feedback = parse_judge_response(text)
    assert score == 10.0


def test_zero_score():
    text = "SCORE: 0/10\nFEEDBACK: Summary is completely empty."
    score, feedback = parse_judge_response(text)
    assert score == 0.0
    assert "empty" in feedback


if __name__ == '__main__':
    tests = [
        test_valid_score_and_feedback,
        test_integer_score,
        test_case_insensitive,
        test_missing_score_returns_zero,
        test_missing_feedback_returns_full_text,
        test_empty_response,
        test_multiline_feedback,
        test_perfect_score,
        test_zero_score,
    ]
    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            passed += 1
            print(f"  PASS: {t.__name__}")
        except Exception as e:
            failed += 1
            print(f"  FAIL: {t.__name__}: {e}")
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
