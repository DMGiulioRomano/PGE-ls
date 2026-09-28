# granular_ls/envelope_shapes.py
"""
Riconoscimento strutturale delle forme envelope PGE.

Helper condivisi tra DiagnosticProvider, HoverProvider e server.py per
riconoscere le forme sintattiche di un envelope serializzato:

  - breakpoint nudo:        [t, v]
  - breakpoint 3-tuple:     [t, v, type]
  - BP group (PGE #64):     [points, interp]  con points = [[t,v]|[t,v,type], ...]
  - loop block (compact):   [pattern, end_time, n_reps, interp?, time_dist?, wrap?]

e le posizioni in cui il builder espande un loop block
(`compact_block_positions`), piu' il lettore unico delle Y di un envelope
(`envelope_y_paths`), che mette insieme le forme come fa il builder.

Le regole discriminanti sono identiche a EnvelopeBuilder in PGE
(src/pge/envelopes/envelope_builder.py): il BP group e' l'unica lista a
2 elementi con elem[0] lista di punti ed elem[1] stringa. Nessuna
collisione con [t, v] (elem[0] numerico), 3-tuple e loop block (len != 2),
o il legacy [[t, v], 'marker'] (elem[0] e' UN punto, non lista di punti).
"""

import re

# Tipi di interpolazione validi per il group interp (mirror di
# EnvelopeBuilder.VALID_INTERP_TYPES in PGE).
VALID_INTERP_TYPES = ('linear', 'cubic', 'step')

# Il pattern con cui il Generator riconosce un'espressione da valutare (mirror
# di `Generator._eval_math_expressions` in PGE, src/pge/engine/generator.py).
# La classe di caratteri e' la sua: se il motore allarga o restringe cio' che
# valuta, questa riga e' il posto che deve seguirlo.
_MATH_EXPRESSION_RE = re.compile(r'\(([a-zA-Z0-9+\-*/.() ]+)\)')


def is_math_expression(value) -> bool:
    """True se il Generator proverebbe a valutare questa stringa.

    Non dice quanto ne uscirebbe — solo che il valore che il motore vede non
    e' quello scritto nello YAML. Serve a tacere, non a decidere: chi la
    chiama sa che su quel valore non ha piu' niente da dire.
    """
    return (isinstance(value, str)
            and _MATH_EXPRESSION_RE.search(value) is not None)


def numeric_string_value(value):
    """Il numero in cui il Generator converte questa stringa, o None.

    Mirror della **coda** di `_eval_math_expressions`: dopo la sostituzione
    delle espressioni il testo viene convertito con `float(...)` se contiene
    un punto e `int(...)` altrimenti, e resta stringa solo se la conversione
    alza `ValueError`. La conversione non pretende le parentesi: `"50"` arriva
    come `50` e `"1.5"` come `1.5`, mentre `"1e3"` e `"abc"` restano stringhe.

    Questa meta' della funzione e' riproducibile esattamente — e' una
    `try/except` — a differenza dell'`eval` fra parentesi, il cui esito non e'
    prevedibile. Da qui la divisione del lavoro: sulle espressioni si tace,
    sulle stringhe numeriche si converte come il motore e poi si decide.
    """
    if not isinstance(value, str):
        return None
    try:
        return float(value) if '.' in value else int(value)
    except ValueError:
        return None


