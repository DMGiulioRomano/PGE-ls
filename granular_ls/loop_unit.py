# granular_ls/loop_unit.py
"""
Registry statico di `pointer.loop_unit` per l'intelligenza LSP.

`loop_unit` e' il meta-parametro che dice in che unita' sono scritte le
posizioni nel sample: `start`, `loop_start`, `loop_end`, `loop_dur`. Con
`normalized` sono frazioni del file (`[0.0, 1.0]`, scalate per
`sample_dur_sec`), altrimenti secondi.

Fino a PGE v8.0.0 una `loop_unit` assente ereditava da `time_mode`
(`params.get('loop_unit') or config.time_mode`), e un solo keyword governava
due assi che non hanno niente in comune: `time_mode` scala l'asse X degli
envelope sulla `duration` dello stream, `loop_unit` il valore delle posizioni
sulla durata del file. PGE #222 li ha separati:

| | prima | dopo |
|---|---|---|
| default | eredita da `time_mode` | `seconds`, indipendente |
| vocabolario | implicito (`!= 'normalized'`) | `seconds` / `absolute` / `normalized` |
| unita' sconosciuta | silenzio, vale "assoluto" | `InvalidFieldValueError` |
| `loop_unit:` vuoto | `None` e' falsy, eredita | errore |

Mirror di `LOOP_UNITS` e `_LOOP_UNIT_SCOPE` in
`src/pge/controllers/pointer_controller.py`, e della lettura che ne fa
`PointerController._pre_normalize_loop_params`. `tests/test_pge_parity.py` li
rilegge dal sorgente del motore (via AST: il modulo importa numpy).

Il valore si legge **via YAML**: `loop_unit: "normalized"  # nota` e'
`normalized`, `loop_unit: null` e' `None`. E' quel che il motore confronta col
vocabolario, e leggerlo a regex dalla riga e' come il mirror vecchio si
portava dentro il commento. `find_loop_unit` e' l'unico lettore della
dichiarazione: hover, diagnostica e semantic token passano tutti di qui, cosi'
non possono leggere la stessa riga in due modi.
"""

import re
from typing import Any, Dict, List, NamedTuple, Optional, Tuple

import yaml

from granular_ls.envelope_shapes import (
    is_3tuple_breakpoint,
    is_bp_group,
    is_loop_block,
    is_math_expression,
    normalize_engine_values,
)

# La chiave nello YAML: identita' del campo in ogni diagnostica.
LOOP_UNIT_PATH = 'pointer.loop_unit'

# Vocabolario chiuso (PGE #222), nell'ordine del motore: `seconds` e' la
# grafia canonica — allinea loop_unit a grain.duration_unit — e `absolute`
# l'alias storico, quello che i config hanno sempre scritto. Sono la stessa
# lettura. Fuori di qui e' un errore del motore, non un sinonimo di assoluto.
LOOP_UNITS: Tuple[str, ...] = ('seconds', 'absolute', 'normalized')

# Il default ora ha un nome, ed e' indipendente da qualunque altra chiave.
LOOP_UNIT_DEFAULT = 'seconds'

# Le chiavi del blocco pointer che `loop_unit` interpreta. `start` e' fra
# queste benche' loop non sia: e' una posizione nel sample come loop_start,
# stesso dominio e stessa unita'.
LOOP_UNIT_SCOPE: Tuple[str, ...] = ('start', 'loop_start', 'loop_end', 'loop_dur')

# Le tre letture che il language server distingue. `absolute` copre le due
# grafie dei secondi; `invalid` e' un valore scritto che il motore rifiuta,
# e sotto il quale non c'e' una scala in cui misurare le posizioni.
MODE_NORMALIZED = 'normalized'
MODE_ABSOLUTE = 'absolute'
MODE_INVALID = 'invalid'

# Documentazione dei valori: completion e hover la leggono da qui.
LOOP_UNIT_VALUE_DOCS: Dict[str, str] = {
    'seconds': (
        'Posizioni nel sample in **secondi** (default).\n\n'
        'Grafia canonica, la stessa di `grain.duration_unit`. E\' la lettura '
        'di una `loop_unit` assente, qualunque sia il `time_mode` dello stream.'
    ),
    'absolute': (
        'Posizioni nel sample in **secondi**: alias storico di `seconds`.\n\n'
        'Stessa lettura, nessuna conversione. E\' la grafia che i config '
        'scrivevano prima che il default avesse un nome.'
    ),
    'normalized': (
        'Posizioni nel sample come **frazione del file**, in `[0.0, 1.0]`.\n\n'
        'I valori di `start`, `loop_start`, `loop_end` e `loop_dur` (scalari '
        'ed envelope, solo i valori Y) vengono moltiplicati per la durata del '
        'sample sorgente (`sample_dur_sec`).'
    ),
}

# L'errore del motore su un valore fuori vocabolario, per chi lo nomina.
INVALID_HINT = (
    "il motore rifiuta il render (`InvalidFieldValueError`): prima di PGE "
    "#222 un valore sconosciuto valeva \"assoluto\" in silenzio"
)


def loop_unit_mode(value: Any) -> str:
    """La lettura delle posizioni per un valore di `loop_unit` scritto.

    Mirror di `_pre_normalize_loop_params` a chiave presente: fuori da
    `LOOP_UNITS` il motore alza, e questo vale per ogni valore — `None` della
    chiave lasciata vuota, un numero, un booleano, una grafia con la maiuscola.
    La chiave assente non passa di qui: e' `LOOP_UNIT_DEFAULT`.
    """
    if not isinstance(value, str) or value not in LOOP_UNITS:
        return MODE_INVALID
    return MODE_NORMALIZED if value == 'normalized' else MODE_ABSOLUTE


