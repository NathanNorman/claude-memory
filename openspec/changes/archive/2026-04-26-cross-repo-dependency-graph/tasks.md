## 1. Build File Parsers

- [ ] 1.1 Create `scripts/cross-repo-deps.py` with CLI argument parsing (`--path`, `--name`, `--update`, `--list`, `--remove`)
- [ ] 1.2 Implement Gradle parser: regex for `implementation`, `api`, `compileOnly`, `runtimeOnly`, `testImplementation` in `.gradle` and `.gradle.kts` files, including `project(':...')` references
- [ ] 1.3 Implement Maven parser: regex for `<dependency>` blocks extracting `groupId`, `artifactId`, `version`, `scope` from `pom.xml`
- [ ] 1.4 Implement npm parser: JSON parse `package.json` and extract `dependencies`, `devDependencies`, `peerDependencies` entries
- [ ] 1.5 Implement pip parser: line-based parse of `requirements.txt` and TOML parse of `pyproject.toml` `[project].dependencies`
- [ ] 1.6 Add build file discovery: walk repo directory tree, detect build system by presence of build files, return list of (parser, file_path) pairs

## 2. Edge Storage and Incremental Updates

- [ ] 2.1 Write extracted dependencies to `edges` table with `edge_type='repo_dependency'`, `source_file=<repo_name>`, `target_file=<group:artifact>`, `metadata` JSON with build_file/scope/version
- [ ] 2.2 Implement content-hash based staleness check for `--update` mode (hash build file contents, store in `codebase_meta` table, skip unchanged repos)
- [ ] 2.3 Implement `--remove` to delete all `repo_dependency` edges for a given codebase
- [ ] 2.4 Implement `--list` to show indexed repos and their dependency edge counts

## 3. MCP Tool Enhancement

- [ ] 3.1 Add `direction='repo_depends_on'` to `dependency_search`: query edges where `source_file=file_path` and `edge_type='repo_dependency'`
- [ ] 3.2 Add `direction='repo_depended_on_by'` to `dependency_search`: query edges where `target_file` matches and `edge_type='repo_dependency'`
- [ ] 3.3 Update `dependency_search` docstring to document new directions

## 4. GraphSidecar Integration

- [ ] 4.1 Ensure `GraphSidecar.load()` includes `repo_dependency` edges (already loads all edge types; verify vertex creation for repo names and dependency identifiers)
- [ ] 4.2 Test BFS traversal from a repo vertex follows `repo_dependency` edges correctly

## 5. Testing

- [ ] 5.1 Add unit tests for each parser with sample build file content (inline strings, no fixture files)
- [ ] 5.2 Add integration test: run `cross-repo-deps.py` on a temp directory with sample build files, verify edges written to DB
- [ ] 5.3 Test `dependency_search` with new directions against edges written by the script
