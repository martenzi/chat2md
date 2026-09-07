#!/usr/bin/env python3
"""
markdown_extraction_tools_v4.0.py  —  TOOLS / AUDIT mode  (v4.0)

Keeps system prompts, agent instructions, and memory files alongside
dialogue.  Drops only raw tool result JSON blobs.  Useful for auditing
what instructions the agent was operating under.

v4.0 fix (see _session_extractor_core_v4.0.py header for full details):
  - TOOLS mode's boilerplate filter is intentionally unchanged — this mode's
    whole purpose is to keep memory/instruction content (including Windsurf's
    <SYSTEM-RETRIEVED-MEMORY> blocks) alongside dialogue for auditing, so it
    was never in scope for the CHAT-mode fix.
  - Invalid-UTF-8 bytes in a source file are now surfaced as a warning in
    conversion_log.md instead of being silently deleted (this part is shared
    with the other two v4.0 modes and applies identically here).

This is a duplicate of markdown_extraction_tools.py (v3.4/v3.5 line) pointed
at the fixed core. The original script and its core are untouched.

USAGE (terminal):
  python3 markdown_extraction_tools_v4.0.py file1.jsonl file2.json folder/

USAGE (macOS Automator / .app):
  In the Run Shell Script action use:
      /usr/bin/python3 /path/to/markdown_extraction_tools_v4.0.py "$@"

OUTPUT FOLDER:
  extracted_TOOLS_YYYYMMDD_HHMMSS/
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
    Config, boilerplate_tools, run = _core.Config, _core.boilerplate_tools, _core.run
except (ImportError, FileNotFoundError, AttributeError):
    print(f"Error: {_CORE_PATH.name} not found in the same directory.")
    sys.exit(1)

TOOLS_CFG = Config(
    mode="TOOLS",
    script_name=Path(__file__).name,
    skip_roles={
        "tool", "function",
        "toolresult", "tool_result", "function_result",
        "info",
    },
    skip_record_types=set(),
    is_boilerplate=boilerplate_tools,
    render_tool_in_codeblock=False,
)

if __name__ == "__main__":
    sys.exit(run(sys.argv[1:], TOOLS_CFG))
