# granular_ls/envelope_guards.py
"""
Lo specchio unico dei guard di forma di un envelope (PGE #211, #288).

Da PGE #211 i guard sulla forma di un envelope non sono piu' di
`grain.read_direction`: vivono in `EnvelopeBuilder` e li attraversa **ogni**
chiave che il motore costruisce come envelope — `density`, `grain.duration`,
`pointer.*`, il valore del blocco pitch, `voices.*` e i kwarg delle strategy,
la `curve` di una finestra. PGE #288 (PR #299) ha aggiunto quelli che stanno
prima del builder, in `Envelope.__init__`: la forma dict, il corpo vuoto, il
tipo dell'envelope. Sotto quelle chiavi lo stesso corpo era un `ValueError`
nudo o un render silenzioso; ora e' un errore di render che nomina la chiave,
e un language server che tace su di lui e' un falso verde.

Questo modulo e' il mirror di quella strada, nell'ordine in cui il motore la
percorre, e si ferma al primo problema come lui:

1. **forma dict** (`Envelope.__init__`): `points` assente o non percorribile
   (null, un numero) → `points`; `type` che la factory non legge → `type`.
   La factory ignora maiuscole e spazi attorno, quindi `type: Step` rende; un
   nome ignoto, `''`, gli spazi soli e `null` no;
2. **forma lista** (`EnvelopeBuilder.extract_interp_type`): lo slot interp del
   compatto che diventa il tipo dell'envelope — quello del compatto diretto, o
   del primo compatto della lista che ne dichiara uno — se la factory non lo
   legge → `compact.interp`. Scritto vuoto vale il default. Lo slot di un
   compatto che l'envelope non legge (dentro un dict, o dopo il primo che lo
   dichiara) non si valida: corretto, resterebbe ignorato;
3. **corpo vuoto** → `points`;
4. **il builder** (`EnvelopeBuilder.parse`): compatto (`n_reps`, `end_time`
   numero finito oltre l'istante in cui il blocco comincia, pattern non vuoto,
   punti piatti con x e y numeri e x in [0, 100] non decrescente,
   distribuzione temporale), BP group (interp, almeno 2 punti), elemento non
   riconosciuto → `point`;
5. **il tipo per punto** (`Envelope._parse_segments`): un `[t, v, type]` con
   un type fuori da `linear | cubic | step` → `point.type`. Qui il confronto e'
   alla lettera, come nel motore: `'Step'` per punto non rende.

Le sotto-posizioni sono quelle che il motore usa come campo di ripiego
(`envelope.<where>`), quando il chiamante non gli passa la chiave; dove la
passa, il campo e' la chiave YAML. La parita' le confronta una per una.

Restano fuori, come nei mirror dedicati:

- ogni corpo dove compare un'espressione fra parentesi: il `Generator` la
  valuta prima del parser, e il suo esito non si prevede;
- `grain.read_direction` e `deviation_probability`, che hanno un mirror
  proprio con la loro semantica davanti (`read_direction.py`,
  `deviation_probability.py`).

**L'istante di partenza di un compatto** non sempre si conosce esattamente,
e allora se ne tiene un intervallo `[lo, hi]`. Dopo breakpoint e BP group e'
il massimo dei loro tempi, esatto anche sotto `time_mode: normalized` perche'
la scala e' monotona. Dopo un compatto e' il suo ultimo breakpoint espanso:
per la distribuzione `linear` si calcola con le stesse operazioni del motore —
esatto in tempi assoluti, con una tolleranza relativa sotto la scala, che
somma i suoi arrotondamenti — e per le altre distribuzioni sta fra l'istante
precedente e la fine del blocco. Un `end_time` sotto `lo` e' un rifiuto certo,
sopra `hi` un'accettazione certa; in mezzo il mirror non sa quale guard il
motore incontrerebbe per primo, e tace sull'intero corpo invece di indovinare.
"""

import math
from dataclasses import dataclass
from typing import Any, Optional, Tuple

from granular_ls.envelope_shapes import (
    contains_math_expression,
    is_bp_group,
    is_bp_group_candidate,
    is_envelope_like,
    is_loop_block,
    is_num,
    normalize_engine_values,
)
from granular_ls.time_distributions import (
    TimeDistIssue,
    check_time_distribution,
    describe_issue,
)

# I nomi che `InterpolationStrategyFactory` costruisce: le chiavi del suo
# registro, che sono anche `EnvelopeBuilder.VALID_INTERP_TYPES`.
INTERP_TYPES: Tuple[str, ...] = ('linear', 'cubic', 'step')

