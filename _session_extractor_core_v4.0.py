#!/usr/bin/env python3
"""
_session_extractor_core_v4.0.py  —  Shared extraction engine (v4.0)

NOT meant to be run directly. Imported by the three wrapper scripts
(markdown_extraction_{chat,tools,raw}_v4.0.py).

This is a duplicate-and-fix of _session_extractor_core.py (v3.4/v3.5 line).
The original file is untouched — this is a new, separately versioned copy,
per the "duplicate, don't overwrite" instruction. See EXTRACTION_v4.0_CHANGELOG.txt
for the full writeup; short version below.

--- WHAT CHANGED IN v4.0 AND WHY (both fixes are additive / format-agnostic) ---

1. FIX: CHAT mode didn't strip Windsurf-legacy memory-injection blocks.
   Windsurf's legacy export format wraps retrieved-memory content in a
   `<SYSTEM-RETRIEVED-MEMORY[uuid]>...</SYSTEM-RETRIEVED-MEMORY[uuid]>` tag pair,
   stored inline inside an ordinary role="assistant" text field (this source
   format has no separate "system"/"tool"/"memory" role to filter on the way
   OpenClaw/Gemini CLI/Codex do). boilerplate_chat() had no marker for this tag,
   so CHAT mode passed it straight through — verified by diffing a CHAT-mode
   and RAW-mode export of the same source file: they came out byte-identical,
   which should never happen (CHAT is supposed to drop exactly this kind of
   memory/system content).

   Fix: added one marker string to the existing boilerplate_chat() substring
   list — the same mechanism already used for OpenClaw/Codex/Gemini markers
   ("<environment_context>", "# agents.md instructions", etc). This is a pure
   list addition using the pre-existing detection idiom, so it cannot affect
   any other provider's output: the literal string "<system-retrieved-memory["
   does not occur in OpenAI/Anthropic/Google/Copilot exports. Verified against
   both sample files that every message containing this tag is >99.9% *covered*
   by the tag span (i.e. these are dedicated memory-dump turns, not messages
   that mix memory content with real dialogue) — so a whole-message skip does
   not risk deleting genuine conversation, consistent with how every other
   marker in that list already behaves.

   Scope note: boilerplate_tools() and boilerplate_raw() were deliberately
   NOT changed. TOOLS mode's own docstring says it's meant to keep "system
   prompts, agent instructions, and memory files alongside dialogue" for
   auditing, and RAW mode is explicitly the forensic/complete-record mode.
   Stripping memory blocks there would contradict what those modes are for.

2. FIX (latent-risk hardening): read_records() used to open every file with
   errors="ignore", which silently *deletes* any byte that isn't valid UTF-8 —
   no warning, no log line, nothing. That's true of every source format this
   suite reads, not just Windsurf. It didn't happen to trigger on the two
   sample files (both checked clean, valid UTF-8 throughout), but it's a
   correctness landmine: the next slightly-corrupted export would just lose
   characters with zero indication anything was wrong.

   Fix: switched to errors="replace" and now COUNT how many U+FFFD replacement
   characters that introduces. The count is threaded through convert_one() and
   surfaced in conversion_log.md as a "Warning" line. Zero behavior change for
   any well-formed UTF-8 file (which is every format this suite has ever been
   run against) — it only changes what happens on bytes that were already
   invalid, from "silently vanish" to "visible + logged".

Everything else (role normalization, OpenClaw/Codex/Gemini/generic record
extraction, dedup, title derivation, file naming) is unchanged from the
v3.4/v3.5 core, byte-for-byte in logic.
"""

import argparse, json, re, sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, List, Optional, Sequence, Set, Tuple

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

COLLECTION_KEYS = ("messages", "items", "turns", "conversation", "conversations", "entries", "mapping")

_BASE_SKIP_TYPES: Set[str] = {
    "session", "session_meta", "event_msg", "turn_context",
    "thinking_level_change", "model_change", "custom", "info",
}
_SKIP_ITEM_TYPES: Set[str] = {
    "tool_call", "tool_result", "tool_use",
    "function_call", "function_call_output", "function_result",
    "custom_tool_call", "custom_tool_call_output",
    "image", "image_url", "input_image", "output_image",
    "audio", "refusal", "error", "toolcall",
}

# ── Data classes ──────────────────────────────────────────────────────────────
@dataclass
class Message:
    role: str
    timestamp: str
    text: str
    thinking: Optional[str] = None

