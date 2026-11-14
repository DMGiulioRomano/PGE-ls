"""
tests/test_maps_parity.py

Parita' dei mirror delle mappe caotiche (`granular_ls/engine_expressions.py`,
`granular_ls/maps.py`) col motore vero, come `test_pge_parity.py`: salta se
PGE non c'e' (env `PGE_SRC` o checkout fratello).

I moduli leggeri del motore (l'interprete, i campi, il catalogo, `map_set`,
il riparo) si importano; le costanti dei controller si leggono dall'AST,
perche' quei moduli tirano dentro numpy, che la CI di qui non installa. La
valutazione al caricamento si confronta con il metodo vero, estratto dal
sorgente come fa `test_pge_parity._load_eval_math_expressions`.
"""
import ast
import math
import re
import sys
from pathlib import Path

import pytest

from granular_ls import engine_expressions as EX
from granular_ls import maps as M

from tests.test_pge_parity import PGE_SRC, _load_eval_math_expressions

pytestmark = pytest.mark.skipif(
    PGE_SRC is None, reason="PGE src non trovato: imposta PGE_SRC")


@pytest.fixture(scope='module')
def engine():
    """I moduli leggeri del motore. Come il fixture di `test_pge_parity`,
    toglie da `sys.modules` cio' che ha importato: `test_schema_bridge`
    importa progetti `pge` sintetici, e uno vero rimasto in cache li
    sostituirebbe."""
    added = PGE_SRC not in sys.path
    if added:
        sys.path.insert(0, PGE_SRC)
    before = set(sys.modules)
    from types import SimpleNamespace
    import importlib
    try:
        mods = {n: importlib.import_module(f'pge.{n}') for n in (
            'expressions.expression', 'expressions.fields', 'maps.catalog',
            'maps.map_set', 'engine.expression_shelter',
            'engine.half_evaluated')}
    except ModuleNotFoundError as err:
        pytest.skip(f"motore senza le mappe caotiche: {err}")
    yield SimpleNamespace(
        expression=mods['expressions.expression'],
        fields=mods['expressions.fields'],
        catalog=mods['maps.catalog'],
        map_set=mods['maps.map_set'],
        shelter=mods['engine.expression_shelter'],
        half_evaluated=mods['engine.half_evaluated'],
    )
    for name in set(sys.modules) - before:
        if name == 'pge' or name.startswith('pge.'):
            del sys.modules[name]
    if added and PGE_SRC in sys.path:
        sys.path.remove(PGE_SRC)


def _literal(relpath: str, name: str):
    """Un letterale a livello di modulo del motore, letto dall'AST."""
    tree = ast.parse((Path(PGE_SRC) / relpath).read_text(encoding='utf-8'))
    for node in tree.body:
        targets = (node.targets if isinstance(node, ast.Assign)
                   else [node.target] if isinstance(node, ast.AnnAssign) else [])
        if any(getattr(t, 'id', None) == name for t in targets):
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} non trovato in {relpath}")


def test_il_linguaggio(engine):
    ex = engine.expression
    assert {k: (v[1], v[2]) for k, v in ex.FUNCTIONS.items()} == EX.FUNCTIONS
    assert list(ex.FUNCTIONS) == list(EX.FUNCTIONS)
    assert tuple(ex.CONSTANTS) == EX.CONSTANTS
    assert ex.MAX_DEPTH == EX.MAX_DEPTH
    assert ex.NAME_PATTERN == EX.NAME_PATTERN
    assert engine.map_set.TIME_NAME == EX.TIME_NAME
    assert tuple(engine.map_set.MAP_EQUATION_VARIABLES) == EX.MAP_EQUATION_VARIABLES
    assert engine.shelter.LOAD_TIME_NAMES == EX.LOAD_TIME_NAMES


def test_i_campi(engine):
    assert tuple(engine.fields.EXPRESSION_FIELDS) == EX.EXPRESSION_FIELDS
    assert dict(engine.fields.EXPRESSION_REFUSALS) == EX.EXPRESSION_REFUSALS


def test_il_catalogo(engine):
    cat, ms = engine.catalog, engine.map_set
    assert tuple(cat.MAP_TYPES) == M.MAP_TYPES
    assert cat.MAP_TYPE_CUSTOM == M.MAP_TYPE_CUSTOM
    assert tuple(cat.SIN_BASED_TYPES) == M.SIN_BASED_TYPES
    assert tuple(ms.MAP_KEYS) == M.MAP_KEYS
    assert (ms.MAP_ITERATE_MAX, ms.MAP_TRANSIENT_MAX, ms.MAP_TRANSIENT_DEFAULT) == (
        M.MAP_ITERATE_MAX, M.MAP_TRANSIENT_MAX, M.MAP_TRANSIENT_DEFAULT)
    assert ms.MAP_CLOCK_GRAIN == M.MAP_CLOCK_GRAIN
    assert (ms.MAP_CUSTOM_X0, ms.MAP_CUSTOM_Y0) == (M.MAP_CUSTOM_X0, M.MAP_CUSTOM_Y0)
    assert frozenset(ms.RESERVED_MAP_NAMES) == M.reserved_map_names()
    assert set(cat.MAP_KINDS) == set(M.MAP_KINDS)
    for name, k in cat.MAP_KINDS.items():
        mine = M.MAP_KINDS[name]
        assert (k.x0, k.r_range, k.value_range, k.variables) == (
            mine.x0, mine.r_range, mine.value_range, mine.variables), name
        if k.variables == 2:
            assert (k.y0, k.y_range) == (mine.y0, mine.y_range), name