# `DEFAULT_INTERP` del builder: il tipo quando nessuno ne dichiara uno.
DEFAULT_INTERP = 'linear'

# `EnvelopeBuilder.DISCONTINUITY_OFFSET`.
DISCONTINUITY_OFFSET = 0.000001

# Gli slot del formato compatto (`EnvelopeBuilder.COMPACT_*`).
_PATTERN, _END_TIME, _N_REPS, _INTERP, _TIME_DIST, _WRAP = range(6)


@dataclass(frozen=True)
class ShapeIssue:
    """Il primo problema di forma di un corpo envelope.

    Attributes:
        where: la sotto-posizione dentro l'envelope, quella che il motore
            nomina come `envelope.<where>` quando non conosce la chiave:
            `points`, `type`, `compact.interp`, `compact.n_reps`,
            `compact.end_time`, `compact.pattern`, `compact.time_dist`,
            `group.interp`, `group.points`, `point`, `point.type`. Per il
            kwarg di una voice strategy che non e' un envelope, `kwarg`.
        value: il valore che il motore riporta nell'errore.
        hint: il perche', scritto per l'editor.
        path: dove sta nel corpo il nodo su cui ancorare la diagnostica —
            chiavi nei dict, indici nelle liste, come `envelope_y_paths`.
            Vuoto per il corpo intero.
        time_dist: per `compact.time_dist`, il `TimeDistIssue` da cui viene
            l'hint (nome, parametri, o la coppia che trabocca).
    """
    where: str
    value: Any
    hint: str
    path: tuple = ()
    time_dist: Optional[TimeDistIssue] = None


# =============================================================================
# HINT — i messaggi del motore riscritti per l'editor
# =============================================================================

DICT_POINTS_HINT = (
    "un envelope in forma dict porta i suoi breakpoint sotto la chiave "
    "`points` — una lista di breakpoint, un formato compatto o un BP group — "
    "e `type` ne è l'interpolazione: {type: cubic, points: [[0, 0], [1, 1]]}. "
    "Qui `points` manca, o non contiene breakpoint (null, un numero)."
)

TYPE_HINT = (
    "il `type` di un envelope in forma dict è l'interpolazione dei segmenti "
    "che non ne dichiarano una propria: linear, cubic, step. Omesso, vale "
    "`linear`; scritto e lasciato vuoto non vale niente."
)

COMPACT_INTERP_HINT = (
    "il quarto elemento del formato compatto è un'interpolazione (linear, "
    "cubic, step), e l'envelope la prende come proprio tipo: nella forma "
    "diretta, o dal primo compatto di una lista che la dichiara. Omesso o "
    "null, il tipo resta `linear`."
)

EMPTY_HINT = (
    "un envelope ha almeno un breakpoint: senza punti non c'è nessun valore "
    "da leggere nel tempo. Un valore costante si scrive come numero, non come "
    "lista vuota."
)

ELEMENT_HINT = (
    "un elemento di un envelope è un breakpoint [t, v] o [t, v, type] con t e "
    "v numeri (anche nella forma {t, v, type}), un BP group [[punti], interp] "
    "o un formato compatto [pattern, end_time, n_reps, ...]."
)

GROUP_POINTS_HINT = (
    "BP group con punti malformati: ogni punto deve essere [t, v] o "
    "[t, v, type] con t, v numerici. Così com'è non è un BP group, e il "
    "motore lo rifiuta come elemento dell'envelope."
)

GROUP_INTERP_HINT = (
    "l'interpolazione di un BP group è una di: linear, cubic, step — scritta "
    "così, minuscola."
)

GROUP_ARITY_HINT = (
    "un BP group richiede almeno 2 punti: con meno non ha segmenti interni, "
    "quindi non c'è nessuna zona a cui applicare l'interpolazione del gruppo. "
    "Un punto isolato si scrive come breakpoint nudo [t, v]."
)

N_REPS_HINT = (
    "il terzo elemento del formato compatto è il numero di ripetizioni del "
    "pattern: un intero >= 1 (`true` non è `1`). Con zero o meno cicli non "
    "c'è nessun breakpoint da generare."
)

END_TIME_TYPE_HINT = (
    "il secondo elemento del formato compatto è l'istante assoluto in cui il "
    "blocco finisce: un numero finito (`true` non è `1`, `.inf` e `.nan` non "
    "sono istanti)."
)

