# granular_ls/maps.py
"""Le mappe caotiche di uno stream (PGE #307): `maps:`, le espressioni del
motore che le leggono, e i due modi nuovi che una mappa puo' muovere —
`grain.iot` (#311) e `pointer.position` (#312).

Mirror delle regole che il motore applica al caricamento:

- `pge/maps/catalog.py` (`MAP_TYPES`, `MAP_KINDS`) e `pge/maps/map_set.py`
  (`MAP_KEYS`, i tetti, i nomi riservati, `_definizione`): ogni violazione e'
  un `MapConfigError`;
- `pge/core/stream.py` (`_wrap_block_expressions`, `_written_as_expression`)
  e `pge/parameters/parser.py`: dove un'espressione vale, dove si rifiuta,
  e cosa succede a una senza le parentesi esterne;
- `DensityController.check_iot_exclusivity` e
  `PointerController._check_position_exclusivity`: i conflitti per presenza.

`check_stream` lavora sul valore Python dello stream (come lo costruisce
PyYAML) e restituisce dei `MapIssue` col path YAML del nodo da segnalare: chi
li riceve (la fase 22 della diagnostica) ne trova la riga sull'albero.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from granular_ls import engine_expressions as EX

# ---------------------------------------------------------------------------
# Il catalogo

#: `pge.maps.catalog.MAP_TYPES`, piu' `custom`.
MAP_TYPES = ('logistic', 'tent', 'sine', 'cubic', 'henon', 'circle')
MAP_TYPE_CUSTOM = 'custom'
MAP_TYPE_VALUES = MAP_TYPES + (MAP_TYPE_CUSTOM,)

#: `pge.maps.catalog.SIN_BASED_TYPES`: fra macchine i grani possono divergere.
SIN_BASED_TYPES = ('sine', 'circle')

#: `pge.maps.map_set.MAP_KEYS`.
MAP_KEYS = ('type', 'r', 'clock', 'iterate', 'x0', 'transient', 'y0', 'x', 'y')

MAP_CLOCK_GRAIN = 'grain'
MAP_ITERATE_MAX = 40
MAP_TRANSIENT_MAX = 100_000
MAP_TRANSIENT_DEFAULT = 300
MAP_CUSTOM_X0 = 0.1
MAP_CUSTOM_Y0 = 0.1

#: Il tetto del clock: un passo per campione al sample rate di default.
OUTPUT_SR = 48000

_NOME = re.compile(r'[A-Za-z_][A-Za-z_0-9]*\Z')


@dataclass(frozen=True)
class MapKindInfo:
    """Cio' che `MAP_KINDS` dichiara di un tipo."""
    x0: float
    r_range: Tuple[float, float]
    value_range: Tuple[float, float]
    variables: int = 1
    y0: float = 0.0
    y_range: Optional[Tuple[float, float]] = None


MAP_KINDS: Dict[str, MapKindInfo] = {
    'logistic': MapKindInfo(0.3, (2.5, 4.0), (0.0, 1.0)),
    'tent': MapKindInfo(0.3, (2.5, 3.999), (0.0, 1.0)),
    'sine': MapKindInfo(0.1, (0.0, 4.0), (-1.0, 1.0)),
    'cubic': MapKindInfo(0.3, (1.5, 3.0), (-1.2, 1.2)),
    'henon': MapKindInfo(0.1, (0.8, 1.4), (-1.3, 1.4), variables=2, y0=0.1,
                         y_range=(-0.39, 0.42)),
    'circle': MapKindInfo(0.2, (0.0, 1.0), (0.0, 1.0)),
}


def reserved_map_names() -> frozenset:
    """`RESERVED_MAP_NAMES`: funzioni, costanti, `t`, e `x`/`y`/`r`."""
    return (frozenset(EX.FUNCTIONS) | frozenset(EX.CONSTANTS)
            | frozenset({EX.TIME_NAME}) | frozenset(EX.MAP_EQUATION_VARIABLES))