def normalize_engine_values(obj):
    """Il corpo come il Generator lo consegna, per la parte decidibile.

    Converte in numero, a qualunque profondita', le stringhe che
    `_eval_math_expressions` convertirebbe; lascia intatto tutto il resto,
    espressioni comprese — quelle si gestiscono tacendo, non normalizzando.

    Serve prima di ogni controllo di forma: senza, `[[0, "1"], [10, "-1"]]`
    non sembra una lista di breakpoint, mentre il motore ci legge
    `[[0, 1], [10, -1]]` e la costruisce.

    Sui dict si scende nei soli valori, come il motore: le chiavi restano
    quelle scritte.
    """
    if isinstance(obj, str):
        numero = numeric_string_value(obj)
        return obj if numero is None else numero
    if isinstance(obj, dict):
        return {k: normalize_engine_values(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [normalize_engine_values(item) for item in obj]
    return obj


def contains_math_expression(obj) -> bool:
    """True se una stringa da valutare compare in `obj`, a qualunque profondita'.

    `_eval_math_expressions` ricorre dentro liste e dict e riconverte il
    risultato a numero: una `(50/2)` sepolta nella Y di un breakpoint arriva
    al parser come `25`, esattamente come farebbe da scalare. Un mirror che
    escludesse le stringhe solo al primo livello segnalerebbe come malformato
    un envelope che il motore costruisce — il modo peggiore in cui un language
    server puo' sbagliarsi.

    Sui dict si scende nei soli valori, come il motore: le chiavi restano
    quelle scritte, `_eval_math_expressions` non le tocca.
    """
    if isinstance(obj, str):
        return is_math_expression(obj)
    if isinstance(obj, dict):
        return any(contains_math_expression(v) for v in obj.values())
    if isinstance(obj, (list, tuple)):
        return any(contains_math_expression(item) for item in obj)
    return False


def is_num(x) -> bool:
    """True se x e' un numero (bool escluso, come in PGE)."""
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def is_breakpoint(item) -> bool:
    """True se item e' un breakpoint nudo [t, v]."""
    return (isinstance(item, list) and len(item) == 2
            and is_num(item[0]) and is_num(item[1]))


def is_3tuple_breakpoint(item) -> bool:
    """True se item e' un breakpoint 3-tuple [t, v, type]."""
    return (isinstance(item, list) and len(item) == 3
            and is_num(item[0]) and is_num(item[1])
            and isinstance(item[2], str))


def is_valid_point(p) -> bool:
    """True se p e' un punto valido dentro un BP group: [t, v] o [t, v, type]."""
    if not isinstance(p, list) or len(p) not in (2, 3):
        return False
    if not is_num(p[0]) or not is_num(p[1]):
        return False
    if len(p) == 3 and not isinstance(p[2], str):
        return False
    return True


def is_bp_group(item) -> bool:
    """
    True se item e' un BP group [points, interp] strutturalmente valido
    (mirror di EnvelopeBuilder._is_bp_group in PGE, issue #64).

    Check strutturale: il valore di interp e il vincolo "almeno 2 punti"
    NON vengono verificati qui — vanno validati a parte per dare errori
    precisi (in PGE: InvalidFieldValueError / ValueError in _expand_bp_group).
    """
    if not isinstance(item, list) or len(item) != 2:
        return False
    points, interp = item
    if not isinstance(interp, str):
        return False
    if not isinstance(points, list):
        return False
    return all(is_valid_point(p) for p in points)


def is_bp_group_candidate(item) -> bool:
    """
    Riconoscimento lasco: item "sembra" un BP group anche se i punti sono
    malformati. Serve alle diagnostiche per segnalare punti malformati
    DENTRO un gruppo invece di ignorare silenziosamente la forma.

    Regola: lista a 2 elementi, elem[1] stringa, elem[0] lista che contiene
    almeno una lista. Esclude il legacy [[t, v], 'marker'] (elem[0] e' un
    punto: contiene solo numeri, nessuna lista).
    """
    if not isinstance(item, list) or len(item) != 2:
        return False
    points, interp = item
    if not isinstance(interp, str) or not isinstance(points, list):
        return False
    # Gruppo vuoto [[], 'interp']: candidato (errore "almeno 2 punti").
    if not points:
        return True
    return any(isinstance(p, list) for p in points)


def is_loop_block(item) -> bool:
    """
    True se item e' un loop block compact
    [pattern, end_time, n_reps, interp?, time_dist?, wrap?]
    (mirror di EnvelopeBuilder.is_compact_format in PGE).

    `end_time` e `n_reps` accettano i bool, come nel motore, che usa
    `isinstance(..., (int, float))` e `isinstance(..., int)` nudi: `True` e'
    un `int` per sottoclasse e li passa. La differenza non e' cosmetica —
    decide quale messaggio riceve `[[[0, 1], [50, -1]], true, 2]`. Con il
    riconoscimento stretto la forma non sarebbe un ciclo e l'utente
    leggerebbe che il valore non e' un envelope; cosi' arriva al guard su
    `end_time`, che e' il suo problema vero. Solo `wrap` pretende un bool
    puro, ed e' il motore a pretenderlo.
    """
    if not isinstance(item, list) or not (3 <= len(item) <= 6):
        return False
    if not isinstance(item[0], list):
        return False
    if item[0] and not all(
            isinstance(p, list) and len(p) in (2, 3) for p in item[0]):
        return False
    if not isinstance(item[1], (int, float)):
        return False
    if not isinstance(item[2], int):
        return False
    if len(item) >= 4 and item[3] is not None and not isinstance(item[3], str):
        return False
    if len(item) >= 5 and item[4] is not None \
            and not isinstance(item[4], (str, dict)):
        return False
    if len(item) == 6 and not isinstance(item[5], bool):
        return False
    return True


def compact_block_positions(body) -> list:
    """
    Dove `EnvelopeBuilder.parse` espande un ciclo compatto in questo corpo.

    Le posizioni sono le sue, e sono due: il corpo intero, quando è lui il
    ciclo (forma diretta), oppure gli elementi della lista — non più in fondo,
    perché un ciclo dentro un ciclo o dentro un BP group non è una forma che
    il builder riconosca. Un dict envelope porta il corpo sotto `points`.

    Returns:
        `None` per il corpo intero, l'indice dell'elemento altrimenti. Lista
        vuota se il corpo non è un envelope o non contiene cicli.
    """
    if isinstance(body, dict):
        body = body.get('points')
    if not isinstance(body, list):
        return []
    if is_loop_block(body):
        return [None]
    if is_bp_group(body):
        return []
    return [i for i, item in enumerate(body) if is_loop_block(item)]


def compact_blocks(body) -> list:
    """I cicli compatti che il builder espande in questo corpo, in ordine."""
    lista = body.get('points') if isinstance(body, dict) else body
    return [lista if pos is None else lista[pos]
            for pos in compact_block_positions(body)]


def _pattern_y_paths(points, path: tuple) -> list:
    """Le Y dei punti dentro una macro-forma (ciclo o BP group).

    Un punto conta solo se il builder lo espanderebbe: `[t, v]` o
    `[t, v, type]` con t e v numerici. Il ciclo compatto ne riconosce la
    forma senza guardare i numeri, ma una Y che non e' un numero non e' una
    Y che un bound possa respingere.
    """
    return [(path + (j, 1), p[1]) for j, p in enumerate(points)
            if is_valid_point(p)]


def envelope_y_paths(body) -> list:
    """
    Le Y di un corpo envelope come le legge il builder, e dove stanno.

    Mirror della lettura di `Envelope.__init__` piu' `EnvelopeBuilder.parse`
    in PGE, ristretta alle Y: sono i valori dei breakpoint espansi, cioe'
    quelli che `GranularParser._validate_and_clip` confronta con i bound e
    per cui, in `strict`, alza `ParameterBoundError`.

      - un dict envelope porta il corpo sotto `points` (`type` e' l'interp
        globale, non un valore);
      - il corpo intero puo' essere un ciclo compatto o un BP group (forma
        diretta): le Y stanno nei punti del suo elemento 0 — ne' `end_time`
        ne' `n_reps` ne' l'interp lo sono;
      - altrimenti ogni elemento e' un breakpoint `[t, v]`, un `[t, v, type]`,
        un dict `{t, v, type?}` (che il builder normalizza in lista prima di
        guardarlo), un ciclo o un BP group.

    Le forme si riconoscono con i predicati di questo modulo, gli stessi del
    builder, sul valore come il Generator lo consegna: le stringhe numeriche
    si convertono (`normalize_engine_values`), le espressioni fra parentesi
    no. Un elemento che ne contiene una si salta intero, e con lui un corpo
    in forma diretta: l'esito dell'`eval` non si prevede, e in uno slot
    strutturale decide la forma stessa (`'(1+1)'` come interp di un gruppo
    diventa `2`, e il gruppo smette di esserlo).

    Un elemento che il builder rifiuta non spegne gli altri: la Y di un
    breakpoint buono resta una Y che, corretto il resto, il motore
    confronterebbe con i bound. Una forma che non e' un envelope invece non
    ha Y — `[t, v]` senza parentesi esterne il builder lo scorre numero per
    numero e alza sul primo, prima di qualunque bound.

    Returns:
        Lista di `(percorso, y)` in ordine di scrittura. `percorso` e' la
        tupla di chiavi e indici che porta dal corpo al numero — `(1, 'v')`
        per la `v` del secondo breakpoint dict, `('points', 0, 2, 1)` per la
        Y del terzo punto di un ciclo diretto dentro un dict — cosi' chi ha il
        nodo YAML del corpo ne ricava la riga. Lista vuota se il corpo non e'
        un envelope.
    """
    body = normalize_engine_values(body)
    prefisso: tuple = ()
    if isinstance(body, dict):
        if 'points' not in body:
            return []
        body, prefisso = body['points'], ('points',)
    if not isinstance(body, list):
        return []

    if is_loop_block(body) or is_bp_group(body):
        if contains_math_expression(body):
            return []
        return _pattern_y_paths(body[0], prefisso + (0,))

    ys: list = []
    for i, item in enumerate(body):
        path = prefisso + (i,)
        if contains_math_expression(item):
            continue
        if isinstance(item, dict):
            if 't' not in item or 'v' not in item:
                continue
            punto = [item['t'], item['v']]
            if 'type' in item:
                punto.append(item['type'])
            if is_breakpoint(punto) or is_3tuple_breakpoint(punto):
                ys.append((path + ('v',), item['v']))
            continue
        # Le macro-forme per prime: un ciclo ha `item[1]` numerico, e letto
        # come breakpoint darebbe il suo `end_time` come Y.
        if is_loop_block(item) or is_bp_group(item):
            ys.extend(_pattern_y_paths(item[0], path + (0,)))
        elif is_breakpoint(item) or is_3tuple_breakpoint(item):
            ys.append((path + (1,), item[1]))
    return ys
