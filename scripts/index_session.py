#!/usr/bin/env python3
"""
Index a single conversation session JSONL into the unified-memory DB.

Parses exchanges, filters noise, chunks, inserts into SQLite + FTS5.
Embeddings are left NULL — the MCP server will embed them lazily on
next index reload (warmup or search).

Usage:
    python3 scripts/index_session.py /path/to/session.jsonl
"""

import hashlib
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

# Add scripts/ to path for conversation_parser
sys.path.insert(0, str(Path(__file__).parent))

from conversation_parser import parse_conversation_jsonl

DB_PATH = Path.home() / '.claude-memory' / 'index' / 'memory.db'
MAX_CHUNK_CHARS = 1600

# Noise filters
NOISE_PREFIXES = ['Edit operation feedback', 'Write operation feedback']
NOISE_NO_ASSISTANT = [
    'Base directory for this skill',
    'local-command-caveat',
    'PreToolUse',
]


def is_noise(user_msg: str, assistant_msg: str) -> bool:
    for prefix in NOISE_PREFIXES:
        if user_msg.startswith(prefix):
            return True
    if not assistant_msg.strip():
        for pattern in NOISE_NO_ASSISTANT:
            if pattern in user_msg:
                return True
    return False


def derive_index_path(session_file: Path) -> tuple[str, str]:
    """Derive (index_path, project) from the session file location."""
    parts = session_file.parts
    try:
        proj_idx = parts.index('projects')
        project = parts[proj_idx + 1]
    except (ValueError, IndexError):
        project = session_file.parent.name
    return f'conversations/{project}/{session_file.name}', project


def main():
    if len(sys.argv) < 2:
        print('Usage: index_session.py <path-to-session.jsonl>', file=sys.stderr)
        sys.exit(1)

    session_path = sys.argv[1]
    session_file = Path(session_path)

    if not session_file.exists():
        sys.exit(0)  # Silently exit if file already deleted
    if not session_file.suffix == '.jsonl':
        sys.exit(0)

    index_path, project = derive_index_path(session_file)

    # Connect to DB
    if not DB_PATH.exists():
        print(f'DB not found: {DB_PATH}', file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(str(DB_PATH), timeout=10.0)
    conn.execute('PRAGMA busy_timeout = 5000')
    conn.execute('PRAGMA journal_mode = WAL')
    conn.row_factory = sqlite3.Row

    # Skip if already indexed
    existing = conn.execute(
        'SELECT COUNT(*) as cnt FROM chunks WHERE file_path = ?',
        (index_path,),
    ).fetchone()
    if existing and existing['cnt'] > 0:
        conn.close()
        sys.exit(0)

    # Parse conversation
    result = parse_conversation_jsonl(session_path)
    if result is None or not result.exchanges:
        conn.close()
        sys.exit(0)

    # Filter noise
    filtered = [
        ex for ex in result.exchanges
        if not is_noise(ex.user_message, ex.assistant_message)
    ]
    if not filtered:
        conn.close()
        sys.exit(0)

    # Exchange-aware chunking
    chunks = []
    current_texts = []
    current_chars = 0
    chunk_start = 0

    for i, ex in enumerate(filtered):
        formatted = f'User: {ex.user_message}'
        if ex.assistant_message:
            formatted += f'\n\nAssistant: {ex.assistant_message}'
        if ex.tool_names:
            unique_tools = list(dict.fromkeys(ex.tool_names))
            formatted += f'\n\nTools: {", ".join(unique_tools)}'

        ex_chars = len(formatted)

        if current_chars + ex_chars > MAX_CHUNK_CHARS and current_texts:
            text = '\n\n---\n\n'.join(current_texts)
            title = f'{project} — {current_texts[0][:80]}'
            chunks.append((text, title, chunk_start + 1, i))
            current_texts = []
            current_chars = 0
            chunk_start = i

        current_texts.append(formatted)
        current_chars += ex_chars

    if current_texts:
        text = '\n\n---\n\n'.join(current_texts)
        title = f'{project} — {current_texts[0][:80]}'
        chunks.append((text, title, chunk_start + 1, len(filtered)))

    if not chunks:
        conn.close()
        sys.exit(0)

    # Insert chunks
    for i, (content, title, start_line, end_line) in enumerate(chunks):
        chunk_id = f'{index_path}:{i}'
        c_hash = hashlib.sha256(content.encode()).hexdigest()[:16]

        conn.execute(
            'INSERT OR REPLACE INTO chunks '
            '(id, file_path, chunk_index, start_line, end_line, '
            'title, content, embedding, hash, updated_at) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)',
            (chunk_id, index_path, i, start_line, end_line,
             title, content, c_hash,
             int(datetime.now().timestamp() * 1000)),
        )

        row = conn.execute(
            'SELECT rowid FROM chunks WHERE id = ?', (chunk_id,)
        ).fetchone()
        if row:
            try:
                conn.execute(
                    'INSERT INTO chunks_fts(rowid, content, title) '
                    'VALUES (?, ?, ?)',
                    (row['rowid'], content, title),
                )
            except Exception:
                pass

    # Update files table
    conn.execute(
        'INSERT OR REPLACE INTO files '
        '(file_path, content_hash, last_indexed, chunk_count) '
        'VALUES (?, ?, ?, ?)',
        (index_path, 'session',
         int(datetime.now().timestamp() * 1000), len(chunks)),
    )
    conn.commit()
    conn.close()

    noise_count = len(result.exchanges) - len(filtered)
    print(
        f'[index-session] {index_path}: {len(chunks)} chunks '
        f'({noise_count} noise filtered)',
        file=sys.stderr,
    )


if __name__ == '__main__':
    main()
