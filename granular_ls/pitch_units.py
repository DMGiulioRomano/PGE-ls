# granular_ls/pitch_units.py
"""
Registry statico delle unità di misura del pitch per l'intelligenza LSP.

Dal refactor unit-driven di PGE (PR #84) il blocco pitch non è più
schema-driven: PITCH_PARAMETER_SCHEMA è vuoto e la superficie YAML vive in
PitchController (src/controllers/pitch_controller.py) e PitchUnit
(src/parameters/pitch_unit.py). Questo modulo replica quella superficie come
metadati statici, nello stesso spirito di voice_strategies.py:
  - Autocompletamento delle chiavi del blocco pitch e dei loro valori
  - Documentazione hover per unità e modificatori
  - Validazione diagnostica (chiavi strict, una sola unità, bounds per unità,
    grammatica edo/value, forme valide di voices.pitch.unit)

Superficie YAML replicata (docs/reference/yaml.md § Blocco Pitch nel repo PGE):

  pitch:
    semitones: 0          # una sola chiave-unità per blocco
    cents: 50
    quarter_tone: 3
    eighth_tone: 6
    ratio: 1.5
    edo: 31               # intero scalare; richiede `value` a fianco
    value: 18             # solo con `edo`
    range: 6              # modificatore: ±variazione casuale nell'unità attiva

  voices:
    pitch:
      unit: semitones     # preset | {edo: N}; chord/spectral solo semitones
"""

import re
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

# Semi-ampiezza in cents (±N) del micro-detune implicito che il motore applica
# alle unità EDO sotto dephase senza `range` esplicito (PGE issue #95).
# Solo documentazione: nessuna validazione LSP collegata.
EDO_IMPLICIT_DETUNE_CENTS = 12.0


@dataclass(frozen=True)
class PitchUnitInfo:
    """
    Metadati di una unità pitch, speculari a PitchUnit.value_bounds() di PGE.

    divisions è None per `ratio` (famiglia moltiplicativa); per la famiglia
    EDO i bounds sono ±3 ottave, cioè ±(3 · divisions), con max_range pari
    a 3 · divisions.
    """
    key: str
    symbol: str
    divisions: Optional[int]
    min_val: float
    max_val: float
    max_range: float
    variation_mode: str
    neutral: float


def _edo_info(key: str, divisions: int, symbol: str) -> PitchUnitInfo:
    bound = 3.0 * divisions
    return PitchUnitInfo(
        key=key,
        symbol=symbol,
        divisions=divisions,
        min_val=-bound,
        max_val=bound,
        max_range=bound,
        variation_mode='quantized',
        neutral=0.0,
    )


# Preset nominali, nell'ordine consigliato per il completamento.
# Bounds speculari a PITCH_UNIT_PRESETS + value_bounds() del motore.
PITCH_UNIT_PRESETS: Dict[str, PitchUnitInfo] = {
    'semitones':    _edo_info('semitones', 12, 'st'),
    'cents':        _edo_info('cents', 1200, 'c'),
    'quarter_tone': _edo_info('quarter_tone', 24, 'qt'),
    'eighth_tone':  _edo_info('eighth_tone', 48, 'et'),
    'ratio':        PitchUnitInfo(
        key='ratio', symbol='x', divisions=None,
        min_val=0.125, max_val=8.0, max_range=2.0,
        variation_mode='additive', neutral=1.0,
    ),
}

# Chiavi-unità del blocco pitch, nell'ordine consigliato per il completamento.
# `edo` è parametrico (edo: N + value: X); gli altri sono preset nominali.
PITCH_UNIT_KEYS: Tuple[str, ...] = (
    'semitones', 'cents', 'quarter_tone', 'eighth_tone', 'edo', 'ratio',
)

# Modificatori non-unità ammessi nel blocco pitch.
# `value` è il valore della griglia EDO (solo con `edo: N`).
PITCH_BLOCK_EXTRA_KEYS: Tuple[str, ...] = ('range', 'value')

# Whitelist completa del blocco pitch (speculare a PITCH_BLOCK_KEYS del
# motore): chiavi fuori da questo insieme sono refusi e vanno segnalate.
PITCH_BLOCK_KEYS = frozenset(PITCH_UNIT_KEYS) | frozenset(PITCH_BLOCK_EXTRA_KEYS)

# Valori preset validi per voices.pitch.unit (stringhe); la forma parametrica
# è `{edo: N}`. `edo` nudo NON è un preset valido.
VOICE_PITCH_UNIT_VALUES: Tuple[str, ...] = (
    'semitones', 'cents', 'quarter_tone', 'eighth_tone', 'ratio',
)

