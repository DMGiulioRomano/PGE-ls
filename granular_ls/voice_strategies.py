# granular_ls/voice_strategies.py
"""
Registry delle voice strategies per l'intelligenza LSP.

Le voci in PGE usano un dispatch strategy-based (VoicePitchStrategyFactory.create(name, **kwargs))
che e' strutturalmente diverso dal sistema ParameterSpec.
Questo modulo fornisce metadati statici per:
  - Autocompletamento chiavi e valori nei blocchi voices.*
  - Documentazione hover per strategy, kwargs e dimensioni
  - Validazione diagnostica (strategy name valido, kwargs richiesti presenti, valori enum validi)

Struttura YAML supportata:
  voices:
    num_voices: 4          # int > 0, richiesto
    pitch:                 # opzionale
      strategy: chord      # nome strategy
      chord: dom7          # kwarg specifico della strategy
    onset_offset:          # opzionale
      strategy: linear
      step: 0.05
    pointer:               # opzionale
      strategy: stochastic
      pointer_range: 0.1
    pan:                   # opzionale
      strategy: linear
      spread: 90.0
"""

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from granular_ls.pitch_units import VOICE_UNIT_DOC


@dataclass(frozen=True)
class VoiceKwargSpec:
    """Specifica di un singolo kwarg di una voice strategy."""
    name: str
    type: str               # 'float', 'int', 'enum', 'bool', 'pitch_unit'
    required: bool
    description: str
    enum_values: Optional[Tuple[str, ...]] = None   # solo per type == 'enum'
    min_val: Optional[float] = None
    max_val: Optional[float] = None


@dataclass(frozen=True)
class VoiceStrategySpec:
    """Specifica di una voice strategy: nome, descrizione e kwargs."""
    name: str
    description: str
    kwargs: Dict[str, VoiceKwargSpec]


# ---------------------------------------------------------------------------
# Accordi disponibili per la strategy pitch 'chord'.
# Speculare a CHORD_INTERVALS di src/strategies/voice_pitch_strategy.py:
# intervalli in semitoni dalla fondamentale. Usato per enum, docs e per
# validare il range di `inversion` ([0, n_note-1]).
# ---------------------------------------------------------------------------

CHORD_INTERVALS: Dict[str, Tuple[int, ...]] = {
    # --- 3 voci ---
    'maj':      (0, 4, 7),
    'min':      (0, 3, 7),
    'dim':      (0, 3, 6),
    'aug':      (0, 4, 8),
    'sus2':     (0, 2, 7),
    'sus4':     (0, 5, 7),
    # --- 4 voci ---
    'dom7':     (0, 4, 7, 10),
    'maj7':     (0, 4, 7, 11),
    'min7':     (0, 3, 7, 10),
    'dim7':     (0, 3, 6, 9),
    'minmaj7':  (0, 3, 7, 11),
    # --- 5 voci ---
    'dom9':     (0, 4, 7, 10, 14),
    'maj9':     (0, 4, 7, 11, 14),
    'min9':     (0, 3, 7, 10, 14),
    '9sus4':    (0, 5, 7, 10, 14),
    # --- 6 voci ---
    'dom9s11':  (0, 4, 7, 10, 14, 18),
    'maj9s11':  (0, 4, 7, 11, 14, 18),
    'min11':    (0, 3, 7, 10, 14, 17),
    # --- 7 voci ---
    'dom13':    (0, 4, 7, 10, 14, 17, 21),
    'min13':    (0, 3, 7, 10, 14, 17, 21),
    'maj13s11': (0, 4, 7, 11, 14, 18, 21),
    'altered':  (0, 4, 7, 10, 13, 15, 20),
}


def _chord_table() -> str:
    """Tabella Markdown degli accordi, generata da CHORD_INTERVALS."""
    rows = ['| Valore | Intervalli (semitoni) | Voci |',
            '|--------|-----------------------|------|']
    for name, intervals in CHORD_INTERVALS.items():
        rows.append(
            f"| `{name}` | {', '.join(str(i) for i in intervals)} "
            f"| {len(intervals)} |"
        )
    return '\n'.join(rows)


# Kwarg `unit` condiviso dalle strategy pitch unit-agnostiche
# (step, range, stochastic). chord e spectral sono semitone-locked e non
# lo espongono nel completamento (il motore accetta solo `semitones`).
_UNIT_KWARG = VoiceKwargSpec(
    name='unit',
    type='pitch_unit',
    required=False,
    description=VOICE_UNIT_DOC,
)


