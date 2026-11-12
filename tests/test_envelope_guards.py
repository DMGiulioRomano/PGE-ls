# tests/test_envelope_guards.py
"""
Lo specchio unico dei guard di forma (PGE #211, #288): `envelope_guards.py`.

Qui le regole una per una, senza motore. Che coincidano col motore lo dice
`tests/test_pge_parity.py` (`test_envelope_shape_mirror_matches_engine` e il
fuzz seminato); che arrivino sotto ogni chiave, con la riga giusta, lo dice
`tests/test_diagnostic_provider.py` (`TestFormaEnvelopeSottoOgniChiave`).
"""

import pytest

from granular_ls.envelope_guards import (
    check_envelope_shape,
    check_voice_kwarg,
    factory_reads,
)
from granular_ls.envelope_shapes import is_envelope_like, is_loop_block
from granular_ls.time_distributions import check_time_distribution

PAT = [[0, 1], [100, 2]]


def _where(body, time_mode='absolute'):
    issue = check_envelope_shape(body, time_mode)
    return None if issue is None else issue.where


# =============================================================================
# Corpi validi: nessun problema
# =============================================================================

@pytest.mark.parametrize('body', [
    [[0, 1], [1, 2]],
    [[0, 1, 'cubic'], [1, 2]],
    [{'t': 0, 'v': 1}, {'t': 1, 'v': 2, 'type': 'step'}],
    [[[0, 1], [1, 2]], 'cubic'],
    [PAT, 1.0, 2],
    [PAT, 1.0, 2, 'Step'],          # la factory legge lo slot senza maiuscole
    [PAT, 1.0, 2, ''],              # lo slot vuoto vale il default
    [PAT, 1.0, 2, None, 'exponential', True],
    [PAT, 1.0, 2, None, None, None],
    {'points': [[0, 1], [1, 2]]},
    {'type': 'Cubic', 'points': [[0, 1], [1, 2]]},
    {'type': ' linear ', 'points': [PAT, 1.0, 2]},
    # lo slot di un compatto che l'envelope non legge non si valida
    {'points': [PAT, 1.0, 2, 'banana']},
    [[PAT, 1.0, 2, 'linear'], [PAT, 2.0, 2, 'banana']],
    # una x ripetuta e' la discontinuita'
    [[[0, 1], [50, 1], [50, 2], [100, 2]], 1.0, 2],
])
def test_corpi_validi(body):
    assert check_envelope_shape(body) is None


@pytest.mark.parametrize('value', [1, 0.5, 'abc', None, True])
def test_uno_scalare_non_e_affare_dello_specchio(value):
    assert check_envelope_shape(value) is None


def test_un_espressione_fra_parentesi_tace():
    assert check_envelope_shape([[0, '(1/0)'], [1, 2], 'x']) is None


# =============================================================================
# Forma dict (Envelope.__init__)
# =============================================================================

@pytest.mark.parametrize('body', [
    {}, {'type': 'linear'}, {'points': None}, {'points': 3},
    {'points': True},
])
def test_dict_senza_points_percorribili(body):
    issue = check_envelope_shape(body)
    assert issue.where == 'points'
    assert issue.value == body
    assert issue.path == ()


@pytest.mark.parametrize('tipo', ['banana', '', '   ', None, 5])
def test_dict_type_che_la_factory_non_legge(tipo):
    issue = check_envelope_shape({'type': tipo, 'points': [[0, 1]]})
    assert issue.where == 'type'
    assert issue.value == tipo
    assert issue.path == ('type',)


def test_il_type_si_guarda_prima_dei_punti():
    assert _where({'type': 'banana', 'points': []}) == 'type'


def test_dict_vuoto_riporta_il_dict():
    issue = check_envelope_shape({'points': []})
    assert issue.where == 'points'
    assert issue.value == {'points': []}


def test_points_stringa_o_dict_arrivano_al_builder():
    """Il builder li scorre elemento per elemento e rifiuta il primo."""
    assert check_envelope_shape({'points': 'ab'}).value == 'a'
    assert check_envelope_shape({'points': {'k': 1}}).value == 'k'
    assert _where({'points': 'ab'}) == 'point'


# =============================================================================
# Forma lista: lo slot interp del compatto, il corpo vuoto
# =============================================================================

def test_lista_vuota():
    issue = check_envelope_shape([])
    assert (issue.where, issue.value) == ('points', [])


@pytest.mark.parametrize('slot', ['cubicc', '   ', 'banana'])
def test_slot_interp_del_compatto_diretto(slot):
    issue = check_envelope_shape([PAT, 1.0, 2, slot])
    assert (issue.where, issue.value, issue.path) == (
        'compact.interp', slot, (3,))


def test_lo_slot_viene_prima_dei_guard_del_builder():
    """`[PAT, 1, true, "cubicc"]` dice `compact.interp`, non `n_reps`."""
    assert _where([PAT, 1, True, 'cubicc']) == 'compact.interp'


