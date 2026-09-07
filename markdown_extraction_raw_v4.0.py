#!/usr/bin/env python3
"""
markdown_extraction_raw_v4.0.py  —  RAW / FORENSIC mode  (v4.0)

Extracts everything: dialogue, thinking blocks, tool results, system messages.
Tool result content is wrapped in ```text``` fences so markdown stays readable.
Use this when you need a complete record of what happened in a session.

v4.0 fix (see _session_extractor_core_v4.0.py header for full details):
  - RAW mode's boilerplate filter is intentionally unchanged — RAW is the
    forensic/complete-record mode, so Windsurf-legacy <SYSTEM-RETRIEVED-MEMORY>
    blocks are still shown here on purpose (that's what makes RAW useful as
    a diff baseline against CHAT mode).
  - Invalid-UTF-8 bytes in a source file are now surfaced as a warning in
    conversion_log.md instead of being silently deleted (this part is shared
    with the other two v4.0 modes and applies identically here).

This is a duplicate of markdown_extraction_raw.py (v3.4/v3.5 line) pointed
at the fixed core. The original script and its core are untouched.

USAGE (terminal):
  python3 markdown_extraction_raw_v4.0.py file1.jsonl file2.json folder/

USAGE (macOS Automator / .app):
  In the Run Shell Script action use:
      /usr/bin/python3 /path/to/markdown_extraction_raw_v4.0.py "$@"

OUTPUT FOLDER:
  extracted_RAW_YYYYMMDD_HHMMSS/
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
    Config, boilerplate_raw, run = _core.Config, _core.boilerplate_raw, _core.run
except (ImportError, FileNotFoundError, AttributeError):
    print(f"Error: {_CORE_PATH.name} not found in the same directory.")
    sys.exit(1)

RAW_CFG = Config(
    mode="RAW",
    script_name=Path(__file__).name,
    skip_roles={
        "info",       # Gemini CLI checkpoint records — not message content
    },
    skip_record_types=set(),
    is_boilerplate=boilerplate_raw,
    render_tool_in_codeblock=True,
)

if __name__ == "__main__":
    sys.exit(run(sys.argv[1:], RAW_CFG))
