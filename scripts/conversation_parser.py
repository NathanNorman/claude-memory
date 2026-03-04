"""
Python port of ~/claude-memory/src/conversation-parser.ts
Parses Claude Code conversation JSONL archives into structured text for graph ingestion.
"""

import json
import os
import sys
from dataclasses import dataclass, field
from typing import Optional

MAX_FILE_BYTES = 20 * 1024 * 1024  # 20MB
MAX_OUTPUT_BYTES = 50 * 1024  # 50KB cap per conversation

SKIP_TYPES = {'progress', 'queue-operation', 'file-history-snapshot'}
SKIP_BLOCK_TYPES = {'tool_use', 'tool_result', 'thinking'}


@dataclass
class ConversationExchange:
    user_message: str
    assistant_message: str
    timestamp: Optional[str] = None
    tool_names: list[str] = field(default_factory=list)


@dataclass
class ParseResult:
    exchanges: list[ConversationExchange]
    session_id: Optional[str] = None
    cwd: Optional[str] = None
    timestamp: Optional[str] = None


def extract_message_text(message: Optional[dict]) -> Optional[str]:
    """Extract plain text from a message's content field."""
    if not message or 'content' not in message:
        return None

    content = message['content']

    # Simple string content (user messages)
    if isinstance(content, str):
        trimmed = content.strip()
        return trimmed if trimmed else None

    # Array of content blocks (assistant messages)
    if isinstance(content, list):
        text_parts = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get('type') in SKIP_BLOCK_TYPES:
                continue
            if block.get('type') == 'text' and isinstance(block.get('text'), str):
                trimmed = block['text'].strip()
                if trimmed:
                    text_parts.append(trimmed)
        return '\n\n'.join(text_parts) if text_parts else None

    return None


def extract_tool_names(content) -> list[str]:
    """Extract tool_use block names from assistant message content."""
    if not content or isinstance(content, str) or not isinstance(content, list):
        return []
    tools = []
    for block in content:
        if isinstance(block, dict) and block.get('type') == 'tool_use':
            name = block.get('name')
            if name:
                tools.append(name)
    return tools


def parse_conversation_jsonl(filepath: str) -> Optional[ParseResult]:
    """
    Parse a conversation JSONL file into structured exchanges.
    Returns ParseResult or None if file is empty/unparseable.
    """
    try:
        size = os.path.getsize(filepath)
    except OSError:
        return None

    if size > MAX_FILE_BYTES or size == 0:
        return None

    session_id = None
    cwd = None
    timestamp = None

    exchanges: list[ConversationExchange] = []

    # Accumulator for current exchange
    current_user_msg = ''
    current_assistant_parts: list[str] = []
    current_tool_names: list[str] = []
    current_timestamp: Optional[str] = None
    has_user = False

    def finalize_exchange():
        nonlocal current_user_msg, current_assistant_parts, current_tool_names
        nonlocal current_timestamp, has_user

        if not has_user:
            return
        user_msg = current_user_msg.strip()
        if user_msg:
            assistant_msg = '\n\n'.join(current_assistant_parts).strip()
            exchanges.append(ConversationExchange(
                user_message=user_msg,
                assistant_message=assistant_msg,
                timestamp=current_timestamp,
                tool_names=current_tool_names,
            ))
        # Reset
        current_user_msg = ''
        current_assistant_parts = []
        current_tool_names = []
        current_timestamp = None
        has_user = False

    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            for line in f:
                trimmed = line.strip()
                if not trimmed:
                    continue

                try:
                    rec = json.loads(trimmed)
                except json.JSONDecodeError:
                    continue

                rec_type = rec.get('type')
                if not rec_type or rec_type in SKIP_TYPES:
                    continue

                # Extract metadata
                if not session_id and rec.get('sessionId'):
                    session_id = rec['sessionId']
                if not cwd and rec.get('cwd'):
                    cwd = rec['cwd']
                if not timestamp and rec.get('timestamp'):
                    timestamp = rec['timestamp']

                if rec_type == 'user':
                    finalize_exchange()
                    current_user_msg = extract_message_text(rec.get('message')) or ''
                    current_timestamp = rec.get('timestamp')
                    has_user = True
                    continue

                if rec_type == 'assistant':
                    text = extract_message_text(rec.get('message'))
                    if text:
                        current_assistant_parts.append(text)
                    # Extract tool names
                    msg = rec.get('message', {})
                    if msg.get('content'):
                        tools = extract_tool_names(msg['content'])
                        current_tool_names.extend(tools)
                    continue

                if rec_type == 'summary':
                    text = extract_message_text(rec.get('message'))
                    if text:
                        current_assistant_parts.append(text)
                    continue

        # Finalize last exchange
        finalize_exchange()

    except Exception as e:
        print(f"WARNING: Failed to parse {filepath}: {e}", file=sys.stderr)
        return None

    if not exchanges:
        return None

    return ParseResult(
        exchanges=exchanges,
        session_id=session_id,
        cwd=cwd,
        timestamp=timestamp,
    )


def format_for_graphiti(result: ParseResult, project_dir: str) -> str:
    """
    Format parsed conversation into text suitable for Graphiti episode ingestion.
    Caps output at MAX_OUTPUT_BYTES.
    """
    parts = []

    # Header
    header = f"Session: {result.session_id or 'unknown'}"
    header += f"\nProject: {project_dir}"
    if result.cwd:
        header += f" | CWD: {result.cwd}"
    if result.timestamp:
        header += f"\nDate: {result.timestamp[:10]}"
    parts.append(header)

    # Exchanges
    total_size = len(header)
    for ex in result.exchanges:
        exchange_text = f"\n[User]: {ex.user_message}"
        if ex.assistant_message:
            exchange_text += f"\n[Assistant]: {ex.assistant_message}"
        if ex.tool_names:
            unique_tools = list(dict.fromkeys(ex.tool_names))  # dedup, preserve order
            exchange_text += f"\n[Tools used]: {', '.join(unique_tools)}"

        total_size += len(exchange_text.encode('utf-8'))
        if total_size > MAX_OUTPUT_BYTES:
            parts.append("\n[... truncated due to size limit ...]")
            break
        parts.append(exchange_text)

    return '\n'.join(parts)
