## ADDED Requirements

### Requirement: Detect SHA drift via git ls-remote
The poll fallback SHALL check each tracked repo by running `git ls-remote <clone_url> HEAD` and comparing the remote SHA against the last indexed SHA stored in `codebase_meta`.

#### Scenario: Remote SHA differs from indexed SHA
- **WHEN** `git ls-remote` returns a SHA that differs from the most recent `codebase_meta` entry for that codebase
- **THEN** the poll fallback enqueues an index job with the old SHA as `before_sha` and the remote SHA as `after_sha`

#### Scenario: Remote SHA matches indexed SHA
- **WHEN** `git ls-remote` returns a SHA that matches the last indexed SHA
- **THEN** no job is enqueued for that repo

#### Scenario: Repo not yet indexed
- **WHEN** `git ls-remote` succeeds but no `codebase_meta` entries exist for the repo
- **THEN** the poll fallback enqueues a job with `before_sha` set to all zeros (triggering a full index)

### Requirement: Read tracked repos from configuration
The poll fallback SHALL read the list of tracked repos from a configuration source. Each entry SHALL include a codebase name and a clone URL. The configuration SHALL support both a JSON config file (`~/.claude-memory/webhook-config.json`) and the `TRACKED_REPOS` environment variable.

#### Scenario: Config file with tracked repos
- **WHEN** `~/.claude-memory/webhook-config.json` exists and contains a `tracked_repos` array
- **THEN** the poll fallback iterates over each entry and checks for SHA drift

#### Scenario: Environment variable override
- **WHEN** the `TRACKED_REPOS` environment variable is set (format: `name=url,name=url`)
- **THEN** the poll fallback uses the environment variable entries instead of the config file

### Requirement: Cron-compatible execution
The poll fallback SHALL be designed as a one-shot script suitable for cron execution. It SHALL exit after checking all tracked repos.

#### Scenario: Run via cron
- **WHEN** the poll script is executed (e.g., every 15 minutes via cron)
- **THEN** it checks all tracked repos, enqueues jobs as needed, and exits with code 0 on success
