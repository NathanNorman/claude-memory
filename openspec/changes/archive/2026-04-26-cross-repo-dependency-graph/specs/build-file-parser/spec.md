## ADDED Requirements

### Requirement: Gradle dependency extraction
The system SHALL extract dependency declarations from `build.gradle` and `build.gradle.kts` files using regex patterns matching `implementation`, `api`, `compileOnly`, `runtimeOnly`, `testImplementation`, and their variants. Each extracted dependency SHALL include group, artifact, and version when present.

#### Scenario: Standard Gradle dependency
- **WHEN** a `build.gradle` file contains `implementation 'com.toasttab:toast-common:1.2.3'`
- **THEN** the parser extracts `{group: "com.toasttab", artifact: "toast-common", version: "1.2.3", scope: "implementation"}`

#### Scenario: Gradle Kotlin DSL dependency
- **WHEN** a `build.gradle.kts` file contains `implementation("com.toasttab:toast-common:1.2.3")`
- **THEN** the parser extracts the same dependency with scope `implementation`

#### Scenario: Gradle project dependency
- **WHEN** a `build.gradle` file contains `implementation project(':submodule-name')`
- **THEN** the parser extracts `{artifact: "submodule-name", scope: "implementation", type: "project"}`

### Requirement: Maven dependency extraction
The system SHALL extract `<dependency>` blocks from `pom.xml` files, capturing `<groupId>`, `<artifactId>`, `<version>`, and `<scope>` elements.

#### Scenario: Standard Maven dependency
- **WHEN** a `pom.xml` contains a `<dependency>` block with `<groupId>com.toasttab</groupId>` and `<artifactId>toast-common</artifactId>`
- **THEN** the parser extracts `{group: "com.toasttab", artifact: "toast-common"}` with version and scope if present

### Requirement: npm dependency extraction
The system SHALL extract entries from `dependencies`, `devDependencies`, and `peerDependencies` objects in `package.json` files.

#### Scenario: npm production dependency
- **WHEN** a `package.json` contains `"dependencies": {"@toast/utils": "^2.0.0"}`
- **THEN** the parser extracts `{artifact: "@toast/utils", version: "^2.0.0", scope: "dependencies"}`

### Requirement: pip dependency extraction
The system SHALL extract package names from `requirements.txt` lines and `dependencies` arrays in `pyproject.toml` `[project]` sections.

#### Scenario: requirements.txt dependency
- **WHEN** a `requirements.txt` contains `sentence-transformers==2.2.2`
- **THEN** the parser extracts `{artifact: "sentence-transformers", version: "==2.2.2"}`

#### Scenario: pyproject.toml dependency
- **WHEN** a `pyproject.toml` contains `dependencies = ["fastapi>=0.100"]` under `[project]`
- **THEN** the parser extracts `{artifact: "fastapi", version: ">=0.100"}`

### Requirement: Edge storage format
The system SHALL store each extracted dependency as a row in the `edges` table with `edge_type='repo_dependency'`, `source_file=<repo_name>`, `target_file=<group:artifact or package_name>`, and `metadata` as JSON containing `{build_file, scope, version, type}`.

#### Scenario: Edge written to database
- **WHEN** the parser extracts a Gradle dependency from repo `toast-analytics`
- **THEN** an edge row is inserted with `codebase='toast-analytics'`, `source_file='toast-analytics'`, `target_file='com.toasttab:toast-common'`, `edge_type='repo_dependency'`

### Requirement: Incremental update support
The system SHALL support `--update` mode that hashes build file contents and skips repos whose build files have not changed since the last run.

#### Scenario: Unchanged build file skipped
- **WHEN** `cross-repo-deps.py --update` runs and a repo's build files have the same content hash as the last run
- **THEN** that repo's edges are not re-extracted or rewritten