# ---------------------------------------------------------------------------
# Registry completo: dimension -> strategy_name -> VoiceStrategySpec
# ---------------------------------------------------------------------------

VOICE_STRATEGY_REGISTRY: Dict[str, Dict[str, VoiceStrategySpec]] = {

    # -----------------------------------------------------------------------
    # PITCH: distribuzione pitch per voce, geometria definita da `unit`
    # -----------------------------------------------------------------------
    'pitch': {
        'step': VoiceStrategySpec(
            name='step',
            description=(
                "Distribuisce le voci a passi fissi nell'unità attiva.\n\n"
                "Posizione della voce `i` = `i`, ampiezza = `step`.\n\n"
                "- **Famiglia EDO** (default `semitones`): offset additivi "
                "`[0, step, 2·step, ...]`. Esempio: `step: 3.0` con 4 voci → "
                "`[0, 3, 6, 9]` semitoni.\n"
                "- **`unit: ratio`**: progressione **geometrica** `step^i`. "
                "Esempio: `step: 2.0` con 4 voci → fattori `[1, 2, 4, 8]` "
                "(ottave). Richiede `step > 0`."
            ),
            kwargs={
                'step': VoiceKwargSpec(
                    name='step',
                    type='float',
                    required=True,
                    description=(
                        "Passo tra voci adiacenti, espresso nell'unità attiva "
                        "(`unit`, default semitoni).\n\n"
                        "Con la famiglia EDO può essere negativo per una "
                        "progressione discendente. Con `unit: ratio` deve "
                        "essere `> 0` (con valore ≤ 0 il motore produce "
                        "identità). Accetta scalare o envelope."
                    ),
                ),
                'unit': _UNIT_KWARG,
            },
        ),

        'range': VoiceStrategySpec(
            name='range',
            description=(
                "Distribuisce le voci nell'intervallo `[identità, pitch_range]`.\n\n"
                "Posizione della voce `i` = `i / (num_voices − 1)` ∈ `[0, 1]`, "
                "ampiezza = `pitch_range`.\n\n"
                "- **Famiglia EDO** (default `semitones`): equidistanti. "
                "Esempio: `pitch_range: 12.0` con 4 voci → `[0, 4, 8, 12]` "
                "semitoni.\n"
                "- **`unit: ratio`**: distribuzione **geometrica** "
                "`pitch_range^posizione`. Esempio: `pitch_range: 2.0` con "
                "4 voci → fattori `[1, 1.26, 1.59, 2]`. Richiede "
                "`pitch_range > 0`."
            ),
            kwargs={
                'pitch_range': VoiceKwargSpec(
                    name='pitch_range',
                    type='float',
                    required=True,
                    min_val=0.0,
                    description=(
                        "Estensione totale della distribuzione, espressa "
                        "nell'unità attiva (`unit`, default semitoni).\n\n"
                        "Famiglia EDO: deve essere ≥ 0. Con `unit: ratio` "
                        "deve essere `> 0`. Accetta scalare o envelope.\n\n"
                        "> Sostituisce la vecchia chiave `semitone_range` "
                        "(hard break: il motore la rifiuta)."
                    ),
                ),
                'unit': _UNIT_KWARG,
            },
        ),

        'chord': VoiceStrategySpec(
            name='chord',
            description=(
                "Assegna le voci agli intervalli di un accordo.\n\n"
                "Se `num_voices > n_note`, le voci in eccesso continuano il "
                "pattern all'ottava superiore: voce `i` → "
                "`intervals[i % n] + (i // n) × 12`.\n\n"
                "**Esempio:** `chord: dom7` con 6 voci → offset "
                "`[0, 4, 7, 10, 12, 16]` semitoni.\n\n"
                "`inversion` ruota gli intervalli (rivolti): il grado `k` "
                "diventa la voce più bassa.\n\n"
                "> Strategy **semitone-locked**: accetta solo "
                "`unit: semitones` (o `unit` assente)."
            ),
            kwargs={
                'chord': VoiceKwargSpec(
                    name='chord',
                    type='enum',
                    required=True,
                    description=(
                        "Tipo di accordo che definisce gli intervalli delle "
                        "voci.\n\n" + _chord_table()
                    ),
                    enum_values=tuple(CHORD_INTERVALS.keys()),
                ),
                'inversion': VoiceKwargSpec(
                    name='inversion',
                    type='int',
                    required=False,
                    min_val=0.0,
                    description=(
                        "Rivolto dell'accordo: ruota gli intervalli in modo "
                        "che il grado `k` diventi la voce più bassa "
                        "(normalizzata a 0).\n\n"
                        "- `inversion: 0` → posizione fondamentale (default)\n"
                        "- `inversion: 1` → primo rivolto (terza al basso)\n"
                        "- ...\n\n"
                        "Range valido: `[0, n_note − 1]` dell'accordo scelto."
                    ),
                ),
            },
        ),

        'stochastic': VoiceStrategySpec(
            name='stochastic',
            description=(
                "Assegna a ogni voce un offset casuale fisso entro il run.\n\n"
                "Posizione della voce = uniforme in `[−1, 1]`, ampiezza = "
                "`pitch_range`.\n\n"
                "- **Famiglia EDO** (default `semitones`): offset in "
                "`[−pitch_range, +pitch_range]`. Esempio: `pitch_range: 6.0` "
                "→ entro ±6 semitoni.\n"
                "- **`unit: ratio`**: simmetrico **geometrico** "
                "`pitch_range^posizione`. Esempio: `pitch_range: 2.0` → "
                "fattori in `[0.5, 2]`. Richiede `pitch_range > 0`."
            ),
            kwargs={
                'pitch_range': VoiceKwargSpec(
                    name='pitch_range',
                    type='float',
                    required=True,
                    min_val=0.0,
                    description=(
                        "Deviazione massima (verso l'alto o il basso), "
                        "espressa nell'unità attiva (`unit`, default "
                        "semitoni).\n\n"
                        "Famiglia EDO: deve essere ≥ 0. Con `unit: ratio` "
                        "deve essere `> 0`. Accetta scalare o envelope.\n\n"
                        "> Sostituisce la vecchia chiave `semitone_range` "
                        "(hard break: il motore la rifiuta)."
                    ),
                ),
                'unit': _UNIT_KWARG,
            },
        ),

        'spectral': VoiceStrategySpec(
            name='spectral',
            description=(
                "Distribuisce le voci sui parziali della serie armonica naturale.\n\n"
                "Voce `i` → parziale `i+1` → `round(12 × log₂(i+1))` semitoni.\n\n"
                "Serie: `[0, 12, 19, 24, 28, 31, 34, 36, ...]` per le prime 8 voci.\n\n"
                "**Esempio:** 4 voci → offset `[0, 12, 19, 24]` semitoni.\n\n"
                "> Strategy **semitone-locked**: accetta solo "
                "`unit: semitones` (o `unit` assente)."
            ),
            kwargs={
                'max_partial': VoiceKwargSpec(
                    name='max_partial',
                    type='int',
                    required=False,
                    min_val=1.0,
                    description=(
                        "Numero di parziali pre-calcolati all'inizializzazione.\n\n"
                        "Default: `16`. Voci oltre questo limite sono calcolate on-demand.\n\n"
                        "Deve essere ≥ 1."
                    ),
                ),
            },
        ),
    },

    # -----------------------------------------------------------------------
    # ONSET_OFFSET: sfasamento temporale di inizio per voce (in secondi)
    # -----------------------------------------------------------------------
    'onset_offset': {
        'linear': VoiceStrategySpec(
            name='linear',
            description=(
                "Distribuisce le voci con un offset temporale lineare uniforme.\n\n"
                "La voce `i` inizia `i × step` secondi dopo la voce 0.\n\n"
                "**Esempio:** `step: 0.05` con 4 voci → onset offset `[0, 0.05, 0.10, 0.15]` s."
            ),
            kwargs={
                'step': VoiceKwargSpec(
                    name='step',
                    type='float',
                    required=True,
                    description=(
                        "Intervallo di tempo in secondi tra voci adiacenti.\n\n"
                        "Può essere negativo."
                    ),
                ),
            },
        ),

        'geometric': VoiceStrategySpec(
            name='geometric',
            description=(
                "Distribuisce le voci con spaziatura temporale esponenziale.\n\n"
                "**Formula:** `offset(v) = step × base^v`\n\n"
                "**Esempio:** `step: 0.01, base: 2.0` con 4 voci → "
                "offset `[0.01, 0.02, 0.04, 0.08]` s."
            ),
            kwargs={
                'step': VoiceKwargSpec(
                    name='step',
                    type='float',
                    required=True,
                    description=(
                        "Passo base in secondi. Viene moltiplicato per `base^v`."
                    ),
                ),
                'base': VoiceKwargSpec(
                    name='base',
                    type='float',
                    required=True,
                    description=(
                        "Base della progressione geometrica. Deve essere > 0.\n\n"
                        "**Esempi:** `base: 2.0` → raddoppio; `base: 1.5` → crescita del 50%."
                    ),
                ),
            },
        ),

        'stochastic': VoiceStrategySpec(
            name='stochastic',
            description=(
                "Assegna offset di onset casuali deterministici a ogni voce.\n\n"
                "Ogni voce riceve un ritardo uniforme in `[0, max_offset]` secondi."
            ),
            kwargs={
                'max_offset': VoiceKwargSpec(
                    name='max_offset',
                    type='float',
                    required=True,
                    min_val=0.0,
                    description=(
                        "Offset temporale massimo in secondi.\n\n"
                        "Ogni voce riceve un delay casuale in `[0, max_offset]`."
                    ),
                ),
            },
        ),
    },

    # -----------------------------------------------------------------------
    # POINTER: offset di posizione di lettura nel sample per voce
    # -----------------------------------------------------------------------
    'pointer': {
        'linear': VoiceStrategySpec(
            name='linear',
            description=(
                "Distribuisce le voci a posizioni di lettura lineari nel sample.\n\n"
                "La voce `i` legge da `base_position + i × step`.\n\n"
                "- default (`normalized` assente / `false`): `step` in **secondi**.\n"
                "- `normalized: true`: `step` come **frazione di `sample_dur_sec`** "
                "(es. `0.1` = 10% del buffer)."
            ),
            kwargs={
                'step': VoiceKwargSpec(
                    name='step',
                    type='float',
                    required=True,
                    description=(
                        "Offset di posizione di lettura tra voci adiacenti.\n\n"
                        "Unità: **secondi** di default; **frazione di `sample_dur_sec`** "
                        "se `normalized: true`."
                    ),
                ),
                'normalized': VoiceKwargSpec(
                    name='normalized',
                    type='bool',
                    required=False,
                    description=(
                        "Se `true`, interpreta `step` come frazione di `sample_dur_sec` "
                        "invece che in secondi.\n\n"
                        "Default: `false` (offset in secondi, retrocompatibile).\n\n"
                        "**Esempio:** `step: 0.1, normalized: true` → ogni voce "
                        "è sfasata del 10% del buffer."
                    ),
                ),
            },
        ),

        'stochastic': VoiceStrategySpec(
            name='stochastic',
            description=(
                "Assegna posizioni di lettura casuali deterministiche.\n\n"
                "Ogni voce riceve una variazione di puntatore in "
                "`[−pointer_range, +pointer_range]`.\n\n"
                "- default (`normalized` assente / `false`): `pointer_range` in **secondi**.\n"
                "- `normalized: true`: `pointer_range` come **frazione di `sample_dur_sec`**."
            ),
            kwargs={
                'pointer_range': VoiceKwargSpec(
                    name='pointer_range',
                    type='float',
                    required=True,
                    description=(
                        "Variazione massima di posizione di lettura.\n\n"
                        "Distribuita uniformemente in `[−range, +range]`.\n\n"
                        "Unità: **secondi** di default; **frazione di `sample_dur_sec`** "
                        "se `normalized: true`."
                    ),
                ),
                'normalized': VoiceKwargSpec(
                    name='normalized',
                    type='bool',
                    required=False,
                    description=(
                        "Se `true`, interpreta `pointer_range` come frazione di `sample_dur_sec` "
                        "invece che in secondi.\n\n"
                        "Default: `false` (offset in secondi, retrocompatibile).\n\n"
                        "**Esempio:** `pointer_range: 0.05, normalized: true` → variazione "
                        "casuale entro ±5% del buffer."
                    ),
                ),
            },
        ),
    },

    # -----------------------------------------------------------------------
    # PAN: posizionamento stereo per voce
    # -----------------------------------------------------------------------
    'pan': {
        'linear': VoiceStrategySpec(
            name='linear',
            description=(
                "Distribuisce le voci equidistanti nello spazio stereo.\n\n"
                "**Esempio:** `spread: 90.0` con 4 voci → pan `[−45°, −15°, +15°, +45°]`."
            ),
            kwargs={
                'spread': VoiceKwargSpec(
                    name='spread',
                    type='float',
                    required=True,
                    min_val=0.0,
                    description=(
                        "Ampiezza totale della distribuzione stereo in gradi.\n\n"
                        "Le voci sono equidistanti da `−spread/2` a `+spread/2`.\n\n"
                        "Deve essere ≥ 0. Valore tipico: 60–120."
                    ),
                ),
            },
        ),

        'random': VoiceStrategySpec(
            name='random',
            description=(
                "Assegna posizioni pan casuali deterministiche.\n\n"
                "Ogni voce riceve una posizione stereo uniforme in `[−spread/2, +spread/2]`."
            ),
            kwargs={
                'spread': VoiceKwargSpec(
                    name='spread',
                    type='float',
                    required=True,
                    min_val=0.0,
                    description=(
                        "Ampiezza totale della distribuzione stereo in gradi.\n\n"
                        "Ogni voce riceve una posizione casuale in `[−spread/2, +spread/2]`.\n\n"
                        "Deve essere ≥ 0."
                    ),
                ),
            },
        ),

        'additive': VoiceStrategySpec(
            name='additive',
            description=(
                "Applica un offset pan fisso a tutte le voci.\n\n"
                "Tutte le voci ricevono lo stesso offset `spread` rispetto al pan base dello stream."
            ),
            kwargs={
                'spread': VoiceKwargSpec(
                    name='spread',
                    type='float',
                    required=True,
                    description=(
                        "Offset pan fisso in gradi applicato a tutte le voci.\n\n"
                        "Può essere negativo (sinistra) o positivo (destra)."
                    ),
                ),
            },
        ),
    },
}

