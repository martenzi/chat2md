#!/usr/bin/env python3
"""
markdown_extraction_chat_v4.0.py  —  CHAT mode  (v4.0)

Extracts clean human/assistant dialogue.  Drops system prompts, memory file
reads, tool calls, and routing metadata.  Ideal for sharing or reviewing what
was actually said.

v4.0 fix (see _session_extractor_core_v4.0.py header for full details):
  - CHAT mode now also strips Windsurf-legacy <SYSTEM-RETRIEVED-MEMORY[...]>
    blocks, which previously slipped through untouched (CHAT and RAW output
    for a Windsurf source used to come out byte-identical — confirmed bug).
  - Invalid-UTF-8 bytes in a source file are now surfaced as a warning in
    conversion_log.md instead of being silently deleted.
  Neither change alters output for OpenClaw / Gemini CLI / Codex / already
  well-formed sources — both are additive and only fire on the specific
  conditions described above.

This is a duplicate of markdown_extraction_chat.py (v3.4/v3.5 line) pointed
at the fixed core. The original script and its core are untouched.

USAGE (terminal):
  python3 markdown_extraction_chat_v4.0.py file1.jsonl file2.json folder/

USAGE (macOS Automator / .app):
  In the Run Shell Script action use:
      /usr/bin/python3 /path/to/markdown_extraction_chat_v4.0.py "$@"
  Pass input as arguments.  Drop any number of files/folders — they all end
  up in one output folder next to the first file.

OUTPUT FOLDER:
  extracted_CHAT_YYYYMMDD_HHMMSS/   (one folder per run, regardless of
  how many files you drop)

WHAT'S IN THE LOG:
  conversion_log.md — report listing every source file (full path), its
  output filename, or the skip reason if extraction failed, plus a
  "Warning" line if any invalid-UTF-8 bytes were found in the source.

FORMAT SUPPORT:
  • OpenClaw JSONL    {"type":"message","message":{...}}
  • Gemini CLI JSON   {"messages":[{type,content,thoughts,toolCalls},...]}
  • Codex rollout     {"type":"response_item","payload":{...}}
  • Generic JSON/L    Any role/content/text shape (incl. Windsurf-legacy
                      flattened {"role":"...","text":"..."} records)
"""

import importlib.util
import sys
from pathlib import Path

# Resolve the fixed core module from the same directory as this script.
# Uses importlib (rather than a plain "from _session_extractor_core import")
# so the dotted "v4.0" filename works and this script can never accidentally
# bind to the older, unfixed _session_extractor_core.py sitting next to it.
_CORE_PATH = Path(__file__).parent / "_session_extractor_core_v4.0.py"
try:
    _spec = importlib.util.spec_from_file_location("_session_extractor_core_v4_0", _CORE_PATH)
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
    skip_record_types=set(),          # _BASE_SKIP_TYPES already covers the rest
    is_boilerplate=boilerplate_chat,
    render_tool_in_codeblock=False,
)

if __name__ == "__main__":
    sys.exit(run(sys.argv[1:], CHAT_CFG))
