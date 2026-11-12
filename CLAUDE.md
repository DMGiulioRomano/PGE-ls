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
bash build.sh --neovim    # install the Neovim client (runs clients/neovim/setup.sh)
```

> `build.sh` copies `server.py` and `granular_ls/` into `clients/vscode/` before packaging — always edit the root copies, not the bundled ones.

## Architecture

```
VSCode extension (clients/vscode/extension.js) / Neovim (clients/neovim/pge-ls.lua)
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

### Clients (`clients/`)

| Dir | Ruolo |
|------|------|
| `clients/vscode/` | Estensione VSCode. **Bundla** una copia di `server.py` + `granular_ls/` + `schema_snapshot.json` (sync via `build.sh`), pacchettizzata in `.vsix`. |
| `clients/neovim/` | Client Neovim (macOS/Linux). **Non bundla**: punta direttamente a `server.py` nella root via il `.venv/` locale. `pge-ls.lua.template` (placeholder `%%PYTHON%%`/`%%SERVER%%`/`%%SNAPSHOT%%`) + `setup.sh` idempotente che genera `~/.config/nvim/lua/pge-ls.lua` con path assolute. Installa con `bash clients/neovim/setup.sh` o `bash build.sh --neovim`. Windows: solo setup manuale documentato nel README. |

### Key modules (`granular_ls/`)

