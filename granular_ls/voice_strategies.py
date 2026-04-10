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


@dataclass(frozen=True)
class VoiceKwargSpec:
    """Specifica di un singolo kwarg di una voice strategy."""
    name: str
    type: str               # 'float', 'int', 'enum', 'bool'
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
# Registry completo: dimension -> strategy_name -> VoiceStrategySpec
# ---------------------------------------------------------------------------

VOICE_STRATEGY_REGISTRY: Dict[str, Dict[str, VoiceStrategySpec]] = {

    # -----------------------------------------------------------------------
    # PITCH: offset in semitoni per voce
    # -----------------------------------------------------------------------
    'pitch': {
        'step': VoiceStrategySpec(
            name='step',
            description=(
                "Distribuisce le voci a intervalli fissi in semitoni.\n\n"
                "La voce `i` riceve un offset di `i × step` semitoni rispetto al pitch base.\n\n"
                "**Esempio:** `step: 3.0` con 4 voci → offset `[0, 3, 6, 9]` semitoni."
            ),
            kwargs={
                'step': VoiceKwargSpec(
                    name='step',
                    type='float',
                    required=True,
                    description=(
                        "Intervallo in semitoni tra voci adiacenti.\n\n"
                        "Può essere negativo per una progressione discendente.\n\n"
                        "**Esempio:** `step: 3.0` → ogni voce è 3 semitoni più alta della precedente."
                    ),
                ),
            },
        ),

        'range': VoiceStrategySpec(
            name='range',
            description=(
                "Distribuisce le voci linearmente su un intervallo totale di semitoni.\n\n"
                "Le voci sono equidistanti tra 0 e `semitone_range`.\n\n"
                "**Formula:** `offset(v) = v × semitone_range / (num_voices − 1)`\n\n"
                "**Esempio:** `semitone_range: 12.0` con 4 voci → offset `[0, 4, 8, 12]` semitoni."
            ),
            kwargs={
                'semitone_range': VoiceKwargSpec(
                    name='semitone_range',
                    type='float',
                    required=True,
                    min_val=0.0,
                    description=(
                        "Intervallo totale in semitoni distribuito linearmente su tutte le voci.\n\n"
                        "Deve essere ≥ 0.\n\n"
                        "**Formula:** `offset(v) = v × semitone_range / (num_voices − 1)`"
                    ),
                ),
            },
        ),

        'chord': VoiceStrategySpec(
            name='chord',
            description=(
                "Assegna le voci agli intervalli di un accordo.\n\n"
                "Se `num_voices > len(chord_intervals)`, le voci in eccesso ricevono "
                "gli stessi intervalli traslati di un'ottava (12 semitoni).\n\n"
                "**Esempio:** `chord: dom7` con 6 voci → offset `[0, 4, 7, 10, 12, 16]` semitoni."
            ),
            kwargs={
                'chord': VoiceKwargSpec(
                    name='chord',
                    type='enum',
                    required=True,
                    description=(
                        "Tipo di accordo che definisce gli intervalli delle voci.\n\n"
                        "| Valore | Intervalli (semitoni) | Nome |\n"
                        "|--------|-----------------------|------|\n"
                        "| `maj` | 0, 4, 7 | Maggiore |\n"
                        "| `min` | 0, 3, 7 | Minore |\n"
                        "| `dom7` | 0, 4, 7, 10 | Dominante settima |\n"
                        "| `maj7` | 0, 4, 7, 11 | Maggiore settima |\n"
                        "| `min7` | 0, 3, 7, 10 | Minore settima |\n"
                        "| `dim` | 0, 3, 6 | Diminuito |\n"
                        "| `aug` | 0, 4, 8 | Aumentato |\n"
                        "| `sus2` | 0, 2, 7 | Sospeso 2ª |\n"
                        "| `sus4` | 0, 5, 7 | Sospeso 4ª |\n"
                        "| `dim7` | 0, 3, 6, 9 | Diminuito settima |\n"
                        "| `minmaj7` | 0, 3, 7, 11 | Minore maggiore settima |"
                    ),
                    enum_values=(
                        'maj', 'min', 'dom7', 'maj7', 'min7',
                        'dim', 'aug', 'sus2', 'sus4', 'dim7', 'minmaj7',
                    ),
                ),
            },
        ),

        'stochastic': VoiceStrategySpec(
            name='stochastic',
            description=(
                "Assegna offset in semitoni casuali ma deterministici a ogni voce.\n\n"
                "Gli offset sono distribuiti uniformemente in `[−semitone_range, +semitone_range]`. "
                "Il seed è calcolato da `stream_id + voice_index`, garantendo riproducibilità "
                "tra esecuzioni diverse.\n\n"
                "**Esempio:** `semitone_range: 6.0` → ogni voce ha un offset fisso entro ±6 semitoni."
            ),
            kwargs={
                'semitone_range': VoiceKwargSpec(
                    name='semitone_range',
                    type='float',
                    required=True,
                    min_val=0.0,
                    description=(
                        "Deviazione massima in semitoni (verso l'alto o il basso).\n\n"
                        "Gli offset sono distribuiti uniformemente in `[−range, +range]`.\n\n"
                        "Deve essere ≥ 0."
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
                "La voce `i` legge da `base_position + i × step`."
            ),
            kwargs={
                'step': VoiceKwargSpec(
                    name='step',
                    type='float',
                    required=True,
                    description=(
                        "Offset di posizione di lettura tra voci adiacenti.\n\n"
                        "Unità: secondi (o normalizzata se `loop_unit: normalized`)."
                    ),
                ),
            },
        ),

        'stochastic': VoiceStrategySpec(
            name='stochastic',
            description=(
                "Assegna posizioni di lettura casuali deterministiche.\n\n"
                "Ogni voce riceve una variazione di puntatore in "
                "`[−pointer_range, +pointer_range]`."
            ),
            kwargs={
                'pointer_range': VoiceKwargSpec(
                    name='pointer_range',
                    type='float',
                    required=True,
                    description=(
                        "Variazione massima di posizione di lettura.\n\n"
                        "Distribuita uniformemente in `[−range, +range]`."
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
        "Range: `[1, 64]` · Variazione: `quantized` (intera)\n\n"
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
        "**pitch** — Strategy di offset in semitoni per voce.\n\n"
        "Dimensione opzionale. Se assente, tutte le voci usano lo stesso pitch.\n\n"
        "Strategy disponibili: `step`, `range`, `chord`, `stochastic`\n\n"
        "```yaml\npitch:\n  strategy: chord\n  chord: dom7\n```"
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
        "```yaml\npointer:\n  strategy: stochastic\n  pointer_range: 0.1\n```"
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
    "- `pitch` — offset pitch in semitoni\n"
    "- `onset_offset` — sfasamento temporale di onset\n"
    "- `pointer` — offset di posizione di lettura\n"
    "- `pan` — posizionamento stereo\n\n"
    "```yaml\nvoices:\n  num_voices: 4\n  pitch:\n    strategy: chord\n    chord: dom7\n```"
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
