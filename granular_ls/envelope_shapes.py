# granular_ls/envelope_shapes.py
"""
Riconoscimento strutturale delle forme envelope PGE.

Helper condivisi tra DiagnosticProvider, HoverProvider e server.py per
riconoscere le forme sintattiche di un envelope serializzato:

  - breakpoint nudo:        [t, v]
  - breakpoint 3-tuple:     [t, v, type]
  - BP group (PGE #64):     [points, interp]  con points = [[t,v]|[t,v,type], ...]
  - loop block (compact):   [pattern, end_time, n_reps, interp?, time_dist?, wrap?]

Le regole discriminanti sono identiche a EnvelopeBuilder in PGE
(src/pge/envelopes/envelope_builder.py): il BP group e' l'unica lista a
2 elementi con elem[0] lista di punti ed elem[1] stringa. Nessuna
collisione con [t, v] (elem[0] numerico), 3-tuple e loop block (len != 2),
o il legacy [[t, v], 'marker'] (elem[0] e' UN punto, non lista di punti).
"""

# Tipi di interpolazione validi per il group interp (mirror di
# EnvelopeBuilder.VALID_INTERP_TYPES in PGE).
VALID_INTERP_TYPES = ('linear', 'cubic', 'step')


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
    (mirror di EnvelopeBuilder._is_compact_format in PGE).
    """
    if not isinstance(item, list) or not (3 <= len(item) <= 6):
        return False
    if not isinstance(item[0], list):
        return False
    if item[0] and not all(
            isinstance(p, list) and len(p) in (2, 3) for p in item[0]):
        return False
    if not is_num(item[1]):
        return False
    if not isinstance(item[2], int) or isinstance(item[2], bool):
        return False
    if len(item) >= 4 and item[3] is not None and not isinstance(item[3], str):
        return False
    if len(item) >= 5 and item[4] is not None \
            and not isinstance(item[4], (str, dict)):
        return False
    if len(item) == 6 and not isinstance(item[5], bool):
        return False
    return True
