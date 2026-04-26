## ADDED Requirements

### REQ-SK-1: Skill Files
- The skill MUST live at `.claude/skills/ratchet/` in the repo (tracked in git)
- The skill MUST include SKILL.md and references/cli-reference.md
- The skill MUST be installable to `~/.claude/skills/ratchet/` via `ratchet install-skill`, `scripts/install-skill.sh`, or Homebrew `post_install`

### REQ-SK-2: SKILL.md Content
- SKILL.md MUST teach Claude when to use the ratchet (after noticing repeated tool-call patterns, during tool improvement sessions, when reviewing what patterns have been detected)
- SKILL.md MUST document all CLI subcommands with examples
- SKILL.md MUST explain the promotion workflow: analyze → review candidates → promote → review PR
- SKILL.md MUST include a "Common Patterns" section that serves as the staging area for patterns not yet promoted

### REQ-SK-3: Homebrew Formula
- `Formula/ratchet.rb` MUST install the Python package and copy skill files to the Cellar
- `post_install` MUST copy skill files to `~/.claude/skills/ratchet/`
- `post_install` MUST run `ratchet install-hooks` and `ratchet install-cron`
- The formula MUST depend on `python@3.12`
- The `test` block MUST run `ratchet status` successfully

### REQ-SK-4: Brew-Skill Pattern Compliance
- The repo MUST follow the brew-skill pattern: `.claude/skills/` tracked in git, `install-skill` CLI subcommand, `scripts/install-skill.sh`, Formula with `post_install`, sdist includes `.claude/`
- `.gitignore` MUST NOT exclude `.claude/` (skill files must be tracked)