# Dimensioni disponibili, nell'ordine consigliato per il completamento
VOICE_DIMENSIONS: List[str] = ['pitch', 'onset_offset', 'pointer', 'pan']

# Chiavi envelope-capable di primo livello dentro voices:.
# Hanno bounds in GRANULAR_PARAMETERS del motore ma nessun ParameterSpec in
# ALL_SCHEMAS (vengono parsati direttamente in _init_voice_manager di stream.py).
# I bounds vengono letti dinamicamente dal bridge via get_raw_bounds().
VOICE_ENVELOPE_KEYS: List[str] = ['num_voices', 'scatter']

# Chiavi di primo livello dentro il blocco voices:
VOICE_TOP_LEVEL_KEYS: List[str] = VOICE_ENVELOPE_KEYS + VOICE_DIMENSIONS

# Documentazione per le chiavi top-level di voices
_VOICE_TOP_LEVEL_DOCS: Dict[str, str] = {
    'num_voices': (
        "**num_voices** — Numero di voci attive per questo stream.\n\n"
        "Range: `[1, 256]` · Variazione: `quantized` (intera)\n\n"
        "Accetta envelope per variare il numero di voci nel tempo:\n"
        "```yaml\nnum_voices: [[0.0, 1], [4.0, 8], [8.0, 1]]\n```\n\n"
        "Ogni voce è una copia indipendente del generatore granulare "
        "con offset configurabili su pitch, onset, pointer e pan."
    ),
    'scatter': (
        "**scatter** — Controllo della sincronizzazione inter-voce nel tempo.\n\n"
        "Range: `[0.0, 1.0]` · Variazione: `additive`\n\n"
        "- `scatter: 0.0` → tutte le voci condividono lo stesso timing inter-onset\n"
        "- `scatter: 1.0` → ogni voce diverge con intervalli inter-onset stocastici\n\n"
        "Accetta envelope per variare la dispersione nel tempo:\n"
        "```yaml\nscatter: [[0.0, 0.0], [4.0, 0.8]]\n```"
    ),
    'pitch': (
        "**pitch** — Strategy di distribuzione pitch per voce.\n\n"
        "Dimensione opzionale. Se assente, tutte le voci usano lo stesso pitch.\n\n"
        "Strategy disponibili: `step`, `range`, `chord`, `stochastic`, `spectral`\n\n"
        "La chiave opzionale `unit` definisce la geometria della "
        "distribuzione: `semitones` (default) | `cents` | `quarter_tone` | "
        "`eighth_tone` | `{edo: N}` | `ratio` (geometrica). "
        "`chord` e `spectral` accettano solo `semitones`.\n\n"
        "```yaml\npitch:\n  strategy: range\n  pitch_range: 12.0\n  unit: semitones\n```"
    ),
    'onset_offset': (
        "**onset_offset** — Strategy di sfasamento temporale per voce.\n\n"
        "Dimensione opzionale. Se assente, tutte le voci iniziano simultaneamente.\n\n"
        "Strategy disponibili: `linear`, `geometric`, `stochastic`\n\n"
        "```yaml\nonset_offset:\n  strategy: linear\n  step: 0.05\n```"
    ),
    'pointer': (
        "**pointer** — Strategy di offset di posizione di lettura per voce.\n\n"
        "Dimensione opzionale. Se assente, tutte le voci leggono dalla stessa posizione.\n\n"
        "Strategy disponibili: `linear`, `stochastic`\n\n"
        "**Flag `normalized`** (opzionale, default `false`): se `true`, gli offset "
        "sono interpretati come **frazione di `sample_dur_sec`** invece che in secondi. "
        "Vale per entrambe le strategy.\n\n"
        "```yaml\npointer:\n  strategy: linear\n  step: 0.1\n  normalized: true\n```"
    ),
    'pan': (
        "**pan** — Strategy di posizionamento stereo per voce.\n\n"
        "Dimensione opzionale. Se assente, tutte le voci usano il pan base dello stream.\n\n"
        "Strategy disponibili: `linear`, `random`, `additive`\n\n"
        "```yaml\npan:\n  strategy: linear\n  spread: 90.0\n```"
    ),
}

