# granular_ls/range_unit.py
"""
Le chiavi `<param>_range_unit` (PGE #267): l'unita' del `_range` dichiarato.

Il `_range` e' per default una **quantita' assoluta**, nell'unita' del
parametro: `duration_range: 0.01` sono 0.01 secondi (o millisecondi, o
campioni, sotto `grain.duration_unit`). Con `<param>_range_unit: relative` il
numero diventa una **frazione del valore base**, letta istante per istante:

| unita' | `duration_range` e' | dominio | scalato da `duration_unit` |
|---|---|---|---|
| assente / `absolute` | una durata | `[min_range, max_range]` del padre | si' |
| `relative` | una frazione della base | `RELATIVE_RANGE_BOUNDS` | no |

Quale chiave governa quale `_range` lo dice il motore, non questo modulo:
`ParameterSpec.range_unit_path`, letto dal bridge (`get_range_unit_bindings`).
Anche il vocabolario viene dal motore (`RANGE_UNITS`, via
`get_range_units`). Qui resta solo quel che non e' un elenco: quale grafia
significa «frazione della base» (`range_unit_is_relative`), la documentazione
dei valori e la lettura della dichiarazione dal testo.

Due cose il motore le rifiuta al parse, e il language server le dice mentre si
scrive (`DiagnosticProvider._check_range_units`):

- una grafia fuori vocabolario, vuota compresa: `InvalidFieldValueError`. La
  chiave assente vale il default; quella scritta e lasciata vuota no — e' una
  riga che qualcuno ha scritto e che nessuno leggerebbe;
- `relative` senza il `_range` che governa: `MissingFieldError`. Non e' una
  chiave inerte: senza range dichiarato scatta il jitter implicito, che e'
  assoluto — esattamente cio' che si stava cercando di evitare.
"""

import re
from typing import Any, List, NamedTuple, Optional, Tuple

import yaml

# La grafia che dichiara la banda relativa. Il vocabolario arriva dal vivo, ma
# quale delle sue voci voglia dire «frazione della base» e' semantica: e' il
# mirror di `RANGE_UNIT_RELATIVE` e `range_unit_is_relative`, che
# `tests/test_pge_parity.py` confronta col motore.
RANGE_UNIT_RELATIVE = 'relative'


def is_relative(value: Any) -> bool:
    """True se la grafia dichiara una banda relativa.

    Mirror di `range_unit_is_relative`: lettura pura, che non valida il
    vocabolario. Una grafia sbagliata legge come non-relativa, e l'errore lo
    dice chi la valida.
    """
    return isinstance(value, str) and value == RANGE_UNIT_RELATIVE


# Documentazione dei valori: completion e hover la leggono da qui. Una grafia
# che il motore aggiunge e questo dizionario no resta completabile, con la
# sola etichetta: il vocabolario non si trascrive, la prosa si'.
RANGE_UNIT_VALUE_DOCS = {
    'absolute': (
        'Il `_range` e\' una **quantita\' assoluta**, nell\'unita\' della '
        'base (default).\n\n'
        'E\' la lettura storica: `duration_range: 0.01` sono 0.01 secondi, o '
        'millisecondi o campioni sotto `grain.duration_unit`, che lo scala '
        'insieme alla base.'
    ),
    'relative': (
        'Il `_range` e\' una **frazione del valore base**, letta istante per '
        'istante, in `[0, 1]`.\n\n'
        'Serve dove la base spazia su piu\' ordini di grandezza: la banda '
        'resta proporzionale lungo tutto l\'envelope. `duration_unit` non la '
        'scala — una frazione non ha unita\'. Richiede il `_range` '
        'esplicito.'
    ),
}


def fmt_bound(value: float) -> str:
    """Un estremo del dominio come si scrive: `1`, non `1.0`."""
    return f'{value:g}'


def range_unit_key_doc(unit_path: str, range_path: str, base_path: str,
                       units: List[str],
                       relative_bounds: Tuple[float, float]) -> str:
    """La documentazione della chiave, costruita dal suo legame.

    I path vengono dal bridge: la stessa prosa vale per il prossimo parametro
    che il motore cabla.
    """
    lo, hi = (fmt_bound(v) for v in relative_bounds)
    valori = '\n'.join(
        f'- `{u}`' + (' (default)' if i == 0 else '') + ': '
        + RANGE_UNIT_VALUE_DOCS.get(u, '').split('\n\n')[0]
        for i, u in enumerate(units)
    )
    return (
        f'**Meta-parametro: unita\' di `{range_path}`.**\n\n'
        f'Dice se il `_range` di `{base_path}` e\' una banda assoluta o una '
        f'frazione del valore base.\n\n'
        f'Valori accettati:\n{valori}\n\n'
        f'Con `relative` il dominio e\' `[{lo}, {hi}]`: `1` e\' una banda larga '
        'quanto la base. `range_anchor: center` la centra (±50% al massimo), '
        '`min` la apre sopra la base (`[base, 2·base]`).\n\n'
        f'`relative` senza `{range_path}` e\' un errore del motore '
        '(`MissingFieldError`): varrebbe il jitter implicito, che e\' '
        'assoluto. Una grafia fuori vocabolario, o la chiave lasciata vuota, '
        'e\' un `InvalidFieldValueError`.\n\n'
        '> **Non e\' un parametro sintetizzabile.** Non accetta envelope o '
        'range.'
    )