END_TIME_OFFSET_HINT = (
    "il secondo elemento del formato compatto è l'istante assoluto in cui il "
    "blocco finisce, non la sua durata, e deve superare quello in cui "
    "comincia: qui {inizio}. Nella forma diretta il blocco comincia a 0; in "
    "una lista mista comincia dall'ultimo breakpoint scritto prima di lui."
)

PATTERN_EMPTY_HINT = (
    "il primo elemento del formato compatto è il pattern del ciclo, e non può "
    "essere vuoto: senza punti non c'è niente da ripetere."
)

PATTERN_POINT_HINT = (
    "un punto del pattern del formato compatto è piatto: [x%, y] o "
    "[x%, y, type], con x e y numeri (`true` non è `1`) e type il nome di "
    "un'interpolazione. Le macro-forme (BP group, formato compatto) non si "
    "annidano dentro un pattern."
)

PATTERN_X_HINT = (
    "la prima coordinata di un punto del pattern è una percentuale del ciclo "
    "e sta in [0, 100]. Fuori da lì il ciclo sfonda i propri confini: sopra "
    "100 il ciclo successivo comincia prima che questo sia finito, sotto 0 "
    "esce un breakpoint a tempo negativo."
)

PATTERN_ORDER_HINT = (
    "le percentuali del pattern non possono tornare indietro: il ciclo si "
    "percorre in avanti una volta sola. Una percentuale ripetuta invece va "
    "bene: è la discontinuità."
)

POINT_TYPE_HINT = (
    "l'interpolazione di un breakpoint per punto è una di: linear, cubic, "
    "step — scritta così, minuscola."
)

KWARG_HINT = (
    "il parametro di una voice strategy è un numero, un nome o un envelope "
    "(lista di breakpoint, dict {points: ...}, BP group, formato compatto). "
    "Una lista o un dict che non è un envelope il motore lo rifiuta al parse."
)


# =============================================================================
# REGOLE — i predicati del motore che `envelope_shapes` non ha
# =============================================================================

def factory_reads(name: Any) -> bool:
    """`InterpolationStrategyFactory.reads` su un nome: stringa che, tolti
    gli spazi attorno e minuscola, e' nel registro. Non bada a maiuscole e
    spazi perche' `canonical` non ci bada."""
    return isinstance(name, str) and name.strip().lower() in INTERP_TYPES


def _is_iterable(value: Any) -> bool:
    """`isinstance(points, Iterable)` sui tipi che lo YAML produce."""
    return isinstance(value, (list, tuple, str, dict))


def _is_point(item: Any) -> bool:
    """`EnvelopeBuilder.is_breakpoint`: `[t, v]` o `[t, v, type]`, t e v
    numeri (bool escluso), type una stringa."""
    if not isinstance(item, list) or len(item) not in (2, 3):
        return False
    if not is_num(item[0]) or not is_num(item[1]):
        return False
    return len(item) == 2 or isinstance(item[2], str)


def _is_pattern_point(point: Any) -> bool:
    """`EnvelopeBuilder.is_pattern_point`: come un breakpoint, ma il type puo'
    essere anche `None` (l'interp al default)."""
    if not isinstance(point, list) or len(point) not in (2, 3):
        return False
    if not is_num(point[0]) or not is_num(point[1]):
        return False
    return len(point) == 2 or point[2] is None or isinstance(point[2], str)


def _dict_as_list(item: Any) -> Optional[list]:
    """`EnvelopeBuilder.dict_as_list`: il breakpoint `{t, v, type?}` come
    lista, o None."""
    if not (isinstance(item, dict) and 't' in item and 'v' in item):
        return None
    if 'type' in item:
        return [item['t'], item['v'], item['type']]
    return [item['t'], item['v']]


def _slot(compact: list, i: int):
    return compact[i] if len(compact) > i else None


def _is_linear_distribution(spec: Any) -> bool:
    """True se lo spec (gia' validato) costruisce la `LinearDistribution`."""
    if spec is None:
        return True
    nome = spec.get('type', 'linear') if isinstance(spec, dict) else spec
    return isinstance(nome, str) and nome.lower() == 'linear'


