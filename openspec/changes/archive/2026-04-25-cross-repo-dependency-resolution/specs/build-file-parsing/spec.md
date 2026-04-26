## ADDED Requirements

### Requirement: Gradle Kotlin DSL dependency extraction
The system SHALL parse `build.gradle.kts` files and extract dependency declarations matching the configurations: `implementation`, `api`, `compileOnly`, `runtimeOnly`, `testImplementation`, `testRuntimeOnly`, and `kapt`. Each extracted dependency SHALL include group, artifact, version (if present), scope, and whether it is an internal project dependency.

#### Scenario: Standard string notation
- **WHEN** a `build.gradle.kts` contains `implementation("com.toasttab:toast-common:1.2.3")`
- **THEN** the parser SHALL return `{group: "com.toasttab", artifact: "toast-common", version: "1.2.3", scope: "implementation", is_internal: false}`

#### Scenario: Internal project dependency
- **WHEN** a `build.gradle.kts` contains `implementation(project(":module-name"))`
- **THEN** the parser SHALL return `{group: null, artifact: "module-name", version: null, scope: "implementation", is_internal: true, module_path: ":module-name"}`

#### Scenario: Dependency without version
- **WHEN** a `build.gradle.kts` contains `implementation("com.toasttab:toast-common")` (no version, managed by BOM)
- **THEN** the parser SHALL return `{group: "com.toasttab", artifact: "toast-common", version: null, scope: "implementation", is_internal: false}`

### Requirement: Gradle Groovy DSL dependency extraction
The system SHALL parse `build.gradle` files and extract dependency declarations using both string notation (`"group:artifact:version"`) and map notation (`group: 'x', name: 'y', version: 'z'`).

#### Scenario: Groovy string notation
- **WHEN** a `build.gradle` contains `implementation 'com.toasttab:toast-common:1.2.3'`
- **THEN** the parser SHALL return the same structured record as the Kotlin DSL equivalent

#### Scenario: Groovy map notation
- **WHEN** a `build.gradle` contains `implementation group: 'com.toasttab', name: 'toast-common', version: '1.2.3'`
- **THEN** the parser SHALL return `{group: "com.toasttab", artifact: "toast-common", version: "1.2.3", scope: "implementation", is_internal: false}`

### Requirement: Gradle version catalog parsing
The system SHALL parse `gradle/libs.versions.toml` files to build an alias-to-coordinate mapping. When a `build.gradle.kts` references `libs.<alias>`, the system SHALL resolve it to the full coordinate using this mapping.

#### Scenario: Version catalog with library definition
- **WHEN** `gradle/libs.versions.toml` contains `[libraries]\ntoast-common = { group = "com.toasttab", name = "toast-common", version.ref = "toastCommon" }` and `[versions]\ntoastCommon = "1.2.3"`
- **THEN** the alias `toast-common` SHALL resolve to `com.toasttab:toast-common:1.2.3`

#### Scenario: Build file referencing version catalog
- **WHEN** a `build.gradle.kts` contains `implementation(libs.toast.common)` and the version catalog maps `toast-common` to `com.toasttab:toast-common:1.2.3`
- **THEN** the parser SHALL return the resolved coordinate `{group: "com.toasttab", artifact: "toast-common", version: "1.2.3", scope: "implementation", is_internal: false}`

### Requirement: Gradle settings file parsing
The system SHALL parse `settings.gradle.kts` and `settings.gradle` to extract `include()` declarations for multi-module project discovery.

#### Scenario: Multi-module project includes
- **WHEN** `settings.gradle.kts` contains `include(":module-a", ":module-b")`
- **THEN** the parser SHALL return the module paths `[":module-a", ":module-b"]`

### Requirement: Maven POM dependency extraction
The system SHALL parse `pom.xml` files using `xml.etree` and extract dependencies from `<dependencies>` sections, handling property interpolation for `${property.name}` references.

