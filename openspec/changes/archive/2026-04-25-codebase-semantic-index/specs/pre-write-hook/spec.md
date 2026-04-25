## Capability: pre-write-hook

### Purpose
Claude Code hook that enforces a codebase search before creating new source files, surfacing existing implementations that may already solve the problem.

### Requirements

1. **Trigger**: `PreToolUse:Write` hook
   - Activates when the Write tool targets a file that does not yet exist AND has a source code extension (`.py`, `.java`, `.kt`, `.scala`, `.sh`, `.sql`, `.js`, `.ts`)
   - Does NOT activate for editing existing files, documentation, config files, or test data

2. **Search execution**:
   - Extracts context from the Write tool parameters: file name + first 200 chars of content
   - Determines which codebase to search based on the current working directory (matches against indexed codebases)
   - Calls `codebase_search` with the extracted context
   - Searches both the specific codebase and conversation memory

3. **Output on match**:
   When similar code is found (score above threshold), prints to stderr:
   ```
   CODEBASE CHECK: Found similar existing code:

   1. class ManifestFinder (toast-analytics/.../ManifestFinder.java:11-29)
      Finds manifest files using predicate: contains("manifest") && contains(".json")

   2. def chunk_python_file (claude-memory/src/code_chunker.py:12-66)
      Extract top-level functions and classes from a Python file

   Review these before proceeding. If your new file duplicates existing functionality,
   consider reusing or extending the existing code instead.
   ```

4. **Output on no match**: Silent pass-through (no output, no delay)

5. **Configuration**:
   - Score threshold configurable via `CODEBASE_CHECK_THRESHOLD` env var (default: 0.3)
   - Can be disabled via `CODEBASE_CHECK_DISABLED=1` env var
   - Hook script location: `~/.claude/hooks/checks/pre-write-codebase-check.py`

### Acceptance Criteria
- Creating `ManifestValidatorCli.java` in toast-analytics surfaces ManifestFinder.java and QueryManifestValidationIT.kt
- Creating `sync_schema.sh` surfaces syncSchemaDumpFromS3.sh
- Creating `README.md` does NOT trigger the hook (not a source extension)
- Editing an existing `.java` file does NOT trigger the hook
- Hook adds less than 2 seconds to Write operations when triggered
