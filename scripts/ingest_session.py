#!/Users/nathan.norman/.claude-memory/graphiti-venv/bin/python3
"""
Background script called by graphiti-session-ingest.py hook.
Parses a single conversation transcript and ingests it into Graphiti.

Usage: python ingest_session.py <transcript_path> <project_dir>
"""
import argparse
import asyncio
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# Add scripts dir for conversation_parser and shared
sys.path.insert(0, str(Path(__file__).parent))

from conversation_parser import parse_conversation_jsonl, format_for_graphiti
from shared import normalize_group_id, create_graphiti_client

from graphiti_core.nodes import EpisodeType


def summarize_for_graphiti(transcript_path: str) -> str:
    """Use Claude Haiku to produce a knowledge-graph-optimized session summary."""
    messages = []
    try:
        with open(transcript_path) as f:
            for line in f:
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                obj_type = obj.get("type", "")
                if obj_type not in ("user", "assistant"):
                    continue
                content = obj.get("message", {}).get("content", "")
                if isinstance(content, list):
                    texts = [c.get("text", "") for c in content if c.get("type") == "text"]
                    content = " ".join(texts)
                if isinstance(content, str) and content.strip():
                    role = "User" if obj_type == "user" else "Assistant"
                    messages.append(f"[{role}]: {content.strip()[:1000]}")
    except Exception:
        return ""

    if not messages:
        return ""

    transcript_excerpt = "\n\n".join(messages)
    if len(transcript_excerpt) > 30000:
        transcript_excerpt = transcript_excerpt[:30000] + "\n...(truncated)"

    prompt = (
        "Summarize this Claude Code session for a knowledge graph. "
        "Extract and name specifically: technologies/tools/frameworks used, "
        "files/functions/classes/services touched, problems encountered (exact error names), "
        "root causes discovered, decisions made, and outcomes. "
        "Write 200-400 words of dense prose using exact technical terms. No bullet points.\n\n"
        "---\n\n"
        + transcript_excerpt
    )

    try:
        env = os.environ.copy()
        env.pop('CLAUDECODE', None)  # prevent nested-session rejection
        result = subprocess.run(
            ['claude', '-p', prompt, '--model', 'haiku', '--tools', '',
             '--no-session-persistence'],
            env=env,
            capture_output=True,
            text=True,
            timeout=90,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
        print(f"Haiku summarization failed (rc={result.returncode}): {result.stderr[:200]}", file=sys.stderr)
    except Exception as e:
        print(f"Haiku summarization error: {e}", file=sys.stderr)

    return ""


async def ingest(transcript_path: str, project_dir: str, body: str = ''):
    short_project = normalize_group_id(project_dir)
    ref_time = datetime.now(timezone.utc)

    if body:
        text = body
        session_name = Path(transcript_path).stem
    else:
        # Parse for metadata (timestamp, session_name) regardless of summary method
        result = parse_conversation_jsonl(transcript_path)
        if result:
            if result.timestamp:
                try:
                    ref_time = datetime.fromisoformat(result.timestamp.replace('Z', '+00:00'))
                except (ValueError, TypeError):
                    pass
            session_name = result.session_id or Path(transcript_path).stem
        else:
            session_name = Path(transcript_path).stem

        # Use Haiku summary for rich entity content; fall back to format_for_graphiti
        text = summarize_for_graphiti(transcript_path)
        if not text:
            if not result:
                return
            text = format_for_graphiti(result, project_dir)

    name = f"session-{session_name[:36]}"

    api_key = os.environ['OPENAI_API_KEY']
    graphiti = create_graphiti_client(api_key)

    try:
        await graphiti.add_episode(
            name=name,
            episode_body=text,
            source_description=f"Claude Code session in {short_project}",
            reference_time=ref_time,
            source=EpisodeType.text,
            group_id='claude-memory',
        )
        print(f"Ingested session {session_name[:8]} from {short_project}", file=sys.stderr)
    except Exception as e:
        print(f"ERROR ingesting {session_name[:8]}: {e}", file=sys.stderr)
    finally:
        await graphiti.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('transcript_path')
    parser.add_argument('project_dir', nargs='?', default='')
    parser.add_argument('--body', default='', help='Pre-built episode text (skips transcript parsing)')
    args = parser.parse_args()

    if not os.path.exists(args.transcript_path):
        sys.exit(1)

    if not os.environ.get('OPENAI_API_KEY'):
        print("OPENAI_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    project_dir = args.project_dir or Path(args.transcript_path).parent.name
    asyncio.run(ingest(args.transcript_path, project_dir, body=args.body))


if __name__ == '__main__':
    main()