#### Scenario: Standard Maven dependency
- **WHEN** a `pom.xml` contains `<dependency><groupId>com.toasttab</groupId><artifactId>toast-common</artifactId><version>1.2.3</version></dependency>`
- **THEN** the parser SHALL return `{group: "com.toasttab", artifact: "toast-common", version: "1.2.3", scope: "compile", is_internal: false}`

#### Scenario: Property interpolation
- **WHEN** a `pom.xml` declares `<properties><toast.version>1.2.3</toast.version></properties>` and a dependency uses `<version>${toast.version}</version>`
- **THEN** the parser SHALL interpolate the version to `1.2.3`

#### Scenario: Parent POM reference
- **WHEN** a `pom.xml` contains a `<parent>` section with `<groupId>com.toasttab</groupId><artifactId>toast-parent</artifactId><version>2.0</version>`
- **THEN** the parser SHALL return the parent as a dependency record with `scope: "parent"`

### Requirement: Python dependency extraction
The system SHALL parse `pyproject.toml` (via `tomllib`) and `requirements.txt` to extract Python package dependencies.

#### Scenario: pyproject.toml dependencies
- **WHEN** a `pyproject.toml` contains `[project]\ndependencies = ["requests>=2.28", "numpy"]`
- **THEN** the parser SHALL return `[{package_name: "requests", version_spec: ">=2.28", is_dev: false}, {package_name: "numpy", version_spec: null, is_dev: false}]`

#### Scenario: requirements.txt with version pins
- **WHEN** a `requirements.txt` contains `requests==2.28.1\nnumpy>=1.24`
- **THEN** the parser SHALL return the same structured records with appropriate version specifiers

#### Scenario: requirements.txt with recursive includes
- **WHEN** a `requirements.txt` contains `-r base-requirements.txt`
- **THEN** the parser SHALL follow the include and parse the referenced file

### Requirement: npm dependency extraction
The system SHALL parse `package.json` to extract `dependencies`, `devDependencies`, and `peerDependencies`.

#### Scenario: npm package.json parsing
- **WHEN** a `package.json` contains `{"dependencies": {"react": "^18.2.0"}, "devDependencies": {"jest": "^29.0"}}`
- **THEN** the parser SHALL return `[{package_name: "react", version_range: "^18.2.0", dep_type: "dependencies"}, {package_name: "jest", version_range: "^29.0", dep_type: "devDependencies"}]`

### Requirement: Build dependency edge storage
The system SHALL store parsed build dependencies as edges in the `edges` table with `edge_type = 'build_dependency'`. The `source_file` SHALL be the relative path of the build file. The `metadata` SHALL contain the dependency coordinate string. For internal project deps, `target_file` SHALL point to the module path within the same codebase.

#### Scenario: External dependency edge
- **WHEN** a Gradle file declares `implementation("com.toasttab:toast-common:1.2.3")`
- **THEN** an edge SHALL be stored with `source_file = "build.gradle.kts"`, `target_file = NULL`, `edge_type = "build_dependency"`, `metadata = "com.toasttab:toast-common:1.2.3"`

#### Scenario: Internal project dependency edge
- **WHEN** a Gradle file declares `implementation(project(":core"))`
- **THEN** an edge SHALL be stored with `source_file = "submodule/build.gradle.kts"`, `target_file = "core"`, `edge_type = "build_dependency"`, `metadata = "project::core"`

### Requirement: CLI --build-deps flag
The system SHALL add a `--build-deps` flag to `codebase-index.py` that triggers build file parsing and dependency edge storage for the specified codebase.

#### Scenario: Index with build deps
- **WHEN** the user runs `codebase-index.py --path ~/repo --name my-repo --build-deps`
- **THEN** the system SHALL discover and parse all build files in the repo and store dependency edges

#### Scenario: Incremental build deps with --update
- **WHEN** the user runs with `--build-deps --update` and a build file has not changed
- **THEN** the system SHALL skip re-parsing that build file
