# mgp-py

uv workspace of Python utilities for the Magic Gateway Protocol (MGP).

## Contents

| Package | Status | Description |
|---|---|---|
| [`packages/mcp-common`](./packages/mcp-common) | scaffold (v0.1.0) | Common utilities shared across MGP servers — mcp tooling, validation, embedding client, LLM provider helpers. |

Planned (no implementation yet):

- `packages/mgp-seal-py` — Python port of [`mgp-rs/crates/mgp-seal`](https://github.com/Cloto-dev/mgp-rs/tree/main/crates/mgp-seal) (Magic Seal HMAC-SHA256 + Ed25519 verification).
- `packages/mgp-sdk-py` — Python port of [`mgp-rs/crates/mgp-sdk`](https://github.com/Cloto-dev/mgp-rs/tree/main/crates/mgp-sdk) (connector manifest validation, source adapters, registry shape).
- `packages/validate-cli` — CLI for connector authors to validate `cloto-connector.json` locally against the MGP spec.

## Quick start

```bash
# Install uv (https://docs.astral.sh/uv/)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Clone and sync the workspace
git clone https://github.com/Cloto-dev/mgp-py.git
cd mgp-py
uv sync --all-packages

# Run tests across all packages
uv run pytest

# Lint
uv run ruff check .
```

## Layout

```
mgp-py/
├── pyproject.toml              # uv workspace root (not published)
├── packages/
│   └── mcp-common/             # First package
│       ├── pyproject.toml
│       ├── src/mcp_common/
│       └── tests/
└── .github/workflows/ci.yml
```

## Related projects

- [mgp-spec](https://github.com/Cloto-dev/mgp-spec) — Magic Gateway Protocol specification (MIT).
- [mgp-rs](https://github.com/Cloto-dev/mgp-rs) — Rust implementation of MGP utilities; sibling workspace to this one (MIT).
- [ClotoCore](https://github.com/Cloto-dev/ClotoCore) — Reference kernel implementation of MGP (BSL → MIT 2028).

## License

[MIT](./LICENSE).
