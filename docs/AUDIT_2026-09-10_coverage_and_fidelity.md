# Audit — coverage and fidelity, 2026-09-10

**Method:** read `_session_extractor_core_v4.0.py` in full (626 lines, byte-identical to the copy in `history-r/tools/markdown_v4/`), then **ran the tool** against constructed records rather than reasoning about it.
**Supersedes in part:** an internal provider-coverage gap analysis — see §1.

---

## 0. Headline

The Gap doc is right that coverage has fallen behind ChatVault. It **understates the problem in one way and overstates it in another**, and its Path A / Path B framing is a false choice.

| | Gap doc says | Audit finds |
|---|---|---|
| Scope of failure | 6 providers SKIP entirely | 6 providers SKIP, **and the providers that "work" are also silently losing tool calls — including in RAW mode** |
| Fix required | Port ~10 ChatVault adapters into the tool | **No adapters need porting.** All 6 failures share one root cause, fixable with three lookup tables |
| Strategic choice | Shrink the claims (A) or become a multi-format converter (B) | Neither. Make the tool *shape-driven* and *self-reporting* — smaller in concept, wider in coverage |

---

## 1. The finding the Gap doc missed: RAW mode is not raw

**Test.** Three JSONL records in the OpenClaw wrapper shape the tool already supports: a user turn, an assistant turn containing `thinking` + `text` + `tool_use` content items, and an assistant turn containing a `tool_result` item. This is the standard Anthropic content-item shape — i.e. Claude Code, Claude Desktop, and Devin ACP.

**Result, RAW mode** (the tier the README advertises as "Forensics / diff baseline — a complete record of everything that happened"):

```
- Messages: 2          ← should be 3

## User — …            ✓ kept
### Assistant (thinking) …  ✓ kept
## Assistant — …       ✓ kept ("Reading it now.")

[tool_use read_file(/etc/hosts)]   ✗ SILENTLY DROPPED
[tool_result "127.0.0.1 localhost"] ✗ ENTIRE MESSAGE SILENTLY DROPPED
```

**Root cause**, `_session_extractor_core_v4.0.py`:

```python
_SKIP_ITEM_TYPES = {"tool_call", "tool_result", "tool_use", "function_call", ...}

def extract_content(content):          # ← takes no cfg
    ...
    if itype in _SKIP_ITEM_TYPES: continue
```

`extract_content()` is **not mode-aware.** The three modes differ only via `Config.skip_roles`, which filters *whole records by role*. A tool call nested as a content item inside an assistant message never reaches that filter — it is dropped inside `extract_content()` in all three modes, unconditionally. The third message then vanishes entirely, because `build_msg()` returns `None` when a message has no text and no thinking left.

**Consequence:** for every provider that nests tool calls as content items rather than emitting them as separate role-tagged records, the tool has **no tier that shows tool calls at all.** The README's block-type table claims RAW keeps "Tool calls ✓" and "Tool results ✓". It does not.

This also contradicts the stated design intent for the middle tier — that TOOLS should carry "all tool calls, all thinking process, all events that was part of the conversational process." In the code, `TOOLS_CFG.skip_roles` *drops* `tool`/`function`/`tool_result` roles and keeps `system`/`developer` instead. So the tier named "Tools" is really an *instructions-and-memory* tier, and tool calls appear in no tier reliably.

**Severity: high.** This is worse than a missing provider, because it is invisible. A SKIP is loud; this is a file that looks complete and is not. It is also exactly the failure class this whole line of work exists to eliminate.

---

## 2. The six SKIPping providers share one root cause

The Gap doc lists them as six separate problems needing six adapters. They are one problem:

