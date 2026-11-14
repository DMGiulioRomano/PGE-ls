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
PGE_SRC=../PythonGranularEngine/src PGE_REQUIRE_PARITY=1 python -m pytest tests/ -q   # parità pretesa, come in CI
```

**La parità con PGE non tace dove dovrebbe fallire** (#70). `tests/pge_engine.py` è l'unico posto che risolve il motore (`PGE_SRC` o sibling) e decide che cosa vuol dire uno skip:

- **Motore assente**: la parità salta, ma con `PGE_REQUIRE_PARITY=1` (letto `== "1"`) `test_pge_engine.py` fallisce. La CI lo passa quando lo step di checkout del motore ha `outcome == 'success'`, e quando lo step è saltato (secret `ENGINE_REPO_TOKEN` assente) lo dice con un `::warning::`. La run chiude sempre con una riga «parità con PGE: motore …» (terminal summary, sopravvive a `-q`).
- **Al motore manca X**: i patti chiamano `engine_lacks(reason)`, non `pytest.skip`. `ENGINE_BASELINE` è il commit del motore contro cui i patti sono scritti: se è antenato del motore sotto test, `engine_lacks` **fallisce** (X è stato spostato o riscritto, non ancora da scrivere); altrimenti salta. In CI il commit registrato deve essere antenato del clone (`fetch-depth: 300`), senza scappatoie per i clone corti. Aggiorna `ENGINE_BASELINE` quando un patto nuovo legge qualcosa che il motore ha aggiunto dopo. Gli unici `pytest.skip` ammessi nei patti sono quelli che non dipendono dall'età del motore (numpy assente, un input che il motore rifiuta): `test_pge_engine.py` li conta.
- **I ripieghi del bridge** (`from_python_path`) scrivono una riga su stderr e finiscono in `SchemaBridge.read_fallbacks`, che la parità pretende vuoto contro il motore vero.

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
| `schema_bridge.py` | Facade over the parameter schema. Exposes `ParameterInfo` value objects with bounds, variation modes, exclusive groups. Loads from a live Python import (`--src`) or a bundled JSON snapshot (`--snapshot`). Every live read that falls back (static copy, or `DistributionFactory._registry` on engines without `modes()`) prints a line on stderr and lands in `read_fallbacks` (#70). Owns the one reading of a parameter's domain: the two bounds are read independently — `max_val=None` with a `min_val` means **no ceiling** (`density` since PGE #272), not "unknown bounds" — via `domain_label` / `outside_domain` / `get_value_domain`, which every generic reader (hover, completion detail, diagnostics bounds) goes through. `get_value_domain` is None for the `loop_unit`-scaled positions, whose registry bounds apply after rescaling, and for the parameters whose bounds **wrap** (`WRAPPING_PARAMETERS`, read live or from the snapshot's `wrapping_parameters`: `pointer_position`, PGE #312), where a finite value outside is valid. `get_raw_value_domain` answers the same question by **name** for the two parameters that have bounds in `GRANULAR_PARAMETERS` and no `ParameterSpec` — `num_voices` and `scatter`, which the engine (`VoicesController` since PGE #284) hands to `parse_parameter` like any other: without it they had no domain to read, so they were checked only as scalars and an envelope breakpoint out of bounds went unreported until the render failed (#59). |
| `yaml_analyzer.py` | Tolerant partial YAML parser — never throws on incomplete input. Returns a `YamlContext` describing cursor position (context type, parent path, indent level, whether inside a stream element). |
| `providers/completion_provider.py` | Filters parameters by context + parent path; builds `CompletionItem` LSP objects; handles exclusive groups and duplicate-key prevention. |
| `providers/hover_provider.py` | Resolves parameter name from cursor, returns Markdown hover with range + variation mode + exclusive group info. |
| `providers/diagnostic_provider.py` | Validates scalar bounds, envelope formats (shape: phase 20 via `envelope_guards`), exclusive group violations, required fields, and duplicate keys. The `pitch:` block has its own strict validation (`_check_pitch_block`) driven by `pitch_units.py`, not by the bridge. |
| `pitch_units.py` | Static registry of the unit-driven pitch surface (mirrors PGE's `PitchController`/`PitchUnit`): unit keys with per-unit bounds (`semitones`, `cents`, `quarter_tone`, `eighth_tone`, `edo`+`value`, `ratio`), `range` modifier, `voices.pitch.unit` values, semitone-locked strategies, hover docs. Since PGE's `PITCH_PARAMETER_SCHEMA` is empty, pitch never comes from the bridge. |
| `loop_unit.py` | Registry of `pointer.loop_unit` (mirror of PGE `PointerController`'s `LOOP_UNITS` / `_LOOP_UNIT_SCOPE`): closed vocabulary `seconds`/`absolute`/`normalized`, default `seconds` **independent of `time_mode`** (PGE #222); `find_loop_unit`, the one reader of the declaration, used by hover, diagnostics and semantic tokens; and `stream_sample`, the one reader of the `sample` whose duration caps the positions in seconds (hover note and diagnostic bounds). |
| `range_unit.py` | The `<param>_range_unit` keys (PGE #267, today only `grain.duration_range_unit`): whether a `_range` is an absolute amount or a fraction of the base. Which key governs which `_range` comes from `ParameterSpec.range_unit_path` via `SchemaBridge.get_range_unit_bindings()`, and the vocabulary from the engine's `RANGE_UNITS` (`get_range_units()`); this module only holds what is not a list: the relative spelling (mirror of `range_unit_is_relative`), value docs, the relative band per `range_anchor` (`RELATIVE_ANCHOR_BANDS`, shared by the `_range` hover note and the `range_anchor` docs), and the YAML reader of the declaration. |
| `stream_files.py` | Mirror of the `- file: <path>` rules for the `streams:` list (PGE #290, `pge/engine/stream_files.py`): `PLACEMENT_KEYS` (mirror of `CHIAVI_DI_PIAZZAMENTO`, the only keys allowed beside `file:`), `default_stream_id` (file name without extension), `resolve_import_path` (relative to the master's folder), `import_problem` (missing/unreadable/malformed file, not exactly one stream, a chained `file:` — the shapes of `_stream_unico`), `value_span` (where the path sits on the line), plus the docs hover and completion share. Diagnostics (phase 21, `_check_file_entries`) need the master's path: `get_diagnostics(text, document_path)`, which the server derives from the URI. The effective id is compared as written, without the engine's math eval. Activation stays on `PGE_*` names. |
| `voice_strategies.py` | Static registry of voice strategies per dimension (pitch/onset_offset/pointer/pan) with kwargs metadata, chord intervals (mirrors PGE's `CHORD_INTERVALS`), and hover docs. |
| `envelope_shapes.py` | Structural recognition of the envelope forms, with the same discriminating rules as PGE's `EnvelopeBuilder` (`is_bp_group`, `is_loop_block`, `is_3tuple_breakpoint`, ...), plus the conversion the `Generator` applies before the parser (`normalize_engine_values`, math expressions) and `is_envelope_like` (mirror of `Envelope.is_envelope_like`, read by the voice-kwarg shape guard in `envelope_guards`). `envelope_y_paths` is the one reader of an envelope's Ys — every spelling the builder builds (dict `{type, points}`, dict breakpoint `{t, v}`, per-point interp, BP group, compact cycle, mixed), each Y with the path to its YAML node: the generic envelope bounds (diagnostics phase 5, read on the YAML tree since #58) and the band/unit checks all go through it. Only the `pitch:` block still reads Ys off the text (`_extract_envelope_y_values`). |
| `envelope_guards.py` | The single mirror of an envelope's **shape guards** (PGE #211, #288), in the order the engine walks them and stopping at the first, like it: `Envelope.__init__` (dict `points`/`type` — the type read by the factory, so `Step` and `' cubic '` render —, the compact interp slot that becomes the envelope type, the empty body), `EnvelopeBuilder.parse` (compact: `n_reps`, `end_time` finite and past the instant the block starts, pattern points flat with x in [0, 100] non-decreasing, time distribution incl. the overflowing pair; BP group interp and arity; unknown element) and `_parse_segments` (per-point type, literal). `check_envelope_shape` returns a `ShapeIssue` whose `where` is the engine's fallback sub-position (`compact.n_reps`, `group.points`, ...), which the parity compares; `check_voice_kwarg` adds the voice-strategy kwarg that is not an envelope. The start instant of a compact after another compact is tracked as an interval (exact only for `linear` in absolute time): where it cannot decide, it is silent on the whole body. Diagnostics phase 20 applies it to every key of `_envelope_value_nodes`; `read_direction.py` and `deviation_probability.py` run it after their own semantics. |
| `engine_expressions.py` | Mirror of the engine's expression language (PGE #308, #313): `pge/expressions/expression.py` (grammar, `FUNCTIONS` with their arity, `CONSTANTS`, `MAX_DEPTH`, `NAME_PATTERN`, `outer_parenthesized`), `pge/expressions/fields.py` (`EXPRESSION_FIELDS`, `EXPRESSION_REFUSALS`) and the shelter's `LOAD_TIME_NAMES`. `compile_expression` says whether a text compiles on given names and which it reads, without building closures. `load_time_value` mirrors `Generator._eval_math_expressions` on a string **without `eval`** (a walk of the Python AST over the operations that evaluation can meet; anything else is "leave the text", like the engine on a failure), and `half_evaluated_proposal` the #309 rule: a string with parentheses, not all inside an outer pair, that as written is not a number and wrapped whole is. Parity in `tests/test_maps_parity.py`, against the real method extracted from the AST. |
| `maps.py` | The chaotic maps of a stream (PGE #307): the catalog (`MAP_TYPES`, `MAP_KINDS`, `MAP_KEYS`, the caps of `iterate`/`transient`/`clock`, reserved names), the `MapConfigError` rules of `map_set._definizione` (on the keys as `parse_maps` sees them: after the load-time evaluation, so `r: (7/2)` is 3.5 — only the `custom` equations `x:`/`y:` are sheltered), where an expression is valid/refused/missing its parentheses (every string of `grain.duration`/`grain.iot` is an expression, `UNIT_SCALED_EXPRESSION_FIELDS`; elsewhere only one written as such), the presence-based exclusivity of `grain.iot` (`IOT_EXCLUSIVE_KEYS`) and `pointer.position` (`POSITION_EXCLUSIVE_KEYS`), a non-finite position, and the #309 rule on stream values (minus `STREAM_TEXT_KEYS`) and on `seed`. `check_stream` returns `MapIssue`s with the YAML path; diagnostics phase 22 anchors them on the composed stream. Also the docs hover and completion share. |
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
