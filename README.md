# Chat2MD — AI Chat-to-Markdown Converter

![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue) ![No dependencies](https://img.shields.io/badge/dependencies-none-green) ![License MIT](https://img.shields.io/badge/license-MIT-green)

A small, dependency-free Python toolkit that converts AI chat JSON/JSONL exports into clean, readable Markdown.

**v5.0** recognizes export formats by structure instead of hardcoding providers: three small
lookup tables (not one function per provider) recognize a format by how it nests —
a wrapper key here, a container key there — so a format the tool has never seen
before often just works. It's been verified against **OpenAI/ChatGPT (both the
simple export and the official `mapping` format), Anthropic/Claude, Claude Desktop,
Google Gemini CLI, OpenAI Codex rollout, Devin CLI, Devin Desktop, Cursor,
Windsurf-legacy, and generic `role`/`content`/`text` formats** — and reports its own
confidence on anything else (see [Coverage self-report](#coverage-self-report) below)
rather than silently guessing.

## Why

Every major AI provider lets you export your chat history locally, and most use JSON or JSONL as the format. The structure of the conversation — who said what, when, and what kind of block it is — is conceptually the same across all of them. What differs is the JSON schema: how each provider names its fields, nests its records, and defines the **block types** that make up the conversational body (dialogue, thinking, tool calls, tool results, system instructions, memory, etc.).

Few tools can read across those schemas. The result is a folder of exports from different providers that you can't search, share, or diff in one consistent format.

AI Chat JSON to Markdown normalizes all of them into one Markdown format with one command. No Node, no pip install, no API keys — just Python 3.9+ and the standard library.

## What it does

Drop one or more JSON/JSONL files (or folders) onto a script and get a timestamped output folder containing one `.md` file per conversation plus a `conversion_log.md` report.

### Three detail levels

AI chat exports are made up of different **block types** — not just dialogue, but also thinking, tool calls, system instructions, memory, and more. The three modes differ only in which block types they keep, so you get exactly the level of detail you need:

| Block type | Chat | Tools | Raw |
|------------|:----:|:-----:|:---:|
| User dialogue | ✓ | ✓ | ✓ |
| Assistant dialogue | ✓ | ✓ | ✓ |
| Thinking / reasoning | ✓ | ✓ | ✓ |
| System / developer messages | — | ✓ | ✓ |
| Agent instructions (agents.md, environment, permissions) | — | ✓ | ✓ |
| Memory files & memory-injection blocks | — | ✓ | ✓ |
| Tool calls (function calls, with arguments) | — | ✓ | ✓ |
| Tool results (function outputs) | — | ✓ | ✓ |
| Images / audio | — | one-line stub | one-line stub |
| Refusals | — | one-line label | one-line label |
| Errors | — | — | one-line label |
| Session metadata (model changes, turn context) | — | — | — |

Session metadata is always dropped — it's structural noise, not conversational content.
Verified against real output row by row — see `tests/run_tests.py::TestAuditThreeRecordCase`
for the tool-call/result case specifically (v4.0's RAW mode silently dropped these; v5.0 fixes it).

| Mode | Best for |
|------|----------|
| **Chat** | Reading, sharing, blogging — just the dialogue |
| **Tools** | Auditing agent behavior — what instructions and memory it operated under |
| **Raw** | Forensics / diff baseline — a complete record of everything that happened |

Output is tagged by mode so you can tell files apart at a glance:

```text
[Chat] session-id--topic-of-the-conversation.md
[Tools] session-id--topic-of-the-conversation.md
[Raw] session-id--topic-of-the-conversation.md
```

Each run also writes:

```text
extracted_CHAT_YYYYMMDD_HHMMSS/conversion_log.md
extracted_TOOLS_YYYYMMDD_HHMMSS/conversion_log.md
extracted_RAW_YYYYMMDD_HHMMSS/conversion_log.md
```

The log lists every source file, its output filename, any warnings (for example, if
invalid UTF-8 had to be replaced during decoding), and a **coverage** percentage —
see below.

## Coverage self-report

Every conversion measures how much of the source file's accountable text (every
string-valued leaf, minus structural noise like ids and timestamps) actually made it
into the Markdown, and writes the ratio to `conversion_log.md`:

```text
- Coverage: 94% of accountable content emitted (RAW mode)
```

At **≥90%** it's silent. Between **50–89%** it adds a `⚠ Warning` with the top
unaccounted key paths by volume. Below **50%** it adds a `⚠⚠ Likely unsupported
shape` line and the run exits non-zero (pass `--allow-low-coverage` to suppress) —
useful in a script, and the reason a `SKIP` here is a loud, specific failure instead
of a suspiciously short output file. Most converters fail silently; this one reports
its own confidence and names what it didn't understand.

## `--diagnose` — investigate an unfamiliar format

```bash
python3 markdown_extraction_chat.py --diagnose path/to/file_or_folder/
```

No conversion, no Markdown output — just `diagnosis.md`: every key path in the file
with occurrence counts and types, the top string-leaf paths by character volume,
distinct record shapes, and which `WRAPPER_KEYS`/`CONTAINER_KEYS`/`ROLE_KEYS` table
entries matched (and which didn't, as candidates to add). Full enumeration, never a
sample.

## Quick start

Try it on the bundled synthetic samples (no real data included):

```bash
# Chat mode across all three provider formats in one run
python3 markdown_extraction_chat.py samples/sample_chatgpt.jsonl samples/sample_gemini.json samples/sample_generic.jsonl
```

Output lands in a timestamped `extracted_CHAT_...` folder next to the first input. See [`samples/`](samples) for the input files and [`samples/output/`](samples/output) for what the Markdown looks like.

## Requirements

- Python 3.9+
- No external packages — only the standard library

## Usage

### Command line

```bash
python3 markdown_extraction_chat.py  chat.jsonl  folder_of_exports/
python3 markdown_extraction_tools.py session.json
python3 markdown_extraction_raw.py  some-folder/
python3 markdown_extraction_chat.py --diagnose some-folder/   # investigate, don't convert
```

You can pass any number of files or folders; all outputs land in a single timestamped folder next to the first input.

### macOS Automator / drag-and-drop

The scripts work as the shell script step inside a macOS `.app`:

```bash
/usr/bin/python3 /path/to/markdown_extraction_chat.py "$@"
```

Set the Automator action to receive input as arguments, then drag JSON/JSONL files or folders onto the app icon.

## File layout

```text
_session_extractor_core.py      # Shared engine (do not run directly)
markdown_extraction_chat.py     # Chat-mode wrapper
markdown_extraction_tools.py    # Tools/Audit-mode wrapper
markdown_extraction_raw.py      # Raw/Forensic-mode wrapper
tests/fixtures/                      # One real (redacted) fixture per provider
tests/run_tests.py                   # Regression + acceptance tests (stdlib unittest)
samples/                             # Synthetic input + example output
docs/                                # v5.0 spec + coverage/fidelity audit
MAINTAINING.md                       # How the shape tables work and how to extend them
```

The wrappers load the core with `importlib.util` from their own folder, so they can
never accidentally bind to a different `_session_extractor_core*.py` sitting nearby.
The current line is a duplicate-and-fix of v4.0, the same way v4.0 was of v3.x; the
regression test asserts CHAT output stays byte-identical to the frozen v4.0 output
in `tests/expected/chat/`.

## Supported export formats

Provider knowledge is table-driven (`WRAPPER_KEYS`/`CONTAINER_KEYS`/`ROLE_KEYS` in
`_session_extractor_core.py`), not per-provider code — see
[MAINTAINING.md](MAINTAINING.md) for how the tables work and how to extend them.
Verified against real (redacted) fixtures in `tests/fixtures/`:

- **OpenAI / ChatGPT** — both the simple `{"type":"message","message":{...}}` shape and the official `mapping`-based export
- **Anthropic / Claude, Claude Desktop**
- **Gemini CLI JSON** — `{"messages":[...]}` with `type`, `content`, `thoughts`, `toolCalls`
- **Codex rollout** — `{"type":"response_item","payload":{...}}`
- **Devin CLI** (`message_nodes[].chat_message`) and **Devin Desktop** (ACP `messages[].payload`)
- **Cursor** — `bubbles[]`, including tool calls stored in `toolFormerData`
- **Generic JSON/JSONL** — any `role`/`content`/`text` shape, including Windsurf-legacy flattened records
- **Windsurf-legacy** — memory-injection blocks are stripped in Chat mode; preserved in Tools/Raw as designed

**Not supported: Antigravity's current export format.** Its real conversation data is
Protocol Buffers inside a SQLite `.db`, not JSON — out of reach for a stdlib-only
JSON reader. See [MAINTAINING.md](MAINTAINING.md#antigravity--a-documented-limitation-not-a-missing-table-row)
for the investigation. Run `--diagnose` on it and the tool will tell you honestly,
rather than silently mis-converting.

## Notable design details

- **User prompts stand out:** a `---` horizontal rule is inserted above every user message, making your own prompts easy to scan.
- **Thinking blocks preserved:** assistant reasoning/thinking is captured as a blockquote under the message.
- **Deduplication:** duplicate messages across merged exports are removed before writing.
- **Title inference:** output filenames are derived from the first meaningful line of the conversation.
- **Safe decoding:** invalid UTF-8 bytes are replaced, counted, and reported instead of silently deleted.

## Samples

The [`samples/`](samples) folder contains small, fully synthetic conversations in three provider formats so you can try the tool immediately — no real chat data is committed.

| Input | Format | Example output |
|-------|--------|----------------|
| `sample_chatgpt.jsonl` | OpenAI/ChatGPT JSONL | [`output/[Chat] sample_chatgpt.md`](samples/output/[Chat]%20sample_chatgpt.md) |
| `sample_gemini.json` | Gemini CLI JSON | [`output/[Chat] sample_gemini.md`](samples/output/[Chat]%20sample_gemini.md) |
| `sample_generic.jsonl` | Generic role/text JSONL | [`output/[Chat] sample_generic.md`](samples/output/[Chat]%20sample_generic.md) |

[`tests/fixtures/`](tests/fixtures) additionally holds one real (redacted) fixture
per newly-supported provider — ChatGPT's official export, Claude Desktop, Devin CLI,
Devin Desktop, and Cursor — exercised by `tests/run_tests.py`.

## Related

[ChatVault](https://github.com/martenzi/ChatVault) — the larger project this grew out of: full ingestion into SQLite with dedup, tagging and semantic search.

## Changelog

See [CHANGELOG.md](CHANGELOG.md) for version history, including v5.0's shape-driven
tables, the RAW-mode tool-call fix, and the coverage self-report.

## License

[MIT](LICENSE) — free for personal and commercial use. Attribution appreciated.
