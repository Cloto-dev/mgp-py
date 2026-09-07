# Issue registry schema

`qa/issue-registry.json` is the defect ledger for this workspace. It is not a record of
past work: `scripts/verify_issues.py` reads every entry on each CI run and checks the
named file for the named pattern, so an entry that no longer describes the tree fails
the build. The registry is a machine-checked claim about the code, and this file
describes the shape of one claim.

This document is named by the registry's own `$schema` field, and the checker refuses a
registry whose `$schema` is absent, is a URL or an absolute path, or does not resolve to
a file in this repository. A pointer nothing follows is free to be wrong in the one way
that matters: in a sibling registry that field held a URL which had rotted into a 404,
and in another it named a file that repository does not contain — both with their gates
green for months.

## File shape

```json
{
  "$schema": "qa/issue-registry.schema.md",
  "description": "...",
  "issues": [ { ... }, { ... } ]
}
```

There is no schema version number at the root. The registry has one producer and one
consumer, both in this repository and versioned together by git, so there is no version
skew for such a field to describe — and a number nothing compares is not a version. It
used to say `"1.0"` here, and the same in three sibling registries whose shapes had
already diverged from this one and from each other. The per-entry `version` field below
is unrelated: it says which release a defect was found in.

## Entry fields

| Field | Required | Meaning |
| --- | --- | --- |
| `id` | yes | `bug-NNN`, permanent and never reused. **Ids are local to this repository**: a `bug-NNN` here and the same number in a sibling registry are unrelated. |
| `title` | yes | One line: what is wrong. This is what the checker prints for the entry. |
| `severity` | yes | `CRITICAL` / `HIGH` / `MEDIUM` / `LOW`. |
| `status` | yes | `open` or `fixed`. `--filter` selects on it. |
| `file` | yes | Repo-relative path the pattern is checked against. **Follows the anchor**, not the defect's history — when a fix lands in a different file than the report named, move this with it. |
| `pattern` | yes | A Python `re` regular expression, compiled once and applied line by line. Never empty: the checker treats an empty pattern as a missing field, because a pattern that matches everything proves nothing. |
| `expected` | yes | `present` or `absent`. See below. |
| `description` | no | The long form: what was measured, how it was reached, why it matters. `title` is the line; this is the case. |
| `measured` | no | **The observed value, not the reading of the code.** What was actually run and what actually came back. An entry whose evidence is only "the code appears to do X" is a guess with a line number attached; this field is where the difference is recorded. |
| `category` | no | Free-form kebab-case grouping (`protocol-conformance`, `resource-budget`, …). |
| `discovered` | no | ISO-8601 date. |
| `version` | no | The version the defect was found in. Per entry — unrelated to the file shape. |

## Why this checker is Python

Two failure modes were observed in sibling registries, and both are silent, which is
the worst property a checker can have:

- A shell reader that joins fields with a delimiter loses any row whose pattern contains
  that delimiter. The row shifts, and the entry drops out of verification while the run
  still reports success.
- "`grep -P`, falling back to `grep -E`" is two regular-expression dialects, so the same
  registry can be checked differently on a developer's machine and in CI. Here every
  pattern is compiled once, by `re`, everywhere.

The siblings have since closed the first of these. The second is structural: a
`grep`-based checker has two dialects available to it and picks whichever the host
provides. **Patterns are therefore not portable between this registry and a sibling's**,
and neither are entries.

## `expected`, and how it changes across a fix

- **`present`** — the pattern must match. For an `open` entry this anchors the *defect*
  (proof it is still there). For a `fixed` entry it anchors the *fix* (proof it has not
  been reverted), which is why a `bug-NNN` marker in a comment makes a good pattern.
- **`absent`** — the pattern must NOT match. Used for a `fixed` entry whose fix
  *removed* the offending construct. A file that no longer exists counts as absent.

A pattern that matches nothing while claiming presence is a failure, not a pass: a pin
that matches nothing reports nothing, so it would let the defect it names disappear
without anyone noticing.

**Fixing a registered bug therefore means editing its entry in the same change**: flip
`status` and re-anchor `pattern`/`expected`/`file` on whatever now proves the fix.

## What the checker refuses

- a registry that does not parse, or that carries no `issues` list
- a `$schema` that is absent, is a URL or absolute path, or resolves to no file here
- a duplicate `id`
- a missing or empty required field
- a `pattern` that does not compile
- an `expected` that is neither `present` nor `absent`
- a `present` pattern that matches nothing, or an `absent` one that still matches
- a run in which **nothing was checked** — an empty registry, or a filter matching
  nothing, is not a clean bill of health

`packages/mcp-common/tests/test_issue_registry.py` drives the shipped script through a
subprocess and asserts each of these, so a change that turns the checker into an
unconditional pass fails there rather than passing quietly.

## Running it

```bash
python scripts/verify_issues.py                 # everything
python scripts/verify_issues.py --filter open
```

Exit code 0 means every entry checked still describes the tree. The test suite runs it
too, so it cannot be skipped by forgetting a step.