| Provider | What the tool hits | What it actually needs |
|---|---|---|
| `chatgpt` (official) | descends `mapping`, finds `{message:{…}}` | unwrap the inner `message` key |
| `claude_desktop` | container is `chat_messages`, role key is `sender` | one container key, one role key |
| `devin_cli` | container `message_nodes`, wrapper `chat_message` | one container key, one wrapper key |
| `devin_desktop_acp` | container `messages`, wrapper `payload`, role in `kind` | one wrapper key, one role key, four role values |
| `cursor` | container `bubbles`, role is numeric `type` 1/2 | one container key, two role-value mappings |
| `antigravity` | container `steps`, wrapper `step_payload` | one container key, one wrapper key |

**Every failure is a missing container key, a missing wrapper key, or a missing role key/value.** None requires provider-specific parsing logic.

The tool already does all three operations — it just does them as **hardcoded named functions** (`_from_openclaw` unwraps `message`; `_from_rollout` unwraps `payload`; `COLLECTION_KEYS` lists seven containers; `norm_role` maps five synonyms) rather than as **data**. That is the entire defect.

Counting it precisely: the six providers need **4 new container keys, 3 new wrapper keys, 2 new role keys, and ~6 new role values.** Fifteen table entries. Not ten adapters.

---

## 3. Why Path B is the wrong shape of fix

Porting ChatVault adapters would:

- **destroy the selling point** — ChatVault's adapters use `orjson`, emit a CIR dict aimed at a database loader, and handle attachments, projects, dedupe and uid stability that a Markdown converter has no use for;
- **create a permanent sync burden** between two repos that will drift again, which is precisely how this gap appeared;
- **add ~10 code paths** to a codebase whose problem is that it already has too many special-case code paths;
- **still not fix §1**, which is not a provider issue at all.

Fifteen table entries fix six providers. Ten ported adapters fix six providers and break the architecture.

---

## 4. What ChatVault is actually good for here

Not its adapters. Its **`src/chatvault/adapters/identifiers.py`** — a module extracted *from this very tool* in September, which has since accumulated shape knowledge from adapters this tool never saw. That is a small, data-shaped, slow-changing surface: role synonyms, content-item type maps, container keys, reasoning locations, timestamp coercion, injected-context markers.

**Copy the knowledge, not the code.** A stdlib-only tool must not import from a project with a 200 MB database and third-party dependencies. §6 of the spec covers how to keep the two in step without coupling them.

Also directly reusable: **ChatVault's `tests/fixtures/`**, which holds real (redacted) records for every provider. That is the test corpus this tool needs and does not have — its `samples/` are three synthetic files.

---

## 5. The thing that would have prevented this entirely

The tool cannot currently tell you when it does not understand a format. It prints `SKIP — No conversational text found` (loud, fine) or writes a file that silently omits half the content (invisible, not fine). There is no measure of *how much of the input reached the output*.

**Add one.** After extraction, walk the source records and total the bytes of every string-valued leaf. Compare against the bytes actually emitted. Report the ratio, and when it falls below a threshold, list the top unaccounted key paths in `conversion_log.md`.

This is ~60 lines. It is also, the most interesting thing in the repo: **most converters fail silently; this one reports its own coverage and names what it did not understand.** It converts an embarrassment into the headline feature, and it makes the tool honest by construction rather than by documentation discipline.

---

## 6. Verdict

The tool's groundwork is genuinely good — good enough that ChatVault's shared identifier module was extracted from it, and good enough that its three-tier model became ChatVault's export profiles. That reputation is deserved.

What went wrong is narrower than "it fell behind": **provider knowledge was expressed as code paths instead of as data**, so every new format required a new function, and formats discovered after v4.0 never got one. Fix the expression, and the coverage problem largely dissolves — including for providers nobody has looked at yet, which is what "agnostic" actually means.

**Do not publish until §1 is fixed.** The RAW-mode tool-call loss is the only finding here that would be genuinely embarrassing on a public repo, because the README explicitly promises the opposite. Everything else is a fair "v1 covers these formats" story.

See `SPEC_v5_shape_driven_extraction.md` for the build.