# ---------------------------------------------------------------------------
# I modi esclusivi

#: `pge.controllers.density_controller.IOT_EXCLUSIVE_KEYS`.
IOT_EXCLUSIVE_KEYS = ('density', 'fill_factor', 'distribution')
#: `pge.controllers.pointer_controller.POSITION_EXCLUSIVE_KEYS`.
POSITION_EXCLUSIVE_KEYS = ('speed_ratio', 'start')

#: Le chiavi di uno stream che sono testo, dove la regola delle parentesi
#: esterne non si applica (`STREAM_TEXT_KEYS`), e quelle di testa che il
#: motore legge (`ENGINE_TOP_LEVEL_KEYS`).
STREAM_TEXT_KEYS = ('stream_id', 'sample')
ENGINE_TOP_LEVEL_KEYS = ('seed',)


# ---------------------------------------------------------------------------
# Documentazione condivisa da hover e completion

MAPS_DOC = (
    "**Mappe iterate dello stream** (PGE #308, #310, #313): `nome -> mappa`. "
    "Un parametro la legge con un'**espressione** fra parentesi, valutata a "
    "ogni grano: `grain.duration: (0.02 + 0.08*m1)`. Le espressioni valgono "
    "sul valore base di " + ", ".join(EX.EXPRESSION_FIELDS) + "; leggono le "
    "mappe, `<nome>.y` per quelle a due variabili, e `t` (secondi "
    "dall'inizio dello stream)."
)

MAP_KEY_DOCS = {
    'type': ("Tipo della mappa: " + ", ".join(MAP_TYPE_VALUES) + ". "
             "`custom` scrive l'equazione in `x:` (e `y:`)."),
    'r': ("Parametro della mappa: numero o envelope (sui tempi dello stream, "
          "segue `time_mode`). Obbligatorio, tranne con una `custom` la cui "
          "equazione non lo legge."),
    'clock': ("Quando la mappa avanza: `grain` (default, un passo per grano) | "
              "passi al secondo, un numero o un envelope positivo e finito, al "
              f"piu' {OUTPUT_SR} (un passo per campione). Esclusivo con "
              "`iterate`."),
    'iterate': (f"Intero da 1 a {MAP_ITERATE_MAX}: a ogni lettura la mappa "
                "riparte da `x0` e fa tanti passi. Al posto di `clock`, e "
                "senza `transient`."),
    'x0': "Stato iniziale (default del tipo; 0.1 per `custom`).",
    'transient': (f"Passi scartati prima del primo grano: intero da 0 a "
                  f"{MAP_TRANSIENT_MAX} (default {MAP_TRANSIENT_DEFAULT}). "
                  "Non con `iterate`."),
    'y0': ("Stato iniziale della seconda variabile: solo per una mappa che ne "
           "ha due (`henon`, una `custom` con `y:`)."),
    'x': ("Equazione di una mappa `custom`, nel linguaggio delle espressioni "
          "con le variabili `x`, `y`, `r`, senza parentesi esterne "
          "(`sin(r*x)*cos(x)`)."),
    'y': ("Equazione della seconda variabile di una `custom` (`0.3*x`): la "
          "mappa si legge anche come `<nome>.y`."),
}


def map_kind_doc(name: str) -> Optional[str]:
    """Una riga per un tipo del catalogo, o per `custom`."""
    if name == MAP_TYPE_CUSTOM:
        return ("**custom** — l'equazione si scrive nello YAML: `x:` "
                "(obbligatoria) e `y:` (facoltativa), con le variabili `x`, "
                f"`y`, `r`. `x0`/`y0` di default {MAP_CUSTOM_X0:g}; `r` serve "
                "solo se un'equazione lo legge. L'intervallo del valore "
                "dipende dall'equazione.")
    k = MAP_KINDS.get(name)
    if k is None:
        return None
    lo, hi = k.value_range
    rlo, rhi = k.r_range
    text = (f"**{name}** — valore in [{lo:g}, {hi:g}], `r` in "
            f"[{rlo:g}, {rhi:g}], `x0` {k.x0:g}.")
    if k.variables == 2 and k.y_range:
        text += (f" Due variabili: la seconda si legge come `<nome>.y`, in "
                 f"[{k.y_range[0]:g}, {k.y_range[1]:g}], `y0` {k.y0:g}.")
    if name in SIN_BASED_TYPES:
        text += " Passa da `sin`: fra due macchine i grani possono divergere."
    return text