@dataclass
class ConvResult:
    source: Path
    output: Optional[Path]
    status: str   # "WROTE" | "SKIP"
    detail: str   # filename or skip reason
    note: str = ""  # v4.0: optional warning surfaced in the log (e.g. bad-byte count)

@dataclass
class Config:
    mode: str                                          # CHAT / TOOLS / RAW
    script_name: str                                   # for the log
    skip_roles: Set[str]                               # discard these roles entirely
    skip_record_types: Set[str]                        # additional record types to skip
    is_boilerplate: Callable[[str, str], bool]         # (role, text) -> bool
    render_tool_in_codeblock: bool                     # RAW mode wraps tool output

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
    """Read a JSON/JSONL file as UTF-8.

    Returns (records, replaced_char_count).

    v4.0 change: previously used errors="ignore", which silently *deletes*
    any byte that isn't valid UTF-8. Now uses errors="replace" so invalid
    bytes become U+FFFD instead of disappearing, and replaced_char_count
    reports how many were found so callers can surface a warning instead of
    corruption vanishing without a trace. For well-formed UTF-8 input (the
    normal case for every supported format) behavior is identical to before.
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
def norm_role(role: Any) -> str:
    if isinstance(role, dict):
        role = role.get("role") or role.get("name") or role.get("type")
    if role is None: return "unknown"
    v = str(role).strip().lower()
    return {"human":"user","assistant_response":"assistant",
            "gemini":"assistant","bot":"assistant","ai":"assistant"}.get(v, v or "unknown")

def coerce_dt(value: Any) -> Optional[datetime]:
    if value in (None, ""): return None
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
                # Gemini CLI format: {text: "..."} with no type field
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
              "<permissions_instructions>","---\n*stinger ·","---\n*mission control ·",
              "---\n*miss peacock ·","# user.md","# soul.md","# identity.md","# memory.md",
              "<system-retrieved-memory["):  # v4.0: Windsurf-legacy memory-injection blocks
        if m in lo: return True
    return _is_tool_json(text) or _is_ts_listing(text, lo)

def boilerplate_tools(role: str, text: str) -> bool:
    if role not in ("user","assistant","unknown"): return False
    lo = text.lower()
    return _is_tool_json(text) or _is_ts_listing(text, lo)

def boilerplate_raw(role: str, text: str) -> bool:
    return _is_ts_listing(text, text.lower())

# ── Message building ──────────────────────────────────────────────────────────
def build_msg(cfg: Config, role: Any, ts: Any, content: Any,
              thinking_extra: Optional[str]=None, stop: Optional[str]=None) -> Optional[Message]:
    r = norm_role(role)
    if r in cfg.skip_roles: return None
    if stop == "error" and not thinking_extra:
        probe, _ = extract_content(content)
        if not probe: return None

    text, think_from_content = extract_content(content)
    final_think = thinking_extra or think_from_content

    if not text and not final_think: return None

    if r == "user" and text:
        text = strip_meta(text)
        text = strip_gemini_refs(text)

    text = clean(text)
    final_think = clean(final_think) if final_think else None

    if not text and not final_think: return None
    if text and cfg.is_boilerplate(r, text): return None

    return Message(role=r, timestamp=fmt_ts(ts), text=text or "(Tool operations)", thinking=final_think)

# ── Record extraction ─────────────────────────────────────────────────────────
def _sort_time(items: Iterable[Any]) -> List[Any]:
    dec = []
    for i, item in enumerate(items):
        ts = None
        if isinstance(item, dict):
            ts = first_of(item, "timestamp","create_time","ts","time")
            if ts is None and isinstance(item.get("message"), dict):
                ts = first_of(item["message"], "timestamp","create_time","ts","time")
        dt = coerce_dt(ts)
        dec.append((0 if dt else 1, dt or datetime.min, i, item))
    dec.sort(key=lambda x: (x[0],x[1],x[2]))
    return [x[3] for x in dec]

def _from_openclaw(cfg: Config, rec: dict) -> List[Message]:
    p = rec["message"]
    stop = p.get("stopReason") or p.get("stop_reason")
    m = build_msg(cfg,
                  p.get("role") or p.get("author"),
                  first_of(p,"timestamp","created_at") or rec.get("timestamp"),
                  first_of(p,"content","text","message"),
                  extract_thinking(p), stop)
    return [m] if m else []

def _from_rollout(cfg: Config, rec: dict) -> List[Message]:
    if rec.get("type") != "response_item": return []
    p = rec.get("payload",{})
    if not isinstance(p,dict) or p.get("type") != "message": return []
    m = build_msg(cfg, p.get("role"),
                  first_of(p,"timestamp","created_at") or rec.get("timestamp"),
                  p.get("content"), extract_thinking(p))
    return [m] if m else []

def _from_history(cfg: Config, rec: dict) -> List[Message]:
    if "session_id" not in rec or "text" not in rec: return []
    m = build_msg(cfg, rec.get("role","user"),
                  first_of(rec,"timestamp","ts","time","created_at"), rec.get("text"))
    return [m] if m else []

def _from_generic(cfg: Config, rec: dict) -> List[Message]:
    role = rec.get("role") or rec.get("type")
    author = rec.get("author")
    if role is None and isinstance(author,dict): role = author.get("role") or author.get("name")
    elif role is None: role = author
    content = first_of(rec, "content","text","parts","body","value","message")
    if role is None or content is None: return []
    stop = rec.get("stopReason") or rec.get("stop_reason")
    m = build_msg(cfg, role,
                  first_of(rec,"timestamp","create_time","ts","time","created_at"),
                  content, extract_thinking(rec), stop)
    return [m] if m else []

def extract_messages(cfg: Config, record: Any) -> List[Message]:
    if isinstance(record, list):
        out: List[Message] = []
        for item in _sort_time(record):
            out.extend(extract_messages(cfg, item))
        return out
    if not isinstance(record, dict): return []
    eff_skip = _BASE_SKIP_TYPES | cfg.skip_record_types
    if record.get("type") in eff_skip: return []
    if record.get("type") == "message" and isinstance(record.get("message"), dict):
        return _from_openclaw(cfg, record)
    for extractor in (_from_rollout, _from_history, _from_generic):
        msgs = extractor(cfg, record)
        if msgs: return msgs
    for key in COLLECTION_KEYS:
        val = record.get(key)
        if isinstance(val, dict):
            msgs = []
            for item in _sort_time(val.values()): msgs.extend(extract_messages(cfg, item))
            if msgs: return msgs
        if isinstance(val, list):
            msgs = []
            for item in _sort_time(val): msgs.extend(extract_messages(cfg, item))
            if msgs: return msgs
    return []

# ── Deduplication ─────────────────────────────────────────────────────────────
def dedupe(messages: Iterable[Message]) -> List[Message]:
    out: List[Message] = []
    last_exact: Optional[Tuple] = None
    last_content: Optional[Tuple] = None
    for m in messages:
        exact = (m.role, m.timestamp, m.text)
        content_key = (m.role, m.text)
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

    messages: List[Message] = []
    for rec in records:
        messages.extend(extract_messages(cfg, rec))
    messages = dedupe(messages)

    if not messages:
        res = ConvResult(source, None, "SKIP", "No conversational text found")
        res.note = bad_byte_note
        return res

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
    return res

# ── Conversion log ────────────────────────────────────────────────────────────
def write_log(log_path: Path, cfg: Config, out_root: Path,
              results: List[ConvResult], started: datetime) -> None:
    wrote   = sum(1 for r in results if r.status == "WROTE")
    skipped = len(results) - wrote

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
            fh.write("\n")

# ── Main entry ────────────────────────────────────────────────────────────────
def run(argv: Sequence[str], cfg: Config) -> int:
    parser = make_parser(cfg.mode)
    add_inputs(parser)
    args = parser.parse_args(argv)

    files = collect_files(args.input_paths)
    if not files:
        print(f"[{cfg.mode}] No JSON/JSONL files found.")
        return 1

    started = datetime.now().astimezone()
    out_root = choose_output_root(files, cfg.mode)
    out_root.mkdir(parents=True, exist_ok=True)

    # For relative-path labelling, use common parent if all files share one
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
        else:
            print(f"          ✗ {res.detail}")
        if res.note:
            print(f"          ⚠ {res.note}")

    log_path = out_root / "conversion_log.md"
    write_log(log_path, cfg, out_root, results, started)

    wrote_n = sum(1 for r in results if r.status == "WROTE")
    print(f"\n[{cfg.mode}] Finished  —  {wrote_n}/{len(files)} converted")
    print(f"  Folder : {out_root}")
    print(f"  Log    : {log_path}")
    return 0