# =============================================================================
# LETTURA DAL TESTO
# =============================================================================
# Stesse convenzioni di indentazione del resto del language server: stream
# come elementi `- ` a indent 2, chiavi di stream e blocchi a indent 4, chiavi
# dei blocchi a 6. La prima chiave di uno stream puo' stare sulla riga del
# trattino.

def split_path(path: str) -> Tuple[Optional[str], str]:
    """`grain.duration_range_unit` -> (`grain`, `duration_range_unit`).

    Un path senza punto e' una chiave di stream: blocco None.
    """
    if '.' in path:
        block, key = path.split('.', 1)
        return block, key
    return None, path


class KeyDecl(NamedTuple):
    """Una chiave come e' scritta nello stream."""
    line: int           # riga della chiave
    end: int            # fine (esclusa) del suo valore block-style
    readable: bool      # False per il frammento a meta' scrittura
    value: Any          # il valore come lo legge YAML (None se vuoto o null)
    inline_empty: bool  # niente dopo i due punti, commento a parte


def _indent(raw: str) -> int:
    return len(raw) - len(raw.lstrip())


def _key_indent(raw: str, n: int, stream_start: int) -> int:
    """Dove comincia la chiave: dopo il trattino sulla riga dello stream."""
    if n == stream_start:
        trattino = re.match(r'^\s*-\s+', raw)
        if trattino:
            return trattino.end()
    return _indent(raw)


def _block_span(lines: List[str], stream_start: int, stream_end: int,
                block: str) -> Optional[Tuple[int, int]]:
    """Interno (inizio, fine esclusa) del blocco `block:` a indent 4."""
    for n in range(stream_start, stream_end):
        raw = lines[n]
        if (_key_indent(raw, n, stream_start) == 4
                and re.match(re.escape(block) + r'\s*:\s*(#.*)?$',
                             raw[4:].rstrip())):
            for j in range(n + 1, stream_end):
                if lines[j].strip() and _indent(lines[j]) <= 4:
                    return n + 1, j
            return n + 1, stream_end
    return None


def find_key(lines: List[str], stream_start: int, stream_end: int,
             path: str) -> Optional[KeyDecl]:
    """La chiave `path` dello stream `[stream_start, stream_end)`, o None.

    Il valore si legge via YAML, come lo legge il motore: `"relative"  # nota`
    e' `relative`, `null` e' None. Il path segue la notazione di
    `ParameterSpec`: `grain.duration_range_unit` sta nel blocco grain,
    `volume_range_unit` sarebbe una chiave di stream.
    """
    block, key = split_path(path)
    if block is None:
        span, indent = (stream_start, stream_end), 4
    else:
        span, indent = _block_span(lines, stream_start, stream_end, block), 6
        if span is None:
            return None
    pattern = re.compile(re.escape(key) + r'\s*:(.*)$')
    start, end = span
    for n in range(start, end):
        raw = lines[n]
        k_indent = _key_indent(raw, n, stream_start)
        if k_indent != indent:
            continue
        m = pattern.match(raw[k_indent:].rstrip())
        if not m:
            continue
        frammento = [raw[k_indent:]]
        fine = n + 1
        while fine < end:
            riga = lines[fine]
            if riga.strip() and _indent(riga) <= k_indent:
                break
            frammento.append(riga[k_indent:])
            fine += 1
        inline = m.group(1).strip()
        try:
            data = yaml.safe_load('\n'.join(frammento))
        except yaml.YAMLError:
            data = None
        readable = isinstance(data, dict) and key in data
        return KeyDecl(
            line=n, end=fine, readable=readable,
            value=data[key] if readable else None,
            inline_empty=not inline or inline.startswith('#'),
        )
    return None


def value_label(value: Any) -> str:
    """Il valore come si scrive in un messaggio: quello che YAML ci legge.

    Una stringa vuota, o con spazi ai bordi, va fra virgolette: il messaggio
    la mette fra backtick, e senza virgolette non si vedrebbe su cosa cade
    l'errore.
    """
    if value is None:
        return 'null'
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, str) and (not value or value != value.strip()):
        return '"' + value + '"'
    return str(value)