# ---------------------------------------------------------------------------
# Le regole

@dataclass(frozen=True)
class MapIssue:
    """Un problema in uno stream: il path del nodo, il messaggio, Error o
    no."""
    path: Tuple[Any, ...]
    message: str
    error: bool = True


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _finite(v) -> bool:
    return _num(v) and math.isfinite(v)


def _int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def two_variables(spec) -> bool:
    if not isinstance(spec, dict):
        return False
    tipo = spec.get('type')
    if tipo == MAP_TYPE_CUSTOM:
        return spec.get('y') is not None
    kind = MAP_KINDS.get(tipo) if isinstance(tipo, str) else None
    return kind is not None and kind.variables == 2


def stream_variables(stream) -> Tuple[str, ...]:
    """`MapSet.variables`: le mappe, `<nome>.y` per quelle a due variabili,
    e `t`."""
    names: List[str] = []
    maps = stream.get('maps') if isinstance(stream, dict) else None
    if isinstance(maps, dict):
        for name, spec in maps.items():
            if isinstance(name, str):
                names.append(name)
                if two_variables(spec):
                    names.append(f'{name}.y')
    names.append(EX.TIME_NAME)
    return tuple(names)


def _ys(v) -> Optional[List[float]]:
    pts = v.get('points') if isinstance(v, dict) else v
    if not isinstance(pts, list):
        return None
    return [p[1] for p in pts
            if isinstance(p, (list, tuple)) and len(p) in (2, 3) and _num(p[1])]


