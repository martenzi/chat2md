# Changelog

## [4.0.0] - 2026-07-26 - Windsurf-Legacy Fix Edition

### Added
- New `_session_extractor_core_v4.0.py` plus `markdown_extraction_*_v4.0.py` wrappers.
- The v4.0 wrappers load the fixed core via `importlib.util` so they cannot accidentally import an older unfixed `_session_extractor_core.py` from the same directory.

### Fixed
- **CHAT mode now strips Windsurf-legacy `<SYSTEM-RETRIEVED-MEMORY[...]>` blocks.** These memory-injection tags sit inside ordinary `role="assistant"` text in Windsurf-legacy exports, so the previous role-based skip lists missed them. CHAT and RAW output for these sources was byte-identical before the fix; now CHAT drops the dedicated memory-dump turns while Tools and Raw keep them by design.
- **Invalid UTF-8 no longer silently disappears.** `read_records()` switched from `errors="ignore"` to `errors="replace"`. The number of replacement characters is counted and surfaced as a warning in `conversion_log.md` and on the console. Well-formed exports are byte-for-byte identical to before.

### Explicitly not changed
- No broad "un-double-escape" or JSON-fragment-repair pass was added for a single leaked tool-call fragment found in one message out of 800 inspected. Such a heuristic would risk mangling legitimate escaped code/diff content in other formats.
- The literal source text `"KRun..."` was not corrected; it is source content, not a rendering artifact.

## [3.5.0] - 2026-04-01 - Miss Peacock Audit Edition

### Added
- **Mode prefix on output filenames:** every `.md` file is prefixed with `[Chat]`, `[Tools]`, or `[Raw]` for instant identification in Finder.
- **User message delimiter:** a `---` horizontal rule is inserted above every user message, making prompts easy to scan in long sessions.

### Changed
- `conversion_log.txt` became `conversion_log.md` — a proper Markdown file that renders cleanly in Quick Look, Obsidian, Typora, etc. Content is unchanged; only the formatting moved from terminal box-drawing characters to Markdown headers and lists.

### Fixed
- **Automator multi-file folder explosion:** `.app` workflows were looping and spawning one Python process per dropped file, producing N output folders. Now all files are passed to a single Python invocation, yielding exactly one folder and one log per run as the core was designed for.

## [3.4.1] - 2026-03-31 - Architectural Refactor

### Added
- **Shared extraction engine:** logic moved into `_session_extractor_core.py` to eliminate duplication across the three mode wrappers.
- **Improved filename derivation:** titles are inferred from the first meaningful line of each session.

### Fixed
- Module-import errors between the wrapper scripts.
- Absolute/relative path handling in the manifest report.

## [3.4.0] - 2026-03-30 - Hardened Batch Edition

### Added
- **Multi-file CLI support:** scripts accept any number of files/folders (`nargs='+'`).
- **Manifest logging:** `conversion_log.txt` reports every source path, output filename, and skip reason.
- **Mode-prefixed output folders:** `extracted_CHAT_YYYYMMDD_HHMMSS/`, `extracted_TOOLS_...`, `extracted_RAW_...`.
- **Automator batch logic:** `.app` bundles pass all dropped files as one batch.

### Fixed
- Subfolder collisions and log overwrites when multiple files were dropped at once.
- Logs that only listed the last file instead of the entire batch.

## [3.3.0] - 2026-03-29 - Hardened Merge Edition

### Added
- **Surgical cleaning:** regex filters strip Gemini CLI `@file` injections and OpenAI/Claude metadata noise.
- **Schema-awareness:** better detection of OpenAI/Claude-style `"message"` wrappers so tool records do not bleed into chat output.