# Strategy voices.pitch intrinsecamente in semitoni: accettano solo
# `unit: semitones` (o `unit` assente). Speculare a SEMITONE_LOCKED del motore.
SEMITONE_LOCKED_STRATEGIES = frozenset({'chord', 'spectral'})


def edo_bounds(divisions: int) -> Tuple[float, float]:
    """Bounds del valore per una griglia EDO a N divisioni: ±3 ottave."""
    bound = 3.0 * divisions
    return (-bound, bound)


def get_unit_info(unit_key: str,
                  divisions: Optional[int] = None) -> Optional[PitchUnitInfo]:
    """
    Metadati per una chiave-unità del blocco pitch.

    Per `edo` serve divisions (int > 0): senza, o con valore invalido,
    ritorna None perché i bounds sono dinamici (±3 · N).
    """
    if unit_key == 'edo':
        if not isinstance(divisions, int) or isinstance(divisions, bool) \
                or divisions <= 0:
            return None
        return _edo_info('edo', divisions, f'edo{divisions}')
    return PITCH_UNIT_PRESETS.get(unit_key)


def parse_edo_divisions(value_str: str) -> Optional[int]:
    """
    Interpreta il valore della chiave `edo: N` del blocco pitch.

    Ritorna N se è un intero > 0, None altrimenti (float, stringa, bool,
    forma annidata). La validazione del motore è EdoUnit.__init__:
    intero puro > 0, bool escluso.
    """
    s = value_str.strip()
    if not re.fullmatch(r'[+-]?\d+', s):
        return None
    n = int(s)
    return n if n > 0 else None


def split_inline_mapping(inner: str) -> list:
    """
    Divide il contenuto di un mapping inline '{...}' in coppie (chiave, valore).

    Split bracket-aware: le virgole dentro [] o {} annidati non separano
    le coppie, cosi' 'semitones: [[0, -12], [30, 12]], range: 6' produce
    due coppie. Frammenti senza ':' vengono ignorati (tolleranza).
    """
    pairs = []
    depth = 0
    current = []
    parts = []
    for ch in inner:
        if ch in '[{':
            depth += 1
        elif ch in ']}':
            depth -= 1
        elif ch == ',' and depth == 0:
            parts.append(''.join(current))
            current = []
            continue
        current.append(ch)
    if current:
        parts.append(''.join(current))
    for part in parts:
        m = re.match(r'^\s*([a-zA-Z_]\w*)\s*:\s*(.*)$', part)
        if m:
            pairs.append((m.group(1), m.group(2).strip()))
    return pairs


def get_pitch_block_entries(document_text: str, cursor_line: int) -> Dict[str, str]:
    """
    Chiavi e valori del blocco pitch: dello stream che contiene cursor_line.

    Scope indent-aware: 'pitch:' a 4 spazi (il blocco stream-level), chiavi
    a 6 spazi. Non confonde il blocco con la dimensione voices.pitch (che
    vive a 6 spazi dentro voices:). Gestisce anche la forma inline
    'pitch: {semitones: 3, range: 1}'. Dict vuoto se il blocco non c'è.

    Condivisa da completion, hover e diagnostica: unica definizione di
    "blocco pitch corrente".
    """
    entries: Dict[str, str] = {}
    if not document_text:
        return entries
    lines = document_text.splitlines()
    if not lines:
        return entries
    cursor = max(0, min(cursor_line, len(lines) - 1))

    def _is_stream_marker(raw: str) -> bool:
        stripped = raw.strip()
        return ((stripped.startswith('- ') or stripped == '-')
                and len(raw) - len(raw.lstrip()) == 2)

    stream_start = 0
    for i in range(cursor, -1, -1):
        if _is_stream_marker(lines[i]):
            stream_start = i
            break
    stream_end = len(lines)
    for i in range(stream_start + 1, len(lines)):
        if _is_stream_marker(lines[i]):
            stream_end = i
            break

    pitch_start = None
    for i in range(stream_start, stream_end):
        raw = lines[i]
        if (len(raw) - len(raw.lstrip()) == 4
                and raw.strip().startswith('pitch:')):
            pitch_start = i
            break
    if pitch_start is None:
        return entries

    inline = lines[pitch_start].strip()[len('pitch:'):].strip()
    if inline.startswith('{'):
        for key, value in split_inline_mapping(inline.strip('{}')):
            entries[key] = value
        return entries

    for i in range(pitch_start + 1, stream_end):
        raw = lines[i]
        stripped = raw.strip()
        if not stripped:
            continue
        leading = len(raw) - len(raw.lstrip())
        if leading <= 4:
            break
        if leading != 6 or stripped.startswith('#'):
            continue
        m = re.match(r'^([a-zA-Z_]\w*)\s*:\s*(.*)$', stripped)
        if m:
            entries[m.group(1)] = m.group(2).strip()
    return entries


