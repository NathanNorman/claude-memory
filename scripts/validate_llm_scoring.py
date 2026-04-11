#!/Users/nathan.norman/.pyenv/versions/3.12.11/bin/python3
"""
Validate LLM boundary scoring by comparing claude CLI scores vs heuristic.
Uses the same prompt as src/prompts/boundary-score-system.txt.

Usage: python3 scripts/validate_llm_scoring.py /path/to/session.jsonl
"""

import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from conversation_parser import parse_conversation_jsonl

PROMPT_DIR = Path(__file__).parent.parent / 'src' / 'prompts'


def load_prompts():
    system = (PROMPT_DIR / 'boundary-score-system.txt').read_text().strip()
    user_tpl = (PROMPT_DIR / 'boundary-score-user.txt').read_text().strip()
    return system, user_tpl


def parse_exchanges(jsonl_path: str):
    """Parse JSONL into simple (user, assistant) pairs."""
    messages = []
    current_user = None

    with open(jsonl_path) as f:
        for line in f:
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            msg_type = obj.get('type', '')
            content = obj.get('message', {}).get('content', '')
            if isinstance(content, list):
                texts = [c.get('text', '') for c in content if c.get('type') == 'text']
                content = ' '.join(texts)
            if not isinstance(content, str) or not content.strip():
                continue

            if msg_type == 'user':
                if current_user is not None:
                    messages.append((current_user, ''))
                current_user = content.strip()[:500]
            elif msg_type == 'assistant' and current_user is not None:
                messages.append((current_user, content.strip()[:500]))
                current_user = None

    if current_user is not None:
        messages.append((current_user, ''))

    return messages


def format_with_boundaries(exchanges, start=0, end=None):
    """Format exchanges with <<<BOUNDARY_N>>> markers."""
    if end is None:
        end = len(exchanges)
    parts = []
    for i in range(start, end):
        user, assistant = exchanges[i]
        parts.append(f'[User]: {user}')
        if assistant:
            parts.append(f'[Assistant]: {assistant}')
        if i < end - 1:
            parts.append(f'\n<<<BOUNDARY_{i}>>>\n')
    return '\n'.join(parts)


def call_claude(prompt: str, model: str = 'sonnet') -> str:
    env = os.environ.copy()
    env.pop('CLAUDECODE', None)
    result = subprocess.run(
        ['claude', '-p', prompt, '--model', model, '--tools', '', '--no-session-persistence'],
        capture_output=True, text=True, timeout=120, env=env,
    )
    if result.returncode != 0:
        raise RuntimeError(f'claude failed: {result.stderr[:200]}')
    return result.stdout.strip()


def score_window(exchanges, start, end, system_prompt, user_template, model='sonnet'):
    """Score boundaries in a window of exchanges using claude CLI."""
    window = exchanges[start:end]
    count = len(window) - 1
    text = format_with_boundaries(exchanges, start, end)

    user_content = user_template.replace('{count}', str(count)).replace('{text}', text)
    full_prompt = system_prompt.replace('{count}', str(count)) + '\n\n---\n\n' + user_content

    response = call_claude(full_prompt, model)

    # Parse JSON from response
    import re
    json_match = re.search(r'\{[^{}]*"scores"\s*:\s*\[[^\]]*\][^{}]*\}', response)
    if json_match:
        parsed = json.loads(json_match.group())
        scores = [min(3.0, max(0.0, float(s))) for s in parsed['scores']]
        if len(scores) != count:
            print(f'  WARNING: Expected {count} scores, got {len(scores)}', file=sys.stderr)
            scores = (scores + [0] * count)[:count]
        return scores

    print(f'  WARNING: Could not parse scores from response: {response[:200]}', file=sys.stderr)
    return [0] * count


def main():
    if len(sys.argv) < 2:
        print('Usage: validate_llm_scoring.py <session.jsonl>', file=sys.stderr)
        sys.exit(1)

    jsonl_path = sys.argv[1]
    model = sys.argv[2] if len(sys.argv) > 2 else 'sonnet'
    exchanges = parse_exchanges(jsonl_path)

    if len(exchanges) < 3:
        print(f'Only {len(exchanges)} exchanges, need at least 3', file=sys.stderr)
        sys.exit(1)

    # Limit to first 20 exchanges for validation
    exchanges = exchanges[:20]
    n = len(exchanges)
    print(f'Scoring {n} exchanges ({n-1} boundaries) with model={model}')

    system_prompt, user_template = load_prompts()

    # Score with a single window
    window_size = min(16, n)
    print(f'\nScoring window [0:{window_size}]...')
    scores = score_window(exchanges, 0, window_size, system_prompt, user_template, model)

    print(f'\n{"Boundary":<12} {"LLM Score":<12} {"Interpretation"}')
    print('-' * 50)
    labels = {0: 'no break', 1: 'weak', 2: 'moderate', 3: 'strong'}
    for i, s in enumerate(scores):
        label = labels.get(round(s), f'~{s:.1f}')
        user_preview = exchanges[i + 1][0][:60]
        print(f'  B{i:<8} {s:<12.1f} {label:<12} → "{user_preview}..."')

    # Summary stats
    avg = sum(scores) / len(scores) if scores else 0
    strong = sum(1 for s in scores if s >= 2.5)
    moderate = sum(1 for s in scores if 1.5 <= s < 2.5)
    weak = sum(1 for s in scores if 0.5 <= s < 1.5)
    none = sum(1 for s in scores if s < 0.5)

    print(f'\nSummary: avg={avg:.2f}, strong={strong}, moderate={moderate}, weak={weak}, none={none}')
    print('Validation complete.')


if __name__ == '__main__':
    main()
