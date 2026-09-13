#!/usr/bin/env python3
"""
tests/run_tests.py  —  stdlib unittest runner for the v5.0 line.

Run: python3 tests/run_tests.py

Covers spec §5 (acceptance criteria):
  5.1  CHAT-mode output for the sample formats is byte-identical to the
       frozen v4.0 output in tests/expected/chat/, modulo the
       "Extracted:" line.
  5.2  RAW keeps tool_use/tool_result (the audit's 3-record test).
  5.3  TOOLS keeps tool_use/tool_result (same test).
  5.4  CHAT is unaffected by the same test (still 2 messages, no tool block).
  5.5  The six previously-SKIPping providers now convert. chatgpt,
       claude_desktop, devin_cli, devin_desktop_acp and cursor reach >=90%
       coverage in TOOLS mode against tests/fixtures/. antigravity is
       asserted to SKIP cleanly (see MAINTAINING.md — its real export
       format is opaque protobuf, out of reach for a stdlib JSON reader;
       no populated fixture is shipped for it, only an empty one).
  5.8  Stdlib-only — no non-stdlib import anywhere in the v5.0 files.
  5.9  UTF-8 replacement-char counting still works.

No pytest, no third-party test runner — stdlib unittest only, per the
project's zero-dependency rule.
"""

import ast
import importlib.util
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_core():
    core_path = ROOT / "_session_extractor_core.py"
    spec = importlib.util.spec_from_file_location("_session_extractor_core_test", core_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


CORE = _load_core()


def _cfg(mode, keep_item_types=frozenset(), skip_roles=frozenset(), render_tool_in_codeblock=False):
    is_boilerplate = {"CHAT": CORE.boilerplate_chat, "TOOLS": CORE.boilerplate_tools,
                       "RAW": CORE.boilerplate_raw}[mode]
    return CORE.Config(
        mode=mode, script_name=f"test_{mode.lower()}.py",
        skip_roles=set(skip_roles), skip_record_types=set(),
        is_boilerplate=is_boilerplate, render_tool_in_codeblock=render_tool_in_codeblock,
        keep_item_types=set(keep_item_types),
    )


CHAT_CFG = _cfg("CHAT", skip_roles={"developer","system","tool","function","toolresult","tool_result","function_result","info"})
TOOLS_CFG = _cfg("TOOLS", skip_roles={"info"}, keep_item_types={"tool_use","tool_result","image","audio","refusal"})
RAW_CFG = _cfg("RAW", skip_roles={"info"}, keep_item_types={"tool_use","tool_result","image","audio","refusal","error"}, render_tool_in_codeblock=True)

THREE_RECORDS = [
    {"type":"message","message":{"role":"user","content":[{"type":"text","text":"Please check /etc/hosts"}],"timestamp":"2026-09-10T10:00:00Z"}},
    {"type":"message","message":{"role":"assistant","content":[{"type":"thinking","thinking":"I should read the file."},{"type":"text","text":"Reading it now."},{"type":"tool_use","id":"t1","name":"read_file","input":{"path":"/etc/hosts"}}],"timestamp":"2026-09-10T10:00:05Z"}},
    {"type":"message","message":{"role":"assistant","content":[{"type":"tool_result","tool_use_id":"t1","content":"127.0.0.1 localhost"}],"timestamp":"2026-09-10T10:00:06Z"}},
]


class TestAuditThreeRecordCase(unittest.TestCase):
    """Spec §5.2 / §5.3 / §5.4 — the audit's own regression case."""

    def _messages(self, cfg):
        out = []
        for rec in THREE_RECORDS:
            out.extend(CORE.extract_messages(cfg, rec))
        return CORE.dedupe(out)

    def test_chat_unaffected(self):
        msgs = self._messages(CHAT_CFG)
        self.assertEqual(len(msgs), 2, "CHAT must still drop the tool-only message")
        self.assertFalse(any(bt not in ("text", "thinking") for m in msgs for bt, _, _ in m.blocks))

    def test_tools_keeps_tool_calls(self):
        msgs = self._messages(TOOLS_CFG)
        self.assertEqual(len(msgs), 3, "TOOLS must keep the tool_result-only message")
        block_types = [bt for m in msgs for bt, _, _ in m.blocks]
        self.assertIn("tool_use", block_types)
        self.assertIn("tool_result", block_types)

    def test_raw_keeps_tool_calls(self):
        msgs = self._messages(RAW_CFG)
        self.assertEqual(len(msgs), 3, "RAW must keep the tool_result-only message")
        block_types = [bt for m in msgs for bt, _, _ in m.blocks]
        self.assertIn("tool_use", block_types)
        self.assertIn("tool_result", block_types)


class TestChatRegressionGolden(unittest.TestCase):
    """Spec §5.1 — CHAT output for the sample formats must be byte-identical
    to the frozen v4.0 output in tests/expected/chat/ (mod the 'Extracted:'
    timestamp line)."""

    EXPECTED = ROOT / "tests" / "expected" / "chat"

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="v5_golden_"))
        for f in (ROOT / "samples").glob("sample_*.json*"):
            shutil.copy(f, cls.tmp / f.name)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    @staticmethod
    def _strip_ts(text):
        return re.sub(r"- Extracted: .*\n", "- Extracted: <REDACTED>\n", text)

    def test_byte_identical(self):
        import subprocess
        subprocess.run([sys.executable, str(ROOT / "markdown_extraction_chat.py"), str(self.tmp)],
                        check=True, capture_output=True)
        out = next(self.tmp.glob("extracted_CHAT_*"))
        actual = {f.name: f for f in out.iterdir() if f.name != "conversion_log.md"}
        expected = {f.name: f for f in self.EXPECTED.iterdir() if f.is_file()}
        self.assertEqual(sorted(expected), sorted(actual),
            "v5.0 must produce the same output filenames as the frozen v4.0 output")
        for fn, exp in expected.items():
            a = self._strip_ts(exp.read_text(encoding="utf-8"))
            b = self._strip_ts(actual[fn].read_text(encoding="utf-8"))
            self.assertEqual(a, b, f"CHAT output diverged from frozen v4.0 output for {fn}")


