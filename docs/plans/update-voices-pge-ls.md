# Piano: aggiornamento voices block PGE-ls

Allinea il language server alle modifiche recenti in PythonGranularEngine:
- PR #24 `feat/spectral-pitch-strategy`
- PR #28 `feature/dynamic-strategy-params`
- `ChordPitchStrategy` con `inversion` e accordi estesi

## Gap identificati

### Gap 1 — `spectral` pitch strategy mancante nel LS

PGE `src/strategies/voice_pitch_strategy.py` ha `SpectralPitchStrategy(max_partial: int = 16)`.
Il LS non la conosce:
- `VOICE_STRATEGY_REGISTRY['pitch']` contiene solo `step, range, chord, stochastic`
- `_VOICE_TOP_LEVEL_DOCS['pitch']` non la cita
- Cascade: completion, hover, diagnostic non la suggeriscono

Formula engine: `offset(i) = round(12 × log2(i+1))` semitoni → serie armonica naturale
`max_partial` è opzionale (default 16), tipo `int`.

### Gap 2 — `inversion` kwarg mancante su `chord`

`ChordPitchStrategy.__init__(chord: str, inversion: int = 0)` in PGE.
Il LS `chord` spec ha solo il kwarg `chord`, manca `inversion`.
- Opzionale, tipo `int`, `min_val=0`
- Semantica: ruota gli intervalli dell'accordo di `k` gradi

### Gap 3 — Enum chord incompleto (11 accordi mancanti su 22)

| Categoria | PGE | LS | Mancanti nel LS |
|---|---|---|---|
| 3 voci | maj, min, dim, aug, sus2, sus4 | maj, min, dim, aug, sus2, sus4 | — |
| 4 voci | dom7, maj7, min7, dim7, minmaj7 | dom7, maj7, min7, dim7, minmaj7 | — |
| 5 voci | dom9, maj9, min9, 9sus4 | — | dom9, maj9, min9, 9sus4 |
| 6 voci | dom9s11, maj9s11, min11 | — | dom9s11, maj9s11, min11 |
| 7 voci | dom13, min13, maj13s11, altered | — | dom13, min13, maj13s11, altered |

### Gap 4 — Dynamic strategy kwargs (Envelope) — non blocca

`_parse_strategy_kwarg` in PGE accetta Envelope (`[[t,v],...]`) per kwarg float
come `step`, `base`, `pointer_range`, ecc.
Il diagnostic LS già salta valori non-scalari (controllo bounds assente per kwarg).
Nessun falso positivo. Non urgente — rinviato.

## Piano di implementazione

**File modificato:** `granular_ls/voice_strategies.py`

Completion, hover e diagnostic leggono il registry dinamicamente → cascade automatico.

### U1 — Aggiunge strategy `spectral`

In `VOICE_STRATEGY_REGISTRY['pitch']` aggiunge:

```python
'spectral': VoiceStrategySpec(
    name='spectral',
    description=(
        "Distribuisce le voci sui parziali della serie armonica naturale.\n\n"
        "Voce i → round(12 × log2(i+1)) semitoni.\n\n"
        "Serie: [0, 12, 19, 24, 28, 31, 34, 36, ...] per le prime 8 voci.\n\n"
        "**Esempio:** `max_partial: 8` con 4 voci → offset [0, 12, 19, 24] semitoni."
    ),
    kwargs={
        'max_partial': VoiceKwargSpec(
            name='max_partial',
            type='int',
            required=False,
            min_val=1.0,
            description=(
                "Numero di parziali pre-calcolati all'init (default: 16).\n\n"
                "Voci oltre questo limite vengono calcolate on-demand.\n\n"
                "Deve essere ≥ 1."
            ),
        ),
    },
),
```

### U2 — Aggiunge `inversion` ai kwargs di `chord`

Nel `VoiceStrategySpec` di `chord`, aggiunge a `kwargs`:

```python
'inversion': VoiceKwargSpec(
    name='inversion',
    type='int',
    required=False,
    min_val=0.0,
    description=(
        "Rivolto dell'accordo: il grado k diventa la voce più bassa.\n\n"
        "- `inversion: 0` → posizione fondamentale (default)\n"
        "- `inversion: 1` → primo rivolto (terza al basso)\n"
        "- `inversion: 2` → secondo rivolto (quinta al basso)\n\n"
        "Deve essere in [0, len(chord_intervals) - 1]."
    ),
),
```

### U3 — Espande chord enum_values e tabella hover

`enum_values` della chord kwarg diventa (22 accordi):

```python
enum_values=(
    'maj', 'min', 'dim', 'aug', 'sus2', 'sus4',
    'dom7', 'maj7', 'min7', 'dim7', 'minmaj7',
    'dom9', 'maj9', 'min9', '9sus4',
    'dom9s11', 'maj9s11', 'min11',
    'dom13', 'min13', 'maj13s11', 'altered',
),
```

Aggiorna la tabella nella `description` per includere i nuovi accordi.

### U4 — Aggiorna `_VOICE_TOP_LEVEL_DOCS['pitch']`

Cambia la riga "Strategy disponibili" da:
```
Strategy disponibili: `step`, `range`, `chord`, `stochastic`
```
a:
```
Strategy disponibili: `step`, `range`, `chord`, `stochastic`, `spectral`
```

Aggiorna il blocco YAML di esempio per mostrare spectral.

### U5 — Build e install

```bash
bash build.sh --install
```

## File interessati

| File | Modifica |
|---|---|
| `granular_ls/voice_strategies.py` | U1–U4 |
| `clients/vscode/granular_ls/voice_strategies.py` | sincronizzato da build.sh |

## Verifica

Dopo build, aprire un file `PGE_*.yaml` in VSCode e verificare:
1. `pitch:\n  strategy: ` → suggerisce `spectral` nell'autocomplete
2. `strategy: spectral` → hover mostra formula serie armonica
3. `strategy: chord\n  chord: ` → suggerisce tutti 22 accordi
4. `strategy: chord\n  chord: dom7\n  inv` → suggerisce `inversion`
5. Diagnostica: nessun errore falso su `chord: dom9` o `chord: altered`
