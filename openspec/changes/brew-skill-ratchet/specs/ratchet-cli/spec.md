## ADDED Requirements

### REQ-CLI-1: Subcommands
The CLI MUST support these subcommands:
- `ratchet status` — Show capture status: hook registered?, cron registered?, DB size, last analysis, registered brew-skills, top sequences
- `ratchet report` — Generate HTML failure + pattern report (absorbs weekly-audit.sh). Supports `--days N` (default 7) and `--json`
- `ratchet analyze` — Run sequence detection on new sessions. Supports `--json`
- `ratchet promote [--id N]` — Promote a sequence: generate code + skill bump + PR. Interactive by default, `--id N` for non-interactive
- `ratchet register <name> --repo <path> --patterns "pat1,pat2"` — Register a brew-skill for attribution
- `ratchet install-hooks` — Register PostToolUse capture hook in settings.json (backup/validate/restore protocol)
- `ratchet install-cron` — Register weekly analysis job in claude-cron
- `ratchet install-skill` — Copy bundled Claude skill to ~/.claude/skills/ratchet/
- `ratchet uninstall` — Remove hooks from settings.json, remove cron job (data preserved)

### REQ-CLI-2: JSON Output
- `ratchet status --json`, `ratchet report --json`, and `ratchet analyze --json` MUST output structured JSON to stdout
- Default output MUST be human-readable colored text to stderr (so stdout is clean for piping)

### REQ-CLI-3: Settings.json Safety
- `ratchet install-hooks` MUST follow the backup/validate/restore protocol:
  1. Copy settings.json to settings.json.bak
  2. Parse, modify, write
  3. Validate with `jq . settings.json > /dev/null`
  4. On validation failure: restore from .bak immediately
- `ratchet install-hooks` MUST be idempotent: if hook already registered, exit 0 with message

### REQ-CLI-4: Entry Point
- The CLI MUST be registered as `ratchet` in pyproject.toml `[project.scripts]`
- The CLI MUST also be invocable as `python3 -m ratchet`
