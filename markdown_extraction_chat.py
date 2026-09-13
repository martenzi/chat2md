#!/usr/bin/env python3
"""
markdown_extraction_chat.py  —  CHAT mode  (v5.0)

Extracts clean human/assistant dialogue.  Drops system prompts, memory file
reads, tool calls, and routing metadata.  Ideal for sharing or reviewing what
was actually said.

v5.0 (see _session_extractor_core.py header for full details): provider
knowledge moved from hardcoded functions to three data tables + one shape
resolver, plus a coverage self-report and --diagnose. CHAT mode's own output
is unchanged from v4.0 — it never keeps content-item tool blocks
(keep_item_types is empty here), so every format that worked in v4.0 keeps
working identically (see tests/run_tests.py's regression check).

This is a duplicate of markdown_extraction_chat_v4.0.py pointed at the v5.0
core. The v4.0 script and core are untouched.

USAGE (terminal):
  python3 markdown_extraction_chat.py file1.jsonl file2.json folder/
  python3 markdown_extraction_chat.py --diagnose folder/   # investigate, don't convert

USAGE (macOS Automator / .app):
  In the Run Shell Script action use:
      /usr/bin/python3 /path/to/markdown_extraction_chat.py "$@"
  Pass input as arguments.  Drop any number of files/folders — they all end
  up in one output folder next to the first file.

OUTPUT FOLDER:
  extracted_CHAT_YYYYMMDD_HHMMSS/   (one folder per run, regardless of
  how many files you drop)

WHAT'S IN THE LOG:
  conversion_log.md — report listing every source file (full path), its
  output filename or skip reason, any UTF-8 warning, and a coverage
  percentage (how much of the source's accountable text made it into the
  Markdown) with the top unaccounted key paths when that's low.

FORMAT SUPPORT:
  Provider knowledge is table-driven (see _session_extractor_core.py's
  WRAPPER_KEYS / CONTAINER_KEYS / ROLE_KEYS) rather than per-provider code,
  so any format that nests in a way those tables recognize converts without
  a code change. Verified against: OpenClaw/Claude, Codex rollout, Gemini
  CLI, generic role/content/text, Windsurf-legacy, ChatGPT official export,
  Claude Desktop, Devin CLI, Devin Desktop ACP, Cursor — see tests/fixtures/.
"""

import importlib.util
import sys
from pathlib import Path

_CORE_PATH = Path(__file__).parent / "_session_extractor_core.py"
try:
    _spec = importlib.util.spec_from_file_location("_session_extractor_core", _CORE_PATH)
    _core = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_core)
    Config, boilerplate_chat, run = _core.Config, _core.boilerplate_chat, _core.run
except (ImportError, FileNotFoundError, AttributeError):
    print(f"Error: {_CORE_PATH.name} not found in the same directory.")
    sys.exit(1)

CHAT_CFG = Config(
    mode="CHAT",
    script_name=Path(__file__).name,
    skip_roles={
        "developer", "system", "tool", "function",
        "toolresult", "tool_result", "function_result",
        "info",
    },
    skip_record_types=set(),          # NON_MESSAGE_TYPES already covers the rest
    is_boilerplate=boilerplate_chat,
    render_tool_in_codeblock=False,
    keep_item_types=set(),            # CHAT never keeps content-item tool blocks
)

if __name__ == "__main__":
    sys.exit(run(sys.argv[1:], CHAT_CFG))
