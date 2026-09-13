# Maintaining the shape tables

`_session_extractor_core.py` recognizes providers by shape, not by name — three
small tables at the top of the file (`WRAPPER_KEYS`, `CONTAINER_KEYS`, `ROLE_KEYS`,
plus `CONTENT_KEYS`/`TIMESTAMP_KEYS` and the role/content-item synonym maps) instead
of one hardcoded function per provider. Extending coverage almost always means
adding a table row, not writing code. Run `--diagnose` on an unfamiliar file first —
it tells you exactly which table entries matched and lists the unmatched keys as
candidates.

## Keeping in step with ChatVault, by hand, on purpose

This tool's role/content/timestamp identifier logic was originally extracted into
ChatVault's `src/chatvault/adapters/identifiers.py` — so the two files describe
overlapping ground. **They are not synced automatically, and never should be**: this
repo is stdlib-only and must not depend on ChatVault or its third-party packages
(`orjson`, etc.), and ChatVault's adapters solve a different problem (a CIR aimed at
a database loader, with attachment/project/dedupe handling this converter has no use
for). Porting adapters instead of table rows was tried in an earlier design pass and
rejected — see `docs/AUDIT_2026-09-10_coverage_and_fidelity.md` §3.

When ChatVault's `identifiers.py` (or a new adapter) learns a new container/wrapper/
role key, add the equivalent row here too, by hand, with a comment naming the format
it came from. The reverse direction matters as much: when `--diagnose` here finds a
shape neither project recognizes, that's worth a note in both places. This is a
fifteen-to-twenty-row table that changes a few times a year — manual is the correct
amount of process for that rate of change.

## Antigravity — a documented limitation, not a missing table row

The original spec assumed Antigravity's `steps[].step_payload` was a nested JSON
dict, like every other supported format. It is not, in the currently-shipping
IDE/CLI builds: it's a schema-less Protocol Buffers blob stored inside a per-
conversation SQLite `.db` file (confirmed against real files — see ChatVault's
`docs/reports/antigravity_shapes_2026-09-09.md`, which reverse-engineered the wire
format field-by-field with no `.proto` schema available anywhere in the app).

A stdlib-JSON-only converter cannot decode that. Doing so would mean either a
third-party `protobuf` dependency (against this repo's zero-dependency guarantee) or
a hand-written protobuf wire-format walker — a fundamentally different, much larger
piece of work than "add a table row," and out of scope for this pass. The
`step_payload`/`steps` table entries are kept anyway (harmless, and correct if a
future export variant ships as plain JSON), but no populated Antigravity fixture is
shipped in `tests/fixtures/antigravity/` — only an empty conversation, used to check
the SKIP path stays clean. `--diagnose` on a real Antigravity `.db`-derived JSON dump
will show every leaf as an opaque base64 string and near-zero coverage; that is the
tool being honest about a shape it cannot read, which is the entire point of §3's
self-report, not a bug to chase away with a fake table entry.

If someone wants real Antigravity support later, it's a standalone project (a
schema-less protobuf wire-format reader, targeting the specific field paths
catalogued in the report above) — not a "add three lines to `WRAPPER_KEYS`" change.

## A note on `_ROLE_SYNONYMS["model"]`

An earlier draft of the shape tables added `"model": "assistant"` to the role
synonym map (Gemini-style `role: "model"` records are common). It was deliberately
left out: `samples/sample_gemini.json`'s `"model"` role renders unmapped in v4.0
(`## Model`, not `## Assistant`), and the regression test
(`tests/run_tests.py::TestChatRegressionGolden`) requires v5.0's CHAT output
to be byte-identical to the frozen v4.0 output in `tests/expected/chat/` for
every format v4.0 already supported. If you want that mapping, update the golden
files (deliberately, with the reason recorded) rather than silently reintroducing
it — don't just add the entry back.

## Where things live

- **Core logic + three tables:** `_session_extractor_core.py` — `WRAPPER_KEYS`,
  `CONTAINER_KEYS`, `ROLE_KEYS`, `CONTENT_KEYS`, `TIMESTAMP_KEYS`,
  `_ROLE_SYNONYMS`, `NON_MESSAGE_TYPES`, `_CONTENT_ITEM_TYPE_MAP`,
  `SIBLING_TOOL_DATA_KEYS` — all in one block near the top, each entry commented
  with the format it was verified against.
- **Coverage exclusions:** `_STRUCTURAL_KEY_NAMES` / `_STRUCTURAL_PATH_PREFIXES`, same
  file, §3 section. Add an entry only when you can name why a path is structural
  (an id, a hash, a UI-state dump) — when in doubt, leave it accountable; an
  under-counted gap is the bug this feature exists to catch, an over-counted one is
  just noise in the report.
- **Fixtures:** `tests/fixtures/<provider>/`, one real (redacted) file per provider,
  sourced from ChatVault's own test fixtures and re-verified by reading in full
  before committing.
- **Regression + acceptance tests:** `tests/run_tests.py` (stdlib `unittest`).