def parse_voice_unit_value(value_str: str) -> Tuple[str, object]:
    """
    Classifica il valore di voices.pitch.unit, speculare a make_pitch_unit().

    Ritorna (kind, payload):
      ('preset', nome)   — preset valido (semitones, cents, ..., ratio)
      ('edo', N)         — forma parametrica {edo: N} con N intero > 0
      ('invalid', val)   — qualunque altra cosa (incluso `edo` nudo,
                           {edo: 0}, chiavi extra nel mapping inline)
    """
    s = value_str.strip().strip('"\'')
    if s in VOICE_PITCH_UNIT_VALUES:
        return ('preset', s)
    m = re.fullmatch(r'\{\s*edo\s*:\s*([^,}]+)\}', s)
    if m:
        divisions = parse_edo_divisions(m.group(1))
        if divisions is not None:
            return ('edo', divisions)
    return ('invalid', s)


# ---------------------------------------------------------------------------
# Documentazione hover / completion
# ---------------------------------------------------------------------------

def _edo_key_doc(info: PitchUnitInfo, nome: str, esempio: str) -> str:
    n = info.divisions
    return (
        f"**{info.key}** — Trasposizione in {nome} ({n}-EDO)\n\n"
        f"Range: `[{info.min_val:g}, {info.max_val:g}]` (±3 ottave) · "
        f"Variazione: `quantized` (gradi interi) · Default: `0`\n\n"
        f"Ratio risultante: `2^(valore/{n})`. "
        "Accetta scalare o envelope `[[t, v], ...]`.\n\n"
        f"```yaml\npitch:\n  {esempio}\n```\n\n"
        "Una sola chiave-unità per blocco pitch. Sotto dephase senza `range`, "
        f"il motore applica un micro-detune implicito di "
        f"±{EDO_IMPLICIT_DETUNE_CENTS:g} cents."
    )


PITCH_KEY_DOCS: Dict[str, str] = {
    'semitones': _edo_key_doc(
        PITCH_UNIT_PRESETS['semitones'], 'semitoni', 'semitones: -12'),
    'cents': _edo_key_doc(
        PITCH_UNIT_PRESETS['cents'], 'cents', 'cents: 50'),
    'quarter_tone': _edo_key_doc(
        PITCH_UNIT_PRESETS['quarter_tone'], 'quarti di tono', 'quarter_tone: 3'),
    'eighth_tone': _edo_key_doc(
        PITCH_UNIT_PRESETS['eighth_tone'], 'ottavi di tono', 'eighth_tone: 6'),
    'ratio': (
        "**ratio** — Rapporto di trasposizione diretto\n\n"
        "Range: `[0.125, 8.0]` · Variazione: `additive` · Default: `1.0`\n\n"
        "Moltiplicatore di frequenza: `1.0` = originale, `2.0` = ottava sopra, "
        "`0.5` = ottava sotto. Accetta scalare o envelope `[[t, v], ...]`.\n\n"
        "```yaml\npitch:\n  ratio: 1.5\n```\n\n"
        "Una sola chiave-unità per blocco pitch."
    ),
    'edo': (
        "**edo** — Griglia EDO arbitraria (Equal Division of the Octave)\n\n"
        "Intero scalare > 0: numero di divisioni per ottava. Richiede "
        "`value: X` a fianco (il valore in gradi della griglia, scalare o "
        "envelope).\n\n"
        "```yaml\npitch:\n  edo: 31\n  value: 18    # 18 gradi di 31-EDO\n```\n\n"
        "Bounds di `value`: `[-3·N, 3·N]` (±3 ottave). Ratio risultante: "
        "`2^(value/N)`.\n\n"
        "> La vecchia forma annidata `edo: {divisions, value}` non è più "
        "valida: `edo` ha una sola grammatica ovunque (intero scalare)."
    ),
    'value': (
        "**value** — Valore della griglia EDO\n\n"
        "Ammesso **solo** con `edo: N` a fianco; per i preset il valore sta "
        "nella chiave stessa (es. `semitones: 7`).\n\n"
        "Bounds: `[-3·N, 3·N]` (±3 ottave) · Variazione: `quantized`\n\n"
        "Accetta scalare o envelope `[[t, v], ...]`."
    ),
    'range': (
        "**range** — Variazione casuale attorno al valore base\n\n"
        "Semi-ampiezza (±) della deviazione, espressa nell'unità attiva del "
        "blocco: semitoni con `semitones`, cents con `cents`, gradi con "
        "`edo`, moltiplicatore con `ratio`.\n\n"
        "Bounds: `[0, 3·N]` per la famiglia EDO (es. `[0, 36]` con "
        "`semitones`), `[0, 2.0]` con `ratio`.\n\n"
        "Da solo (senza chiave-unità) si applica al default `semitones` "
        "neutro. Accetta scalare o envelope."
    ),
}