def _is_envelope_like(value: Any) -> bool:
    """Mirror di `Envelope.is_envelope_like`: le forme che il motore scala."""
    if isinstance(value, dict):
        return 'points' in value
    if not isinstance(value, list) or not value:
        return False
    if is_loop_block(value) or is_bp_group(value):
        return True
    for item in value:
        if isinstance(item, list) and len(item) == 2:
            return True
        if is_loop_block(item) or is_bp_group(item):
            return True
        if is_3tuple_breakpoint(item):
            return True
        if isinstance(item, dict) and 't' in item and 'v' in item:
            return True
    return False


# ponytail: va via con l'avviso di migrazione del motore
# (`PointerController._warn_loop_unit_migration`), dopo una release. Il conto
# lo tiene PGE #242.
def rescaling_would_change(value: Any) -> bool:
    """True se la vecchia conversione a frazione del file muoveva `value`.

    Mirror di `_rescaling_would_change` (PointerController, PGE #222), che
    decide chi riceve l'avviso `[LOOP_UNIT]`. Uno zero e' zero sotto qualunque
    fattore di scala — ed e' la forma piu' comune del corpus, `start: 0` — e
    quel che la conversione lascia passare, una stringa, non si muoveva
    nemmeno prima.

    Le stringhe numeriche arrivano al controller gia' convertite dal
    `Generator`, quindi si convertono come lui. Uno scalare scritto come
    espressione fra parentesi no: il suo valore lo calcola il motore, e qui
    non si decide. Dentro un envelope l'espressione non conta — la forma e'
    envelope-like comunque, e il motore la scala.
    """
    if is_math_expression(value):
        return False
    value = normalize_engine_values(value)
    if value is None or isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return value != 0
    return _is_envelope_like(value)


# =============================================================================
# LETTURA DELLA DICHIARAZIONE DAL TESTO
# =============================================================================
# Stesse convenzioni di indentazione del resto del language server: stream
# come elementi `- ` a indent 2, blocchi a indent 4, chiavi dei blocchi a 6.

_LOOP_UNIT_KEY = re.compile(r'^loop_unit\s*:(.*)$')


class LoopUnitDecl(NamedTuple):
    """`loop_unit` come e' scritta nel blocco pointer di uno stream."""
    line: int           # riga della chiave
    readable: bool      # False per il frammento a meta' scrittura
    value: Any          # il valore come lo legge YAML (None se vuoto o null)
    inline_empty: bool  # niente dopo i due punti, commento a parte


def _indent(raw: str) -> int:
    return len(raw) - len(raw.lstrip())


def _is_stream_marker(raw: str) -> bool:
    stripped = raw.strip()
    return (stripped.startswith('- ') or stripped == '-') and _indent(raw) == 2


def stream_span(lines: List[str], line: int) -> Optional[Tuple[int, int]]:
    """Inizio e fine (esclusa) dello stream che contiene `line`, o None."""
    start = None
    for i in range(min(line, len(lines) - 1), -1, -1):
        if _is_stream_marker(lines[i]):
            start = i
            break
    if start is None:
        return None
    for i in range(start + 1, len(lines)):
        if _is_stream_marker(lines[i]):
            return start, i
    return start, len(lines)


def pointer_span(lines: List[str], stream_start: int,
                 stream_end: int) -> Optional[Tuple[int, int]]:
    """Riga di `pointer:` (indent 4) e fine (esclusa) del blocco, o None."""
    for i in range(stream_start, stream_end):
        raw = lines[i]
        if _indent(raw) == 4 and raw.strip().startswith('pointer:'):
            for j in range(i + 1, stream_end):
                if lines[j].strip() and _indent(lines[j]) <= 4:
                    return i, j
            return i, stream_end
    return None


def _read_value(lines: List[str], key_line: int, block_end: int,
                key_indent: int) -> Tuple[bool, Any]:
    """Il valore della chiave come lo legge YAML, inline o block-style."""
    frammento = [lines[key_line][key_indent:]]
    for n in range(key_line + 1, block_end):
        riga = lines[n]
        if riga.strip() and _indent(riga) <= key_indent:
            break
        frammento.append(riga[key_indent:])
    try:
        data = yaml.safe_load('\n'.join(frammento))
    except yaml.YAMLError:
        return False, None
    if not isinstance(data, dict) or 'loop_unit' not in data:
        return False, None
    return True, data['loop_unit']


def find_loop_unit(lines: List[str], line: int) -> Optional[LoopUnitDecl]:
    """La `loop_unit` dello stream che contiene `line`, o None se assente.

    Assente vuol dire `LOOP_UNIT_DEFAULT`: nessun'altra chiave dello stream
    viene consultata, `time_mode` compreso.
    """
    span = stream_span(lines, line)
    if span is None:
        return None
    pointer = pointer_span(lines, *span)
    if pointer is None:
        return None
    p_start, p_end = pointer
    for n in range(p_start + 1, p_end):
        raw = lines[n]
        if _indent(raw) != 6:
            continue
        m = _LOOP_UNIT_KEY.match(raw.strip())
        if not m:
            continue
        inline = m.group(1).strip()
        readable, value = _read_value(lines, n, p_end, 6)
        return LoopUnitDecl(
            line=n, readable=readable, value=value,
            inline_empty=not inline or inline.startswith('#'),
        )
    return None


def loop_unit_label(value: Any) -> str:
    """Il valore come si scrive in un messaggio: quello che YAML ci legge."""
    if value is None:
        return 'null'
    if isinstance(value, bool):
        return 'true' if value else 'false'
    return str(value)
