## 1. Repo scaffold and packaging

- [ ] Create new repo `~/brew-skill-ratchet` with `git init`
- [ ] Create `pyproject.toml` with hatchling build, `ratchet` entry point, Python >=3.10, sdist includes `.claude/`
- [ ] Create `src/ratchet/__init__.py` with `__version__`
- [ ] Create `src/ratchet/cli.py` with argparse skeleton: status, report, analyze, promote, register, install-hooks, install-cron, install-skill, uninstall
- [ ] Create `.gitignore` (Python standard, `.claude-work/` excluded, `.claude/` tracked)
- [ ] Create `CLAUDE.md` with project overview and dev commands
- [ ] Verify `python3 -m ratchet --help` works

## 2. SQLite database layer

- [ ] Create `src/ratchet/db.py` with schema init (tool_calls, brew_skills, sequences tables), WAL mode, busy_timeout=5000
- [ ] Implement `insert_tool_call()` — single row insert, <10ms
- [ ] Implement `get_brew_skill_patterns()` — load all attribution patterns
- [ ] Implement `upsert_sequence()` — insert or update detected sequence
- [ ] Implement `get_sequences(min_frequency, status)` — query sequences by threshold
- [ ] Database created at `~/.ratchet/ratchet.db` on first access

## 3. PostToolUse capture hook

- [ ] Create `src/ratchet/capture.py` — reads hook payload from stdin, writes to SQLite
- [ ] Implement brew-skill attribution: load patterns from DB, substring match against tool_input
- [ ] Implement succeeded/error extraction from hook payload
- [ ] Add shebang and make executable as a hook script
- [ ] Test: capture hook completes in <100ms (time the script)

## 4. Settings.json safety module

- [ ] Create `src/ratchet/settings.py` with `add_hook()`, `remove_hook()`, `has_hook()`
- [ ] Implement backup/validate/restore protocol (cp .bak → modify → jq validate → restore on failure)
- [ ] Implement idempotent check: if hook already registered, skip
- [ ] Wire into `ratchet install-hooks` and `ratchet uninstall` CLI subcommands
- [ ] Test: install-hooks is idempotent (run twice, verify single hook entry)

## 5. Sequence detection analyzer

- [ ] Create `src/ratchet/analyzer.py`
- [ ] Implement JSONL transcript reader: extract user-message boundaries and tool calls in order
- [ ] Implement sequence extraction: filter to attributed calls, split on user-message boundaries, discard sequences < 2 calls
- [ ] Implement method-name extraction from tool_input (Python method calls, CLI subcommands)
- [ ] Implement cross-session sequence matching: group identical sequences, count frequency, track sessions
- [ ] Implement incremental analysis: only process sessions newer than last run
- [ ] Wire into `ratchet analyze` CLI subcommand with `--json` output

## 6. Report generation

- [ ] Create `src/ratchet/report.py` — HTML report generator
- [ ] Implement failure breakdown (by tool, common errors, repeat offenders) — parity with weekly-audit.sh
- [ ] Implement pattern section: top sequences by frequency, brew-skill, status
- [ ] Implement `--days N` and `--json` flags
- [ ] Wire into `ratchet report` CLI subcommand
- [ ] Reports saved to `~/.ratchet/reports/`

## 7. Automated promotion (proposer)

- [ ] Create `src/ratchet/proposer.py`
- [ ] Implement target repo validation: check repo exists, main branch clean, brew-skill registered
- [ ] Implement worktree creation: `git worktree add` in target repo
- [ ] Implement Claude invocation: structured prompt with sequence data, client.py source, SKILL.md, references
- [ ] Implement PR creation: `gh pr create` with ratchet-generated title and body
- [ ] Implement status tracking: update sequence to `promoted` with PR URL
- [ ] Wire into `ratchet promote` CLI subcommand
- [ ] Worktree cleanup on completion or failure

## 8. Claude Code skill

- [ ] Create `.claude/skills/ratchet/SKILL.md` — when to use, CLI examples, promotion workflow, Common Patterns section
- [ ] Create `.claude/skills/ratchet/references/cli-reference.md` — all subcommands with flags and examples
- [ ] Wire `ratchet install-skill` CLI subcommand (copy skill to `~/.claude/skills/ratchet/`)
- [ ] Create `scripts/install-skill.sh` (standalone bash installer)

## 9. Homebrew formula and distribution

- [ ] Create `Formula/ratchet.rb` — depends on python@3.12, install pip3 + skill to Cellar, post_install copies skill + runs install-hooks + install-cron
- [ ] Create `scripts/build-formula.sh` — extract version, generate SHA256, patch formula
- [ ] Create cron integration: `ratchet install-cron` registers weekly analysis in claude-cron
- [ ] Test: `ratchet install-skill` copies skill correctly
- [ ] Test: `ratchet install-hooks` registers hook and validates settings.json
- [ ] Test: `ratchet status` shows all components wired

## 10. Register toastweb-wrapper as first target

- [ ] Add `ratchet.toml` to `~/toastweb_wrapper/` with attribution patterns
- [ ] Run `ratchet register toastweb-wrapper --repo ~/toastweb_wrapper --patterns "toastweb_wrapper,ToastWebClient,toastweb-wrapper"`
- [ ] Run `ratchet analyze` and verify it finds patterns in existing session transcripts
- [ ] Review detected sequences — confirm they match real usage patterns