# Documentazione del blocco voices nel suo insieme
VOICES_BLOCK_DOC = (
    "**voices** — Configura voci multiple per questo stream.\n\n"
    "Ogni voce è una copia indipendente del generatore granulare. "
    "Le voci condividono tutti i parametri dello stream ma possono avere "
    "offset indipendenti su pitch, onset, pointer e pan tramite strategy-based dispatch.\n\n"
    "Chiavi disponibili:\n"
    "- `num_voices` — numero di voci (richiesto)\n"
    "- `pitch` — distribuzione pitch nell'unità attiva (`unit`)\n"
    "- `onset_offset` — sfasamento temporale di onset\n"
    "- `pointer` — offset di posizione di lettura\n"
    "- `pan` — posizionamento stereo\n\n"
    "```yaml\nvoices:\n  num_voices: 4\n  pitch:\n    strategy: chord\n    chord: dom7\n```\n\n"
    "**Tempo degli envelope.** I breakpoint temporali `[t, v]` degli envelope "
    "dentro `voices.*` (es. `pan.step`, `pointer.pointer_range`) **ereditano** il "
    "`time_mode` dello stream, come gli envelope diretti: con `time_mode: normalized` "
    "i tempi sono in `[0, 1]` e vengono scalati su `duration`. La forma dict con "
    "`time_mode`/`time_unit` locale sovrascrive quello dello stream."
)