def _linear_compact_end(compact: list, offset: float) -> float:
    """L'ultimo breakpoint espanso di un compatto `linear` valido, con le
    stesse operazioni di `_expand_compact_format` e `LinearDistribution`:
    e' l'istante da cui comincia l'elemento successivo."""
    pattern = compact[_PATTERN]
    end_time, n_reps = compact[_END_TIME], compact[_N_REPS]
    wrap = _slot(compact, _WRAP)
    wrap = bool(wrap) if wrap is not None else False

    cycle_duration = (end_time - offset) / n_reps
    rep = n_reps - 1
    cycle_start = offset + rep * cycle_duration
    i = len(pattern) - 1
    t = cycle_start + ((pattern[i][0] / 100.0) * cycle_duration)
    if (rep > 0 and i == 0) or (rep == 0 and i == 0 and offset > 0):
        t += DISCONTINUITY_OFFSET
    if wrap and pattern[-1][0] < 100:
        t = max(t, cycle_start + cycle_duration - DISCONTINUITY_OFFSET)
    return t


# =============================================================================
# LA STRADA DEL MOTORE
# =============================================================================

def check_envelope_shape(body: Any,
                         time_mode: str = 'absolute') -> Optional[ShapeIssue]:
    """
    Il primo problema di forma che il motore troverebbe costruendo `body`
    come envelope, o None.

    Args:
        body: il valore della chiave com'e' scritto nello YAML. Solo una lista
            o un dict diventano un envelope: altro non e' affare di questa
            funzione, e torna None.
        time_mode: il modo temporale con cui il motore scala il corpo
            (`time_mode` dello stream; in forma dict lo sovrascrive il suo
            `time_unit`). Decide solo se l'istante di fine di un compatto che
            segue un compatto si puo' confrontare esattamente o soltanto con
            un limite inferiore.
    """
    if not isinstance(body, (list, dict)):
        return None
    if contains_math_expression(body):
        return None
    body = normalize_engine_values(body)

    if isinstance(body, dict):
        if not _is_iterable(body.get('points')):
            return ShapeIssue('points', body, DICT_POINTS_HINT)
        tipo = body.get('type', DEFAULT_INTERP)
        if not factory_reads(tipo):
            return ShapeIssue('type', tipo, TYPE_HINT, ('type',))
        raw, prefisso = body['points'], ('points',)
        time_mode = body.get('time_unit', time_mode)
    else:
        issue = _check_type_slot(body)
        if issue is not None:
            return issue
        raw, prefisso = body, ()

    if not raw:
        return ShapeIssue('points', body, EMPTY_HINT)

    exact = time_mode == 'absolute'
    issue = _check_builder(raw, prefisso, exact)
    if issue is _INDECISO:
        return None
    if issue is not None:
        return issue
    return _check_point_types(raw, prefisso)


def check_voice_kwarg(value: Any,
                      time_mode: str = 'absolute') -> Optional[ShapeIssue]:
    """
    Il kwarg di una voice strategy come lo legge `_parse_strategy_kwarg`
    (PGE #288): un envelope si costruisce e passa dai guard di forma; una
    lista o un dict che envelope non e' — `[1, 2, 3]`, `[]`, un dict senza
    `points` — si ferma al parse sul campo del kwarg (`where='kwarg'`).
    """
    if not isinstance(value, (list, dict)):
        return None
    if contains_math_expression(value):
        return None
    normalizzato = normalize_engine_values(value)
    if not is_envelope_like(normalizzato):
        return ShapeIssue('kwarg', value, KWARG_HINT)
    if isinstance(normalizzato, dict):
        time_mode = normalizzato.get('time_mode', time_mode)
        if time_mode == 'normalized':
            time_mode = normalizzato.get('time_unit', 'normalized')
    return check_envelope_shape(value, time_mode)


def _check_type_slot(body: list) -> Optional[ShapeIssue]:
    """`EnvelopeBuilder.extract_interp_type`: lo slot che diventa il tipo."""
    if is_loop_block(body):
        candidati = [((), body)]
    else:
        candidati = [((i,), item) for i, item in enumerate(body)
                     if is_loop_block(item)]
    for path, compact in candidati:
        slot = _slot(compact, _INTERP)
        if slot is None:
            continue
        if not factory_reads(slot or DEFAULT_INTERP):
            return ShapeIssue('compact.interp', slot, COMPACT_INTERP_HINT,
                              path + (_INTERP,))
        return None
    return None


# Il ritorno di `_check_builder` quando il guard su `end_time` di un compatto
# non si decide: nessun problema da riportare, e nemmeno i guard che vengono
# dopo, perche' il motore potrebbe fermarsi prima di loro.
_INDECISO = object()


