#!/Users/nathan.norman/.pyenv/versions/3.12.11/bin/python3
"""
Build a reference database for use as a unified-memory addon.

Takes a directory of markdown/text files, chunks them, generates embeddings,
and produces a SQLite database that unified-memory can search via the
`source` parameter on `memory_search`.

Usage:
    build-reference-db.py ./spark-sql-docs/ -o spark-sql.db
    build-reference-db.py ./my-references/ -o my-skill.db --name my-skill

Workflow:
    1. Gather reference material as .md, .txt, or .rst files in a directory
    2. Run this script to produce a .db file
    3. Place the .db next to your SKILL.md in ~/.claude/skills/<name>/
    4. In SKILL.md, document: memory_search(source="<db-stem>")
    5. Agents can now search your reference material via unified-memory

The output DB uses the same schema as unified-memory's memory.db, so the
existing FlatSearchBackend and VectorSearchBackend classes work on it
without modification.
"""

import argparse
import hashlib
import os
import sqlite3
import struct
import sys
import time
from pathlib import Path

# Supported file extensions
SUPPORTED_EXTENSIONS = {'.md', '.txt', '.rst', '.html', '.htm'}

# Chunking config
MAX_CHUNK_CHARS = 1600

# Embedding model
DEFAULT_MODEL = 'bge-base-en-v1.5'
MODEL_REPOS = {
    'bge-base-en-v1.5': 'BAAI/bge-base-en-v1.5',
    'bge-small-en-v1.5': 'BAAI/bge-small-en-v1.5',
    'all-MiniLM-L6-v2': 'all-MiniLM-L6-v2',
}
MODEL_DIMS = {
    'bge-base-en-v1.5': 768,
    'bge-small-en-v1.5': 384,
    'all-MiniLM-L6-v2': 384,
}


def create_schema(conn: sqlite3.Connection) -> None:
    """Create the required tables for a reference database."""
    conn.execute('''
        CREATE TABLE IF NOT EXISTS chunks (
            id TEXT PRIMARY KEY,
            file_path TEXT NOT NULL,
            chunk_index INTEGER NOT NULL,
            start_line INTEGER,
            end_line INTEGER,
            title TEXT,
            content TEXT NOT NULL,
            embedding BLOB,
            hash TEXT,
            updated_at INTEGER
        )
    ''')
    conn.execute('''
        CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts
        USING fts5(content, title, content='chunks', content_rowid='rowid')
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS files (
            file_path TEXT PRIMARY KEY,
            content_hash TEXT,
            last_indexed INTEGER,
            chunk_count INTEGER,
            summary TEXT
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    ''')
    conn.commit()


def discover_files(input_dir: Path) -> list[Path]:
    """Recursively find supported files, skip others."""
    found = []
    skipped_exts = set()
    for root, _dirs, files in os.walk(input_dir):
        for fname in sorted(files):
            fp = Path(root) / fname
            if fp.suffix.lower() in SUPPORTED_EXTENSIONS:
                found.append(fp)
            elif not fname.startswith('.'):
                skipped_exts.add(fp.suffix.lower())
    if skipped_exts:
        print(f'  Skipped file types: {", ".join(sorted(skipped_exts))}')
    return found


def chunk_markdown(content: str, file_path: str) -> list[dict]:
    """Split markdown by ## headings into chunks."""
    lines = content.split('\n')
    chunks: list[dict] = []
    current_title = file_path
    current_lines: list[str] = []
    start_line = 1

    for i, line in enumerate(lines, 1):
        if line.startswith('## ') and current_lines:
            chunks.append({
                'title': current_title,
                'content': '\n'.join(current_lines),
                'start_line': start_line,
                'end_line': i - 1,
            })
            current_title = line.lstrip('#').strip()
            current_lines = [line]
            start_line = i
        else:
            current_lines.append(line)

    if current_lines:
        chunks.append({
            'title': current_title,
            'content': '\n'.join(current_lines),
            'start_line': start_line,
            'end_line': len(lines),
        })

    return chunks or [{
        'title': file_path,
        'content': content,
        'start_line': 1,
        'end_line': len(lines),
    }]


