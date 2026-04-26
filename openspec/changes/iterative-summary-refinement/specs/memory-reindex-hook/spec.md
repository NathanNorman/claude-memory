## MODIFIED Requirements

### Requirement: SessionEnd hook passes environment variables to index_session.py

The `~/.claude/hooks/memory-reindex.py` SessionEnd hook SHALL pass through the `MEMORY_SUMMARY_*` environment variables to the spawned `index_session.py` subprocess. Since the hook uses `subprocess.Popen` which inherits the parent environment by default, and the variables are set in the user's shell profile, no code change is required in the hook itself.

#### Scenario: Environment variables inherited
- **WHEN** `MEMORY_SUMMARY_ENABLED=1` is set in the user's shell environment
- **THEN** the `index_session.py` subprocess spawned by the hook SHALL see `MEMORY_SUMMARY_ENABLED=1` in its `os.environ`

#### Scenario: No environment variables set
- **WHEN** no `MEMORY_SUMMARY_*` variables are set in the environment
- **THEN** the `index_session.py` subprocess SHALL use default values (summarization disabled)

### Requirement: Hook latency unaffected

The SessionEnd hook SHALL continue to complete in under 100ms. The hook only spawns a subprocess and exits -- all LLM work happens in the detached `index_session.py` process.

#### Scenario: Hook timing with summarization enabled
- **WHEN** `MEMORY_SUMMARY_ENABLED=1` and the hook spawns `index_session.py`
- **THEN** the hook process SHALL exit within 100ms (the LLM calls happen asynchronously in the spawned process)