def test_i_modi_esclusivi():
    assert _literal('pge/controllers/density_controller.py',
                    'IOT_EXCLUSIVE_KEYS') == M.IOT_EXCLUSIVE_KEYS
    assert _literal('pge/controllers/pointer_controller.py',
                    'POSITION_EXCLUSIVE_KEYS') == M.POSITION_EXCLUSIVE_KEYS
    assert _literal('pge/engine/half_evaluated.py',
                    'STREAM_TEXT_KEYS') == M.STREAM_TEXT_KEYS
    assert _literal('pge/engine/half_evaluated.py',
                    'ENGINE_TOP_LEVEL_KEYS') == M.ENGINE_TOP_LEVEL_KEYS
    assert _literal('pge/core/stream.py', '_UNIT_SCALED_EXPRESSION_FIELDS'
                    ) == M.UNIT_SCALED_EXPRESSION_FIELDS


def test_i_parametri_che_avvolgono(engine):
    """Il bridge li legge dal vivo; qui si confronta col letterale che il
    motore dichiara, e che il bridge deve trovare."""
    from granular_ls.schema_bridge import SchemaBridge
    bridge = SchemaBridge.from_python_path(PGE_SRC)
    assert bridge.get_wrapping_parameters() == frozenset(_literal(
        'pge/parameters/parameter_definitions.py', 'WRAPPING_PARAMETERS'))


CORPUS = [
    "(0.02 + 0.08*m1)", "(m1.y)", "(clip(m1, 0, 1))", "(clip(m1, 0))",
    "(min(1, 2, m1))", "(max())", "(pow(2, 3, 4))", "(-m1**2)", "(2**3**2)",
    "(m1 % 2)", "(m1 ^ 2)", "(boh(m1))", "(m9)", "(t)", "(pi*e)", "(0.02 +)",
    "((m1)", "(m1))", "(.5 + 1e-3)", "(1e)", "(sin(t) * cos(m1, 2))",
    "-30 + 20*m1", "(" * 50 + "m1" + ")" * 50, "(" * 51 + "m1" + ")" * 51,
    "(a.b.c)", "", "   ", "sin(r*x)*cos(x)",
]


@pytest.mark.parametrize('testo', CORPUS)
def test_compila_come_il_motore(engine, testo):
    variabili = ('m1', 'm1.y', 't', 'x', 'r')
    try:
        compilata = engine.expression.compile_expression(testo, variabili)
        atteso = (True, tuple(compilata.names))
    except Exception:
        atteso = (False, ())
    mia = EX.compile_expression(testo, variabili)
    assert (mia.ok, mia.names) == atteso


@pytest.mark.parametrize('testo', ['(a) + (b)', '((a))', '(a', 'a)', ' (a) '])
def test_parentesi_esterne(engine, testo):
    assert EX.outer_parenthesized(testo) == engine.expression.outer_parenthesized(testo)


VALUTAZIONI = [
    '(10 + 5)', '(10/2)', '(pi * 2)', '(max(3, 7))', '(pow(2, 3))',
    '((10/2)/10)', '(10/2)/10', '-(90/2)', 'voce (1).wav', '(1,2)', '(abs)',
    '(7//2)', '(2*2)', '(int(3.7))', '(float(2))', '(min(1, 2, 3))',
    '(m1)', '(1/0)', 'take (2) di (3)', '(boh + 1) e basta', '11 - (2026)',
    '(pi)*2', '2*(3+4)', '(1+2)*(3+4)', '(-3)', '(abs(-2.5))', '(pi.real)',
    '(max(1.5, 2.5))', '(e)', '((1))', '(1 2)', '(pi.conjugate())',
    '(pi.hex)', '(e.imag)', '(3.real)',
]


@pytest.mark.parametrize('testo', VALUTAZIONI)
def test_la_valutazione_al_caricamento(testo, capsys):
    evaluate, _pattern = _load_eval_math_expressions()
    atteso = evaluate(testo)
    capsys.readouterr()
    mio = EX.load_time_value(testo)
    assert mio == atteso and type(mio) is type(atteso)


@pytest.mark.parametrize('testo', VALUTAZIONI)
def test_la_meta_valutata(engine, testo, capsys):
    """`half_evaluated_proposal` dice si' dove `find_half_evaluated` del
    motore, con la prova vera, trova la stringa."""
    evaluate, _ = _load_eval_math_expressions()
    import contextlib
    import io

    def probe(text):
        def numero(v):
            return isinstance(v, (int, float)) and not isinstance(v, bool)
        with contextlib.redirect_stdout(io.StringIO()):
            if numero(evaluate(text)):
                return None
            v = evaluate(f"({text.strip()})")
        return v if numero(v) else None

    trovata = engine.half_evaluated.find_half_evaluated(
        {'streams': [{'stream_id': 's', 'volume': testo}]}, probe)
    assert (EX.half_evaluated_proposal(testo) is not None) == (trovata is not None)