def chunk_plaintext(content: str, file_path: str) -> list[dict]:
    """Split plain text by paragraph boundaries, respecting max chunk size."""
    paragraphs = content.split('\n\n')
    chunks: list[dict] = []
    current_parts: list[str] = []
    current_chars = 0
    line_offset = 1

    for para in paragraphs:
        para_chars = len(para)
        if current_chars + para_chars > MAX_CHUNK_CHARS and current_parts:
            text = '\n\n'.join(current_parts)
            lines_in_chunk = text.count('\n') + 1
            chunks.append({
                'title': file_path,
                'content': text,
                'start_line': line_offset,
                'end_line': line_offset + lines_in_chunk - 1,
            })
            line_offset += lines_in_chunk
            current_parts = []
            current_chars = 0

        current_parts.append(para)
        current_chars += para_chars

    if current_parts:
        text = '\n\n'.join(current_parts)
        lines_in_chunk = text.count('\n') + 1
        chunks.append({
            'title': file_path,
            'content': text,
            'start_line': line_offset,
            'end_line': line_offset + lines_in_chunk - 1,
        })

    return chunks or [{
        'title': file_path,
        'content': content,
        'start_line': 1,
        'end_line': content.count('\n') + 1,
    }]


def extract_html_text(html: str) -> str:
    """Extract main article content from HTML, stripping boilerplate.

    Uses trafilatura (combines readability + jusText) for high-quality
    content extraction that strips nav bars, sidebars, footers, etc.
    """
    try:
        import trafilatura
        text = trafilatura.extract(html, include_comments=False, include_tables=True)
        if text and len(text) > 50:
            return text
    except Exception:
        pass
    # Fallback: basic tag stripping if trafilatura fails or returns nothing
    import re
    text = re.sub(r'<(script|style|nav|header|footer|noscript)[^>]*>.*?</\1>', '', html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def chunk_file(content: str, file_path: str, suffix: str) -> list[dict]:
    """Route to the appropriate chunker based on file type."""
    if suffix == '.md':
        return chunk_markdown(content, file_path)
    elif suffix in ('.html', '.htm'):
        text = extract_html_text(content)
        if not text.strip():
            return []
        return chunk_plaintext(text, file_path)
    else:
        return chunk_plaintext(content, file_path)


def load_quantization_params(model_name: str):
    """Try to load quantization params from the primary memory.db."""
    primary_db = Path.home() / '.claude-memory' / 'index' / 'memory.db'
    if not primary_db.exists():
        return None
    try:
        import numpy as np
        sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))
        from quantize import generate_rotation

        conn = sqlite3.connect(str(primary_db), timeout=5.0)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            'SELECT dims, bit_width, rotation_seed, codebook '
            'FROM quantization_meta WHERE model_name = ? '
            'ORDER BY created_at DESC LIMIT 1',
            (model_name,),
        ).fetchone()
        conn.close()

        if not row:
            return None

        dims = row['dims']
        bit_width = row['bit_width']
        seed = row['rotation_seed']
        codebook_blob = row['codebook']
        n_centroids = 1 << bit_width
        codebook = np.array(
            struct.unpack(f'{n_centroids}f', codebook_blob),
            dtype=np.float32,
        )
        fwd, inv = generate_rotation(dims, seed)

        return {
            'dims': dims,
            'bit_width': bit_width,
            'seed': seed,
            'codebook': codebook,
            'codebook_blob': codebook_blob,
            'rotate_fn': fwd,
            'inv_rotate_fn': inv,
        }
    except Exception as e:
        print(f'  Could not load quantization params: {e}')
        return None


