## Why

Claude Code skills and their underlying CLI tools co-evolve — repeated usage patterns in skills should become methods in tools, and tool updates should be reflected in skills. Today this ratchet is entirely manual: a human notices patterns, writes code, updates SKILL.md. The data to automate detection already exists in session JSONL transcripts and the failure audit system, but nobody is asking it the right question: "what tool-call sequences keep repeating?"

The brew-skill-ratchet is a new repo that ships as a single Homebrew package containing: a Python CLI for analysis and automation, a Claude Code skill teaching Claude how to use it, a SQLite database for tool-call patterns, PostToolUse capture hooks, and cron-scheduled analysis — all installable via `brew install ratchet`.

## What Changes

- **New repo**: `brew-skill-ratchet` — a brew-skill that observes other brew-skills and improves them
- **PostToolUse capture hook**: Logs all tool calls (not just failures) with brew-skill attribution to a SQLite database
- **Sequence detection**: Weekly analysis mines the tool-call DB for repeated multi-step patterns, grouped by brew-skill and split on user-message boundaries
- **Automated promotion**: When a pattern crosses a frequency threshold, Claude writes the new method in `client.py`, updates SKILL.md + references, and opens a PR — code change and skill bump in the same PR
- **CLI**: `ratchet status`, `ratchet report`, `ratchet analyze`, `ratchet promote`, `ratchet install-hooks`, `ratchet install-cron`
- **Self-installing**: `brew install` handles the Python package + skill; `ratchet install-hooks` and `ratchet install-cron` wire up the capture hook and weekly analysis (with settings.json backup/validate protocol)
- **Absorbs failure audit**: The existing `~/.claude/failure_audits/` system becomes a subset — failures are tool calls where `succeeded=false`

## Capabilities

### New Capabilities
- `tool-call-capture`: PostToolUse hook that logs all tool calls to SQLite with brew-skill attribution, success/failure status, and session context
- `sequence-detection`: Analysis engine that mines tool-call data for repeated multi-step patterns, using user-message boundaries from JSONL transcripts as sequence delimiters
- `automated-promotion`: Claude-driven pipeline that takes a detected pattern, writes a new method + tests in the target brew-skill repo, updates the skill files, and opens a PR
- `ratchet-cli`: CLI interface for status, reporting, analysis, promotion, and self-installation (hooks + cron)
- `ratchet-skill`: Claude Code skill teaching Claude how to use the ratchet CLI and interpret its output

### Modified Capabilities

(none — this is a new repo)

## Impact

- **New repo**: `brew-skill-ratchet` with Python package, Claude skill, Homebrew formula, install scripts
- **`~/.claude/settings.json`**: PostToolUse hook added via `ratchet install-hooks` (backup/validate protocol)
- **`claude-cron`**: Weekly analysis job registered via `ratchet install-cron`
- **`~/.ratchet/`**: New data directory for SQLite DB + reports
- **Existing brew-skills** (e.g., `toastweb_wrapper`): Target repos for automated PRs — no changes required, PRs are opened externally
- **`~/.claude/failure_audits/`**: Superseded over time as ratchet captures all tool calls including failures
