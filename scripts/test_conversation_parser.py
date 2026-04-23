#!/usr/bin/env python3
"""
Smoke tests for conversation_parser.py.

Run: python3 ~/.claude-memory/scripts/test_conversation_parser.py
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

# Add scripts dir
sys.path.insert(0, str(Path(__file__).parent))

from conversation_parser import (
    extract_message_text,
    parse_conversation_jsonl,
    ParseResult,
    ConversationExchange,
)


class TestExtractMessageTextString(unittest.TestCase):
    """Test extract_message_text with simple string content."""

    def test_plain_string(self):
        msg = {'content': 'Hello world'}
        self.assertEqual(extract_message_text(msg), 'Hello world')

    def test_whitespace_only(self):
        msg = {'content': '   \n  '}
        self.assertIsNone(extract_message_text(msg))

    def test_no_content_key(self):
        self.assertIsNone(extract_message_text({}))
        self.assertIsNone(extract_message_text(None))


class TestExtractMessageTextBlocks(unittest.TestCase):
    """Test extract_message_text with array content, tool_use blocks skipped."""

    def test_text_blocks_joined(self):
        msg = {'content': [
            {'type': 'text', 'text': 'First part'},
            {'type': 'text', 'text': 'Second part'},
        ]}
        self.assertEqual(extract_message_text(msg), 'First part\n\nSecond part')

    def test_tool_use_blocks_skipped(self):
        msg = {'content': [
            {'type': 'text', 'text': 'Here is the answer'},
            {'type': 'tool_use', 'name': 'Read', 'input': {'file': 'test.py'}},
            {'type': 'text', 'text': 'More text'},
        ]}
        result = extract_message_text(msg)
        self.assertEqual(result, 'Here is the answer\n\nMore text')
        self.assertNotIn('Read', result)

    def test_thinking_blocks_skipped(self):
        msg = {'content': [
            {'type': 'thinking', 'text': 'Let me think...'},
            {'type': 'text', 'text': 'The answer is 42'},
        ]}
        self.assertEqual(extract_message_text(msg), 'The answer is 42')


class TestParseMinimalConversation(unittest.TestCase):
    """Test round-trip JSONL -> ParseResult."""

    def test_round_trip(self):
        records = [
            {'type': 'user', 'sessionId': 'abc-123', 'cwd': '/home/test',
             'timestamp': '2026-01-15T10:00:00Z',
             'message': {'content': 'Fix the bug'}},
            {'type': 'assistant',
             'message': {'content': [{'type': 'text', 'text': 'I fixed it.'}]}},
        ]
        with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False) as f:
            for r in records:
                f.write(json.dumps(r) + '\n')
            tmp_path = f.name

        try:
            result = parse_conversation_jsonl(tmp_path)
            self.assertIsNotNone(result)
            self.assertEqual(result.session_id, 'abc-123')
            self.assertEqual(result.cwd, '/home/test')
            self.assertEqual(result.timestamp, '2026-01-15T10:00:00Z')
            self.assertEqual(len(result.exchanges), 1)
            self.assertEqual(result.exchanges[0].user_message, 'Fix the bug')
            self.assertEqual(result.exchanges[0].assistant_message, 'I fixed it.')
        finally:
            os.unlink(tmp_path)

    def test_empty_file_returns_none(self):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False) as f:
            tmp_path = f.name
        try:
            result = parse_conversation_jsonl(tmp_path)
            self.assertIsNone(result)
        finally:
            os.unlink(tmp_path)


if __name__ == '__main__':
    unittest.main()
