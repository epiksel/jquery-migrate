# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Purpose

A single-file Python utility (`main.py`) that scans `.js`, `.html`, and `.twig` files to detect and automatically transform deprecated jQuery APIs for jQuery v4 compatibility. No external dependencies.

## Commands

```bash
# Detect only — lists deprecated patterns with line numbers, no writes
python main.py --scan

# Preview changes without writing to disk
python main.py --dry-run

# Apply all transformations
python main.py
```

## Architecture

**`main.py`** is the entire application. Its sections, top to bottom:

### Configuration (top of file)
- `BASE_DIR` — root directory to scan (edit this to target a different project)
- `THIRD_PARTY` — list of path substrings to skip (vendor libraries: bootstrap, jQuery itself, jscolor, etc.)
- `EXTENSIONS` — file types to process (`.twig`, `.js`, `.html`)

### Parsing Utilities
Low-level helpers used by transformation functions:
- `parse_args(s)` — splits function arguments respecting nested parens/brackets
- `find_close_paren` / `find_close_any` — bracket-matching that skips over string literals, line comments, block comments, and Twig template syntax (`{% %}`, `{{ }}`)
- `find_prop_at_depth0` — finds object properties at nesting level 0
- `extract_fn` — extracts a function declaration with its args and body

### Transformation Functions
15 functions, each handling one deprecated jQuery pattern:

| Pattern | Replacement |
|---|---|
| `$.inArray(val, arr)` | `arr.indexOf(val)` |
| `$.isFunction(x)` | `typeof x === 'function'` |
| `$.trim(x)` | `x.trim()` |
| `$.now()` | `Date.now()` |
| `$.type(x)` | `typeof x` |
| `$.expr[":"]` | `$.expr.pseudos` |
| `.size()` | `.length` |
| `.bind()` / `.unbind()` | `.on()` / `.off()` |
| `.delegate(sel, event, fn)` | `.on(event, sel, fn)` |
| `.undelegate(sel, event)` | `.off(event, sel)` |
| `$.proxy(fn, ctx)` | `fn.bind(ctx)` |
| `e.which` | `e.key` or `e.button` (via KEY_MAP / BUTTON_MAP) |
| `$(sel).load(url)` | `$.get(url, fn)` |
| `$.ajax({ success/error/complete })` | Promise chains (`.then()` / `.catch()` / `.always()`) |
| `$.get(url, callback)` | `$.get(url).then(callback)` |

The `TRANSFORMS` list at the bottom of the file defines the execution order; transformations are applied sequentially to each file's content.

For ambiguous cases that can't be auto-resolved, functions insert `// TODO:` comments to flag them for manual review.

### File Collection & Processing
- `collect_files()` — walks `BASE_DIR`, filters by extension, skips hidden dirs and `THIRD_PARTY` paths
- `scan_file(path)` — matches 16 regex scan patterns, reports line number + preview (used in `--scan` mode)
- `fix_file(path)` — reads file (UTF-8 with error fallback), applies all transforms, writes only if content changed (skips write in `--dry-run`)

### Adding a New Transformation
1. Write a `transform_<name>(content: str) -> str` function using the parsing utilities for bracket-aware matching
2. Add the function to the `TRANSFORMS` list
3. Add a corresponding regex to `SCAN_PATTERNS` for `--scan` mode detection