def _check_builder(raw: Any, prefisso: tuple, exact: bool):
    """`EnvelopeBuilder.parse`, guard per guard: il primo `ShapeIssue`,
    None, o `_INDECISO`."""
    if isinstance(raw, list) and is_loop_block(raw):
        issue, _tempo = _check_compact(raw, _Tempo(0.0, 0.0), prefisso,
                                       exact)
        return issue
    if isinstance(raw, list) and is_bp_group(raw):
        return _check_group(raw, prefisso)
    # Un BP group diretto con punti malformati non e' un BP group: il motore
    # lo scorre elemento per elemento e rifiuta il primo che non riconosce.
    # L'utente pero' ha scritto un gruppo, ed e' di quello che va detto cosa
    # non va: l'hint lo nomina, il valore resta quello del motore.
    gruppo_diretto = isinstance(raw, list) and is_bp_group_candidate(raw)

    tempo = _Tempo(0.0, 0.0)
    for i, item in _items(raw):
        path = prefisso if isinstance(raw, (str, dict)) else prefisso + (i,)
        scritto = item
        come_lista = _dict_as_list(item)
        if come_lista is not None:
            item = come_lista
        if is_loop_block(item):
            issue, tempo = _check_compact(item, tempo, path, exact)
            if issue is not None:
                return issue
            if tempo is None:
                return _INDECISO
        elif is_bp_group(item):
            issue = _check_group(item, path)
            if issue is not None:
                return issue
            tempo = tempo.dopo(item[0][-1][0])
        elif _is_point(item):
            tempo = tempo.dopo(item[0])
        else:
            nel_gruppo = gruppo_diretto and i == 0
            hint = (GROUP_POINTS_HINT
                    if nel_gruppo or is_bp_group_candidate(scritto)
                    else ELEMENT_HINT)
            return ShapeIssue('point', scritto, hint, path)
    return None


def _items(raw: Any):
    """Gli elementi che `parse` scorre: quelli di una lista, i caratteri di
    una stringa, le chiavi di un dict (`for item in raw_points`)."""
    if isinstance(raw, dict):
        return enumerate(raw.keys())
    return enumerate(raw)


def _check_group(group: list, path: tuple) -> Optional[ShapeIssue]:
    """`EnvelopeBuilder._expand_bp_group`: interp alla lettera, 2 punti."""
    points, interp = group
    if interp not in INTERP_TYPES:
        return ShapeIssue('group.interp', interp, GROUP_INTERP_HINT,
                          path + (1,))
    if len(points) < 2:
        return ShapeIssue('group.points', points, GROUP_ARITY_HINT, path)
    return None


@dataclass(frozen=True)
class _Tempo:
    """L'istante da cui comincia il prossimo elemento (`current_time` del
    builder), come intervallo: `lo <= current_time <= hi`."""
    lo: float
    hi: float

    def dopo(self, t: float) -> '_Tempo':
        """Dopo un breakpoint, o il punto finale di un BP group: il builder
        fa `max(current_time, t)`."""
        return _Tempo(max(self.lo, t), max(self.hi, t))


# La tolleranza relativa sull'istante calcolato sotto la scala dei tempi:
# il motore fa le stesse operazioni su numeri gia' moltiplicati per la durata,
# e gli arrotondamenti non coincidono. Larga di parecchi ordini rispetto a
# quelli, stretta rispetto a qualunque distanza che si scriva in uno YAML.
_TOLLERANZA_SCALA = 1e-9