def _check_map(name: str, spec, out: List[MapIssue]) -> None:
    base = ('maps', name)
    campo = f'maps.{name}'
    if not isinstance(spec, dict):
        out.append(MapIssue(base, f"`{campo}`: una mappa e' un dizionario "
                                  "(type, r, ...)."))
        return
    for key in spec:
        if key not in MAP_KEYS:
            out.append(MapIssue(base + (key,), f"`{campo}.{key}`: chiave "
                                f"sconosciuta (chiavi di una mappa: "
                                f"{', '.join(MAP_KEYS)})."))
    tipo = spec.get('type')
    if tipo is None:
        out.append(MapIssue(base, f"`{campo}`: manca il tipo della mappa (tipi: "
                                  f"{', '.join(MAP_TYPE_VALUES)})."))
        return
    reads_r, two = True, False
    if tipo == MAP_TYPE_CUSTOM:
        reads_r, two = _check_custom(base, campo, spec, out)
    elif not isinstance(tipo, str) or tipo not in MAP_KINDS:
        out.append(MapIssue(base + ('type',), f"`{campo}.type`: tipo "
                            f"sconosciuto `{tipo}` (tipi: "
                            f"{', '.join(MAP_TYPE_VALUES)})."))
        return
    else:
        two = MAP_KINDS[tipo].variables == 2
        for key in ('x', 'y'):
            if key in spec:
                out.append(MapIssue(base + (key,), f"`{campo}.{key}`: si "
                                    f"scrive solo con `type: custom` — {tipo} "
                                    "ha gia' la sua equazione, e non si "
                                    "capirebbe quale delle due vale."))
    r = spec.get('r')
    if r is None:
        if reads_r:
            kind = MAP_KINDS.get(tipo)
            hint = (f" Per {tipo}: da {kind.r_range[0]:g} a {kind.r_range[1]:g}."
                    if kind else " L'equazione lo legge: un numero o un envelope.")
            out.append(MapIssue(base, f"`{campo}`: manca `r`, il parametro "
                                      f"della mappa.{hint}"))
    elif isinstance(r, (list, dict)):
        if any(not math.isfinite(y) for y in (_ys(r) or [])):
            out.append(MapIssue(base + ('r',), f"`{campo}.r`: atteso un "
                                "envelope di numeri finiti."))
    elif not _finite(r):
        out.append(MapIssue(base + ('r',), f"`{campo}.r`: atteso un numero "
                            "finito o un envelope."))
    for key in ('x0', 'y0'):
        if key in spec and not _finite(spec.get(key)):
            out.append(MapIssue(base + (key,), f"`{campo}.{key}`: atteso un "
                                "numero finito."))
    if 'y0' in spec and not two:
        out.append(MapIssue(base + ('y0',), f"`{campo}.y0`: la mappa ha una "
                            "variabile sola, y0 non entra nel passo — lo stato "
                            "iniziale e' x0."))
    if 'iterate' in spec:
        if 'clock' in spec:
            out.append(MapIssue(base + ('iterate',), f"`{campo}`: `clock` e "
                                "`iterate` insieme — una mappa o avanza con un "
                                "clock, o si rilegge da x0 a ogni lettura. "
                                "Tieni una delle due chiavi."))
        n = spec.get('iterate')
        if not _int(n) or not 1 <= n <= MAP_ITERATE_MAX:
            out.append(MapIssue(base + ('iterate',), f"`{campo}.iterate`: "
                                f"atteso un intero da 1 a {MAP_ITERATE_MAX} "
                                "(i passi fatti da x0 a ogni lettura)."))
        if 'transient' in spec:
            out.append(MapIssue(base + ('transient',), f"`{campo}.transient`: "
                                "con `iterate` non c'e' un transitorio, a ogni "
                                "lettura si riparte da x0."))
        return
    if 'clock' in spec:
        c = spec.get('clock')
        if c != MAP_CLOCK_GRAIN:
            valori = (_ys(c) or []) if isinstance(c, (list, dict)) else [c]
            if any(not (_finite(v) and 0 < v <= OUTPUT_SR) for v in valori):
                out.append(MapIssue(base + ('clock',), f"`{campo}.clock`: "
                                    f"`{MAP_CLOCK_GRAIN}` (un passo per grano), "
                                    "oppure i passi al secondo — un numero "
                                    "positivo e finito, al piu' "
                                    f"{OUTPUT_SR} (uno per campione), in ogni "
                                    "punto se e' un envelope."))
    if 'transient' in spec:
        tr = spec.get('transient')
        if not _int(tr) or not 0 <= tr <= MAP_TRANSIENT_MAX:
            out.append(MapIssue(base + ('transient',), f"`{campo}.transient`: "
                                f"atteso un intero da 0 a {MAP_TRANSIENT_MAX} "
                                "(i passi scartati prima del primo grano)."))


def _check_custom(base, campo, spec, out) -> Tuple[bool, bool]:
    if spec.get('x') is None:
        out.append(MapIssue(base, f"`{campo}`: manca `x`, l'equazione della "
                                  "mappa (con `type: custom` il passo si "
                                  "scrive: `x: sin(r*x)*cos(x)`; variabili x, "
                                  "y, r)."))
        return False, False
    letti = set()
    fx = _equation(base + ('x',), f'{campo}.x', spec.get('x'), out)
    if fx is not None:
        letti |= set(fx)
    two = spec.get('y') is not None
    if 'y' in spec and not two:
        out.append(MapIssue(base + ('y',), f"`{campo}.y`: e' scritta ma vuota — "
                                           "scrivi l'equazione della seconda "
                                           "variabile, o togli la chiave."))
    if two:
        fy = _equation(base + ('y',), f'{campo}.y', spec.get('y'), out)
        if fy is not None:
            letti |= set(fy)
    elif fx is not None and 'y' in fx:
        out.append(MapIssue(base + ('x',), f"`{campo}.x`: l'equazione legge y, "
                                           "ma la mappa non ha una seconda "
                                           "variabile — aggiungi `y:` (es. "
                                           "`y: 0.3*x`), o togli y da x:."))
    return 'r' in letti, two


