"""
Call resolution cascade: maps extracted call sites to target symbols.

Implements a 6-strategy cascade ordered by confidence, adapted from
Codebase-Memory (arXiv 2603.27277). Each strategy attempts to resolve
a callee name to a specific file/symbol in the codebase.

Strategies (in order):
1. Import-map exact match (0.95)
2. Import-map suffix fallback (0.85)
3. Same-module prefix match (0.90)
4. Unique-name project-wide (0.75)
5. Suffix + import-distance by directory proximity (0.55)
6. Fuzzy string similarity (0.30-0.40)
"""

import os
from difflib import SequenceMatcher


def resolve_call_targets(
    call_sites: list[dict],
    symbol_table: dict[str, list[dict]],
    import_map: dict[tuple[str, str], str],
) -> list[dict]:
    """Resolve call sites to target symbols using a 6-strategy cascade.

    Args:
        call_sites: List of dicts from extract_call_sites(), each with keys:
            file_path, caller_symbol, callee_name, callee_receiver, line
        symbol_table: Maps symbol name -> list of {file_path, kind, start_line, end_line}
        import_map: Maps (source_file, imported_name) -> resolved target file path

    Returns:
        List of resolved edge dicts with keys:
            source_file, target_file (None if unresolved), edge_type ('calls' or 'calls_unresolved'),
            confidence, strategy, metadata (dict with callee_name, callee_receiver, caller_symbol)
    """
    # Precompute name index for strategies 4-6: symbol_name -> [(file_path, kind)]
    name_index: dict[str, list[dict]] = symbol_table

    # Precompute suffix index: last component of symbol name -> [(full_name, entries)]
    suffix_index: dict[str, list[tuple[str, list[dict]]]] = {}
    for sym_name, entries in symbol_table.items():
        suffix = sym_name.rsplit('.', 1)[-1]
        if suffix not in suffix_index:
            suffix_index[suffix] = []
        suffix_index[suffix].append((sym_name, entries))

    results: list[dict] = []
    for call in call_sites:
        resolved = _resolve_single(call, name_index, suffix_index, import_map)
        results.append(resolved)

    return results


def _resolve_single(
    call: dict,
    name_index: dict[str, list[dict]],
    suffix_index: dict[str, list[tuple[str, list[dict]]]],
    import_map: dict[tuple[str, str], str],
) -> dict:
    """Try each strategy in order, return on first match."""
    source_file = call['file_path']
    callee_name = call['callee_name']
    callee_receiver = call.get('callee_receiver')

    base_metadata = {
        'callee_name': callee_name,
        'callee_receiver': callee_receiver,
        'caller_symbol': call['caller_symbol'],
        'line': call['line'],
    }

    # Strategy 1: Import-map exact match (0.95)
    target = _strategy_import_exact(source_file, callee_name, callee_receiver, import_map)
    if target:
        return _make_edge(source_file, target, 0.95, 'import_exact', base_metadata)

    # Strategy 2: Import-map suffix fallback (0.85)
    target = _strategy_import_suffix(source_file, callee_name, callee_receiver, import_map)
    if target:
        return _make_edge(source_file, target, 0.85, 'import_suffix', base_metadata)

    # Strategy 3: Same-module prefix match (0.90)
    target = _strategy_same_module(source_file, callee_name, name_index)
    if target:
        return _make_edge(source_file, target, 0.90, 'same_module', base_metadata)

    # Strategy 4: Unique-name project-wide (0.75)
    target = _strategy_unique_name(callee_name, name_index)
    if target:
        return _make_edge(source_file, target, 0.75, 'unique_name', base_metadata)

    # Strategy 5: Suffix + import-distance (0.55)
    target = _strategy_suffix_distance(source_file, callee_name, suffix_index)
    if target:
        return _make_edge(source_file, target, 0.55, 'suffix_distance', base_metadata)

    # Strategy 6: Fuzzy string similarity (0.30-0.40)
    target, confidence = _strategy_fuzzy(callee_name, name_index)
    if target:
        return _make_edge(source_file, target, confidence, 'fuzzy', base_metadata)

    # Unresolved
    return _make_edge(source_file, None, None, None, base_metadata)


def _make_edge(
    source_file: str,
    target_file: str | None,
    confidence: float | None,
    strategy: str | None,
    metadata: dict,
) -> dict:
    """Construct an edge dict."""
    edge_type = 'calls' if target_file else 'calls_unresolved'
    full_metadata = dict(metadata)
    if confidence is not None:
        full_metadata['confidence'] = confidence
    if strategy:
        full_metadata['strategy'] = strategy
    return {
        'source_file': source_file,
        'target_file': target_file,
        'edge_type': edge_type,
        'confidence': confidence,
        'metadata': full_metadata,
    }


# ──────────────────────────────────────────────────────────────
# Strategy implementations
# ──────────────────────────────────────────────────────────────