def build(input_dir: Path, output_path: Path, model_name: str) -> None:
    """Build a reference database from a directory of files."""
    dims = MODEL_DIMS[model_name]
    repo_id = MODEL_REPOS[model_name]

    # Discover files
    print(f'Scanning {input_dir}...')
    files = discover_files(input_dir)
    if not files:
        print('No supported files found.')
        sys.exit(1)
    print(f'  Found {len(files)} files')

    # Create DB
    if output_path.exists():
        output_path.unlink()
    conn = sqlite3.connect(str(output_path))
    conn.execute('PRAGMA journal_mode = WAL')
    conn.row_factory = sqlite3.Row
    create_schema(conn)

    # Stamp meta
    conn.execute(
        'INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)',
        ('embedding_model', model_name),
    )
    conn.execute(
        'INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)',
        ('embedding_dims', str(dims)),
    )
    conn.commit()

    # Chunk all files
    print('Chunking...')
    all_chunks: list[tuple[str, dict]] = []  # (relative_path, chunk)
    for fp in files:
        try:
            content = fp.read_text(errors='replace')
        except Exception as e:
            print(f'  Skipping {fp}: {e}')
            continue
        if not content.strip():
            continue

        rel_path = str(fp.relative_to(input_dir))
        chunks = chunk_file(content, rel_path, fp.suffix.lower())

        # Insert file record
        file_hash = hashlib.sha256(content.encode()).hexdigest()[:16]
        conn.execute(
            'INSERT OR REPLACE INTO files '
            '(file_path, content_hash, last_indexed, chunk_count) '
            'VALUES (?, ?, ?, ?)',
            (rel_path, file_hash, int(time.time() * 1000), len(chunks)),
        )

        for i, chunk in enumerate(chunks):
            chunk_id = f'{rel_path}:{i}'
            content_hash = hashlib.sha256(chunk['content'].encode()).hexdigest()[:16]
            conn.execute(
                'INSERT INTO chunks '
                '(id, file_path, chunk_index, start_line, end_line, '
                'title, content, embedding, hash, updated_at) '
                'VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)',
                (
                    chunk_id, rel_path, i,
                    chunk['start_line'], chunk['end_line'],
                    chunk['title'], chunk['content'],
                    content_hash, int(time.time() * 1000),
                ),
            )

            # FTS5
            row = conn.execute(
                'SELECT rowid FROM chunks WHERE id = ?', (chunk_id,)
            ).fetchone()
            if row:
                conn.execute(
                    'INSERT INTO chunks_fts(rowid, content, title) VALUES (?, ?, ?)',
                    (row['rowid'], chunk['content'], chunk['title']),
                )

            all_chunks.append((rel_path, chunk))

    conn.commit()
    print(f'  {len(all_chunks)} chunks from {len(files)} files')

    # Generate embeddings
    print(f'Embedding with {model_name} ({dims}d)...')
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer(repo_id)

    texts = [chunk['content'] for _, chunk in all_chunks]
    embeddings = model.encode(texts, normalize_embeddings=True, show_progress_bar=True)

    # Check for quantization
    import numpy as np
    quant_params = load_quantization_params(model_name)
    use_quant = quant_params is not None

    if use_quant:
        from quantize import quantize as quant_fn
        print(f'  Using {quant_params["bit_width"]}-bit quantization')

        # Write quantization_meta to output DB
        conn.execute('''
            CREATE TABLE IF NOT EXISTS quantization_meta (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                model_name TEXT NOT NULL,
                dims INTEGER NOT NULL,
                bit_width INTEGER NOT NULL,
                rotation_seed INTEGER NOT NULL,
                codebook BLOB NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(model_name, dims, bit_width)
            )
        ''')
        conn.execute(
            'INSERT OR REPLACE INTO quantization_meta '
            '(model_name, dims, bit_width, rotation_seed, codebook) '
            'VALUES (?, ?, ?, ?, ?)',
            (
                model_name, quant_params['dims'],
                quant_params['bit_width'], quant_params['seed'],
                quant_params['codebook_blob'],
            ),
        )
    else:
        print('  Using float32 embeddings (no quantization params found)')

    # Store embeddings — iterate by rowid order to match chunk insertion order
    rows = conn.execute(
        'SELECT rowid FROM chunks ORDER BY rowid'
    ).fetchall()

    for row, emb in zip(rows, embeddings):
        emb_arr = np.array(emb, dtype=np.float32)
        if use_quant:
            blob = quant_fn(emb_arr, quant_params['rotate_fn'], quant_params['codebook'])
        else:
            blob = struct.pack(f'{dims}f', *emb_arr.tolist())

        conn.execute(
            'UPDATE chunks SET embedding = ? WHERE rowid = ?',
            (blob, row['rowid']),
        )

    conn.commit()

    # Final stats
    total_chunks = conn.execute('SELECT COUNT(*) FROM chunks').fetchone()[0]
    total_embedded = conn.execute(
        'SELECT COUNT(*) FROM chunks WHERE embedding IS NOT NULL'
    ).fetchone()[0]
    db_size = output_path.stat().st_size

    conn.close()

    print(f'\nDone! {output_path}')
    print(f'  Chunks: {total_chunks}')
    print(f'  Embedded: {total_embedded}')
    print(f'  Size: {db_size / 1024 / 1024:.1f} MB')
    print(f'  Model: {model_name} ({dims}d, {"quantized" if use_quant else "float32"})')


def main():
    parser = argparse.ArgumentParser(
        description='Build a reference database for unified-memory addon search',
    )
    parser.add_argument(
        'input_dir',
        type=Path,
        help='Directory containing reference files (.md, .txt, .rst)',
    )
    parser.add_argument(
        '-o', '--output',
        type=Path,
        required=True,
        help='Output .db file path',
    )
    parser.add_argument(
        '--model',
        default=DEFAULT_MODEL,
        choices=list(MODEL_DIMS.keys()),
        help=f'Embedding model (default: {DEFAULT_MODEL})',
    )
    args = parser.parse_args()

    if not args.input_dir.is_dir():
        print(f'Error: {args.input_dir} is not a directory')
        sys.exit(1)

    build(args.input_dir, args.output, args.model)


if __name__ == '__main__':
    main()
