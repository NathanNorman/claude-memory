"""
SCIP indexer integration for compiler-grade code intelligence.

Optional Tier 2 indexing that runs SCIP indexers (scip-java, scip-typescript,
scip-python) to produce high-confidence edges that supplement tree-sitter results.
SCIP edges replace tree-sitter edges for the same call site (higher confidence).

Usage:
    from scip_parser import detect_scip_languages, run_scip_indexer, parse_scip_output

SCIP protobuf format:
    .scip files are protobuf-encoded Index messages containing Documents,
    each with Occurrences that reference Symbols. We parse these to extract
    call edges, definitions, and cross-references.
"""

import json
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

log = logging.getLogger('scip-parser')

# SCIP indexer binary names and their language mappings
SCIP_INDEXERS = {
    'java': {
        'binary': 'scip-java',
        'build_files': ['build.gradle.kts', 'build.gradle', 'pom.xml'],
        'install_hint': 'npm install -g @sourcegraph/scip-java',
    },
    'typescript': {
        'binary': 'scip-typescript',
        'build_files': ['tsconfig.json'],
        'install_hint': 'npm install -g @sourcegraph/scip-typescript',
    },
    'python': {
        'binary': 'scip-python',
        'build_files': ['pyproject.toml', 'setup.py', 'setup.cfg'],
        'install_hint': 'pip install scip-python',
    },
}


def detect_scip_languages(repo_path: Path) -> list[str]:
    """Auto-detect which SCIP indexers to run based on build file presence.

    Returns a list of language keys (e.g., ['java', 'typescript']).
    """
    detected = []
    for lang, config in SCIP_INDEXERS.items():
        for build_file in config['build_files']:
            if (repo_path / build_file).exists():
                detected.append(lang)
                break
    return detected


def _find_binary(binary_name: str) -> Optional[str]:
    """Find a SCIP indexer binary on PATH."""
    return shutil.which(binary_name)


def run_scip_indexer(repo_path: Path, language: str) -> Optional[Path]:
    """Execute the appropriate SCIP indexer and return the .scip file path.

    Returns None if the indexer is not installed or fails.
    """
    config = SCIP_INDEXERS.get(language)
    if not config:
        log.warning('Unknown SCIP language: %s', language)
        return None

    binary = _find_binary(config['binary'])
    if not binary:
        log.warning(
            'SCIP indexer not found: %s. Install with: %s',
            config['binary'], config['install_hint'],
        )
        return None

    output_path = repo_path / 'index.scip'

    # Build the command based on language
    if language == 'java':
        cmd = [binary, 'index', '--output', str(output_path)]
    elif language == 'typescript':
        cmd = [binary, 'index', '--output', str(output_path)]
    elif language == 'python':
        cmd = [binary, 'index', '--output', str(output_path), str(repo_path)]
    else:
        cmd = [binary, 'index', '--output', str(output_path)]

    log.info('Running SCIP indexer: %s', ' '.join(cmd))

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=str(repo_path),
            timeout=300,  # 5 min timeout
        )
        if result.returncode != 0:
            log.warning(
                'SCIP indexer failed for %s (exit %d): %s',
                language, result.returncode,
                result.stderr[:500] if result.stderr else 'no stderr',
            )
            return None

        if output_path.exists():
            log.info('SCIP index created: %s (%d bytes)', output_path, output_path.stat().st_size)
            return output_path
        else:
            log.warning('SCIP indexer ran but no output file found at %s', output_path)
            return None

    except subprocess.TimeoutExpired:
        log.warning('SCIP indexer timed out for %s (300s limit)', language)
        return None
    except Exception as e:
        log.warning('SCIP indexer error for %s: %s', language, e)
        return None