# Documentazione del blocco pitch nel suo insieme (hover su `pitch:`).
PITCH_BLOCK_DOC = (
    "**pitch** — Intonazione dei grani (modello unit-driven)\n\n"
    "Una sola **chiave-unità** per blocco; l'unità è la fonte di verità per "
    "conversione e bounds.\n\n"
    "| Chiave | Unità | Bounds |\n"
    "|--------|-------|--------|\n"
    "| `semitones` | semitoni (12-EDO) | `[-36, 36]` |\n"
    "| `cents` | cents (1200-EDO) | `[-3600, 3600]` |\n"
    "| `quarter_tone` | quarti di tono (24-EDO) | `[-72, 72]` |\n"
    "| `eighth_tone` | ottavi di tono (48-EDO) | `[-144, 144]` |\n"
    "| `edo` + `value` | griglia EDO arbitraria | `[-3·N, 3·N]` |\n"
    "| `ratio` | moltiplicatore diretto | `[0.125, 8]` |\n\n"
    "**Modificatori:** `range` — ±variazione casuale nell'unità attiva; "
    "`value` — valore in gradi, solo con `edo: N`.\n\n"
    "```yaml\npitch:\n  semitones: [[0, -12], [30, 12]]\n  range: 6\n```\n\n"
    "Regole (validate dal motore):\n"
    "- più chiavi-unità nello stesso blocco → errore;\n"
    "- chiavi sconosciute (refusi come `semitone`) → errore;\n"
    "- `pitch:` vuoto o non-mapping → errore — per nessuna trasposizione "
    "ometti del tutto il blocco;\n"
    "- senza chiave-unità: default `semitones` neutro (ratio 1.0)."
)

# Documentazione breve dei VALORI di voices.pitch.unit (preset + forma edo).
# Usata da completion (menu valori) e hover (cursore sul valore).
VOICE_UNIT_VALUE_DOCS: Dict[str, str] = {
    **{
        key: (
            f"**{key}** — famiglia EDO, {info.divisions} divisioni/ottava "
            f"({info.symbol}).\n\nDistribuzione additiva nel log "
            "(equidistante): ratio `2^(posizione·ampiezza/"
            f"{info.divisions})`."
        )
        for key, info in PITCH_UNIT_PRESETS.items()
        if info.divisions is not None
    },
    'ratio': (
        "**ratio** — moltiplicatore diretto.\n\nDistribuzione **geometrica**: "
        "fattore `ampiezza^posizione`. L'ampiezza (`step`/`pitch_range`) "
        "deve essere `> 0`: con valore ≤ 0 il motore produce identità."
    ),
}

# Documentazione del kwarg `unit` di voices.pitch (strategy unit-agnostiche).
VOICE_UNIT_DOC = (
    "Unità di misura dell'ampiezza della distribuzione pitch "
    "(`step` / `pitch_range`).\n\n"
    "Valori: `semitones` (default) | `cents` | `quarter_tone` | "
    "`eighth_tone` | `{edo: N}` | `ratio`\n\n"
    "- **Famiglia EDO** — distribuzione additiva nel log (equidistante): "
    "ratio `2^(posizione·ampiezza/N)`.\n"
    "- **`ratio`** — distribuzione **geometrica**: fattore "
    "`ampiezza^posizione`. Con `step: 2` e 4 voci → `[1, 2, 4, 8]` "
    "(ottave). L'ampiezza deve essere `> 0`: con valore ≤ 0 il motore "
    "produce identità (nessun effetto).\n\n"
    "Le strategy `chord` e `spectral` sono definite in semitoni: accettano "
    "solo `unit: semitones` (oppure `unit` assente)."
)