def _strategy_import_exact(
    source_file: str,
    callee_name: str,
    callee_receiver: str | None,
    import_map: dict[tuple[str, str], str],
) -> str | None:
    """Strategy 1: Check if callee matches an imported symbol exactly.

    Checks both the receiver name and the callee name against the import map.
    For `UserService.getUser()`, we check if source_file imports 'UserService'.
    """
    # Check receiver as import name (e.g., import UserService -> UserService.getUser())
    if callee_receiver:
        # Try the receiver itself (common for Java/Kotlin class imports)
        receiver_base = callee_receiver.split('.')[0]
        target = import_map.get((source_file, receiver_base))
        if target:
            return target
        # Try full receiver
        target = import_map.get((source_file, callee_receiver))
        if target:
            return target

    # Check callee name directly (bare function call that matches an import)
    target = import_map.get((source_file, callee_name))
    if target:
        return target

    return None


def _strategy_import_suffix(
    source_file: str,
    callee_name: str,
    callee_receiver: str | None,
    import_map: dict[tuple[str, str], str],
) -> str | None:
    """Strategy 2: Callee matches the last component of an imported symbol.

    For import `com.example.utils.StringHelper`, matches callee `StringHelper`.
    """
    search_name = callee_receiver.split('.')[0] if callee_receiver else callee_name

    for (src, imp_name), target in import_map.items():
        if src != source_file:
            continue
        # Check if the last component of the import matches
        suffix = imp_name.rsplit('.', 1)[-1]
        if suffix == search_name:
            return target

    return None


def _strategy_same_module(
    source_file: str,
    callee_name: str,
    name_index: dict[str, list[dict]],
) -> str | None:
    """Strategy 3: Target symbol exists in the same package/directory.

    Looks for a symbol with the matching name in the same directory as the caller.
    """
    source_dir = os.path.dirname(source_file)

    # Check exact name match first
    entries = name_index.get(callee_name, [])
    for entry in entries:
        if os.path.dirname(entry['file_path']) == source_dir and entry['file_path'] != source_file:
            return entry['file_path']

    # Check if callee_name matches the suffix of a qualified symbol in the same dir
    for sym_name, entries in name_index.items():
        if sym_name.endswith('.' + callee_name) or sym_name == callee_name:
            for entry in entries:
                if os.path.dirname(entry['file_path']) == source_dir and entry['file_path'] != source_file:
                    return entry['file_path']

    return None


def _strategy_unique_name(
    callee_name: str,
    name_index: dict[str, list[dict]],
) -> str | None:
    """Strategy 4: Exactly one symbol with that name exists project-wide."""
    entries = name_index.get(callee_name, [])
    if len(entries) == 1:
        return entries[0]['file_path']

    # Also check for qualified names ending with callee_name
    matching_files: set[str] = set()
    for sym_name, entries in name_index.items():
        if sym_name == callee_name or sym_name.endswith('.' + callee_name):
            for entry in entries:
                matching_files.add(entry['file_path'])

    if len(matching_files) == 1:
        return matching_files.pop()

    return None


def _directory_distance(path_a: str, path_b: str) -> int:
    """Count the number of directory components that differ between two paths."""
    parts_a = os.path.dirname(path_a).split(os.sep)
    parts_b = os.path.dirname(path_b).split(os.sep)

    # Find common prefix length
    common = 0
    for a, b in zip(parts_a, parts_b):
        if a == b:
            common += 1
        else:
            break

    # Distance = parts unique to a + parts unique to b
    return (len(parts_a) - common) + (len(parts_b) - common)


def _strategy_suffix_distance(
    source_file: str,
    callee_name: str,
    suffix_index: dict[str, list[tuple[str, list[dict]]]],
) -> str | None:
    """Strategy 5: Suffix match weighted by directory proximity.

    Finds symbols whose name ends with callee_name, picks the closest by
    directory distance.
    """
    candidates: list[tuple[str, int]] = []  # (file_path, distance)

    matches = suffix_index.get(callee_name, [])
    for _, entries in matches:
        for entry in entries:
            if entry['file_path'] != source_file:
                dist = _directory_distance(source_file, entry['file_path'])
                candidates.append((entry['file_path'], dist))

    if not candidates:
        return None

    # Pick the closest candidate
    candidates.sort(key=lambda x: x[1])
    return candidates[0][0]


def _strategy_fuzzy(
    callee_name: str,
    name_index: dict[str, list[dict]],
) -> tuple[str | None, float]:
    """Strategy 6: Fuzzy string similarity as last resort.

    Uses SequenceMatcher ratio. Returns (target_file, confidence) where
    confidence is between 0.30 and 0.40 scaled by similarity.
    """
    best_file: str | None = None
    best_ratio = 0.0
    min_threshold = 0.7  # Minimum similarity to consider

    for sym_name, entries in name_index.items():
        # Compare against the last component of the symbol name
        suffix = sym_name.rsplit('.', 1)[-1]
        ratio = SequenceMatcher(None, callee_name.lower(), suffix.lower()).ratio()
        if ratio > best_ratio and ratio >= min_threshold:
            best_ratio = ratio
            best_file = entries[0]['file_path']

    if best_file:
        # Scale confidence: 0.7 similarity -> 0.30, 1.0 similarity -> 0.40
        confidence = 0.30 + (best_ratio - 0.7) * (0.10 / 0.3)
        return best_file, round(confidence, 3)

    return None, 0.0
