# SPEC v5 — Shape-driven extraction

**Written:** 2026-09-10 · **Status: not started**
**Audience:** a coding model executing this end to end. Written to be executed without further questions.
**Read first:** `AUDIT_2026-09-10_coverage_and_fidelity.md` (the evidence), then `_session_extractor_core_v4.0.py` **in full**.

---

## 0. Goal and non-goals

**Goal:** make the tool as provider-agnostic as a stdlib-only converter can be, and make it *tell you* when it doesn't understand something — while keeping today's functionality, architecture and zero-dependency guarantee intact.

**Non-goals — do not build these:**

- No ChatVault adapters ported in. No `orjson`, no third-party packages, ever.
- No database, no config files, no plugin system, no provider auto-detection registry.
- No new CLI surface beyond §4. No GUI changes; the three `.app` wrappers keep working unchanged.
- No rewrite. `v5` is a **duplicate-and-fix** of the v4.0 line, following the same convention v4.0 used against v3.x: new files `_session_extractor_core.py` + `markdown_extraction_{chat,tools,raw}.py`, originals untouched.

**Sizing:** roughly 300 lines changed, mostly data. If a change is growing past that, it is the wrong change.

---

## 1. The core change — three tables replace four hardcoded extractors

### 1.1 What is wrong today

`extract_messages()` dispatches through `_from_openclaw` → `_from_rollout` → `_from_history` → `_from_generic`, then falls back to descending `COLLECTION_KEYS`. Each of those functions hardcodes one provider's nesting. A format whose wrapper key is `chat_message` instead of `message` fails, despite being structurally identical.

### 1.2 What replaces it

Three module-level tables and one generic resolver.

```python
# ── Keys whose VALUE IS the real record. Unwrap recursively, then re-resolve.
#    Sources (verified in ChatVault fixtures): OpenClaw/Claude `message`,
#    Codex rollout `payload`, Devin CLI `message_nodes[].chat_message`,
#    Devin Desktop ACP `messages[].payload`, ChatGPT `mapping.<id>.message`,
#    Antigravity `steps[].step_payload`.
WRAPPER_KEYS: tuple[str, ...] = (
    "message", "chat_message", "payload", "step_payload", "msg", "data",
)

# ── Keys whose VALUE IS a collection of records. Descend into each.
#    Extends v4.0's COLLECTION_KEYS with the containers found since.
CONTAINER_KEYS: tuple[str, ...] = (
    "messages", "chat_messages", "message_nodes", "bubbles", "steps",
    "items", "turns", "conversation", "conversations", "entries",
    "mapping", "events", "history", "nodes",
)

# ── Keys that may carry the role. Checked in order; first non-empty wins.
ROLE_KEYS: tuple[str, ...] = (
    "role", "sender", "author", "kind", "speaker", "from", "type",
)
```

`ROLE_KEYS` deliberately ends with `type`, which is also used for non-role purposes — so it is checked last, and a value that resolves to a known non-message record type (`NON_MESSAGE_TYPES`) must not be treated as a role.

Extend `norm_role`'s synonym map with the values those keys carry:

```python
_ROLE_SYNONYMS.update({
    # ACP (Devin Desktop)
    "user_message": "user", "agent_message": "assistant",
    "agent_thought": "assistant", "tool_call": "tool", "subagent": "assistant",
    # Cursor numeric type codes
    "1": "user", "2": "assistant",
    # misc observed
    "human": "user", "model": "assistant", "chatbot": "assistant",
})
```

**Numeric roles need care:** `1`/`2` are Cursor-specific. Only apply the numeric mapping when the role came from a key in a record that also looks like a Cursor bubble (e.g. has `bubbleId` or `richText`). Guard it; do not map bare integers globally, or every `type: 1` in every format becomes a user turn. Implement the guard as a small predicate, and say in a comment what evidence it keys on.

### 1.3 The resolver

Replace the four `_from_*` functions with one:

