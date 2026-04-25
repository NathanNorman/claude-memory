## ADDED Requirements

### Requirement: Dataset format
The dataset MUST be a JSON file at `benchmarks/data/engineering_docs_qa.json` containing an array of QA objects. Each object MUST have: `question` (string), `answer` (string), `category` (one of: "single-hop", "multi-hop", "procedural", "entity-relationship"), `source_docs` (array of source file paths that contain the answer), and `difficulty` (one of: "easy", "medium", "hard").

### Requirement: Dataset size
The dataset MUST contain at least 50 QA pairs, with a target of 80-100. Each category MUST have at least 10 QA pairs.

### Requirement: Source documents
QA pairs MUST be derived from real engineering documentation available in `~/llm-memory/` source configs (sources_devx.json, sources_ghes_ops.json, sources_csi_idp.json). The actual markdown files MUST be collected into `benchmarks/data/engineering_docs/` for reproducibility.

### Requirement: Question categories
Four categories:
- **single-hop**: Answer found in a single document passage. "What port does X run on?"
- **multi-hop**: Answer requires connecting facts from 2+ documents. "Which team owns the service that handles X?"
- **procedural**: Answer involves ordered steps from a runbook. "What are the steps to do X?"
- **entity-relationship**: Answer requires understanding relationships between services/tools. "What services depend on X?"

### Requirement: Answer quality
Each answer MUST be verifiable against the source documents. Answers MUST be concise (1-3 sentences or a short list). No answers that require subjective judgment.

### Requirement: Generation process
QA pairs MUST be generated via LLM from the source docs, then human-curated. The generation script MUST be committed for reproducibility.