def test_si_valida_solo_lo_slot_del_primo_compatto_che_lo_dichiara():
    body = [[0, 0], [PAT, 1.0, 2, None], [PAT, 2.0, 2, 'cubicc'],
            [PAT, 3.0, 2, 'banana']]
    issue = check_envelope_shape(body)
    assert (issue.where, issue.value, issue.path) == (
        'compact.interp', 'cubicc', (2, 3))


# =============================================================================
# Il builder: BP group, formato compatto, elemento sconosciuto
# =============================================================================

@pytest.mark.parametrize('body, where, value', [
    ([[[0, 1]], 'linear'], 'group.points', [[0, 1]]),
    ([[], 'cubic'], 'group.points', []),
    ([[[0, 1], [1, 2]], 'Step'], 'group.interp', 'Step'),
    ([[[[0, 1]], 'linear'], [1.0, 1]], 'group.points', [[0, 1]]),
    ([PAT, 1.0, 0], 'compact.n_reps', 0),
    ([PAT, 1.0, True], 'compact.n_reps', True),
    # 2.0 non e' un int: niente compatto, e il pattern e' un elemento
    ([PAT, 1.0, 2.0], 'point', PAT),
    ([PAT, True, 2], 'compact.end_time', True),
    ([PAT, 0, 2], 'compact.end_time', 0),
    ([PAT, float('inf'), 2], 'compact.end_time', float('inf')),
    ([[], 1.0, 2], 'compact.pattern', []),
    ([[[0, 1], [150, 2]], 1.0, 2], 'compact.pattern', 150),
    ([[[-10, 1], [100, 2]], 1.0, 2], 'compact.pattern', -10),
    ([[[100, 1], [0, 2]], 1.0, 2], 'compact.pattern', 0),
    ([[[0, 'a'], [100, 2]], 1.0, 2], 'compact.pattern', [0, 'a']),
    ([[[0, True], [100, 2]], 1.0, 2], 'compact.pattern', [0, True]),
    ([[[0, 1, 5], [100, 2]], 1.0, 2], 'compact.pattern', [0, 1, 5]),
    ([[[[[0, 1], [1, 2]], 'linear'], [100, 2]], 1.0, 2],
     'compact.pattern', [[[0, 1], [1, 2]], 'linear']),
    ([PAT, 1.0, 2, None, 'banana'], 'compact.time_dist', 'banana'),
    ([PAT, 1.0, 2, None, {'type': None}], 'compact.time_dist',
     {'type': None}),
    ([[0, 0], [1, 10], 'cycle'], 'point', 'cycle'),
    ([[0, 0], {'t': 0, 'v': 'a'}], 'point', {'t': 0, 'v': 'a'}),
    ([[0, 0], [1]], 'point', [1]),
])
def test_guard_del_builder(body, where, value):
    issue = check_envelope_shape(body)
    assert issue.where == where
    assert issue.value == value


def test_la_coppia_che_trabocca_riporta_il_ciclo():
    ciclo = [PAT, 10.0, 400, 'linear', {'type': 'geometric', 'ratio': 10}]
    issue = check_envelope_shape(ciclo)
    assert issue.where == 'compact.time_dist'
    assert issue.value == ciclo
    assert issue.time_dist.kind == 'overflow'


def test_il_bp_group_malformato_si_chiama_bp_group():
    issue = check_envelope_shape([[[0.0, 10], [1.0]], 'cubic'])
    assert issue.where == 'point'
    assert 'BP group' in issue.hint


# =============================================================================
# L'istante da cui comincia un compatto in una lista mista
# =============================================================================

def test_end_time_oltre_l_ultimo_breakpoint():
    assert _where([[0, 1], [0.5, 2], [PAT, 0.5, 2]]) == 'compact.end_time'
    assert _where([[0, 1], [0.5, 2], [PAT, 0.6, 2]]) is None


def test_end_time_dopo_un_bp_group():
    body = [[[[0, 1], [2, 2]], 'linear'], [PAT, 1.0, 2]]
    assert _where(body) == 'compact.end_time'


def test_end_time_dopo_un_compatto_linear_e_esatto():
    """Il compatto finisce al 100% del pattern: comincia li' il successivo."""
    assert _where([[PAT, 1.0, 4], [PAT, 1.0, 2]]) == 'compact.end_time'
    assert _where([[PAT, 1.0, 4], [PAT, 1.1, 2]]) is None


def test_end_time_dopo_un_compatto_che_non_arriva_al_cento():
    """Il pattern finisce al 50%: l'ultimo breakpoint e' a meta' dell'ultimo
    ciclo (0.875 con quattro cicli in 1.0), non alla fine del blocco."""
    body = [[[[0, 1], [50, 2]], 1.0, 4], [PAT, 0.9, 2]]
    assert _where(body) is None
    body = [[[[0, 1], [50, 2]], 1.0, 4], [PAT, 0.875, 2]]
    assert _where(body) == 'compact.end_time'


def test_end_time_dopo_un_compatto_non_lineare():
    """Senza l'istante esatto, il pattern al 100% dice che il blocco finisce
    a `end_time`, a meno degli arrotondamenti della somma delle durate."""
    body = [[PAT, 1.0, 4, None, 'exponential'], [PAT, 0.5, 2]]
    assert _where(body) == 'compact.end_time'
    body = [[PAT, 1.0, 4, None, 'exponential'], [PAT, 1.5, 2]]
    assert _where(body) is None


