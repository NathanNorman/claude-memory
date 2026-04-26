# Session Notes — 2026-03-30

## What happened

Started by reading Google Research's blog post on TurboQuant, then fetched and read all three papers in the family:

- **QJL** (AAAI 2025, arXiv:2406.03482) — 1-bit quantization via JL transform + sign bit
- **PolarQuant** (AISTATS 2026, arXiv:2502.02617) — polar coordinate quantization after random rotation
- **TurboQuant** (ICLR 2026, arXiv:2504.19874) — unified framework: Cartesian scalar quantization + QJL residual correction

## Deep research phase

After initial read, identified 6 weak points in understanding. Launched 6 parallel research agents:

1. **Polar transform inverse** — Worked through a full d=8 numeric example. The inverse formula's indicator functions are binary tree path selection (each coordinate's binary representation picks cos/sin at each level).
2. **Panter-Dite formula** — The 1/3 power comes from optimizing λ(x) under linear budget with quadratic distortion penalty. Exponent = 1/(r+1) for r-th moment; MSE (r=2) → 1/3. Breaks at low b because "locally constant density" assumption fails.
3. **TurboQuant vs PolarQuant** — The blog post is **wrong** — TurboQuant never uses polar coordinates. It treats PolarQuant as a competing baseline. Both use random rotation but decompose the sphere differently.
4. **Outlier handling** — Error is additive across outlier/inlier groups. Mixed-precision config has **no formal theorem** — it's engineering on top of uniform-bit theory. PolarQuant eliminates outliers via rotation instead of splitting.
5. **QJL distortion constants** — The "smaller constants" claim is about concentration bounds (4/3 vs 4 in sample complexity), not variance. Sign-quantization converts sub-exponential tails to sub-Gaussian.
6. **Shannon lower bound** — Verified the algebra: d/(2πe) from SLB cancels 2πe/d from sphere entropy. Inner product lower bound is existential (some y is hard), not universal.

## Industry adoption research

Searched GitHub for framework integration. Found remarkably fast adoption (4 days from publication):

- **vLLM** — Issue #38171 (70 👍), PR #38280 with working integration, 7.5× cache reduction benchmarked
- **SGLang** — Issue #21618, draft PR #21617 with 42 passing tests and Triton kernels
- Also: llama.cpp, Ollama, MLX, LM Studio all have open feature requests

## Blog post

Wrote and deployed a full article to the Vercel blog at `nathan-norman.vercel.app/blog/turboquant-kv-cache-compression`. Covers all three papers, corrects the blog post's PolarQuant misconception, includes benchmark tables and the adoption timeline.

## Application to unified-memory

Explored applying TurboQuant to `~/claude-memory`. Key conclusions:

- At current scale (2,115 vectors, 384-dim, 3.2MB) — **not worth it** for storage alone
- The real unlock is what **becomes practical**: higher-dim embedding models, fine-grained chunking, codebase indexing, ColBERT-style multi-vector
- Total one-time indexing cost for everything: ~2 hours on CPU — totally doable as overnight job
- **Bottleneck is embedding computation** (98 embeds/sec on CPU), not storage. TurboQuant solves storage/search but not embedding generation.

## OpenSpec change created

Created `unified-memory-turboquant-upgrade` with 4 artifacts:

- **Proposal** — 3 new capabilities (vector-quantization, bulk-indexer, codebase-embedding)
- **Design** — 6 decisions (MSE-only, 4-bit default, Walsh-Hadamard rotation, Python-only quantization, configurable model, AST chunking)
- **Specs** — 3 capability specs with 19 total requirements
- **Tasks** — 12 tasks across 3 phases, ready for implementation
