## ADDED Requirements

### Requirement: Ensure bare mirror exists
The mirror manager SHALL provide an `ensure_mirror(repo_name, clone_url)` function. If no mirror exists for the repo, it SHALL create one via `git clone --bare`. If a mirror already exists, it SHALL run `git fetch origin '+refs/heads/*:refs/heads/*'` to update it. The function SHALL return the path to the mirror directory.

#### Scenario: First mirror for a repo
- **WHEN** `ensure_mirror()` is called for a repo with no existing mirror directory
- **THEN** a bare clone is created at `<MIRROR_DIR>/<repo_name>.git` and the mirror path is returned

#### Scenario: Mirror already exists
- **WHEN** `ensure_mirror()` is called for a repo that already has a mirror directory
- **THEN** `git fetch` is run to update all branches and the mirror path is returned

### Requirement: Diff changed files between SHAs
The mirror manager SHALL provide a `git_diff_files(mirror_path, before_sha, after_sha)` function that returns a list of (status, filepath) tuples from `git diff --name-status`.

#### Scenario: Normal diff between related SHAs
- **WHEN** `git_diff_files()` is called with two SHAs where before is an ancestor of after
- **THEN** a list of (status, filepath) tuples is returned (e.g., `("M", "src/foo.py")`, `("D", "old.py")`, `("A", "new.py")`)

#### Scenario: Unrelated SHAs (force push)
- **WHEN** `git_diff_files()` is called with SHAs that have no common ancestry
- **THEN** the function raises an exception indicating the diff failed

### Requirement: Read file content at a specific SHA
The mirror manager SHALL provide a `git_show_file(mirror_path, sha, filepath)` function that returns the file content as a string using `git show <sha>:<filepath>`.

#### Scenario: File exists at SHA
- **WHEN** `git_show_file()` is called for a file that exists at the given SHA
- **THEN** the file content is returned as a string

#### Scenario: File does not exist at SHA
- **WHEN** `git_show_file()` is called for a file that does not exist at the given SHA
- **THEN** the function raises an exception

### Requirement: Configurable mirror directory
The mirror manager SHALL use the `MIRROR_DIR` environment variable for the base mirror directory, defaulting to `~/.claude-memory/mirrors/`.

#### Scenario: Custom mirror directory
- **WHEN** `MIRROR_DIR` is set to `/data/mirrors`
- **THEN** mirrors are stored under `/data/mirrors/<repo_name>.git`

#### Scenario: Default mirror directory
- **WHEN** `MIRROR_DIR` is not set
- **THEN** mirrors are stored under `~/.claude-memory/mirrors/<repo_name>.git`

### Requirement: Cleanup old mirrors
The mirror manager SHALL provide a `cleanup_old_mirrors(max_age_days)` function that removes mirror directories for repos that have not been fetched within the specified number of days.

#### Scenario: Mirror older than max age
- **WHEN** `cleanup_old_mirrors(30)` is called and a mirror has not been fetched in 45 days
- **THEN** the mirror directory is removed

#### Scenario: Mirror within max age
- **WHEN** `cleanup_old_mirrors(30)` is called and a mirror was fetched 10 days ago
- **THEN** the mirror directory is retained