def _check_compact(compact: list, tempo: '_Tempo', path: tuple,
                   exact: bool) -> Tuple[Optional[ShapeIssue],
                                         Optional['_Tempo']]:
    """`EnvelopeBuilder._expand_compact_format`, nell'ordine dei suoi guard.

    Returns:
        `(problema, istante da cui comincia l'elemento successivo)`. Il
        secondo e' None quando il mirror non sa decidere il guard su
        `end_time` (vedi il docstring del modulo): allora tace.
    """
    pattern = compact[_PATTERN]
    end_time, n_reps = compact[_END_TIME], compact[_N_REPS]

    if not is_num(n_reps) or n_reps < 1:
        return ShapeIssue('compact.n_reps', n_reps, N_REPS_HINT,
                          path + (_N_REPS,)), tempo
    if not is_num(end_time) or not math.isfinite(end_time):
        return ShapeIssue('compact.end_time', end_time, END_TIME_TYPE_HINT,
                          path + (_END_TIME,)), tempo
    if end_time <= tempo.lo:
        return ShapeIssue('compact.end_time', end_time,
                          END_TIME_OFFSET_HINT.format(inizio=tempo.lo),
                          path + (_END_TIME,)), tempo
    if end_time <= tempo.hi:
        return None, None
    # Da qui `end_time > current_time` di certo; l'istante esatto serve solo
    # per un compatto `linear` con un inizio esatto.
    offset = tempo.lo if tempo.lo == tempo.hi else None
    if not pattern:
        return ShapeIssue('compact.pattern', pattern, PATTERN_EMPTY_HINT,
                          path + (_PATTERN,)), tempo
    precedente = None
    for j, point in enumerate(pattern):
        point_path = path + (_PATTERN, j)
        if not _is_pattern_point(point):
            return ShapeIssue('compact.pattern', point, PATTERN_POINT_HINT,
                              point_path), tempo
        x = point[0]
        if not 0 <= x <= 100:
            return ShapeIssue('compact.pattern', x, PATTERN_X_HINT,
                              point_path), tempo
        if precedente is not None and x < precedente:
            return ShapeIssue('compact.pattern', x, PATTERN_ORDER_HINT,
                              point_path), tempo
        precedente = x

    spec = _slot(compact, _TIME_DIST)
    dist = check_time_distribution(spec, n_reps)
    if dist is not None:
        return ShapeIssue('compact.time_dist',
                          compact if dist.kind == 'overflow' else spec,
                          describe_issue(dist), path, dist), tempo

    if offset is not None and _is_linear_distribution(spec):
        fine = _linear_compact_end(compact, offset)
        if exact:
            return None, _Tempo(fine, fine)
        margine = _TOLLERANZA_SCALA * max(1.0, abs(fine))
        return None, _Tempo(fine - margine, fine + margine)
    # Senza l'istante esatto: ogni punto espanso sta fra l'inizio del blocco
    # e la sua fine, piu' la discontinuita' che il primo punto di un ciclo puo'
    # prendere. E l'ultimo ciclo finisce a `end_time` qualunque sia la
    # distribuzione, a meno degli arrotondamenti della somma delle durate:
    # se il pattern arriva al 100% (o `wrap` chiude il ciclo) l'ultimo punto
    # sta li'.
    margine = _TOLLERANZA_SCALA * max(1.0, abs(end_time))
    hi = end_time + DISCONTINUITY_OFFSET + margine
    wrap = _slot(compact, _WRAP)
    if pattern[-1][0] == 100:
        lo = end_time - margine
    elif wrap is not None and bool(wrap):
        lo = end_time - DISCONTINUITY_OFFSET - margine
    else:
        lo = tempo.lo
    return None, _Tempo(max(tempo.lo, lo), hi)


def _check_point_types(raw: Any, prefisso: tuple) -> Optional[ShapeIssue]:
    """`Envelope._parse_segments`: il type di un breakpoint espanso, alla
    lettera. Si arriva qui solo con un corpo che il builder ha accettato, e
    lo si scorre nell'ordine in cui il builder lo espande."""
    if is_loop_block(raw):
        return _pattern_types(raw, prefisso)
    if is_bp_group(raw):
        return _group_types(raw, prefisso)
    for i, item in enumerate(raw):
        path = prefisso + (i,)
        if isinstance(item, dict):
            if 'type' in item and item['type'] not in INTERP_TYPES:
                return ShapeIssue('point.type', item['type'],
                                  POINT_TYPE_HINT, path + ('type',))
        elif is_loop_block(item):
            issue = _pattern_types(item, path)
            if issue is not None:
                return issue
        elif is_bp_group(item):
            issue = _group_types(item, path)
            if issue is not None:
                return issue
        elif len(item) == 3 and item[2] not in INTERP_TYPES:
            return ShapeIssue('point.type', item[2], POINT_TYPE_HINT,
                              path + (2,))
    return None


def _pattern_types(compact: list, path: tuple) -> Optional[ShapeIssue]:
    for j, point in enumerate(compact[_PATTERN]):
        if (len(point) == 3 and point[2] is not None
                and point[2] not in INTERP_TYPES):
            return ShapeIssue('point.type', point[2], POINT_TYPE_HINT,
                              path + (_PATTERN, j, 2))
    return None


def _group_types(group: list, path: tuple) -> Optional[ShapeIssue]:
    for j, point in enumerate(group[0]):
        if len(point) == 3 and point[2] not in INTERP_TYPES:
            return ShapeIssue('point.type', point[2], POINT_TYPE_HINT,
                              path + (0, j, 2))
    return None
