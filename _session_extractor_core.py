#!/usr/bin/env python3
"""
_session_extractor_core.py  —  Shared extraction engine (v5.0)

NOT meant to be run directly. Imported by the three wrapper scripts
(markdown_extraction_{chat,tools,raw}.py) and by --diagnose.

This is a duplicate-and-fix of _session_extractor_core_v4.0.py (626 lines),
following the same convention v4.0 used against v3.x. The v4.0 file is
untouched under legacy/. See docs/SPEC_v5_shape_driven_extraction.md and
docs/AUDIT_2026-09-10_coverage_and_fidelity.md for the full writeup; short
version below.

--- WHAT CHANGED IN v5.0 AND WHY ---

1. RAW mode is now actually raw (spec §2). `extract_content()` used to
   unconditionally drop every content-item type in `_SKIP_ITEM_TYPES`
   (tool_use/tool_result/image/...) in ALL THREE modes, including RAW —
   despite the README promising RAW keeps everything. Fixed by making
   content-item extraction mode-aware: `Config.keep_item_types` says which
   mapped block types a given mode keeps, and kept items are rendered as
   their own blocks (`Message.blocks`) instead of being silently discarded.
   TOOLS_CFG's `skip_roles` also no longer drops `tool`/`function`/
   `tool_result`/`function_result` records — a tier named "Tools" dropping
   tool records was the same defect at the record level.

2. Provider knowledge is now three data tables + one resolver instead of
   four hardcoded `_from_*` functions (spec §1). `resolve_record()` unwraps
   WRAPPER_KEYS, descends CONTAINER_KEYS, and reads ROLE_KEYS/CONTENT_KEYS/
   TIMESTAMP_KEYS on whatever's left — by shape, not by provider name. This
   is what lets chatgpt (official `mapping` export), claude_desktop,
   devin_cli, devin_desktop_acp and cursor convert without any new code
   path; each only needed table entries. `extract_content()` itself
   (text/thinking extraction) is UNCHANGED, verbatim — a new sibling,
   `extract_content_ex()`, wraps the same logic and additionally captures
   kept item-type blocks, so CHAT mode's output for every v4.0-supported
   format is provably identical (see tests/run_tests.py).

3. Coverage self-report (spec §3): after extraction, every source file's
   string-valued leaves are totalled and compared against what actually
   made it into the Markdown. The ratio is written to conversion_log.md,
   with the top unaccounted key paths named when it's low. This is what
   makes "the tool doesn't understand this shape" a loud, specific message
   instead of a suspiciously short output file.

4. `--diagnose` (spec §4): a reconnaissance mode that enumerates every key
   path, value type, and role/wrapper/container match in a file or folder,
   with no conversion and no Markdown output. Investigation, not extraction.

Everything else — role normalization base cases, text cleaning, dedup,
title derivation, file naming, UTF-8 handling — is unchanged from v4.0,
byte-for-byte in logic. Still zero non-stdlib imports.

ANTIGRAVITY NOTE (see MAINTAINING.md): the spec's own audit assumed
Antigravity's `steps[].step_payload` was a nested JSON dict like the other
five formats. It is not, in the currently-shipping IDE/CLI builds — it's a
schema-less Protocol Buffers blob inside a SQLite `.db` (confirmed against
real files; see ChatVault's `docs/reports/antigravity_shapes_2026-09-09.md`
investigation). A stdlib-JSON-only converter cannot decode that without a
hand-written protobuf wire-format walker, which is out of scope for this
tool (see MAINTAINING.md). The WRAPPER_KEYS/CONTAINER_KEYS entries for
`step_payload`/`steps` are kept anyway — harmless, and correct if a future
export variant is plain JSON — but §5.5's antigravity leg is honestly
unmet: the coverage self-report (§3) reports it low rather than faking a
number, which is exactly the behavior that feature exists to produce.
"""

import argparse, json, re, sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

# ── Compiled patterns ─────────────────────────────────────────────────────────
LONG_B64     = re.compile(r"^[A-Za-z0-9+/]{60,}={0,2}$")
HEX_LONG     = re.compile(r"^[0-9a-fA-F]{60,}$")
WS_RE        = re.compile(r"\s+")
JSON_FILE_RE = re.compile(r"\.json(l)?($|\.)", re.IGNORECASE)

_METADATA_RE = re.compile(
    r'(?:Conversation info|Sender)\s*\(untrusted metadata\):\s*```json.*?```\s*',
    re.DOTALL | re.IGNORECASE,
)
_TS_RE = re.compile(r'^\[[\w\s,:/+\-]+\]\s*', re.MULTILINE)
_GEMINI_REF_RE = re.compile(
    r'\n*-{3}\s*Content from referenced files\s*-{3}.*?-{3}\s*End of content\s*-{3}\s*',
    re.DOTALL | re.IGNORECASE,
)

# ═══════════════════════════════════════════════════════════════════════════
# §1 — THE THREE TABLES (spec §1.2). Provider knowledge as data, not code.
# Keep this block in one place; MAINTAINING.md explains how to extend it.
# ═══════════════════════════════════════════════════════════════════════════

# Keys whose VALUE IS the real record. Unwrap recursively, then re-resolve.
# Sources (verified against real fixtures — see tests/fixtures/<provider>/):
#   message       OpenClaw/Claude, ChatGPT mapping node
#   payload       Codex rollout, Devin Desktop ACP (messages[].payload)
#   chat_message  Devin CLI (message_nodes[].chat_message)
#   step_payload  Antigravity (steps[].step_payload) — see the module
#                 docstring's ANTIGRAVITY NOTE; kept for shape-completeness
#                 even though the real payload is opaque protobuf, not JSON.
WRAPPER_KEYS: Tuple[str, ...] = (
    "message", "chat_message", "payload", "step_payload", "msg", "data",
)

# Keys whose VALUE IS a collection of records. Descend into each.
# Extends v4.0's COLLECTION_KEYS with the containers found since.
CONTAINER_KEYS: Tuple[str, ...] = (
    "messages", "chat_messages", "message_nodes", "bubbles", "steps",
    "items", "turns", "conversation", "conversations", "entries",
    "mapping", "events", "history", "nodes",
)

# Keys that may carry the role. Checked in order; first non-empty wins.
# "type" is deliberately last — it's overloaded for non-role purposes (see
# NON_MESSAGE_TYPES below), so a value that resolves to a known
# non-message record type must not be treated as a role.
ROLE_KEYS: Tuple[str, ...] = (
    "role", "sender", "author", "kind", "speaker", "from", "type",
)

# Keys that may carry the message body, checked via first_of() (first
# present+non-empty wins). Ported from v4.0's _from_generic content lookup.
CONTENT_KEYS: Tuple[str, ...] = (
    "content", "text", "parts", "body", "value", "message",
)

# Keys that may carry a timestamp, checked via first_of().
TIMESTAMP_KEYS: Tuple[str, ...] = (
    "timestamp", "create_time", "created_at", "createdAt", "ts", "time",
)

COLLECTION_KEYS = CONTAINER_KEYS  # v4.0 name, kept as an alias — see MAINTAINING.md

