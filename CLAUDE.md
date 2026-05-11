# mgp-py Development Rules

uv workspace of Python utilities for **MGP** (see [`mgp-spec`](https://github.com/Cloto-dev/mgp-spec) for the canonical specification). Each package under `packages/*` is published independently; the workspace root itself is not a publishable package.

> Inherits: `../CLAUDE.md` — Conventions (RFC 2119), Public Repository English-only, Git Rules.

## Mandatory Reads

- **`../mgp-spec/docs/MGP_SPEC.md` + `../mgp-spec/docs/MGP_CONNECTOR.md`** — Protocol authority. `mgp-py` is a downstream consumer of these specs.
- **`../mgp-spec/schemas/connector/v1.json`** — JSON Schema authority for any `cloto-connector.json` validator that ships in this workspace (currently planned in `packages/mgp-sdk-py`).
- **`packages/mcp-common/src/mcp_common/`** — Current public surface (10 modules) historically sourced from `cloto-mcp-servers/servers/common/`. Treat this workspace as the single source of truth going forward.

## Workspace Layout

| Package | Path | Status | Role |
|---|---|---|---|
| `mcp-common` | `packages/mcp-common/` | v0.5.1 | 10 modules — foundation (`validation`, `isolation`, `no_persist`, `mcp_utils`), network/cache (`embedding_client`, `semantic_cache`, `search`), MCP tooling (`mgp_utils`), streaming (`mcp_stream_interceptor`, `llm_provider`) |
| `mgp-seal-py` | `packages/mgp-seal-py/` | planned | Python port of `mgp-rs/crates/mgp-seal` (HMAC-SHA256 + Ed25519 verification) |
| `mgp-sdk-py`  | `packages/mgp-sdk-py/`  | planned | Python port of `mgp-rs/crates/mgp-sdk` (connector manifest validation, source adapters, registry shape) |
| `validate-cli` | `packages/validate-cli/` | planned | CLI for connector authors to validate `cloto-connector.json` locally |

**Workspace root** (`pyproject.toml`): `package = false`, `[tool.uv.workspace] members = ["packages/*"]`, Python `>=3.11`.

## Commands

```bash
uv sync --all-packages       # Install workspace + dev tooling
uv run pytest                # Run all package tests
uv run ruff check .          # Lint
uv run ruff format --check . # Format check
uv run ruff format .         # Auto-format
```

CI (`.github/workflows/ci.yml`) runs `uv sync --all-packages` → `ruff check .` → `pytest` on push to `main` and on every PR.

## Dev Dependency Discipline (`feedback_uv_dev_dependencies`)

- **MUST**: Dev tooling (ruff, pytest, pytest-asyncio, etc.) is declared in the **root** `pyproject.toml`'s `[dependency-groups].dev`, **not** in any package's `pyproject.toml`. `uv sync` implicitly enables the `dev` group at the workspace root.
- **MUST**: Any new dev-only dep goes in root `[dependency-groups].dev`. Package-level `[project].dependencies` is for runtime deps only.
- **Reason**: Phase 2 Step 1 (PR #1) initially declared `ruff` / `pytest` inside `packages/mcp-common/pyproject.toml`; CI failed because `uv sync` did not pick them up at root. Lifting to `[dependency-groups].dev` greened CI.

## pytest-asyncio (strict mode)

- **MUST**: `asyncio_mode = "strict"` in root `[tool.pytest.ini_options]`. Only tests explicitly decorated `@pytest.mark.asyncio` run as coroutines; sync tests remain unaffected.

## ruff Configuration

- **`line-length = 100`** (root). Upstream `cloto-mcp-servers/servers/common/` uses `120`; when porting modules in, long signatures and strings **MUST** be wrapped (multi-line params, intermediate variable, implicit string join). Behavior is unchanged; only formatting moves.
- **`target-version = "py311"`** consistent with workspace Python floor.
- **Lint select**: `E`, `F`, `I`, `W`, `UP`. `UP037` may auto-unquote string annotations under `from __future__ import annotations` — review such changes carefully.

## Per-Package Version & Release

- **MUST**: Each `packages/<pkg>/pyproject.toml` carries its own `version = "X.Y.Z"`. Workspace root is `version = "0.0.0"` and `package = false` (never published).
- **SHOULD**: Tag releases per package, namespaced: `{package-name}-vX.Y.Z` (e.g. `mcp-common-v0.5.1`). At time of writing the repo has no tags — pin via commit SHA or branch ref until tagging starts.
- **SHOULD**: Document version bump rationale (minor for additive ports, patch for fixes) in the bumping PR. Phase 2 Step 2-α/β/γ/δ used minor bumps (cadence); Step 2-ε was patch (release-signal choice). Cadence is not automatic.

## Adding a New Workspace Package

Before proposing a new `packages/<name>/` directory:

1. **MUST** apply the **5-axes split principle** (`feedback_repo_split_5_axes`) at the package level — does this content really warrant its own package vs. living inside `mcp-common` (or another existing one)?
2. **MUST** declare dev tooling in root `[dependency-groups].dev`, not in the new package's `pyproject.toml`.
3. **MUST** ensure the new directory matches the workspace glob (currently `packages/*` — any subdir is auto-discovered).

## Connector Schema Conformance (when `mgp-sdk-py` lands)

- **MUST**: When `packages/mgp-sdk-py` ships its `cloto-connector.json` validator, it conforms to `mgp-spec/schemas/connector/v1.json` and follows the SDK leniency α/β taxonomy (`feedback_sdk_leniency_taxonomy`): strict-by-default validators reject acceptance leniency (β); lenient modes are explicit opt-in APIs.

## Serial Sub-Phase Land Methodology (`feedback_serial_sub_phase_land_methodology`)

Multi-PR migrations within a single session use four reusable strategies (established 2026-05-11 across Phase 2 Step 2-α/β/γ/δ/ε):

- **Layer split** — Stack sub-PRs by dependency layers (foundation → network/cache → MCP tooling → streaming → integration).
- **Forward-ref tolerance** — Cross-package type hints can sit in `TYPE_CHECKING:` blocks dangling until the referenced module lands in a later sub-phase. Mark transient ones with a comment.
- **Duck-typed mock tests** — When upstream tests cover multiple modules at once, write small duck-typed tests for the in-PR module rather than porting cross-module tests prematurely; bring upstream tests in when their full dependency chain lands.
- **Step pattern repetition** — Apply the same flow (`cp source → cp test → adjust imports → ruff check/fix → ruff format → pytest → version bump → commit → push → PR → CI poll → admin merge`) per sub-phase for compounding throughput.

## Public Repo Implications

This repo is `visibility=public` and MIT-licensed. Per the parent rule:

- All Markdown, docstrings, `pyproject` field strings, commit messages, and PR descriptions **MUST** be English.

## Prohibited

- **MUST NOT**: Place protocol normative text in this repo — that lives in [`mgp-spec`](https://github.com/Cloto-dev/mgp-spec).
- **MUST NOT**: Add a new dev-only dependency directly to a package `pyproject.toml`. Use root `[dependency-groups].dev`.
- **MUST NOT**: Bypass `ruff check` / `ruff format --check` via `--no-verify` push. Fix the violation; CI will catch otherwise.
- **SHOULD NOT**: Bundle unrelated dep updates with feature PRs — keep `uv.lock` churn reviewable.
