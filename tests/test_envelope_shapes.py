# tests/test_envelope_shapes.py
"""
Le Y di un envelope come le legge il builder del motore (issue #58).

`envelope_y_paths` e' il lettore unico delle Y di un corpo envelope: le
restituisce con il percorso che porta dal corpo al numero, cosi' chi ha il
nodo YAML ne ricava la riga. La regola e' quella di `Envelope.__init__` piu'
`EnvelopeBuilder.parse` in PGE: sono le Y dei breakpoint espansi, cioe' i
valori che `GranularParser._validate_and_clip` confronta con i bound.

La domanda che questi test fanno e' sempre la stessa: il builder vede una Y
in questo punto? Se si', il lettore la restituisce; se il builder non ci
vede un breakpoint (o rifiuta la forma prima dei bound), il lettore tace.
"""

import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest

from granular_ls.envelope_shapes import envelope_y_paths


class TestLeGrafieDelBuilder:
    """Ogni forma che `EnvelopeBuilder.parse` costruisce, una per test."""

    def test_breakpoint_nudi(self):
        assert envelope_y_paths([[0, 1], [10, 5]]) == [
            ((0, 1), 1), ((1, 1), 5)]

    def test_interp_per_punto(self):
        # [t, v, type]: la Y e' sempre l'elemento 1, il tipo non e' un valore.
        assert envelope_y_paths([[0, 1, 'step'], [10, 5]]) == [
            ((0, 1), 1), ((1, 1), 5)]

    def test_breakpoint_dict(self):
        # Il builder normalizza {t, v, type?} in [t, v, type?] prima di
        # guardarlo: la Y sta sotto la chiave `v`.
        assert envelope_y_paths(
            [{'t': 0, 'v': 1}, {'t': 10, 'v': 5, 'type': 'step'}]) == [
            ((0, 'v'), 1), ((1, 'v'), 5)]

    def test_ciclo_compatto_diretto(self):
        # Le Y stanno nel pattern: ne' `end_time` (10) ne' `n_reps` (4) lo
        # sono, e leggerli come Y darebbe un Error su uno YAML che rende.
        assert envelope_y_paths([[[0, 1], [100, 5]], 10, 4]) == [
            ((0, 0, 1), 1), ((0, 1, 1), 5)]

    def test_bp_group_diretto(self):
        assert envelope_y_paths([[[0, 1], [10, 5]], 'cubic']) == [
            ((0, 0, 1), 1), ((0, 1, 1), 5)]

    def test_misto(self):
        corpo = [
            [0, 1],
            [[[5, 0], [10, 5]], 'cubic'],
            [[[0, 2], [100, 3, 'step']], 20, 2],
            {'t': 30, 'v': 7},
        ]
        assert envelope_y_paths(corpo) == [
            ((0, 1), 1),
            ((1, 0, 0, 1), 0), ((1, 0, 1, 1), 5),
            ((2, 0, 0, 1), 2), ((2, 0, 1, 1), 3),
            ((3, 'v'), 7),
        ]

    def test_dict_type_points(self):
        # `Envelope.__init__` prende `points` e lo passa al builder: il resto
        # della lettura e' identico a quello della lista.
        assert envelope_y_paths(
            {'type': 'cubic', 'points': [[0, 1], [10, 5]]}) == [
            (('points', 0, 1), 1), (('points', 1, 1), 5)]

    def test_dict_senza_type(self):
        assert envelope_y_paths({'points': [[0, 1], [10, 5]]}) == [
            (('points', 0, 1), 1), (('points', 1, 1), 5)]

    def test_dict_con_ciclo_diretto(self):
        assert envelope_y_paths(
            {'type': 'step', 'points': [[[0, 1], [100, 5]], 10, 4]}) == [
            (('points', 0, 0, 1), 1), (('points', 0, 1, 1), 5)]

    def test_stringhe_numeriche_come_le_consegna_il_generator(self):
        # `_eval_math_expressions` converte "5" in 5 prima del parser: il
        # motore ci vede un breakpoint, e il lettore pure.
        assert envelope_y_paths([[0, '1'], [10, '5.5']]) == [
            ((0, 1), 1), ((1, 1), 5.5)]


class TestDoveIlBuilderNonVedeUnaY:
    """Il silenzio: niente Y dove il builder non legge un breakpoint."""

    @pytest.mark.parametrize('corpo', [
        5, 0.5, 'cubic', None, True, [], {},
        {'type': 'cubic'},             # dict senza `points`
        [0, 5],                        # breakpoint singolo senza parentesi:
                                       # il builder lo scorre e alza su `0`
        [[0, True], [10, False]],      # i bool non sono numeri, come in PGE
        [[0, 1, 5]],                   # tipo per punto non stringa
        [{'t': 0}],                    # dict senza `v`
        [{'t': 0, 'v': 1, 'type': 5}],  # dict con tipo non stringa
        [[{'t': 0, 'v': 1}, [10, 5]], 'cubic'],  # punto dict dentro un gruppo
    ], ids=repr)
    def test_nessuna_y(self, corpo):
        assert envelope_y_paths(corpo) == []

    def test_gli_elementi_malformati_non_spengono_gli_altri(self):
        # Il motore rifiuta la lista per l'elemento malformato, ma la Y del
        # breakpoint buono e' una Y: il lettore la restituisce.
        assert envelope_y_paths([[0, 1], 'x', [10, 5]]) == [
            ((0, 1), 1), ((2, 1), 5)]


class TestEspressioni:
    """Un'espressione fra parentesi rende l'elemento indecidibile.

    Il suo esito non si prevede senza rifare l'`eval`, e dove sta in uno slot
    strutturale (l'interp di un gruppo, l'`end_time` di un ciclo) decide la
    forma stessa: `'(1+1)'` diventa `2`, e il gruppo smette di esserlo. Si
    salta l'elemento intero, come fa gia' la fase 20 con i cicli.
    """

    def test_l_espressione_salta_il_suo_elemento_non_gli_altri(self):
        assert envelope_y_paths([[0, '(10/2)'], [10, 999]]) == [
            ((1, 1), 999)]

    def test_espressione_nell_interp_di_un_gruppo(self):
        assert envelope_y_paths([[[0, 1], [10, 999]], '(1+1)']) == []

    def test_espressione_in_un_ciclo_diretto(self):
        assert envelope_y_paths([[[0, '(pi)'], [100, 999]], 10, 4]) == []

    def test_espressione_in_un_gruppo_dentro_una_lista(self):
        corpo = [[0, 1], [[['(5)', 0], [10, 5]], 'cubic']]
        assert envelope_y_paths(corpo) == [((0, 1), 1)]