# ---------------------------------------------------------------------------
# Funzioni di accesso
# ---------------------------------------------------------------------------

def get_strategies_for_dimension(dim: str) -> List[str]:
    """Nomi delle strategy disponibili per la dimensione data."""
    return list(VOICE_STRATEGY_REGISTRY.get(dim, {}).keys())


def get_strategy_spec(dim: str, strategy: str) -> Optional[VoiceStrategySpec]:
    """Restituisce la VoiceStrategySpec per una strategy specifica, o None."""
    return VOICE_STRATEGY_REGISTRY.get(dim, {}).get(strategy)


def get_kwarg_spec(dim: str, strategy: str,
                   kwarg_name: str) -> Optional[VoiceKwargSpec]:
    """Restituisce la VoiceKwargSpec per un kwarg specifico, o None."""
    spec = get_strategy_spec(dim, strategy)
    if spec is None:
        return None
    return spec.kwargs.get(kwarg_name)


def find_kwarg_in_dimension(dim: str,
                             kwarg_name: str) -> Optional[VoiceKwargSpec]:
    """
    Cerca un kwarg per nome in tutte le strategy di una dimensione.
    Restituisce il primo match trovato.
    Utile per l'hover quando non conosciamo la strategy attiva.
    """
    for strategy_spec in VOICE_STRATEGY_REGISTRY.get(dim, {}).values():
        if kwarg_name in strategy_spec.kwargs:
            return strategy_spec.kwargs[kwarg_name]
    return None


def get_top_level_doc(key: str) -> Optional[str]:
    """Documentazione per una chiave top-level del blocco voices."""
    return _VOICE_TOP_LEVEL_DOCS.get(key)
