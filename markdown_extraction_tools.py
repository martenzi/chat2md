#!/usr/bin/env python3
"""
markdown_extraction_tools.py  —  TOOLS / AUDIT mode  (v5.0)

Keeps system prompts, agent instructions, memory files, thinking, and tool
calls/results alongside dialogue. Useful for auditing what instructions and
tools the agent was operating under.

v5.0 fixes (see _session_extractor_core.py header for full details):
  - TOOLS now actually keeps tool calls. `skip_roles` no longer drops
    `tool`/`function`/`tool_result`/`function_result` records (a tier named
    "Tools" dropping tool records was the audit's finding — a mode called
    "Tools" that drops tool content), and `keep_item_types` now keeps
    tool_use/tool_result content-items (nested inside an assistant message,
    e.g. Anthropic-style content), rendered as their own blocks. image/audio/
    refusal payload bodies are dropped in favor of a one-line stub.
  - Provider knowledge moved to data tables — see the CHAT wrapper's
    docstring for the list of formats this now supports.

This is a duplicate of markdown_extraction_tools_v4.0.py pointed at the
v5.0 core. The v4.0 script and core are untouched.

USAGE (terminal):
  python3 markdown_extraction_tools.py file1.jsonl file2.json folder/
  python3 markdown_extraction_tools.py --diagnose folder/

OUTPUT FOLDER:
  extracted_TOOLS_YYYYMMDD_HHMMSS/
"""

import importlib.util
import sys
from pathlib import Path

_CORE_PATH = Path(__file__).parent / "_session_extractor_core.py"
try:
    _spec = importlib.util.spec_from_file_location("_session_extractor_core", _CORE_PATH)
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
        "info",
    },
    skip_record_types=set(),
    is_boilerplate=boilerplate_tools,
    render_tool_in_codeblock=False,
    keep_item_types={"tool_use", "tool_result", "image", "audio", "refusal"},
)

if __name__ == "__main__":
    sys.exit(run(sys.argv[1:], TOOLS_CFG))
