# brew-skill-ratchet: Context

## What this is

A self-improving feedback loop for Claude Code tool development. When Claude uses a CLI tool (like `toastweb_wrapper`) through its Claude Code skill, usage patterns emerge — the same 3-step sequence gets repeated across sessions. Today, a human notices these patterns and manually promotes them into methods. The ratchet automates this: detect patterns from session data, have Claude write the code + skill update, open a PR, human reviews.

## Where the idea came from

This grew out of porting `toastweb_wrapper` to the **brew-skill pattern** — a repo structure where a CLI tool and its Claude Code skill live together, versioned and distributed as one Homebrew package. Once tool and skill are in the same repo, co-evolution becomes possible. The ratchet is the automation layer that makes co-evolution happen without human intervention.

## Key artifacts from the brainstorm session (2026-04-13)

- **Brew-skill template**: `~/brew-skill-template/` — the repo pattern (README has the co-evolution loop diagram)
- **First port**: `~/toastweb_wrapper/` — toastweb_wrapper now has `.claude/skills/toastweb-toast/` in-repo, Formula, install-skill CLI
- **Explainer**: `~/explainers/brew-skill-pattern.html` (published to GitHub Pages) — covers the pattern + the automated vision
- **Existing failure audit**: `~/.claude/failure_audits/weekly-audit.sh` — captures PostToolUseFailure to JSON, weekly HTML report. The ratchet absorbs and extends this.
- **Existing session hooks**: `~/.claude/hooks/memory-session-summary.py`, `memory-reindex.py` — already capture session data to SQLite
- **CLI best practices**: `~/personal-docs/docs/architecture/2026-03-28-cli-setup-script-best-practices.mdx` — patterns for install-hooks safety, idempotent setup, JSON output

## The promotion pipeline

```
1. OBSERVED    — Script mines session JSONLs, detects repeated tool-call sequences
2. CODIFIED    — Claude writes method in client.py + tests
3. SKILL BUMP  — SKILL.md + references updated in same PR
4. MERGED      — Human reviews PR. brew upgrade ships both.
```

Stages 1-3 are automated. The human only reviews and merges.

## The new repo will live at `~/brew-skill-ratchet/`

It ships as a single Homebrew package containing:
- Python CLI (`ratchet`)
- Claude Code skill (`.claude/skills/ratchet/`)
- SQLite database (`~/.ratchet/ratchet.db`)
- PostToolUse capture hook (registered via `ratchet install-hooks`)
- Weekly analysis cron job (registered via `ratchet install-cron`)

First target: `toastweb_wrapper` (60+ API methods, most-used brew-skill, richest pattern history).