def _equation(path, campo, v, out) -> Optional[Tuple[str, ...]]:
    if _num(v):
        return ()
    if not isinstance(v, str):
        out.append(MapIssue(path, f"`{campo}`: un'equazione e' un testo, nel "
                                  "linguaggio delle espressioni, con le "
                                  "variabili x, y, r."))
        return None
    c = EX.compile_expression(v, EX.MAP_EQUATION_VARIABLES)
    if not c.ok:
        hint = f" {c.problem.hint}." if c.problem.hint else ""
        out.append(MapIssue(path, f"`{campo}`: equazione non valida, "
                                  f"{c.problem.reason}.{hint}"))
        return None
    return c.names


def _check_maps(stream: dict, out: List[MapIssue]) -> None:
    if 'maps' not in stream:
        return
    raw = stream.get('maps')
    if not isinstance(raw, dict):
        out.append(MapIssue(('maps',), "`maps` e' un dizionario: nome della "
                                       "mappa -> mappa (es. `maps: {m1: "
                                       "{type: logistic, r: 3.5}}`)."))
        return
    if not raw:
        out.append(MapIssue(('maps',), "`maps`: nessuna mappa dichiarata; se "
                                       "non servono, togli la chiave."))
        return
    reserved = reserved_map_names()
    for name, spec in raw.items():
        if not isinstance(name, str) or not _NOME.match(name):
            out.append(MapIssue(('maps', name), f"`maps.{name}`: il nome di una "
                                "mappa e' fatto di lettere, cifre e `_`, e non "
                                "comincia con una cifra."))
            continue
        if name in reserved:
            out.append(MapIssue(('maps', name), f"`maps.{name}`: nome riservato "
                                "— in un'espressione vuol dire gia' un'altra "
                                f"cosa (riservati: {', '.join(sorted(reserved))})."))
            continue
        _check_map(name, spec, out)


def _strings(node, prefix=()):
    """`(dotted, path, testo)` delle stringhe nei campi di uno stream (non
    dentro le liste: un breakpoint non e' un campo)."""
    if not isinstance(node, dict):
        return
    for k, v in node.items():
        if not isinstance(k, str):
            continue
        if not prefix and k in ('maps',) + STREAM_TEXT_KEYS:
            continue
        path = prefix + (k,)
        if isinstance(v, str):
            yield '.'.join(path), path, v
        elif isinstance(v, dict):
            yield from _strings(v, path)


def _check_expressions(stream: dict, out: List[MapIssue]) -> None:
    variables = stream_variables(stream)
    declared = frozenset(n for n in variables if '.' not in n)
    for dotted, path, text in _strings(stream):
        if dotted in EX.EXPRESSION_FIELDS:
            if EX.outer_parenthesized(text):
                if EX.load_time_constant(text):
                    continue
                c = EX.compile_expression(text, variables)
                if not c.ok:
                    hint = f" {c.problem.hint}." if c.problem.hint else ""
                    out.append(MapIssue(path, f"Espressione non valida per "
                                              f"`{dotted}`: {c.problem.reason}."
                                              f"{hint}"))
                continue
            # Senza parentesi esterne la stringa passa prima dalla
            # valutazione al caricamento: `-(90/2)` diventa un numero, e
            # `(10/2)/10` e' l'errore della meta' valutata, gia' detto.
            if (_num(EX.load_time_value(text))
                    or EX.half_evaluated_proposal(text) is not None):
                continue
            if EX.compile_expression(text, variables).ok:
                out.append(MapIssue(path, f"`{dotted}`: un'espressione si "
                                          f"scrive fra parentesi — es. "
                                          f"`({text.strip()})`."))
            continue
        if not EX.outer_parenthesized(text):
            continue
        if not (EX.names_in(text) & declared) and EX.load_time_constant(text):
            continue
        if dotted == 'deviation_probability' or dotted.startswith(
                'deviation_probability.'):
            vuole = "una probabilita' (0-100) o un envelope"
        else:
            vuole = "un numero o un envelope"
        hint = EX.EXPRESSION_REFUSALS.get(dotted, EX.GENERIC_REFUSAL)
        out.append(MapIssue(path, f"`{dotted}`: qui un'espressione non vale, "
                                  f"il campo vuole {vuole} — {hint}."))


