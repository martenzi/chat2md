# AI Session Extractor

A small, dependency-free Python toolkit that converts JSON/JSONL AI conversation exports into clean, readable Markdown.

It works across providers — **OpenAI/ChatGPT, Anthropic/Claude, Google Gemini CLI, OpenAI Codex rollout, Windsurf-legacy, and generic `role`/`content`/`text` formats** — from a single set of scripts. Built because no existing tool covered all of these cleanly.

## Why

Every AI provider exports conversations in a different JSON shape, and most converters handle only one. If you use more than one provider — or you audit agent behavior across tools — you end up with a folder of incompatible exports and nothing that reads them all.

AI Session Extractor normalizes all of them into one Markdown format with one command. No Node, no pip install, no API keys — just Python 3.9+ and the standard library.

## What it does

Drop one or more JSON/JSONL files (or folders) onto a script and get a timestamped output folder containing one `.md` file per conversation plus a `conversion_log.md` report.

Three extraction modes let you choose how much detail you want:

| Mode | What it keeps | Best for |
|------|---------------|----------|
| **Chat** | Human/assistant dialogue + thinking blocks only | Reading, sharing, blogging |
| **Tools** | Dialogue + system/developer instructions and memory files | Auditing agent behavior |
| **Raw** | Everything — tool results, system messages, memory, full context | Forensics / diff baseline |

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

The log lists every source file, its output filename, and any warnings (for example, if invalid UTF-8 had to be replaced during decoding).

## Quick start

Try it on the bundled synthetic samples (no real data included):

```bash
# Chat mode across all three provider formats in one run
python3 markdown_extraction_chat_v4.0.py samples/sample_chatgpt.jsonl samples/sample_gemini.json samples/sample_generic.jsonl
```

Output lands in a timestamped `extracted_CHAT_...` folder next to the first input. See [`samples/`](samples) for the input files and [`samples/output/`](samples/output) for what the Markdown looks like.

## Requirements

- Python 3.9+
- No external packages — only the standard library

## Usage

### Command line

```bash
python3 markdown_extraction_chat_v4.0.py  chat.jsonl  folder_of_exports/
python3 markdown_extraction_tools_v4.0.py session.json
python3 markdown_extraction_raw_v4.0.py  some-folder/
```

You can pass any number of files or folders; all outputs land in a single timestamped folder next to the first input.

### macOS Automator / drag-and-drop

The scripts work as the shell script step inside a macOS `.app`:

```bash
/usr/bin/python3 /path/to/markdown_extraction_chat_v4.0.py "$@"
```

Set the Automator action to receive input as arguments, then drag JSON/JSONL files or folders onto the app icon.

## File layout

```text
_session_extractor_core_v4.0.py      # Shared engine (do not run directly)
markdown_extraction_chat_v4.0.py     # Chat-mode wrapper
markdown_extraction_tools_v4.0.py    # Tools/Audit-mode wrapper
markdown_extraction_raw_v4.0.py     # Raw/Forensic-mode wrapper
samples/                             # Synthetic input + example output
```

The wrappers load the fixed `v4.0` core with `importlib.util` so they can never accidentally bind to an older `_session_extractor_core.py` in the same folder.

## Supported export formats

- **OpenAI / ChatGPT JSONL** — `{"type":"message","message":{...}}`
- **Gemini CLI JSON** — `{"messages":[...]}` with `type`, `content`, `thoughts`, `toolCalls`
- **Codex rollout** — `{"type":"response_item","payload":{...}}`
- **Generic JSON/JSONL** — any `role`/`content`/`text` shape, including Windsurf-legacy flattened records
- **Windsurf-legacy** — detected and handled in v4.0 (memory-injection blocks are stripped in Chat mode; preserved in Tools/Raw as designed)

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

## Sponsor

If this tool saves you time, consider sponsoring its development:

<a href="https://github.com/sponsors/martenzi"><img src="https://img.shields.io/badge/Sponsor-%E2%9D%A4-ff69b4" alt="Sponsor this project"></a>

## Changelog

See [CHANGELOG.md](CHANGELOG.md) for version history and the v4.0 Windsurf-legacy fixes.

## License

[MIT](LICENSE) — free for personal and commercial use. Attribution appreciated.