def test_end_time_uguale_alla_fine_di_un_compatto_non_lineare_tace():
    """Sette cicli `logarithmic` in 1.0 finiscono a 0.9999999999999999 nel
    motore, e un compatto che segue con `end_time: 1.0` rende; con quattro
    finiscono a 1.0 e non rende. Il mirror non rifa' la somma delle durate,
    quindi sul bordo tace invece di indovinare."""
    for n_reps in (4, 7):
        body = [[PAT, 1.0, n_reps, None, 'logarithmic'], [PAT, 1.0, 2]]
        assert check_envelope_shape(body) is None


def test_quando_non_si_decide_si_tace_su_tutto():
    """Pattern al 50% e distribuzione `exponential`: l'ultimo breakpoint sta
    da qualche parte dentro l'ultimo ciclo, e un `end_time` li' dentro non si
    decide. Tacere sul resto del corpo, anche su un difetto sicuro piu'
    avanti: il motore potrebbe fermarsi prima."""
    body = [[[[0, 1], [50, 2]], 1.0, 4, None, 'exponential'],
            [PAT, 0.9, 2], [1]]
    assert check_envelope_shape(body) is None


def test_sotto_normalized_il_compatto_successivo_con_tolleranza():
    """Sotto la scala il motore rifa' i conti su tempi moltiplicati per la
    durata: lontano dal bordo il verdetto e' lo stesso, sul bordo si tace."""
    assert _where([[PAT, 1.0, 4], [PAT, 0.5, 2]], 'normalized') \
        == 'compact.end_time'
    assert _where([[PAT, 1.0, 4], [PAT, 1.1, 2]], 'normalized') is None
    assert _where([[PAT, 1.0, 4], [PAT, 1.0, 2]], 'normalized') is None
    assert _where([[PAT, 1.0, 4], [PAT, 1.0, 2]], 'absolute') \
        == 'compact.end_time'


# =============================================================================
# Il tipo per punto (Envelope._parse_segments)
# =============================================================================

@pytest.mark.parametrize('body, path', [
    ([[0, 1, 'banana'], [1, 2]], (0, 2)),
    ([[0, 1, 'Step'], [1, 2]], (0, 2)),
    ([[0, 1], {'t': 1, 'v': 2, 'type': 'cubicc'}], (1, 'type')),
    ([[[0, 1, 'x'], [1, 2]], 'linear'], (0, 0, 2)),
    ([[[0, 1, 'x'], [100, 2]], 1.0, 2], (0, 0, 2)),
])
def test_tipo_per_punto_alla_lettera(body, path):
    issue = check_envelope_shape(body)
    assert issue.where == 'point.type'
    assert issue.path == path


def test_il_tipo_per_punto_viene_dopo_tutto_il_builder():
    body = [[0, 1, 'banana'], [1, 2], [1]]
    assert _where(body) == 'point'


# =============================================================================
# Il kwarg di una voice strategy (PGE #288)
# =============================================================================

@pytest.mark.parametrize('value', [[1, 2, 3], [], {'a': 1}, [[1, 2, 3]]])
def test_kwarg_lista_o_dict_che_non_e_un_envelope(value):
    issue = check_voice_kwarg(value)
    assert (issue.where, issue.value) == ('kwarg', value)


@pytest.mark.parametrize('value', [3, 'major', None])
def test_kwarg_scalare_o_nome(value):
    assert check_voice_kwarg(value) is None


def test_kwarg_envelope_passa_dai_guard_di_forma():
    assert check_voice_kwarg([[0, 1], [1, 2]]) is None
    assert check_voice_kwarg([PAT, 1.0, 0]).where == 'compact.n_reps'


# =============================================================================
# I predicati condivisi che questa regola ha corretto
# =============================================================================

def test_factory_reads():
    assert factory_reads('Step') and factory_reads(' cubic ')
    assert not factory_reads('') and not factory_reads(None)


def test_wrap_null_e_ancora_un_compatto():
    """`is_compact_format` accetta `wrap: null` (vale il default)."""
    assert is_loop_block([PAT, 1.0, 2, None, None, None])
    assert not is_loop_block([PAT, 1.0, 2, None, None, 1])


def test_type_null_della_distribuzione_e_un_nome_sbagliato():
    """Il default `linear` vale per lo spec omesso, non per `{type: null}`."""
    assert check_time_distribution(None) is None
    assert check_time_distribution({'type': None}).kind == 'name'


def test_is_envelope_like():
    assert is_envelope_like([[0, 1]])
    assert is_envelope_like({'points': None})
    assert not is_envelope_like([])
    assert not is_envelope_like([1, 2, 3])
    assert not is_envelope_like({'a': 1})


def test_un_espressione_a_piu_argomenti_tace():
    """PGE #309: la classe del regex ha la virgola, `(max(1, 2))` si valuta."""
    assert check_envelope_shape([[0, '(max(1, 2))'], [1, 2], 'x']) is None