```python
def resolve_record(cfg, rec, depth=0):
    """Turn one dict into zero or more Messages, by shape, not by provider.

    1. If rec['type'] is in NON_MESSAGE_TYPES -> return [].
    2. If any WRAPPER_KEYS key holds a dict -> unwrap and recurse
       (merging outer timestamp/model if the inner lacks them).
    3. If any CONTAINER_KEYS key holds a dict/list -> descend into each,
       time-sorted, and concatenate.
    4. Otherwise treat rec as a leaf record: role via ROLE_KEYS,
       content via CONTENT_KEYS, timestamp via TIMESTAMP_KEYS,
       thinking via extract_thinking().
    """
```

Requirements:

- **Depth-limit the recursion** (say 12) and count it, so a pathological or cyclic structure cannot hang. Report if the limit is hit.
- **Merge outer context on unwrap.** Devin CLI's timestamp lives on the outer `message_nodes` row, not inside `chat_message`; ChatGPT's `mapping` node carries `parent`/`children` outside `message`. When unwrapping, carry down any timestamp/model the inner record lacks.
- **Preserve v4.0 behaviour exactly** for the four formats that work today. Prove it (§5.1).
- Keep `_from_history`'s `session_id` + `text` special case only if step 4 does not already cover it. Check; delete it if redundant.

### 1.4 Why this is the agnostic answer

Provider knowledge stops being code and becomes fifteen table rows. A format the tool has never seen works if it nests in any of the observed ways — which is most of them, because there are only so many ways to nest a conversation. When one genuinely doesn't, §3's coverage report says so by name, and the fix is one table entry rather than one function.

---

## 2. Fix RAW mode — make block filtering mode-aware

**This is the highest-severity item. Do it first.** See audit §1.

`extract_content()` unconditionally drops every item type in `_SKIP_ITEM_TYPES`, in all three modes. RAW therefore loses tool calls and tool results entirely for content-item-nesting providers, and drops whole messages that contained only those.

### 2.1 Change

Add a `keep_item_types: set[str]` field to `Config` and thread `cfg` into `extract_content()`.

| Mode | Behaviour |
|---|---|
| **CHAT** | Unchanged from v4.0 — drop tool/function/image/audio/refusal items. Dialogue only. |
| **TOOLS** | **Keep** `tool_call`/`tool_use`/`tool_result`/`function_call`/`function_call_output`/`function_result`/`custom_tool_call*`/`toolcall`, rendered as their own blocks. Keep thinking. Keep system/developer records (as today). Drop `image`/`audio`/`refusal` payload bodies, rendering a one-line stub instead. |
| **RAW** | Keep everything. No item type is dropped. `refusal` and `error` render as their own labelled blocks. |

Also change `TOOLS_CFG.skip_roles`: it currently drops `tool`, `function`, `toolresult`, `tool_result`, `function_result`. **Remove those** — a tier called "Tools" that drops tool records is the same defect at the record level. Keep only `info`.

### 2.2 Rendering

`Message` gains an optional ordered `blocks: list[tuple[str, str, str|None]]` — `(type, text, tool_name)` — so a message can carry text, thinking and tool calls in source order rather than the current text-plus-optional-thinking pair. `write_md()` renders:

- `text` → as now
- `thinking` → as now (`### Role (thinking)` + blockquote)
- `tool_use` → `> **tool: <name>**` followed by the arguments in a fenced ```json block
- `tool_result` → fenced ```text block, with the existing `render_tool_in_codeblock` styling
- `image`/`audio`/`attachment` → one-line stub `*[image]*` etc.
- `refusal`/`error` → `**refusal:** …` / `**error:** …`
- anything unrecognised → `*[<type> block]*` **with its text if it has any** — never a bare placeholder that discards content

**Never emit an empty message.** If every block is filtered out for the current mode, skip the message — but count it (§3).

### 2.3 Update the README's block-type table to match reality

