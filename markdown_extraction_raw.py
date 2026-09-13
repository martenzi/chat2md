#!/usr/bin/env python3
"""
markdown_extraction_raw.py  —  RAW / FORENSIC mode  (v5.0)

Extracts everything: dialogue, thinking blocks, tool calls, tool results,
system messages, images/audio (as stubs), refusals and errors. Use this
when you need a complete record of what happened in a session.

v5.0 fix (see _session_extractor_core.py header for full details):
  - RAW mode is now actually raw. v4.0's `extract_content()` unconditionally
    dropped every tool_use/tool_result/image/... content item in ALL THREE
    modes, including RAW, despite the README promising RAW keeps everything
    (audit §1, the highest-severity finding). `keep_item_types` now includes
    every mapped block type, rendered in source order as its own block
    (tool_use as a fenced json block, tool_result as fenced text, refusal/
    error as labelled lines, image/audio as one-line stubs).
  - Provider knowledge moved to data tables — see the CHAT wrapper's
    docstring for the list of formats this now supports.

This is a duplicate of markdown_extraction_raw_v4.0.py pointed at the v5.0
core. The v4.0 script and core are untouched.

USAGE (terminal):
  python3 markdown_extraction_raw.py file1.jsonl file2.json folder/
  python3 markdown_extraction_raw.py --diagnose folder/

OUTPUT FOLDER:
  extracted_RAW_YYYYMMDD_HHMMSS/
"""

import importlib.util
import sys
from pathlib import Path

_CORE_PATH = Path(__file__).parent / "_session_extractor_core.py"
try:
    _spec = importlib.util.spec_from_file_location("_session_extractor_core", _CORE_PATH)
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
    keep_item_types={"tool_use", "tool_result", "image", "audio", "refusal", "error"},
)

if __name__ == "__main__":
    sys.exit(run(sys.argv[1:], RAW_CFG))
