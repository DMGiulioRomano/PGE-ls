# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Project Is

PGE-ls is a **Language Server Protocol (LSP) implementation** that provides intelligent YAML editing assistance for the **Python Granular Engine** (a granular synthesis framework). It activates on files matching `PGE_*.yaml` and provides autocompletion, hover docs, diagnostics, envelope snippets, and go-to-file navigation.

## Commands

### Setup
```bash
bash setup.sh   # installs Python deps (pygls==1.3.1, lsprotocol, pytest), Node deps, creates .vscode/launch.json
```

### Tests
```bash
python -m pytest tests/ -q                          # run all tests
python -m pytest tests/test_schema_bridge.py        # single module
python -m pytest tests/test_yaml_analyzer.py -k "test_name"  # single test
```

### Build & Package
```bash
bash build.sh             # syncs server.py + granular_ls/ into clients/vscode/, packages .vsix
bash build.sh --install   # build and install directly into VSCode
bash build.sh --all       # build both VSCode and Pulsar clients
```

> `build.sh` copies `server.py` and `granular_ls/` into `clients/vscode/` before packaging — always edit the root copies, not the bundled ones.

## Architecture

```
VSCode extension (clients/vscode/extension.js)
    ↓  stdio
server.py  (pygls LanguageServer, handles LSP lifecycle)
    ↓
SchemaBridge  (loads parameter schema once at startup)
    ↓
Three stateless providers (each request receives full document text):
  CompletionProvider → YamlAnalyzer → CompletionItem[]
  HoverProvider      → YamlAnalyzer → Hover
  DiagnosticProvider → YamlAnalyzer → Diagnostic[]
```

### Key modules (`granular_ls/`)

| File | Role |
|------|------|
| `schema_bridge.py` | Facade over the parameter schema. Exposes `ParameterInfo` value objects with bounds, variation modes, exclusive groups. Loads from a live Python import (`--src`) or a bundled JSON snapshot (`--snapshot`). |
| `yaml_analyzer.py` | Tolerant partial YAML parser — never throws on incomplete input. Returns a `YamlContext` describing cursor position (context type, parent path, indent level, whether inside a stream element). |
| `providers/completion_provider.py` | Filters parameters by context + parent path; builds `CompletionItem` LSP objects; handles exclusive groups and duplicate-key prevention. |
| `providers/hover_provider.py` | Resolves parameter name from cursor, returns Markdown hover with range + variation mode + exclusive group info. |
| `providers/diagnostic_provider.py` | Validates scalar bounds, envelope formats, exclusive group violations, required fields, and duplicate keys. |
| `envelope_snippets.py` | Generates 11 envelope template variants. Y bounds are derived from the matched parameter; `end_time` is calculated from stream duration or defaults to 1.0. |

### Schema loading modes

`server.py` accepts two mutually exclusive startup flags:
- `--src <path>` — imports schema live from the granular project's `src/` directory (development mode)
- `--snapshot <path>` — loads a pre-generated `schema_snapshot.json` (distribution mode, used by the packaged `.vsix`)

### Sync requirement

`clients/vscode/` contains a **bundled copy** of `server.py` and `granular_ls/`. `build.sh` handles the sync. Do not edit files under `clients/vscode/granular_ls/` or `clients/vscode/server.py` directly.