The current table claims RAW keeps tool calls, which it does not. After this fix it will. Verify the table row by row against actual output before publishing; do not hand-edit it from intent.

---

## 3. Coverage self-report — the differentiating feature

See audit §5. This is what makes the tool honest by construction.

### 3.1 Measure

Before extraction, walk every source record and total the character count of every string-valued leaf, recording the count per key path. After extraction, total the characters actually written to Markdown.

```
coverage = emitted_chars / accountable_chars
```

`accountable_chars` excludes paths that are legitimately structural — ids, hashes, uuids, booleans-as-strings, timestamps, and anything on the `NON_MESSAGE_TYPES` path. **Define the exclusion list explicitly in code with a comment per entry**, so the number means something. When in doubt, count it as accountable — over-reporting a gap is safe, under-reporting is the bug this feature exists to prevent.

### 3.2 Report

In `conversion_log.md`, per file:

```markdown
- **Coverage:** 94% of accountable content emitted (RAW mode)
- **Unaccounted paths (top 5 by volume):**
  - `steps[].step_payload.observation` — 12,400 chars, 47 records
  - `meta.workspace_context` — 3,100 chars, 1 record
```

Thresholds: **≥90%** report silently; **50–89%** add a `⚠ Warning` line; **<50%** add a `⚠⚠ Likely unsupported shape` line naming the top unaccounted paths and pointing at the tables in §1 as the place to add support.

A file that currently produces `SKIP — No conversational text found` should instead produce that same skip **plus** the top key paths it saw, so the user knows what the format looks like without opening it.

### 3.3 Exit code

Non-zero exit if any file falls below 50%, so the tool is usable in a script. Add `--allow-low-coverage` to suppress.

---

## 4. `--diagnose` — the investigation mode

The tool's real daily use is reconnaissance on unfamiliar CLI-tool data. Make that a first-class mode instead of a side effect.

```bash
python3 markdown_extraction_raw.py --diagnose path/to/file_or_folder
```

Emits `diagnosis.md` — no conversion, no Markdown output:

1. Every key path in the file, full depth, with occurrence counts and value types.
2. Every string-valued leaf path ranked by total character volume.
3. Distinct record shape signatures (sorted top-level key sets) with counts.
4. Which `WRAPPER_KEYS` / `CONTAINER_KEYS` / `ROLE_KEYS` matched, and which did not.
5. Distinct values found under each matched role key.
6. A suggested table entry for anything unmatched: *"add `foo_payload` to WRAPPER_KEYS"*.

**This must never sample.** Full enumeration of every record of every file. That rule is the point of the mode.

---

## 5. Acceptance criteria

| # | Criterion | How |
|---|---|---|
| 5.1 | **No regression on today's working formats** | Run v4.0 and v5.0 CHAT mode over every file in `samples/` and `windsurf-legacy_chats/`. Output must be byte-identical except the `Extracted:` timestamp line. Any difference must be an intended §2 change, explained. |
| 5.2 | **RAW keeps tool calls** | The audit §1 three-record test: RAW emits 3 messages including `tool_use read_file` with its input and the `tool_result` body. |
| 5.3 | **TOOLS keeps tool calls** | Same test in TOOLS mode emits the tool blocks. |
| 5.4 | **CHAT unchanged** | Same test in CHAT mode emits exactly the v4.0 output. |
| 5.5 | **Six previously-SKIPping providers now convert** | One real fixture each for `chatgpt` (official mapping), `claude_desktop`, `devin_cli`, `devin_desktop_acp`, `cursor`, `antigravity`. Each produces a file with ≥90% coverage. |
| 5.6 | **Coverage report is honest** | A deliberately unsupported shape reports <50% and names its top paths. A fully-supported file reports ≥90%. Neither number is hardcoded or fudged. |
| 5.7 | **`--diagnose` enumerates fully** | On a file with a key appearing in exactly one record out of 400, `--diagnose` lists it. |
| 5.8 | **Still stdlib-only** | `python3 -c "import ast,sys; ..."` sweep over all v5 files confirms no non-stdlib import. Runs on a clean Python 3.9. |
| 5.9 | **UTF-8 accounting preserved** | v4.0's `errors="replace"` + U+FFFD counting still works and still surfaces in the log. |
| 5.10 | **The `.app` wrappers still work** | Automator wrappers point at v5 scripts and run drag-and-drop. |