def _check_modes(stream: dict, out: List[MapIssue]) -> None:
    grain = stream.get('grain')
    if isinstance(grain, dict) and 'iot' in grain:
        conflitti = [k for k in IOT_EXCLUSIVE_KEYS if k in stream]
        if conflitti:
            out.append(MapIssue(('grain', 'iot'), "`grain.iot` e' l'attesa "
                                "fino al grano successivo: con lei la densita' "
                                "non si decide, emerge dagli onset, e non puo' "
                                f"stare insieme a {', '.join(conflitti)}. E' un "
                                "errore del motore, non una priorita': tieni "
                                "`grain.iot`, oppure density / fill_factor (con "
                                "distribution)."))
    pointer = stream.get('pointer')
    if isinstance(pointer, dict) and 'position' in pointer:
        conflitti = [k for k in POSITION_EXCLUSIVE_KEYS if k in pointer]
        if conflitti:
            out.append(MapIssue(('pointer', 'position'), "`pointer.position` "
                                "e' la posizione di lettura nella finestra "
                                "attiva, da 0 a 1: prende il posto della legge "
                                "di moto, e non puo' stare insieme a "
                                f"{', '.join('pointer.' + k for k in conflitti)}."))
        pos = pointer.get('position')
        valori = [pos] if _num(pos) else (_ys(pos) or [])
        if any(_num(v) and not math.isfinite(v) for v in valori):
            out.append(MapIssue(('pointer', 'position'), "`pointer.position` "
                                "rientra a capo della finestra, ma un `.nan` o "
                                "un `.inf` e' un errore di bounds del motore."))


def _check_half_evaluated(node, path, out) -> None:
    if isinstance(node, dict):
        for k, v in node.items():
            if not path and k in ('maps',) + STREAM_TEXT_KEYS:
                continue
            _check_half_evaluated(v, path + (k,), out)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            _check_half_evaluated(v, path + (i,), out)
    elif isinstance(node, str):
        proposta = EX.half_evaluated_proposal(node)
        if proposta is not None:
            out.append(MapIssue(path, half_evaluated_message(node, proposta)))


def half_evaluated_message(text: str, proposta: str) -> str:
    return (f"`{text}`: l'espressione sta per meta' fuori dalle parentesi "
            "esterne, e il caricamento ne valuterebbe solo un pezzo. E' un "
            f"errore del motore: scrivila tutta fra parentesi, `{proposta}`.")


def check_stream(stream) -> List[MapIssue]:
    """Tutti i problemi di `maps:`, delle espressioni e dei modi esclusivi di
    uno stream."""
    out: List[MapIssue] = []
    if not isinstance(stream, dict):
        return out
    _check_maps(stream, out)
    _check_modes(stream, out)
    _check_expressions(stream, out)
    _check_half_evaluated(stream, (), out)
    return out


def check_top_level(document) -> List[MapIssue]:
    """La regola delle parentesi esterne fuori dagli stream: solo sulle
    chiavi di testa che il motore legge (`seed`)."""
    out: List[MapIssue] = []
    if not isinstance(document, dict):
        return out
    for key in ENGINE_TOP_LEVEL_KEYS:
        if key in document:
            _check_half_evaluated(document[key], (key,), out)
    return out