class TestNewProviderFixtures(unittest.TestCase):
    """Spec §5.5 — the previously-SKIPping providers now convert, at >=90%
    coverage in TOOLS mode (the complete-content tier)."""

    FIXTURE_ROOT = ROOT / "tests" / "fixtures"
    PROVIDERS_EXPECT_COVERAGE = ["chatgpt", "claude_desktop", "devin_cli", "devin_desktop_acp", "cursor"]

    def _convert(self, provider, fname, cfg):
        records, _ = CORE.read_records(self.FIXTURE_ROOT / provider / fname)
        self.assertTrue(records, f"{provider}/{fname} produced no records at all")
        accountable, per_path = CORE.measure_accountable(records)
        messages = []
        for rec in records:
            messages.extend(CORE.extract_messages(cfg, rec))
        messages = CORE.dedupe(messages)
        return messages, accountable

    def test_providers_reach_coverage_bar(self):
        for provider in self.PROVIDERS_EXPECT_COVERAGE:
            fdir = self.FIXTURE_ROOT / provider
            fname = next(f.name for f in fdir.iterdir() if f.name.startswith("sample"))
            with self.subTest(provider=provider):
                messages, accountable = self._convert(provider, fname, TOOLS_CFG)
                self.assertTrue(messages, f"{provider} produced zero messages — SKIP, not a low-coverage convert")
                emitted_text = CORE._emitted_text(messages)
                coverage = min(len(emitted_text) / accountable, 1.0) if accountable else 1.0
                self.assertGreaterEqual(coverage, 0.90,
                    f"{provider} TOOLS-mode coverage {coverage:.0%} is below the 90% bar")

    def test_antigravity_empty_fixture_skips_cleanly(self):
        records, _ = CORE.read_records(self.FIXTURE_ROOT / "antigravity" / "sample_empty.jsonl")
        messages = []
        for rec in records:
            messages.extend(CORE.extract_messages(TOOLS_CFG, rec))
        self.assertEqual(messages, [], "the empty antigravity fixture must produce zero messages")


class TestStdlibOnly(unittest.TestCase):
    """Spec §5.8 — no non-stdlib import anywhere in the v5.0 files."""

    ALLOWED = {"argparse","json","re","sys","dataclasses","datetime","pathlib",
               "typing","importlib","ast","unittest","shutil","tempfile","subprocess"}

    def test_no_third_party_imports(self):
        files = ["_session_extractor_core.py", "markdown_extraction_chat.py",
                 "markdown_extraction_tools.py", "markdown_extraction_raw.py"]
        for fname in files:
            tree = ast.parse((ROOT / fname).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for n in node.names:
                        mod = n.name.split(".")[0]
                        self.assertIn(mod, self.ALLOWED, f"{fname} imports non-stdlib module {mod!r}")
                elif isinstance(node, ast.ImportFrom):
                    mod = (node.module or "").split(".")[0]
                    self.assertIn(mod, self.ALLOWED, f"{fname} imports non-stdlib module {mod!r}")


class TestUtf8ReplacementCounting(unittest.TestCase):
    """Spec §5.9 — invalid UTF-8 bytes become U+FFFD and get counted, never
    silently dropped."""

    def test_bad_bytes_are_replaced_and_counted(self):
        tmp = Path(tempfile.mkdtemp(prefix="v5_utf8_"))
        try:
            p = tmp / "bad.jsonl"
            p.write_bytes(b'{"role":"user","text":"hello \xff world"}\n')
            records, replaced = CORE.read_records(p)
            self.assertEqual(len(records), 1)
            self.assertGreaterEqual(replaced, 1)
            self.assertIn("�", records[0]["text"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