_ROLE_SYNONYMS: Dict[str, str] = {
    "human": "user", "assistant_response": "assistant",
    "gemini": "assistant", "bot": "assistant", "ai": "assistant",
    # v5.0 — Devin Desktop ACP `messages[].kind` values
    "user_message": "user", "agent_message": "assistant",
    "agent_thought": "assistant", "tool_call": "tool", "subagent": "assistant",
    # v5.0 — Cursor numeric type codes (bubbleId/richText-guarded — see
    # _looks_like_cursor_bubble(); a bare `1`/`2` in an unrelated format is
    # never mapped by this)
    "1": "user", "2": "assistant",
    # v5.0 — misc observed
    "chatbot": "assistant",
    # NOTE: the spec draft (§1.2) also suggested mapping "model" -> "assistant"
    # here. Deliberately NOT added: samples/sample_gemini.json's "model" role
    # is v4.0-unmapped (renders as "## Model", not "## Assistant"), and §5.1
    # requires byte-identical CHAT output for every v4.0-supported format.
    # Regression correctness wins over the illustrative example — see
    # tests/run_tests.py's regression check and MAINTAINING.md.
}

# Record `type` values that are session/turn bookkeeping, never a message.
# Ported verbatim from v4.0's _BASE_SKIP_TYPES.
NON_MESSAGE_TYPES: Set[str] = {
    "session", "session_meta", "event_msg", "turn_context",
    "thinking_level_change", "model_change", "custom", "info",
}

# Content-item `type` values the v4.0 core unconditionally dropped
# (_SKIP_ITEM_TYPES), mapped to the block type they render as when a mode's
# `keep_item_types` asks for them (spec §2.1). Ported from ChatVault's
# `adapters/identifiers.py::CONTENT_ITEM_TYPE_MAP` (itself extracted from
# this tool) — see MAINTAINING.md.
_SKIP_ITEM_TYPES: Set[str] = {
    "tool_call", "tool_result", "tool_use",
    "function_call", "function_call_output", "function_result",
    "custom_tool_call", "custom_tool_call_output",
    "image", "image_url", "input_image", "output_image",
    "audio", "refusal", "error", "toolcall",
}
_CONTENT_ITEM_TYPE_MAP: Dict[str, str] = {
    "tool_call": "tool_use", "tool_result": "tool_result", "tool_use": "tool_use",
    "function_call": "tool_use", "function_call_output": "tool_result",
    "function_result": "tool_result", "custom_tool_call": "tool_use",
    "custom_tool_call_output": "tool_result",
    "image": "image", "image_url": "image", "input_image": "image", "output_image": "image",
    "audio": "audio", "refusal": "refusal", "error": "error", "toolcall": "tool_use",
}

_MAX_RESOLVE_DEPTH = 12

# ── Data classes ──────────────────────────────────────────────────────────────
@dataclass
class Message:
    role: str
    timestamp: str
    text: str
    thinking: Optional[str] = None
    # v5.0: ordered (block_type, text, tool_name) triples — text/thinking
    # plus any kept tool_use/tool_result/image/audio/refusal/error blocks,
    # in source order. Empty unless the mode's keep_item_types found extras;
    # write_md() falls back to the plain text/thinking rendering whenever
    # this holds nothing but text/thinking, so v4.0 output is unaffected.
    blocks: List[Tuple[str, str, Optional[str]]] = field(default_factory=list)

@dataclass
class ConvResult:
    source: Path
    output: Optional[Path]
    status: str   # "WROTE" | "SKIP"
    detail: str   # filename or skip reason
    note: str = ""          # optional warning (e.g. bad-byte count)
    coverage: Optional[float] = None          # v5.0: emitted/accountable ratio
    top_unaccounted: List[Tuple[str, int, int]] = field(default_factory=list)  # (path, chars, records)
    top_key_paths: List[Tuple[str, int]] = field(default_factory=list)  # for SKIP files

@dataclass
class Config:
    mode: str                                          # CHAT / TOOLS / RAW
    script_name: str                                   # for the log
    skip_roles: Set[str]                                # discard these roles entirely
    skip_record_types: Set[str]                         # additional record types to skip
    is_boilerplate: Callable[[str, str], bool]          # (role, text) -> bool
    render_tool_in_codeblock: bool                      # RAW mode wraps tool output
    keep_item_types: Set[str] = field(default_factory=set)  # v5.0 — mapped block types to keep