---

## 6. Test corpus — and how to stay in step with ChatVault without coupling

### 6.1 Fixtures

The repo ships three synthetic samples. That is not enough to have caught this gap, and it will not catch the next one.

Take one representative fixture per provider from ChatVault's `tests/fixtures/`, **redact any real content**, and commit them under `tests/fixtures/<provider>/`. Add a plain `python3 tests/run_tests.py` runner (stdlib `unittest` — no pytest, per §0) that asserts each fixture converts and meets its coverage threshold.

**Redaction is mandatory and must be verified by reading each file after redacting**, not by running a regex and trusting it. These are going on a public repo.

### 6.2 Keeping the tables current

Do **not** import from ChatVault, and do not automate a sync. Instead:

- Keep §1's three tables in one clearly-marked block at the top of the core, with a comment on each entry naming the format it came from.
- Add a short `MAINTAINING.md`: *"When ChatVault's `adapters/identifiers.py` learns a new container/wrapper/role key, add it here too. The two files are kept in step by hand, deliberately — this repo is stdlib-only and must not depend on ChatVault."*
- The reverse direction matters as much: when `--diagnose` finds a shape neither project knows, it should end up in both.

This is a fifteen-row table that changes a few times a year. Manual is correct.

---

## 7. Build order

| # | Phase | Why here |
|---|---|---|
| 1 | §2 — mode-aware item filtering | Highest severity; blocks publication; independent of everything else |
| 2 | §5.1 regression harness + §6.1 fixtures | Nothing else is safe to change until regressions are detectable |
| 3 | §1 — tables + resolver | The coverage fix |
| 4 | §3 — coverage self-report | Depends on 1–3 being stable |
| 5 | §4 — `--diagnose` | Reuses §3's enumeration |
| 6 | README + block-type table corrected against real output | Last, so it describes what exists |

**Publish after phase 6, not before.** Phase 1 alone removes the only genuinely embarrassing finding; everything after it is upside.

---

## 8. Pre-answered questions

**Q: Duplicate to v5 files, or edit v4.0 in place?**
Duplicate, per §0. This repo's own convention (v4.0 did exactly this to v3.x, and the changelog explains why). Keeps a working tool available while v5 stabilises.

**Q: Should `Message` become a proper block list, or keep `text` + `thinking`?**
Block list (§2.2). The current pair cannot represent text-then-tool-then-text ordering, and that ordering is the thing TOOLS and RAW exist to show.

**Q: What about `subagent` records that nest a whole sub-conversation?**
Render inline, indented, under a `### Subagent` heading, in TOOLS and RAW only. Do not create separate output files — that is ChatVault's job, not a converter's.

**Q: Cursor's `bubbles` have no timestamps. Ordering?**
Fall back to source order when no timestamp is present. `_sort_time()` already does this (it sorts undated items last, stably, by original index) — verify it holds when *no* record in a file has a timestamp, rather than assuming.

**Q: How far should `--diagnose` go on a 250 MB file?**
Full enumeration, but stream it — never hold every record in memory at once. Report elapsed time. If a file is genuinely too large, say so with the number rather than silently truncating.

**Q: Should the tool auto-detect provider and print a name?**
No. Naming a provider means claiming you recognised it, and a near-miss shape would produce a confident wrong label. Report matched *shapes* (`wrapper=chat_message, container=message_nodes, role_key=role`) instead — factual, and more useful when debugging a new format.

**Q: Does the coverage metric go in the Markdown output too, or only the log?**
Log only. The `.md` files are for reading; polluting them with tooling metadata defeats the purpose.