| File | Role |
|------|------|
| `schema_bridge.py` | Facade over the parameter schema. Exposes `ParameterInfo` value objects with bounds, variation modes, exclusive groups. Loads from a live Python import (`--src`) or a bundled JSON snapshot (`--snapshot`). Owns the one reading of a parameter's domain: the two bounds are read independently — `max_val=None` with a `min_val` means **no ceiling** (`density` since PGE #272), not "unknown bounds" — via `domain_label` / `outside_domain` / `get_value_domain`, which every generic reader (hover, completion detail, diagnostics bounds) goes through. `get_value_domain` is None for the `loop_unit`-scaled positions, whose registry bounds apply after rescaling. `get_raw_value_domain` answers the same question by **name** for the two parameters that have bounds in `GRANULAR_PARAMETERS` and no `ParameterSpec` — `num_voices` and `scatter`, which the engine (`VoicesController` since PGE #284) hands to `parse_parameter` like any other: without it they had no domain to read, so they were checked only as scalars and an envelope breakpoint out of bounds went unreported until the render failed (#59). |
| `yaml_analyzer.py` | Tolerant partial YAML parser — never throws on incomplete input. Returns a `YamlContext` describing cursor position (context type, parent path, indent level, whether inside a stream element). |
| `providers/completion_provider.py` | Filters parameters by context + parent path; builds `CompletionItem` LSP objects; handles exclusive groups and duplicate-key prevention. |
| `providers/hover_provider.py` | Resolves parameter name from cursor, returns Markdown hover with range + variation mode + exclusive group info. |
| `providers/diagnostic_provider.py` | Validates scalar bounds, envelope formats (shape: phase 20 via `envelope_guards`), exclusive group violations, required fields, and duplicate keys. The `pitch:` block has its own strict validation (`_check_pitch_block`) driven by `pitch_units.py`, not by the bridge. |
| `pitch_units.py` | Static registry of the unit-driven pitch surface (mirrors PGE's `PitchController`/`PitchUnit`): unit keys with per-unit bounds (`semitones`, `cents`, `quarter_tone`, `eighth_tone`, `edo`+`value`, `ratio`), `range` modifier, `voices.pitch.unit` values, semitone-locked strategies, hover docs. Since PGE's `PITCH_PARAMETER_SCHEMA` is empty, pitch never comes from the bridge. |
| `loop_unit.py` | Registry of `pointer.loop_unit` (mirror of PGE `PointerController`'s `LOOP_UNITS` / `_LOOP_UNIT_SCOPE`): closed vocabulary `seconds`/`absolute`/`normalized`, default `seconds` **independent of `time_mode`** (PGE #222); `find_loop_unit`, the one reader of the declaration, used by hover, diagnostics and semantic tokens; and `stream_sample`, the one reader of the `sample` whose duration caps the positions in seconds (hover note and diagnostic bounds). Also holds the mirror of `_rescaling_would_change` for the migration warning (ponytail, PGE #242). |
| `range_unit.py` | The `<param>_range_unit` keys (PGE #267, today only `grain.duration_range_unit`): whether a `_range` is an absolute amount or a fraction of the base. Which key governs which `_range` comes from `ParameterSpec.range_unit_path` via `SchemaBridge.get_range_unit_bindings()`, and the vocabulary from the engine's `RANGE_UNITS` (`get_range_units()`); this module only holds what is not a list: the relative spelling (mirror of `range_unit_is_relative`), value docs, the relative band per `range_anchor` (`RELATIVE_ANCHOR_BANDS`, shared by the `_range` hover note and the `range_anchor` docs), and the YAML reader of the declaration. |
| `stream_files.py` | Mirror of the `- file: <path>` rules for the `streams:` list (PGE #290, `pge/engine/stream_files.py`): `PLACEMENT_KEYS` (mirror of `CHIAVI_DI_PIAZZAMENTO`, the only keys allowed beside `file:`), `default_stream_id` (file name without extension), `resolve_import_path` (relative to the master's folder), `import_problem` (missing/unreadable/malformed file, not exactly one stream, a chained `file:` — the shapes of `_stream_unico`), `value_span` (where the path sits on the line), plus the docs hover and completion share. Diagnostics (phase 21, `_check_file_entries`) need the master's path: `get_diagnostics(text, document_path)`, which the server derives from the URI. The effective id is compared as written, without the engine's math eval. Activation stays on `PGE_*` names. |
| `voice_strategies.py` | Static registry of voice strategies per dimension (pitch/onset_offset/pointer/pan) with kwargs metadata, chord intervals (mirrors PGE's `CHORD_INTERVALS`), and hover docs. |
| `envelope_shapes.py` | Structural recognition of the envelope forms, with the same discriminating rules as PGE's `EnvelopeBuilder` (`is_bp_group`, `is_loop_block`, `is_3tuple_breakpoint`, ...), plus the conversion the `Generator` applies before the parser (`normalize_engine_values`, math expressions) and `is_envelope_like` (mirror of `Envelope.is_envelope_like`, shared by `loop_unit` and the voice kwargs). `envelope_y_paths` is the one reader of an envelope's Ys — every spelling the builder builds (dict `{type, points}`, dict breakpoint `{t, v}`, per-point interp, BP group, compact cycle, mixed), each Y with the path to its YAML node: the generic envelope bounds (diagnostics phase 5, read on the YAML tree since #58) and the band/unit checks all go through it. Only the `pitch:` block still reads Ys off the text (`_extract_envelope_y_values`). |
| `envelope_guards.py` | The single mirror of an envelope's **shape guards** (PGE #211, #288), in the order the engine walks them and stopping at the first, like it: `Envelope.__init__` (dict `points`/`type` — the type read by the factory, so `Step` and `' cubic '` render —, the compact interp slot that becomes the envelope type, the empty body), `EnvelopeBuilder.parse` (compact: `n_reps`, `end_time` finite and past the instant the block starts, pattern points flat with x in [0, 100] non-decreasing, time distribution incl. the overflowing pair; BP group interp and arity; unknown element) and `_parse_segments` (per-point type, literal). `check_envelope_shape` returns a `ShapeIssue` whose `where` is the engine's fallback sub-position (`compact.n_reps`, `group.points`, ...), which the parity compares; `check_voice_kwarg` adds the voice-strategy kwarg that is not an envelope. The start instant of a compact after another compact is tracked as an interval (exact only for `linear` in absolute time): where it cannot decide, it is silent on the whole body. Diagnostics phase 20 applies it to every key of `_envelope_value_nodes`; `read_direction.py` and `deviation_probability.py` run it after their own semantics. |
| `envelope_snippets.py` | Generates 15 envelope template variants (incl. the BP group `[points, interp]` forms). Y bounds are derived from the matched parameter; `end_time` is calculated from stream duration or defaults to 1.0. `draw_bounds` is the single Y window for snippets and the envelope GUI (`server._resolve_envelope_context`); a parameter with no ceiling draws up to a per-parameter ceiling declared with its reason in `OPEN_CEILING_DRAW_MAX` (density: the engine's `DENSITY_NOTICE_THRESHOLD`), and parity requires an entry for each. For the `loop_unit`-scaled positions (`SchemaBridge.unit_scaled_paths`) the window stays, but the snippet doc says the range depends on `loop_unit` instead of declaring the registry bounds as the YAML number's domain. |

### Schema loading modes

`server.py` accepts two mutually exclusive startup flags:
- `--src <path>` — imports schema live from the granular project's `src/` directory (development mode)
- `--snapshot <path>` — loads a pre-generated `schema_snapshot.json` (distribution mode, used by the packaged `.vsix`)

### Sync requirement

`clients/vscode/` contains a **bundled copy** of `server.py` and `granular_ls/`. `build.sh` handles the sync. Do not edit files under `clients/vscode/granular_ls/` or `clients/vscode/server.py` directly.

## Documentation

Internal design docs live under `docs/`. When you add a new file there, add a corresponding entry in this table.


- [envelope_gui_total_preview.md](docs/envelope_gui_total_preview.md) = Come viene gestito il tempo nell'anteprima totale dell'EnvelopeEditor (asse X assoluto, boundary drag, decorazioni).

## Git Workflow

**No direct commits to `main`.** Every change must go through a branch and pull request:

1. Create a feature branch: `git checkout -b feat/my-feature`
2. Make changes and commit on the branch
3. Push and open a PR: `gh pr create`
4. Merge via PR only — never `git push` directly to `main`

This applies to all changes including documentation, tests, and version bumps.