# ── Argument parser ───────────────────────────────────────────────────────────
def make_parser(mode: str) -> argparse.ArgumentParser:
    return argparse.ArgumentParser(
        description=(
            f"Convert AI chat histories (JSON/JSONL) to Markdown — {mode} mode.\n"
            "Accepts one or more files or folders; all output goes into one folder."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

def add_inputs(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("input_paths", nargs="+",
                        help="JSON/JSONL files or folders (mix OK)")
    parser.add_argument("--diagnose", action="store_true",
                        help="Investigate the input instead of converting it: write "
                             "diagnosis.md (full key-path enumeration, no Markdown output).")
    parser.add_argument("--allow-low-coverage", action="store_true",
                        help="Exit 0 even if a file's coverage falls below 50%%.")

# ── File discovery ────────────────────────────────────────────────────────────
def collect_files(raw_paths: List[str]) -> List[Path]:
    """Expand file/folder args into a deduped, sorted list of JSON/JSONL files."""
    seen: Set[Path] = set()
    out: List[Path] = []
    for raw in raw_paths:
        p = Path(raw).expanduser().resolve()
        if p.is_file():
            if JSON_FILE_RE.search(p.name) and p not in seen:
                seen.add(p); out.append(p)
        elif p.is_dir():
            for child in sorted(p.rglob("*")):
                if not child.is_file(): continue
                rel = child.parts[len(p.parts):]
                if any(pt.startswith("extracted_") for pt in rel[:-1]): continue
                if JSON_FILE_RE.search(child.name) and child not in seen:
                    seen.add(child); out.append(child)
    return out

def choose_output_root(files: List[Path], mode: str) -> Path:
    """Single output folder, named with mode + timestamp, next to first file."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return files[0].parent / f"extracted_{mode}_{ts}"

# ── I/O ───────────────────────────────────────────────────────────────────────
def read_records(path: Path) -> Tuple[List[Any], int]:
    """Read a JSON/JSONL file as UTF-8. Returns (records, replaced_char_count).

    Uses errors="replace" (never "ignore", which silently deletes invalid
    bytes) so bad bytes become U+FFFD and get counted instead of vanishing.
    """
    if ".jsonl" in path.name.lower():
        recs: List[Any] = []
        replaced = 0
        try:
            raw = path.read_bytes()
        except OSError:
            return [], 0
        for raw_line in raw.split(b"\n"):
            if not raw_line.strip():
                continue
            line = raw_line.decode("utf-8", errors="replace")
            replaced += line.count("�")
            line = line.strip()
            if not line:
                continue
            try:
                recs.append(json.loads(line))
            except json.JSONDecodeError:
                pass
        if recs:
            return recs, replaced
    try:
        raw = path.read_bytes()
    except OSError:
        return [], 0
    text = raw.decode("utf-8", errors="replace")
    replaced = text.count("�")
    try:
        data = json.loads(text)
        return (data if isinstance(data, list) else [data]), replaced
    except json.JSONDecodeError:
        return [], 0

# ── Text helpers ──────────────────────────────────────────────────────────────
def strip_meta(text: str) -> str:
    text = _METADATA_RE.sub("", text)
    return _TS_RE.sub("", text).strip()

def strip_gemini_refs(text: str) -> str:
    return _GEMINI_REF_RE.sub("", text).strip() if text else text

def clean(text: str) -> str:
    text = (text.replace("<final>","").replace("</final>","")
                .replace("\r\n","\n").replace("\r","\n").replace("\x00",""))
    lines: List[str] = []
    prev_blank = False
    for raw in text.split("\n"):
        s = raw.strip()
        if not s:
            if not prev_blank and lines: lines.append("")
            prev_blank = True; continue
        if "thinkingsignature" in s.lower(): continue
        if LONG_B64.match(s) or HEX_LONG.match(s): continue
        lines.append(raw.rstrip()); prev_blank = False
    while lines and not lines[-1].strip(): lines.pop()
    cleaned = "\n".join(lines).strip()
    return "" if cleaned.upper() in {"N/A","NA","NULL","NONE"} else cleaned

# ── Role / timestamp ──────────────────────────────────────────────────────────
def _looks_like_cursor_bubble(rec: Any) -> bool:
    """Guard for the Cursor-specific numeric role mapping (1=user, 2=assistant).

    Only trust a bare integer role when the record also carries Cursor-bubble
    evidence (a `bubbleId` or `richText` key) — otherwise a `type: 1` in some
    unrelated format would silently become a user turn.
    """
    if not isinstance(rec, dict): return False
    keys_lower = {str(k).lower() for k in rec.keys()}
    return "bubbleid" in keys_lower or "richtext" in keys_lower

def norm_role(role: Any, rec: Any = None) -> str:
    if isinstance(role, dict):
        role = role.get("role") or role.get("name") or role.get("type")
    if role is None: return "unknown"
    v = str(role).strip().lower()
    if v in ("1", "2") and not _looks_like_cursor_bubble(rec):
        return v or "unknown"
    return _ROLE_SYNONYMS.get(v, v or "unknown")

def coerce_dt(value: Any) -> Optional[datetime]:
    if value in (None, ""): return None
    if isinstance(value, bool): return None
    if isinstance(value, (int, float)):
        s = float(value)
        if s > 1_000_000_000_000: s /= 1000.0
        try: return datetime.fromtimestamp(s, tz=timezone.utc).astimezone()
        except: return None
    if isinstance(value, str):
        raw = value.strip()
        if not raw: return None
        if raw.isdigit(): return coerce_dt(int(raw))
        try:
            dt = datetime.fromisoformat(raw.replace("Z","+00:00"))
            return dt.astimezone() if dt.tzinfo else dt
        except: return None
    return None

def fmt_ts(value: Any) -> str:
    dt = coerce_dt(value)
    return dt.strftime("%Y-%m-%d %H:%M:%S") if dt else ""

def first_of(d: dict, *keys: str) -> Any:
    for k in keys:
        if k in d and d[k] not in (None,""):
            return d[k]
    return None

# ── Content extraction ────────────────────────────────────────────────────────
def extract_content(content: Any) -> Tuple[str, Optional[str]]:
    """UNCHANGED from v4.0, verbatim — see tests/run_tests.py's byte-identical
    CHAT-mode regression. Text/thinking extraction only; never sees tool
    blocks (those are v5.0's addition, in extract_content_ex below)."""
    if content is None: return ("", None)
    if isinstance(content, str): return (strip_gemini_refs(content), None)

    if isinstance(content, list):
        parts: List[str] = []
        thinking: Optional[str] = None
        for item in content:
            if not isinstance(item, dict): continue
            itype = str(item.get("type","")).lower().strip()
            if itype in _SKIP_ITEM_TYPES: continue
            if itype == "text":
                t = item.get("text","").strip()
                if t: parts.append(t)
            elif itype in ("thinking","reasoning"):
                thinking = (item.get("thinking") or item.get("text")
                            or item.get("redacted_thinking") or "").strip() or thinking
            elif itype == "":
                t = item.get("text","").strip()
                if t and not re.match(r'^-{3}\s*(Content from referenced files|End of content)',t,re.I) \
                       and not re.match(r'^Content from @',t):
                    parts.append(t)
        dialogue = strip_gemini_refs("\n\n".join(p for p in parts if p))
        return (dialogue.strip(), thinking)

    if isinstance(content, dict):
        ct = str(content.get("type","")).lower().strip()
        if ct == "text" and "text" in content: return (content["text"], None)
        if ct in ("input_text","output_text"): return (content.get("text",""), None)
        if isinstance(content.get("content"), list):
            return extract_content(content["content"])
    return ("", None)

def _tool_name(item: dict) -> Optional[str]:
    if item.get("name"): return str(item["name"])
    fn = item.get("function")
    if isinstance(fn, dict) and fn.get("name"): return str(fn["name"])
    if item.get("tool_name"): return str(item["tool_name"])
    return None

def _stringify_tool_item(item: dict, mapped: str) -> str:
    """Best-effort text rendering for a kept tool_use/tool_result/image/... block."""
    if mapped == "tool_use":
        payload = item.get("input")
        if payload is None: payload = item.get("arguments")
        if payload is None and isinstance(item.get("function"), dict):
            payload = item["function"].get("arguments")
        if isinstance(payload, str):
            try: payload = json.loads(payload)
            except Exception: pass
        try:
            return json.dumps(payload, indent=2, ensure_ascii=False) if payload is not None else "{}"
        except Exception:
            return str(payload)
    if mapped == "tool_result":
        out = item.get("content")
        if out is None: out = item.get("output")
        if isinstance(out, list):
            parts = []
            for sub in out:
                if isinstance(sub, dict):
                    parts.append(sub.get("text") or json.dumps(sub, ensure_ascii=False))
                else:
                    parts.append(str(sub))
            return "\n".join(p for p in parts if p)
        if isinstance(out, dict):
            try: return json.dumps(out, indent=2, ensure_ascii=False)
            except Exception: return str(out)
        return str(out) if out is not None else ""
    # image / audio / refusal / error — one-line stub source text
    return str(item.get("text") or item.get("message") or "")

def extract_content_ex(cfg: Config, content: Any) -> Tuple[str, Optional[str], List[Tuple[str,str,Optional[str]]]]:
    """extract_content(), plus kept item-type blocks (spec §2.1).

    Text/thinking merge logic is identical to extract_content() — this is
    the same tri-branch with one addition: content items whose type is in
    _SKIP_ITEM_TYPES are, if cfg.keep_item_types asks for their mapped
    type, captured as (mapped_type, text, tool_name) in source order
    instead of being silently dropped.
    """
    if content is None: return ("", None, [])
    if isinstance(content, str): return (strip_gemini_refs(content), None, [])

    if isinstance(content, list):
        parts: List[str] = []
        thinking: Optional[str] = None
        extra: List[Tuple[str,str,Optional[str]]] = []
        for item in content:
            if not isinstance(item, dict): continue
            itype = str(item.get("type","")).lower().strip()
            if itype in _SKIP_ITEM_TYPES:
                mapped = _CONTENT_ITEM_TYPE_MAP.get(itype, itype)
                if mapped in cfg.keep_item_types:
                    extra.append((mapped, _stringify_tool_item(item, mapped), _tool_name(item)))
                continue
            if itype == "text":
                t = item.get("text","").strip()
                if t: parts.append(t)
            elif itype in ("thinking","reasoning"):
                thinking = (item.get("thinking") or item.get("text")
                            or item.get("redacted_thinking") or "").strip() or thinking
            else:
                # v5.0: bare-text items with no recognized type (Gemini CLI
                # {"text": "..."} chunks) and ACP-style streaming chunks
                # ({"content": {"text": "...", "type": "text"}, "sessionUpdate": ...})
                # or nested tool-result wrappers ({"content": [...] , "type": "content"}).
                t = str(item.get("text","")).strip()
                if not t and isinstance(item.get("content"), dict):
                    t = str(item["content"].get("text","")).strip()
                if t and not re.match(r'^-{3}\s*(Content from referenced files|End of content)',t,re.I) \
                       and not re.match(r'^Content from @',t):
                    parts.append(t)
        dialogue = strip_gemini_refs("\n\n".join(p for p in parts if p))
        return (dialogue.strip(), thinking, extra)

    if isinstance(content, dict):
        ct = str(content.get("type","")).lower().strip()
        if ct == "text" and "text" in content: return (content["text"], None, [])
        if ct in ("input_text","output_text"): return (content.get("text",""), None, [])
        if isinstance(content.get("content"), list):
            return extract_content_ex(cfg, content["content"])
        # v5.0: ChatGPT's official export shape —
        # {"content_type": "text"|"code"|..., "parts": ["...", ...]}
        if isinstance(content.get("parts"), list):
            parts = [str(p) for p in content["parts"] if isinstance(p,(str,int,float)) and str(p).strip()]
            text = strip_gemini_refs("\n\n".join(parts)).strip()
            return (text, None, [])
        # v5.0: Devin Desktop ACP tool_call payload —
        # {"content": {"content": [...], "kind": "execute", ...}} (a dict
        # whose OWN "content" key is itself a dict, not a list — the plain
        # `isinstance(content.get("content"), list)` check above misses this
        # one extra level of nesting).
        inner = content.get("content")
        if isinstance(inner, dict) and isinstance(inner.get("content"), list):
            return extract_content_ex(cfg, inner["content"])
        # v5.0: ACP tool_call payload with no embedded result body (e.g. a
        # "read"/"search" call whose result never got persisted) — surface
        # its own invocation arguments (rawInput / locations) as a tool_use
        # block, so the call itself isn't silently invisible in TOOLS/RAW.
        if content.get("toolCallId") and "tool_use" in cfg.keep_item_types:
            args: Dict[str, Any] = {}
            if isinstance(content.get("rawInput"), dict):
                args.update(content["rawInput"])
            if isinstance(content.get("locations"), list):
                paths = [l.get("path") for l in content["locations"]
                         if isinstance(l, dict) and l.get("path")]
                if paths: args["locations"] = paths
            if args:
                tool_name = content.get("kind") or content.get("title")
                try:
                    arg_text = json.dumps(args, indent=2, ensure_ascii=False)
                except Exception:
                    arg_text = str(args)
                return ("", None, [("tool_use", arg_text, str(tool_name) if tool_name else None)])
    return ("", None, [])

def extract_thinking(rec: dict) -> Optional[str]:
    think = first_of(rec, "thoughts","thinking","reasoning")
    if think is None:
        for item in (rec.get("content") if isinstance(rec.get("content"),list) else []):
            if isinstance(item,dict) and item.get("type") in ("thinking","reasoning"):
                think = (item.get("thinking") or item.get("text") or item.get("redacted_thinking"))
                break
    if think is None: return None
    if isinstance(think, list):
        parts = []
        for t in think:
            if isinstance(t, dict):
                subj = t.get("subject") or t.get("name") or ""
                desc = t.get("description") or t.get("text") or ""
                parts.append(f"**{subj}**\n\n{desc}" if subj and desc else desc or subj)
            elif isinstance(t, str): parts.append(t)
        return "\n\n".join(p for p in parts if p) or None
    return str(think) if think else None

# ── Boilerplate helpers (used by Config.is_boilerplate) ──────────────────────
def _is_tool_json(text: str) -> bool:
    try:
        obj = json.loads(text)
        return isinstance(obj, dict) and ("results" in obj or "tool_use_id" in obj)
    except: return False

def _is_ts_listing(text: str, lo: str) -> bool:
    return text.startswith("healthz") or "params.ts" in lo or ("apiV1" in text and text.count(".ts")>5)

def boilerplate_chat(role: str, text: str) -> bool:
    if role not in ("user","assistant","unknown"): return False
    lo = text.lower()
    for m in ("# agents.md instructions","<instructions>","<environment_context>",
              "<permissions_instructions>",
              "# user.md","# soul.md","# identity.md","# memory.md",
              "<system-retrieved-memory["):
        if m in lo: return True
    return _is_tool_json(text) or _is_ts_listing(text, lo)

def boilerplate_tools(role: str, text: str) -> bool:
    if role not in ("user","assistant","unknown"): return False
    lo = text.lower()
    return _is_tool_json(text) or _is_ts_listing(text, lo)

def boilerplate_raw(role: str, text: str) -> bool:
    return _is_ts_listing(text, text.lower())

# ── Message building ──────────────────────────────────────────────────────────
# Sibling keys — held next to (not inside) a leaf record's content field —
# whose value is a single tool call's own {name, args, result}-shaped dict.
# Ported from a real shape (Cursor's `bubbles[].toolFormerData`: {name,
# params, rawArgs, result, ...}) rather than nested content items, so
# _SKIP_ITEM_TYPES/CONTENT_ITEM_TYPE_MAP never sees it. Kept as its own
# small table for the same reason as §1's three — extend here, not with a
# new code path, when another format's tool call turns out to live beside
# the content field instead of inside it.
SIBLING_TOOL_DATA_KEYS: Tuple[str, ...] = ("toolFormerData",)

def _sibling_tool_blocks(cfg: Config, rec: Any) -> List[Tuple[str,str,Optional[str]]]:
    if not isinstance(rec, dict) or "tool_use" not in cfg.keep_item_types:
        return []
    blocks: List[Tuple[str,str,Optional[str]]] = []
    for key in SIBLING_TOOL_DATA_KEYS:
        td = rec.get(key)
        if not isinstance(td, dict): continue
        name = td.get("name") or td.get("tool")
        args = td.get("rawArgs") or td.get("params") or td.get("args") or td.get("input")
        if isinstance(args, str):
            try: args = json.loads(args)
            except Exception: pass
        if args is not None:
            try: arg_text = json.dumps(args, indent=2, ensure_ascii=False)
            except Exception: arg_text = str(args)
            blocks.append(("tool_use", arg_text, str(name) if name else None))
        result = td.get("result") or td.get("output")
        if "tool_result" in cfg.keep_item_types and isinstance(result, str) and result.strip():
            blocks.append(("tool_result", result, None))
    return blocks

def build_msg(cfg: Config, role: Any, ts: Any, content: Any,
              thinking_extra: Optional[str]=None, stop: Optional[str]=None,
              rec: Any=None) -> Optional[Message]:
    r = norm_role(role, rec)
    if r in cfg.skip_roles: return None
    if stop == "error" and not thinking_extra:
        probe, _, _ = extract_content_ex(cfg, content)
        if not probe: return None

    text, think_from_content, extra_blocks = extract_content_ex(cfg, content)
    final_think = thinking_extra or think_from_content
    sibling_blocks = _sibling_tool_blocks(cfg, rec)

    if not text and not final_think and not extra_blocks and not sibling_blocks: return None

    if r == "user" and text:
        text = strip_meta(text)
        text = strip_gemini_refs(text)

    text = clean(text)
    final_think = clean(final_think) if final_think else None

    if not text and not final_think and not extra_blocks and not sibling_blocks: return None
    if text and cfg.is_boilerplate(r, text): return None

    blocks: List[Tuple[str,str,Optional[str]]] = []
    if final_think: blocks.append(("thinking", final_think, None))
    if text: blocks.append(("text", text, None))
    for bt, btext, bname in extra_blocks + sibling_blocks:
        btext_clean = clean(btext) if isinstance(btext, str) else btext
        if not btext_clean and bt not in ("image","audio"): continue
        blocks.append((bt, btext_clean, bname))

    return Message(role=r, timestamp=fmt_ts(ts), text=text or "(Tool operations)",
                    thinking=final_think, blocks=blocks)

# ── Record resolution (spec §1.3) ──────────────────────────────────────────────
def _sort_time(items: Iterable[Any]) -> List[Any]:
    dec = []
    for i, item in enumerate(items):
        ts = None
        if isinstance(item, dict):
            ts = first_of(item, *TIMESTAMP_KEYS)
            if ts is None and isinstance(item.get("message"), dict):
                ts = first_of(item["message"], *TIMESTAMP_KEYS)
        dt = coerce_dt(ts)
        dec.append((0 if dt else 1, dt or datetime.min, i, item))
    dec.sort(key=lambda x: (x[0],x[1],x[2]))
    return [x[3] for x in dec]

_depth_limit_hits = 0  # surfaced in the log if a file ever hits it

def resolve_record(cfg: Config, rec: Any, depth: int = 0) -> List[Message]:
    """Turn one record into zero or more Messages, by shape, not by provider.

    1. If rec['type'] is a known non-message type -> [].
    2. If any WRAPPER_KEYS key holds a dict -> unwrap and recurse, merging
       the outer timestamp/model down if the inner record lacks them.
    3. If any CONTAINER_KEYS key holds a dict/list -> descend into each,
       time-sorted, and concatenate.
    4. Otherwise treat rec as a leaf record: role via ROLE_KEYS, content via
       CONTENT_KEYS, timestamp via TIMESTAMP_KEYS, thinking via
       extract_thinking().
    """
    global _depth_limit_hits
    if depth > _MAX_RESOLVE_DEPTH:
        _depth_limit_hits += 1
        return []

    if isinstance(rec, list):
        out: List[Message] = []
        for item in _sort_time(rec):
            out.extend(resolve_record(cfg, item, depth + 1))
        return out
    if not isinstance(rec, dict):
        return []

    eff_skip = NON_MESSAGE_TYPES | cfg.skip_record_types
    rtype = rec.get("type")
    if isinstance(rtype, str) and rtype.strip().lower() in eff_skip:
        return []

    # 2. wrapper unwrap
    for wk in WRAPPER_KEYS:
        val = rec.get(wk)
        if isinstance(val, dict):
            inner = dict(val)
            inner_has_ts = any(inner.get(tk) not in (None,"") for tk in TIMESTAMP_KEYS)
            if not inner_has_ts:
                outer_ts = first_of(rec, *TIMESTAMP_KEYS)
                if outer_ts is not None:
                    inner["timestamp"] = outer_ts
            if "model" not in inner and rec.get("model") is not None:
                inner["model"] = rec["model"]
            return resolve_record(cfg, inner, depth + 1)

    # 3. container descent
    for ck in CONTAINER_KEYS:
        val = rec.get(ck)
        if isinstance(val, dict):
            msgs: List[Message] = []
            for item in _sort_time(list(val.values())):
                msgs.extend(resolve_record(cfg, item, depth + 1))
            if msgs: return msgs
        elif isinstance(val, list):
            msgs = []
            for item in _sort_time(val):
                msgs.extend(resolve_record(cfg, item, depth + 1))
            if msgs: return msgs

    # 4. leaf record
    role = None
    for rk in ROLE_KEYS:
        v = rec.get(rk)
        if v in (None, ""): continue
        if rk == "type" and isinstance(v, str) and v.strip().lower() in eff_skip:
            continue
        role = v
        break
    if role is None:
        return []
    content = first_of(rec, *CONTENT_KEYS)
    has_sibling_tool_data = any(isinstance(rec.get(k), dict) for k in SIBLING_TOOL_DATA_KEYS)
    if content is None and not has_sibling_tool_data:
        return []
    ts = first_of(rec, *TIMESTAMP_KEYS)
    stop = rec.get("stopReason") or rec.get("stop_reason")
    m = build_msg(cfg, role, ts, content, extract_thinking(rec), stop, rec)
    return [m] if m else []

def extract_messages(cfg: Config, record: Any) -> List[Message]:
    return resolve_record(cfg, record, 0)

# ── Deduplication ─────────────────────────────────────────────────────────────
def dedupe(messages: Iterable[Message]) -> List[Message]:
    out: List[Message] = []
    last_exact: Optional[Tuple] = None
    last_content: Optional[Tuple] = None
    for m in messages:
        # v5.0: fold in the non-text/thinking blocks (tool calls etc.) so two
        # messages that happen to share the same v4.0-style (role, text) —
        # e.g. an identical "Reading it now." aside repeated before two
        # different tool calls — aren't wrongly collapsed into one. A
        # CHAT-mode message never carries such blocks (keep_item_types is
        # empty there), so this is a no-op for v4.0-identical output.
        extra_sig = tuple((bt, bt_text, bn) for bt, bt_text, bn in m.blocks if bt not in ("text","thinking"))
        exact = (m.role, m.timestamp, m.text, extra_sig)
        content_key = (m.role, m.text, extra_sig)
        if exact == last_exact or content_key == last_content: continue
        out.append(m)
        last_exact = exact; last_content = content_key
    return out

# ── Output naming ─────────────────────────────────────────────────────────────
def _slug(value: str, fallback: str) -> str:
    s = re.sub(r"[^0-9A-Za-z._-]+","-",value).strip("-._")
    return (s[:60] or fallback[:60] or "session").strip("-._") or "session"

def _strip_json_sfx(s: str) -> str:
    return re.sub(r"\.jsonl?(?:[._-].*)?$","",s,flags=re.IGNORECASE)

def derive_title(messages: Sequence[Message], fallback: str) -> str:
    skip = ("agents.md instructions","<instructions>","<environment_context>","current_date>","timezone>")
    for m in messages:
        lines = [l.strip(" #-*\t") for l in m.text.splitlines() if l.strip()]
        if not lines: continue
        line = WS_RE.sub(" ", lines[0]).strip(" .:-")
        if not line: continue
        if any(p in line.lower() for p in skip): continue
        return " ".join(line.split()[:10])[:90]
    return fallback

def output_name(source: Path, title: str, input_root: Path) -> str:
    try:
        rel = source.relative_to(input_root)
        stem = "__".join(_strip_json_sfx(p) for p in rel.parts)
    except ValueError:
        stem = _strip_json_sfx(source.name)
    ss = _slug(stem, source.stem)
    ts = _slug(title.lower(), source.stem)
    return ss + ".md" if ts == ss else ss + "--" + ts + ".md"

# ── Markdown writing ──────────────────────────────────────────────────────────
_STUB_TYPES = {"image", "audio", "attachment"}
_LABELLED_TYPES = {"refusal": "refusal", "error": "error"}

def _write_legacy_message(fh, cfg: Config, m: Message) -> None:
    """v4.0-identical rendering — used whenever a message carries nothing
    beyond text/thinking, in every mode (this is what makes CHAT mode, and
    plain TOOLS/RAW messages, byte-identical to v4.0)."""
    role_label = m.role.capitalize() if m.role != "unknown" else "Message"
    stamp = f" — {m.timestamp}" if m.timestamp else ""
    if m.role == "user":
        fh.write("---\n\n")
    if m.thinking:
        fh.write(f"### {role_label} (thinking){stamp}\n\n")
        fh.write("> " + m.thinking.replace("\n","\n> ") + "\n\n")
    fh.write(f"## {role_label}{stamp}\n\n")
    tool_roles = {"tool","toolresult","tool_result","function","function_result","system"}
    if cfg.render_tool_in_codeblock and m.role.lower() in tool_roles:
        fh.write(f"```text\n{m.text}\n```\n\n")
    else:
        fh.write(f"{m.text}\n\n")

def _write_block_message(fh, m: Message) -> None:
    """v5.0 rendering for a message carrying kept tool/image/etc. blocks
    (spec §2.2), in source order."""
    role_label = m.role.capitalize() if m.role != "unknown" else "Message"
    stamp = f" — {m.timestamp}" if m.timestamp else ""
    if m.role == "user":
        fh.write("---\n\n")
    heading_written = False
    for btype, text, tool_name in m.blocks:
        if btype == "thinking":
            fh.write(f"### {role_label} (thinking){stamp}\n\n")
            fh.write("> " + text.replace("\n","\n> ") + "\n\n")
            continue
        if not heading_written:
            fh.write(f"## {role_label}{stamp}\n\n")
            heading_written = True
        if btype == "text":
            fh.write(f"{text}\n\n")
        elif btype == "tool_use":
            fh.write(f"> **tool: {tool_name or 'unknown'}**\n\n")
            fh.write(f"```json\n{text}\n```\n\n")
        elif btype == "tool_result":
            fh.write(f"```text\n{text}\n```\n\n")
        elif btype in _STUB_TYPES:
            fh.write(f"*[{btype}]*\n\n")
        elif btype in _LABELLED_TYPES:
            fh.write(f"**{_LABELLED_TYPES[btype]}:** {text}\n\n")
        else:
            fh.write(f"*[{btype} block]*\n\n" + (f"{text}\n\n" if text else ""))
    if not heading_written:
        fh.write(f"## {role_label}{stamp}\n\n")

def write_md(cfg: Config, path: Path, label: str, title: str,
             messages: Sequence[Message]) -> None:
    now = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
    with path.open("w", encoding="utf-8") as fh:
        fh.write(f"# {title}\n\n")
        fh.write(f"- Source: `{label}`\n")
        fh.write(f"- Mode: {cfg.mode}\n")
        fh.write(f"- Extracted: {now}\n")
        fh.write(f"- Messages: {len(messages)}\n\n")
        for m in messages:
            has_extra = any(bt not in ("text","thinking") for bt,_,_ in m.blocks)
            if has_extra:
                _write_block_message(fh, m)
            else:
                _write_legacy_message(fh, cfg, m)

# ═══════════════════════════════════════════════════════════════════════════
# §3 — Coverage self-report (spec §3)
# ═══════════════════════════════════════════════════════════════════════════

# Key names that are legitimately structural, not conversational content —
# excluded from the accountable-character total. One entry per reason, so
# the exclusion is auditable rather than a guess. When in doubt, a path is
# NOT excluded (over-reporting a gap is safe; under-reporting is the bug
# this feature exists to prevent).
_STRUCTURAL_KEY_NAMES: Set[str] = {
    "id", "uuid", "message_id", "conversation_id", "session_id", "node_id",
    "parent_node_id", "row_id", "tool_use_id", "toolCallId", "bubbleId",
    "composerId", "trajectory_id", "cascade_id", "parent", "children",
    "workspace_path", "composerid", "modelcallid",             # identifiers / id references
    "toolcallbinary",                                          # opaque encoded blob, not text (Cursor)
    "type", "role", "sender", "author", "kind", "content_type",
    "step_type", "capabilitytype",                              # role/type tags (counted via the extracted text itself, not as free content)
    "timestamp", "create_time", "created_at", "createdAt",
    "ts", "time", "source_mtime", "updated_at", "update_time",  # timestamps
    "schema", "schema_version", "_v", "step_format",            # format/version tags
    "title", "name", "model", "model_slug",                     # conversation/model labels, not dialogue body
}
_HASHLIKE_RE = re.compile(r"^[0-9a-fA-F-]{8,}$")

# Path prefixes that are whole structural subtrees — client-UI configuration
# dumps, not conversational content, that some formats embed in every
# record (e.g. Devin Desktop ACP's `meta.info.configOptions`/
# `availableCommands`, which list the client's own mode/model picker
# entries — present whether or not the user ever touched them).
_STRUCTURAL_PATH_PREFIXES: Tuple[str, ...] = (
    "meta.info.configOptions",
    "meta.info.availableCommands",
    # Cursor's composer_data carries a denormalized summary/UI-state layer
    # alongside the actual bubbles: fullConversationHeadersOnly duplicates
    # each bubble's own text as a search-index preview, richText is a
    # duplicate serialization of the same conversation for the editor
    # widget, and originalFileStates is pre-edit file snapshots (content
    # keys/hashes, not dialogue).
    "composer_data.fullConversationHeadersOnly",
    "composer_data.richText",
    "composer_data.originalFileStates",
)

def _has_structural_prefix(path: str) -> bool:
    return any(path == p or path.startswith(p + ".") or path.startswith(p + "[]")
               for p in _STRUCTURAL_PATH_PREFIXES)

def _is_structural_leaf(key: str, value: str) -> bool:
    # A namespaced key (e.g. ACP's "cognition.ai/timestamp") still counts as
    # structural if its final segment (after the last "/" or ".") matches —
    # the namespace prefix doesn't change what the value *is*.
    last_segment = re.split(r"[./]", key)[-1] if key else key
    if key in _STRUCTURAL_KEY_NAMES or key.lower() in _STRUCTURAL_KEY_NAMES: return True
    if last_segment in _STRUCTURAL_KEY_NAMES or last_segment.lower() in _STRUCTURAL_KEY_NAMES: return True
    if value in ("true","false","null","True","False","None"): return True
    if _HASHLIKE_RE.match(value) and ("-" in value or len(value) >= 32): return True
    return False

def walk_leaves(obj: Any, path: str = "", leaf_key: str = "") -> Iterable[Tuple[str, str, str]]:
    """Yield (key_path, string_value, leaf_key) for every string-valued leaf
    in obj. `leaf_key` is the real dict key the value was stored under —
    kept separate from `path` (a "."-joined display string) because a real
    JSON key can itself contain a literal "." (seen in the wild: ACP's
    "cognition.ai/timestamp"), which would otherwise corrupt a
    path.rsplit(".", 1) reconstruction of the key name."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from walk_leaves(v, f"{path}.{k}" if path else str(k), str(k))
    elif isinstance(obj, list):
        for v in obj:
            yield from walk_leaves(v, f"{path}[]", leaf_key)
    elif isinstance(obj, str):
        yield (path, obj, leaf_key)

def measure_accountable(records: List[Any]) -> Tuple[int, Dict[str, Tuple[int,int,List[str]]]]:
    """Total accountable characters across all records, plus a per-path
    breakdown of (char_count, record_count, sample_values) — the sample
    values let convert_one() later test which paths' content genuinely
    didn't make it to the emitted Markdown (see _find_unaccounted_paths),
    rather than just reporting the highest-volume paths regardless of
    whether they were actually emitted."""
    total = 0
    per_path: Dict[str, List[Any]] = {}
    for rec in records:
        # Some providers store the same string twice at different paths
        # within one record (e.g. Claude Desktop's chat_messages[].text
        # duplicating chat_messages[].content[].text). Count each distinct
        # value once per record so that denormalization doesn't silently
        # halve the measured coverage of content that was, in fact, fully
        # emitted — cross-record repeats (the same sentence appearing in
        # two different messages) are NOT deduped, since those are
        # legitimately separate accountable content.
        seen_in_record: Set[str] = set()
        for path, val, leaf_key in walk_leaves(rec):
            if _is_structural_leaf(leaf_key, val) or _has_structural_prefix(path):
                continue
            if val in seen_in_record:
                continue
            seen_in_record.add(val)
            total += len(val)
            bucket = per_path.setdefault(path, [0, 0, []])
            bucket[0] += len(val)
            bucket[1] += 1
            if len(bucket[2]) < 8:
                bucket[2].append(val)
    return total, {k: (v[0], v[1], v[2]) for k, v in per_path.items()}

def _emitted_text(messages: Sequence[Message]) -> str:
    parts: List[str] = []
    for m in messages:
        if m.blocks:
            for _, text, _ in m.blocks:
                if text: parts.append(text)
        else:
            if m.text: parts.append(m.text)
            if m.thinking: parts.append(m.thinking)
    return "\n".join(parts)

def _norm_for_membership(s: str) -> str:
    """Whitespace-stripped form used only to test whether a source value
    survived into the emitted text. write_md()/clean() reformat whitespace
    (blank-line collapsing, trailing-space trims), and a tool_use block's
    arguments are re-serialized with json.dumps(indent=2) rather than kept
    byte-identical to the source's often-compact JSON string — an
    exact-substring test would misreport either as missing content."""
    return WS_RE.sub("", s)

def _find_unaccounted_paths(
    per_path: Dict[str, Tuple[int,int,List[str]]], emitted_text: str, limit: int = 5,
) -> List[Tuple[str,int,int]]:
    """Rank paths by how much of their *actual* content is absent from the
    emitted text (sampled up to 8 values per path — exact for any path with
    8 or fewer occurrences, an estimate for higher-frequency ones), so the
    report names genuine gaps instead of merely high-volume paths."""
    norm_emitted = _norm_for_membership(emitted_text)
    scored: List[Tuple[str,int,int]] = []
    for path, (chars, n_records, samples) in per_path.items():
        if not samples:
            continue
        missing = sum(1 for s in samples if s and _norm_for_membership(s) not in norm_emitted)
        if missing == 0:
            continue
        missing_chars = round(chars * (missing / len(samples)))
        if missing_chars <= 0:
            continue
        scored.append((path, missing_chars, n_records))
    scored.sort(key=lambda x: -x[1])
    return scored[:limit]

# ═══════════════════════════════════════════════════════════════════════════
# §4 — --diagnose (spec §4)
# ═══════════════════════════════════════════════════════════════════════════

def diagnose_records(records: List[Any]) -> Dict[str, Any]:
    """Full enumeration (never a sample) of a file's shape."""
    key_paths: Dict[str, Dict[str, int]] = {}   # path -> {type_name: count}
    leaf_volume: Dict[str, int] = {}
    shape_sigs: Dict[Tuple[str, ...], int] = {}
    matched_wrapper: Set[str] = set()
    matched_container: Set[str] = set()
    matched_role: Set[str] = set()
    role_values: Dict[str, Set[str]] = {}

    def walk(obj: Any, path: str, top_level: bool) -> None:
        tname = type(obj).__name__
        key_paths.setdefault(path or "$", {}).setdefault(tname, 0)
        key_paths[path or "$"][tname] += 1
        if isinstance(obj, dict):
            if top_level or True:
                sig = tuple(sorted(obj.keys()))
                shape_sigs[sig] = shape_sigs.get(sig, 0) + 1
            for wk in WRAPPER_KEYS:
                if isinstance(obj.get(wk), dict): matched_wrapper.add(wk)
            for ck in CONTAINER_KEYS:
                if isinstance(obj.get(ck), (dict, list)): matched_container.add(ck)
            for rk in ROLE_KEYS:
                if obj.get(rk) not in (None, ""):
                    matched_role.add(rk)
                    role_values.setdefault(rk, set()).add(str(obj.get(rk))[:40])
            for k, v in obj.items():
                child_path = f"{path}.{k}" if path else str(k)
                walk(v, child_path, False)
        elif isinstance(obj, list):
            for v in obj:
                walk(v, f"{path}[]", False)
        elif isinstance(obj, str):
            leaf_volume[path] = leaf_volume.get(path, 0) + len(obj)

    for rec in records:
        walk(rec, "", True)

    return {
        "key_paths": key_paths,
        "leaf_volume": leaf_volume,
        "shape_sigs": shape_sigs,
        "matched_wrapper": matched_wrapper,
        "matched_container": matched_container,
        "matched_role": matched_role,
        "role_values": role_values,
        "unmatched_wrapper": set(WRAPPER_KEYS) - matched_wrapper,
        "unmatched_container": set(CONTAINER_KEYS) - matched_container,
        "unmatched_role": set(ROLE_KEYS) - matched_role,
    }

def write_diagnosis(out_path: Path, files: List[Path], started: datetime) -> None:
    with out_path.open("w", encoding="utf-8") as fh:
        fh.write(f"# Diagnosis — {started.strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        fh.write(f"- **Files:** {len(files)}\n\n")
        fh.write("Full enumeration — every record of every file, no sampling.\n\n")
        for f in files:
            t0 = datetime.now()
            records, replaced = read_records(f)
            d = diagnose_records(records)
            elapsed = (datetime.now() - t0).total_seconds()
            fh.write(f"---\n\n## {f.name}\n\n")
            fh.write(f"- **Path:** `{f}`\n")
            fh.write(f"- **Records:** {len(records)}\n")
            fh.write(f"- **Elapsed:** {elapsed:.2f}s\n")
            if replaced:
                fh.write(f"- **Warning:** {replaced} invalid-UTF-8 byte(s) replaced during decode\n")
            fh.write("\n### Matched table entries\n\n")
            fh.write(f"- wrapper: {sorted(d['matched_wrapper']) or '(none)'}\n")
            fh.write(f"- container: {sorted(d['matched_container']) or '(none)'}\n")
            fh.write(f"- role: {sorted(d['matched_role']) or '(none)'}\n")
            fh.write("\n### Unmatched table entries (candidates to add)\n\n")
            fh.write(f"- wrapper: {sorted(d['unmatched_wrapper']) or '(none)'}\n")
            fh.write(f"- container: {sorted(d['unmatched_container']) or '(none)'}\n")
            fh.write(f"- role: {sorted(d['unmatched_role']) or '(none)'}\n")
            fh.write("\n### Role values observed, by key\n\n")
            for rk, vals in sorted(d["role_values"].items()):
                fh.write(f"- `{rk}`: {sorted(vals)}\n")
            fh.write("\n### Distinct record shapes (top-level key sets), by count\n\n")
            for sig, count in sorted(d["shape_sigs"].items(), key=lambda x: -x[1])[:15]:
                fh.write(f"- {count}× `{{{', '.join(sig)}}}`\n")
            fh.write("\n### Top string-leaf paths by character volume\n\n")
            for path, vol in sorted(d["leaf_volume"].items(), key=lambda x: -x[1])[:20]:
                fh.write(f"- `{path}` — {vol} chars\n")
            fh.write("\n")
    print(f"[diagnose] wrote {out_path}")

def run_diagnose(input_paths: List[str]) -> int:
    files = collect_files(input_paths)
    if not files:
        print("[diagnose] No JSON/JSONL files found.")
        return 1
    started = datetime.now().astimezone()
    out_path = files[0].parent / "diagnosis.md"
    write_diagnosis(out_path, files, started)
    return 0

# ── Convert one file ──────────────────────────────────────────────────────────
def convert_one(cfg: Config, source: Path, out_root: Path,
                input_root: Path) -> ConvResult:
    records, replaced_chars = read_records(source)
    bad_byte_note = (
        f"{replaced_chars} byte(s) in the source file were not valid UTF-8 and were "
        f"replaced with � during decode — check the original export for "
        f"truncation or binary corruption."
    ) if replaced_chars else ""

    if not records:
        res = ConvResult(source, None, "SKIP", "No valid JSON records found")
        res.note = bad_byte_note
        return res

    accountable, per_path = measure_accountable(records)

    messages: List[Message] = []
    for rec in records:
        messages.extend(extract_messages(cfg, rec))
    messages = dedupe(messages)

    if not messages:
        res = ConvResult(source, None, "SKIP", "No conversational text found")
        res.note = bad_byte_note
        res.top_key_paths = sorted(
            ((p, v[0]) for p, v in per_path.items()), key=lambda x: -x[1]
        )[:5]
        return res

    emitted_text = _emitted_text(messages)
    emitted = len(emitted_text)
    coverage = (emitted / accountable) if accountable else 1.0
    coverage = min(coverage, 1.0)

    title = derive_title(messages, source.stem)
    fname = output_name(source, title, input_root)
    mode_tag = cfg.mode.capitalize()   # Chat | Tools | Raw
    fname = f"[{mode_tag}] {fname}"
    out_path = out_root / fname
    try:
        label = str(source.relative_to(input_root))
    except ValueError:
        label = str(source)
    write_md(cfg, out_path, label, title, messages)
    res = ConvResult(source, out_path, "WROTE", fname)
    res.note = bad_byte_note
    res.coverage = coverage
    if coverage < 0.90:
        res.top_unaccounted = _find_unaccounted_paths(per_path, emitted_text)
    return res

# ── Conversion log ────────────────────────────────────────────────────────────
def write_log(log_path: Path, cfg: Config, out_root: Path,
              results: List[ConvResult], started: datetime) -> bool:
    """Returns True if any WROTE file fell below the 50% coverage floor."""
    wrote   = sum(1 for r in results if r.status == "WROTE")
    skipped = len(results) - wrote
    any_very_low = False

    with log_path.open("w", encoding="utf-8") as fh:
        fh.write(f"# Extraction Report — {cfg.mode} — {started.strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        fh.write(f"- **Script:** `{cfg.script_name}`\n")
        fh.write(f"- **Mode:** {cfg.mode}\n")
        fh.write(f"- **Output folder:** `{out_root}`\n")
        fh.write(f"- **Files:** {len(results)} input · {wrote} converted · {skipped} skipped\n\n")
        fh.write("---\n\n")
        for r in results:
            status_icon = "✅" if r.status == "WROTE" else "❌"
            fh.write(f"## {status_icon} {r.source.name}\n\n")
            fh.write(f"- **Source:** `{r.source}`\n")
            if r.output:
                fh.write(f"- **Output:** `{r.detail}`\n")
            else:
                fh.write(f"- **Skipped:** {r.detail}\n")
            if r.note:
                fh.write(f"- **Warning:** ⚠ {r.note}\n")
            if r.coverage is not None:
                pct = round(r.coverage * 100)
                fh.write(f"- **Coverage:** {pct}% of accountable content emitted ({cfg.mode} mode)\n")
                if r.coverage < 0.50:
                    any_very_low = True
                    fh.write("- **⚠⚠ Likely unsupported shape** — top unaccounted paths:\n")
                    for path, chars, n in r.top_unaccounted:
                        fh.write(f"  - `{path}` — {chars:,} chars, {n} records\n")
                    fh.write("  - Consider adding a table entry — see WRAPPER_KEYS/CONTAINER_KEYS/"
                              "ROLE_KEYS in `_session_extractor_core.py`, or run `--diagnose`.\n")
                elif r.coverage < 0.90 and r.top_unaccounted:
                    fh.write("- **⚠ Warning** — top unaccounted paths:\n")
                    for path, chars, n in r.top_unaccounted:
                        fh.write(f"  - `{path}` — {chars:,} chars, {n} records\n")
            if r.top_key_paths:
                fh.write("- **Top key paths seen (file was skipped):**\n")
                for path, chars in r.top_key_paths:
                    fh.write(f"  - `{path}` — {chars:,} chars\n")
            fh.write("\n")
    return any_very_low

# ── Main entry ────────────────────────────────────────────────────────────────
def run(argv: Sequence[str], cfg: Config) -> int:
    parser = make_parser(cfg.mode)
    add_inputs(parser)
    args = parser.parse_args(argv)

    if args.diagnose:
        return run_diagnose(args.input_paths)

    files = collect_files(args.input_paths)
    if not files:
        print(f"[{cfg.mode}] No JSON/JSONL files found.")
        return 1

    started = datetime.now().astimezone()
    out_root = choose_output_root(files, cfg.mode)
    out_root.mkdir(parents=True, exist_ok=True)

    parents = {f.parent for f in files}
    input_root = parents.pop() if len(parents) == 1 else Path.cwd()

    results: List[ConvResult] = []
    for source in files:
        res = convert_one(cfg, source, out_root, input_root)
        results.append(res)
        icon = "✓" if res.status == "WROTE" else "✗"
        print(f"  {icon} [{res.status}] {res.source.name}")
        if res.output:
            print(f"          → {res.detail}")
            if res.coverage is not None:
                print(f"          coverage: {round(res.coverage*100)}%")
        else:
            print(f"          ✗ {res.detail}")
        if res.note:
            print(f"          ⚠ {res.note}")

    log_path = out_root / "conversion_log.md"
    any_very_low = write_log(log_path, cfg, out_root, results, started)

    wrote_n = sum(1 for r in results if r.status == "WROTE")
    print(f"\n[{cfg.mode}] Finished  —  {wrote_n}/{len(files)} converted")
    print(f"  Folder : {out_root}")
    print(f"  Log    : {log_path}")

    if any_very_low and not args.allow_low_coverage:
        print(f"  ⚠⚠ At least one file fell below 50% coverage — exiting non-zero. "
              f"Pass --allow-low-coverage to suppress.")
        return 2
    return 0