def parse_scip_output(scip_path: Path) -> list[dict]:
    """Parse a .scip protobuf file to extract edges.

    Returns a list of edge dicts with keys:
        source_file, target_file, edge_type, metadata, confidence

    Since SCIP protobuf parsing requires the scip protobuf schema,
    we use scip CLI's `print` subcommand to get JSON output, then parse that.
    Falls back to a simpler heuristic approach if the CLI is unavailable.
    """
    edges = []

    # Try using scip CLI to convert to JSON
    scip_cli = shutil.which('scip')
    if scip_cli:
        try:
            result = subprocess.run(
                [scip_cli, 'print', '--json', str(scip_path)],
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.returncode == 0 and result.stdout:
                return _parse_scip_json(result.stdout)
        except Exception as e:
            log.warning('scip print failed, trying direct parse: %s', e)

    # Fallback: try parsing protobuf directly
    return _parse_scip_protobuf(scip_path)


def _parse_scip_json(json_text: str) -> list[dict]:
    """Parse SCIP JSON output from `scip print --json`."""
    edges = []
    try:
        data = json.loads(json_text)
    except json.JSONDecodeError as e:
        log.warning('Failed to parse SCIP JSON: %s', e)
        return edges

    # SCIP JSON has documents with occurrences referencing symbols
    documents = data.get('documents', [])

    # Build symbol -> file mapping from definitions
    symbol_files: dict[str, str] = {}
    for doc in documents:
        rel_path = doc.get('relativePath', '')
        for occ in doc.get('occurrences', []):
            symbol = occ.get('symbol', '')
            # SymbolRole: Definition = 1
            roles = occ.get('symbolRoles', 0)
            if roles & 1:  # Definition bit
                symbol_files[symbol] = rel_path

    # Extract reference edges (file references symbol defined elsewhere)
    for doc in documents:
        source_file = doc.get('relativePath', '')
        for occ in doc.get('occurrences', []):
            symbol = occ.get('symbol', '')
            roles = occ.get('symbolRoles', 0)
            if not (roles & 1) and symbol in symbol_files:
                # This is a reference, not a definition
                target_file = symbol_files[symbol]
                if target_file and target_file != source_file:
                    # Determine edge type from symbol descriptor
                    edge_type = _classify_scip_symbol(symbol)
                    edges.append({
                        'source_file': source_file,
                        'target_file': target_file,
                        'edge_type': edge_type,
                        'metadata': json.dumps({
                            'source': 'scip',
                            'symbol': symbol[:200],
                        }),
                        'confidence': 0.95,  # SCIP has high confidence
                    })

    log.info('Parsed %d edges from SCIP JSON (%d documents)', len(edges), len(documents))
    return edges


def _classify_scip_symbol(symbol: str) -> str:
    """Classify a SCIP symbol string into an edge type."""
    # SCIP symbol format: scheme ` package ` descriptor
    # Method calls typically have `()` in the descriptor
    if '().' in symbol or '(' in symbol:
        return 'calls'
    elif '#' in symbol:
        return 'extends'
    else:
        return 'import'


def _parse_scip_protobuf(scip_path: Path) -> list[dict]:
    """Attempt direct protobuf parsing of SCIP file.

    This is a best-effort fallback when the scip CLI is not available.
    Returns an empty list if parsing fails (graceful degradation).
    """
    log.info('Direct protobuf parsing not yet implemented — install scip CLI for full SCIP support')
    return []


def merge_scip_edges(
    existing_edges: list[dict],
    scip_edges: list[dict],
) -> list[dict]:
    """Merge SCIP edges with existing tree-sitter edges.

    SCIP edges replace tree-sitter edges for the same (source_file, target_file) pair.
    SCIP-only edges are added with source=scip in metadata.
    """
    # Build lookup of existing edges by (source, target)
    existing_map: dict[tuple[str, str], dict] = {}
    for e in existing_edges:
        key = (e.get('source_file', ''), e.get('target_file', ''))
        existing_map[key] = e

    # Merge: SCIP takes precedence
    merged_map = dict(existing_map)
    scip_only = 0
    replaced = 0

    for se in scip_edges:
        key = (se['source_file'], se['target_file'])
        if key in merged_map:
            merged_map[key] = se  # Replace with higher confidence
            replaced += 1
        else:
            merged_map[key] = se
            scip_only += 1

    log.info(
        'Edge merge: %d existing, %d SCIP → %d replaced, %d SCIP-only added',
        len(existing_edges), len(scip_edges), replaced, scip_only,
    )
    return list(merged_map.values())
