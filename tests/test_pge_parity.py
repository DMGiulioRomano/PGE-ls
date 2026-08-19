"""
tests/test_pge_parity.py

Test di PARITÀ tra i mirror statici di PGE-ls e la superficie reale di PGE.

PGE-ls replica a mano parti della superficie pubblica di PythonGranularEngine
(nomi strategy, accordi, bounds delle unità pitch, nomi finestra, bounds dei
parametri). Questi mirror possono divergere dal motore senza che nessun altro
test se ne accorga: la suite "normale" verifica i valori ricopiandoli a mano
nelle fixture, non importandoli da PGE.

Questo modulo importa il sorgente PGE reale e confronta. Salta in modo pulito
se PGE non è disponibile (sviluppo locale senza checkout sibling); in CI il
repo PythonGranularEngine viene clonato e il path passato via env PGE_SRC, così
la parità gira davvero.

Drift storici che questo test avrebbe intercettato:
  - pan strategy rinominate linear/random/additive -> range/stochastic/step
    (PGE 1845ad3)
  - bound minimo pitch ratio 0.125 -> 0.001 (PGE 5219aa3)
  - alias finestra 'triangle' assente lato LS
"""
import os
import sys
from pathlib import Path

import pytest


def _resolve_pge_src():
    """Path al 'src' di PythonGranularEngine: env PGE_SRC o checkout sibling."""
    env = os.environ.get('PGE_SRC')
    if env and Path(env).exists():
        return str(Path(env).resolve())
    sibling = (Path(__file__).resolve().parent.parent.parent
               / 'PythonGranularEngine' / 'src')
    if sibling.exists():
        return str(sibling)
    return None


PGE_SRC = _resolve_pge_src()

pytestmark = pytest.mark.skipif(
    PGE_SRC is None,
    reason="PGE src non trovato: imposta PGE_SRC o clona PythonGranularEngine "
           "come sibling di PGE-ls.",
)


# Package top-level di PGE: importarli inquina sys.modules. Il teardown del
# fixture li rimuove per non perturbare test successivi (es. test_schema_bridge,
# che importa progetti 'parameters' sintetici via from_python_path).
# 'pge' e' il package unico del layout corrente (refactor PGE PR #162);
# gli altri sono i top-level del layout legacy flat.
_PGE_TOP_PACKAGES = (
    'pge',
    'parameters', 'strategies', 'controllers', 'shared',
    'core', 'engine', 'envelopes', 'export', 'rendering',
)


@pytest.fixture(scope='module')
def pge():
    """Importa i moduli sorgente di PGE (eseguito solo se non skippato)."""
    added_path = PGE_SRC not in sys.path
    if added_path:
        sys.path.insert(0, PGE_SRC)
    before = set(sys.modules)
    from types import SimpleNamespace
    # Import dual-layout: 'pge.<mod>' (corrente) o '<mod>' flat (legacy).
    from granular_ls.schema_bridge import _import_pge_module
    _pitch_strat = _import_pge_module('strategies.voice_pitch_strategy')
    VOICE_PITCH_STRATEGIES = _pitch_strat.VOICE_PITCH_STRATEGIES
    CHORD_INTERVALS = _pitch_strat.CHORD_INTERVALS
    VOICE_ONSET_STRATEGIES = _import_pge_module(
        'strategies.voice_onset_strategy').VOICE_ONSET_STRATEGIES
    VOICE_POINTER_STRATEGIES = _import_pge_module(
        'strategies.voice_pointer_strategy').VOICE_POINTER_STRATEGIES
    VOICE_PAN_STRATEGIES = _import_pge_module(
        'strategies.voice_pan_strategy').VOICE_PAN_STRATEGIES
    PITCH_UNIT_PRESETS = _import_pge_module(
        'parameters.pitch_unit').PITCH_UNIT_PRESETS
    GRANULAR_PARAMETERS = _import_pge_module(
        'parameters.parameter_definitions').GRANULAR_PARAMETERS
    WindowRegistry = _import_pge_module(
        'controllers.window_registry').WindowRegistry
    _stream_config = _import_pge_module('core.stream_config')
    StreamContext = _stream_config.StreamContext
    StreamConfig = _stream_config.StreamConfig
    from dataclasses import fields as dc_fields
    # Chiavi stream-level attese: campi di StreamContext (meno sample_dur_sec)
    # + campi di StreamConfig (meno il riferimento context) + flag del Generator.
    expected_stream_keys = (
        [f.name for f in dc_fields(StreamContext) if f.name != 'sample_dur_sec']
        + [f.name for f in dc_fields(StreamConfig) if f.name != 'context']
        + ['solo', 'mute']
    )
    yield SimpleNamespace(
        voice_strategies={
            'pitch': VOICE_PITCH_STRATEGIES,
            'onset_offset': VOICE_ONSET_STRATEGIES,
            'pointer': VOICE_POINTER_STRATEGIES,
            'pan': VOICE_PAN_STRATEGIES,
        },
        CHORD_INTERVALS=CHORD_INTERVALS,
        PITCH_UNIT_PRESETS=PITCH_UNIT_PRESETS,
        GRANULAR_PARAMETERS=GRANULAR_PARAMETERS,
        WindowRegistry=WindowRegistry,
        expected_stream_keys=expected_stream_keys,
    )
    # Teardown: ripristina sys.modules/sys.path allo stato precedente.
    for name in set(sys.modules) - before:
        root = name.split('.')[0]
        if root in _PGE_TOP_PACKAGES:
            sys.modules.pop(name, None)
    if added_path and PGE_SRC in sys.path:
        sys.path.remove(PGE_SRC)


# =============================================================================
# Voice strategy names
# =============================================================================

# Superficie che il language server supporta in ANTICIPO sul motore mergiato:
# strategy presenti su un branch PGE non ancora in main. La parità le tollera
# come solo-LS finché l'engine non le assorbe (allora la voce diventa innocua e
# va rimossa). Serve solo per aggiunte deliberate, non maschera drift genuino:
# lo squilibrio solo-PGE resta un errore in ogni caso.
#
#   'chord_progression' — PGE issue #86 (branch claude/chord-interpolation),
#   PGE-ls issue #28. Rimuovere quando #86 è mergiata in main.
PENDING_LS_AHEAD = {
    'pitch': {'chord_progression'},
}


@pytest.mark.parametrize('dimension', ['pitch', 'onset_offset', 'pointer', 'pan'])
def test_voice_strategy_names_match(pge, dimension):
    from granular_ls.voice_strategies import get_strategies_for_dimension
    ls_names = set(get_strategies_for_dimension(dimension))
    pge_names = set(pge.voice_strategies[dimension].keys())
    pending = PENDING_LS_AHEAD.get(dimension, set())
    only_ls = ls_names - pge_names - pending
    only_pge = pge_names - ls_names
    assert not only_ls and not only_pge, (
        f"Drift nei nomi strategy per '{dimension}': "
        f"solo-LS={only_ls}, solo-PGE={only_pge} "
        f"(pending tollerati: {pending & (ls_names - pge_names)})"
    )


# =============================================================================
# Chord intervals
# =============================================================================

def test_chord_keys_match(pge):
    from granular_ls.voice_strategies import CHORD_INTERVALS as LS_CHORD
    assert set(LS_CHORD) == set(pge.CHORD_INTERVALS), (
        f"Drift accordi: solo-LS={set(LS_CHORD) - set(pge.CHORD_INTERVALS)}, "
        f"solo-PGE={set(pge.CHORD_INTERVALS) - set(LS_CHORD)}"
    )


def test_chord_intervals_match(pge):
    from granular_ls.voice_strategies import CHORD_INTERVALS as LS_CHORD
    for name, intervals in pge.CHORD_INTERVALS.items():
        assert tuple(LS_CHORD[name]) == tuple(intervals), (
            f"Intervalli diversi per '{name}': LS={LS_CHORD[name]} PGE={intervals}"
        )


# =============================================================================
# Pitch unit bounds
# =============================================================================

def test_pitch_unit_bounds_match(pge):
    from granular_ls.pitch_units import PITCH_UNIT_PRESETS as LS_PRESETS
    assert set(LS_PRESETS) == set(pge.PITCH_UNIT_PRESETS)
    for key, factory in pge.PITCH_UNIT_PRESETS.items():
        bounds = factory().value_bounds()
        info = LS_PRESETS[key]
        assert info.min_val == bounds.min_val, f"{key}.min_val"
        assert info.max_val == bounds.max_val, f"{key}.max_val"
        assert info.max_range == bounds.max_range, f"{key}.max_range"
        assert info.variation_mode == bounds.variation_mode, f"{key}.variation_mode"


# =============================================================================
# Grain envelope / window names (inclusi gli alias)
# =============================================================================

def test_grain_envelope_names_match(pge):
    from granular_ls.schema_bridge import SchemaBridge
    bridge = SchemaBridge.from_python_path(PGE_SRC)
    ls_names = set(bridge.get_grain_envelope_names())
    pge_names = set(pge.WindowRegistry.all_names())
    assert ls_names == pge_names, (
        f"Drift finestre grano: solo-LS={ls_names - pge_names}, "
        f"solo-PGE={pge_names - ls_names}"
    )


# =============================================================================
# Parameter bounds (bridge vs GRANULAR_PARAMETERS)
# =============================================================================

def test_parameter_bounds_match(pge):
    from granular_ls.schema_bridge import SchemaBridge
    bridge = SchemaBridge.from_python_path(PGE_SRC)
    for name, b in pge.GRANULAR_PARAMETERS.items():
        raw = bridge.get_raw_bounds(name)
        assert raw is not None, f"Parametro '{name}' assente nel bridge"
        assert raw['min_val'] == b.min_val, f"{name}.min_val"
        assert raw['max_val'] == b.max_val, f"{name}.max_val"
        assert raw['min_range'] == b.min_range, f"{name}.min_range"
        assert raw['max_range'] == b.max_range, f"{name}.max_range"
        assert raw['variation_mode'] == b.variation_mode, f"{name}.variation_mode"


# =============================================================================
# Distribution modes
# =============================================================================

def test_distribution_modes_match(pge):
    from granular_ls.schema_bridge import SchemaBridge, _import_pge_module
    DistributionFactory = _import_pge_module(
        'shared.distribution_strategy').DistributionFactory
    bridge = SchemaBridge.from_python_path(PGE_SRC)
    ls_modes = set(bridge.get_distribution_modes())
    pge_modes = set(DistributionFactory._registry.keys())
    assert ls_modes == pge_modes


def test_range_anchors_match(pge):
    """L'enum di range_anchor del LS non deve divergere da RANGE_ANCHORS di PGE.

    Skip se l'engine precede la feature (RANGE_ANCHORS assente): la parità è un
    guardrail contro il drift, non un requisito che l'engine sia già aggiornato.
    Stessa filosofia dello skip di modulo quando PGE_SRC manca — si attiva da
    solo appena l'engine espone la costante.
    """
    from granular_ls.schema_bridge import SchemaBridge, _import_pge_module
    RANGE_ANCHORS = getattr(
        _import_pge_module('shared.distribution_strategy'),
        'RANGE_ANCHORS', None)
    if RANGE_ANCHORS is None:
        pytest.skip("engine precede RANGE_ANCHORS (range-anchor-mode non ancora "
                    "in questo checkout di PGE)")
    bridge = SchemaBridge.from_python_path(PGE_SRC)
    assert set(bridge.get_range_anchors()) == set(RANGE_ANCHORS)


def _literal_from_engine_source(relpath: str, name: str):
    """Legge un letterale di modulo dal sorgente PGE senza importarlo.

    `core/stream.py` tira dentro soundfile e numpy, che la CI del language
    server non installa (installa solo pygls, lsprotocol, PyYAML e pytest, per
    restare veloce). Importarlo qui farebbe fallire la parità proprio dove
    dovrebbe girare. L'AST basta: la costante è un letterale, e leggerla così
    non esegue nulla del motore.

    Ritorna None se il file o il nome non esistono — engine più vecchio.
    """
    import ast
    source = Path(PGE_SRC) / relpath
    if not source.exists():
        return None
    tree = ast.parse(source.read_text(encoding='utf-8'))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == name:
                try:
                    return ast.literal_eval(node.value)
                except ValueError:
                    return None
    return None


def test_grain_duration_units_match(pge):
    """L'enum di grain.duration_unit del LS non deve divergere da PGE.

    È il drift che ha prodotto l'issue #36: PGE ha aggiunto 'milliseconds' e il
    LS ha continuato a segnalare come errore uno YAML valido, perché la tupla
    era ricopiata a mano e nulla la confrontava con l'originale.
    """
    from granular_ls.providers.diagnostic_provider import _GRAIN_DURATION_UNITS
    engine_units = _literal_from_engine_source(
        'pge/core/stream.py', 'GRAIN_DURATION_UNITS')
    if engine_units is None:
        engine_units = _literal_from_engine_source(
            'core/stream.py', 'GRAIN_DURATION_UNITS')
    if engine_units is None:
        pytest.skip("engine precede GRAIN_DURATION_UNITS")
    assert set(_GRAIN_DURATION_UNITS) == set(engine_units)


def test_milliseconds_factor_matches(pge):
    """Il fattore di conversione dei millisecondi è ricopiato: se PGE lo cambia,
    i bound in ms del LS scivolano di un ordine di grandezza senza dirlo."""
    from granular_ls.providers.diagnostic_provider import (
        _GRAIN_DURATION_UNIT_SECONDS,
    )
    from granular_ls.schema_bridge import _import_pge_module
    factor = getattr(
        _import_pge_module('shared.constants'), 'SECONDS_PER_MILLISECOND', None)
    if factor is None:
        pytest.skip("engine precede SECONDS_PER_MILLISECOND")
    assert _GRAIN_DURATION_UNIT_SECONDS['milliseconds'] == factor


def test_non_seconds_units_all_have_a_factor(pge):
    """Ogni unità non-secondi dev'essere convertibile: una nuova unità che PGE
    aggiunge senza fattore qui produrrebbe bound in secondi su valori che
    secondi non sono."""
    from granular_ls.providers.diagnostic_provider import (
        _GRAIN_DURATION_UNITS, _GRAIN_DURATION_UNIT_SECONDS,
        _GRAIN_DURATION_UNIT_LABELS,
    )
    non_seconds = set(_GRAIN_DURATION_UNITS) - {'seconds'}
    assert non_seconds <= set(_GRAIN_DURATION_UNIT_SECONDS)
    assert non_seconds <= set(_GRAIN_DURATION_UNIT_LABELS)


# =============================================================================
# Stream context keys (StreamContext + StreamConfig + flag Generator)
# =============================================================================

def test_stream_context_keys_match(pge):
    from granular_ls.schema_bridge import SchemaBridge
    bridge = SchemaBridge.from_python_path(PGE_SRC)
    ls_keys = set(bridge.get_stream_context_keys())
    pge_keys = set(pge.expected_stream_keys)
    assert ls_keys == pge_keys, (
        f"Drift stream context keys: solo-LS={ls_keys - pge_keys}, "
        f"solo-PGE={pge_keys - ls_keys}"
    )


# =============================================================================
# Fedeltà dello snapshot: la modalità distribuzione (.vsix) non deve perdere
# superficie rispetto alla modalità --src.
# =============================================================================

def test_snapshot_roundtrip_preserves_surface(pge):
    import os
    import tempfile
    from granular_ls.schema_bridge import SchemaBridge

    src_bridge = SchemaBridge.from_python_path(PGE_SRC)
    snap = src_bridge.generate_snapshot()
    fd, path = tempfile.mkstemp(suffix='.json')
    try:
        with os.fdopen(fd, 'w') as fh:
            fh.write(snap)
        snap_bridge = SchemaBridge.from_snapshot(path)
    finally:
        os.unlink(path)

    for getter in ('get_deviation_probability_keys', 'get_stream_context_keys',
                   'get_grain_envelope_names', 'get_distribution_modes',
                   'get_range_anchors'):
        assert set(getattr(snap_bridge, getter)()) == set(getattr(src_bridge, getter)()), (
            f"Lo snapshot perde superficie su {getter}()"
        )
    # Regressione del bug snapshot: le deviation_probability keys non devono essere vuote.
    assert snap_bridge.get_deviation_probability_keys()


# =============================================================================
# grain.read_direction: il mirror decide come il motore (PGE #207)
# =============================================================================
#
# `granular_ls/read_direction.py` replica a mano la semantica della chiave —
# dominio a due valori, `step` imposto, guard sulle macro-forme — e i bound dei
# costruttori del registro delle distribuzioni temporali. Il motore quei bound
# li applica costruendo; il LS non ha niente da costruire, quindi li ricopia.
# È il punto del modulo che può divergere in silenzio, e questo test è il posto
# che se ne accorge: stessa domanda ai due lati, stessa risposta attesa.
#
# Il confronto è accetta/rifiuta, non sul messaggio: gli hint sono scritti per
# due superfici diverse (un errore di render, una diagnostica nell'editor) e
# pretenderli identici legherebbe il LS a una stringa del motore.

READ_DIRECTION_CORPUS = [
    # scalari, compresi i casi che i soli bounds [-1, 1] non distinguono
    1, -1, 1.0, -1.0, 0, 0.5, -0.5, 2, -2, None, True, False, 'avanti',
    [], {}, 10 ** 400,
    # envelope come lista di breakpoint
    [[0, 1], [12, -1]], [[0, 1], [12, 0.5]], [[0, 1]],
    [[0, 1, 'step'], [12, -1]], [[0, 1, 'linear'], [12, -1]],
    # dict {points, type}
    {'points': [[0, 1], [12, -1]]},
    {'type': 'step', 'points': [[0, 1], [12, -1]]},
    {'type': 'linear', 'points': [[0, 1], [12, -1]]},
    {'type': 'step'},
    {'points': [[0, 1], [12, -1]], 'time_unit': 'normalized'},
    # forma dict per-punto
    [{'t': 0, 'v': 1}, {'t': 5, 'v': -1}],
    [{'t': 'x', 'v': 1}],
    [{'t': 0, 'v': 1, 'type': 'linear'}],
    # BP group
    [[[0, 1], [5, -1]], 'step'], [[[0, 1], [5, -1]], 'linear'],
    [[[0, 1]], 'step'], [[], 'step'],
    # formato compatto: arità, segno, percentuali del pattern
    [[[0, 1], [50, -1]], 2.0, 2],
    [[[0, 1], [50, -1]], 2.0, 2, 'step'],
    [[[0, 1], [50, -1]], 2.0, 2, 'linear'],
    [[[0, 1], [50, -1]], 2.0, 0],
    [[[0, 1], [50, -1]], 2.0, True],
    [[[0, 1], [50, -1]], True, 2],
    [[[0, 1], [50, -1]], 0, 2],
    [[[0, 1], [150, -1]], 2.0, 2],
    [[[0, 1], [-10, -1]], 2.0, 2],
    [[[100, 1], [0, -1]], 2.0, 2],
    [[[50, 1], [50, -1]], 2.0, 2],
    [[], 2.0, 2],
    [[[[0, 1], [5, -1]], 'step'], 2.0, 2],
    # distribuzione temporale: nomi e bound dei costruttori
    [[[0, 1], [50, -1]], 2.0, 2, 'step', 'exponential'],
    [[[0, 1], [50, -1]], 2.0, 2, 'step', 'geo'],
    [[[0, 1], [50, -1]], 2.0, 2, 'step', 'bogus'],
    [[[0, 1], [50, -1]], 2.0, 2, 'step', None],
    [[[0, 1], [50, -1]], 2.0, 2, 'step', {'type': 'bogus'}],
    [[[0, 1], [50, -1]], 2.0, 2, 'step', {'type': 5}],
    [[[0, 1], [50, -1]], 2.0, 2, 'step', {'ratio': 1.5}],
    [[[0, 1], [50, -1]], 2.0, 2, 'step', {'type': 'geometric', 'ratio': 0}],
    [[[0, 1], [50, -1]], 2.0, 2, 'step', {'type': 'geometric', 'ratio': 1.5}],
    [[[0, 1], [50, -1]], 2.0, 2, 'step', {'type': 'exponential', 'rate': 0}],
    [[[0, 1], [50, -1]], 2.0, 2, 'step', {'type': 'exponential', 'rate': 2}],
    [[[0, 1], [50, -1]], 2.0, 2, 'step', {'type': 'exponential', 'ratio': 2}],
    [[[0, 1], [50, -1]], 2.0, 2, 'step', {'type': 'logarithmic', 'base': 1}],
    [[[0, 1], [50, -1]], 2.0, 2, 'step', {'type': 'logarithmic', 'base': 2}],
    [[[0, 1], [50, -1]], 2.0, 2, 'step', {'type': 'power', 'exponent': 'x'}],
    [[[0, 1], [50, -1]], 2.0, 2, 'step', {'type': 'power', 'exponent': 2}],
    [[[0, 1], [50, -1]], 2.0, 2, 'step', 'linear', True],
    # liste miste
    [[0, 1], [[[0, 1], [50, -1]], 5.0, 2]],
    [[0, 1], [[[0, 1], [50, -1]], 5.0, 2, 'linear']],
]


@pytest.mark.parametrize('raw', READ_DIRECTION_CORPUS,
                         ids=lambda v: repr(v)[:60])
def test_read_direction_mirror_matches_engine(pge, raw):
    from granular_ls.read_direction import check_read_direction
    from granular_ls.schema_bridge import _import_pge_module

    try:
        normalize = _import_pge_module(
            'parameters.read_direction').normalize_read_direction
    except Exception:
        pytest.skip("engine precede grain.read_direction (PGE #207 non ancora "
                    "in questo checkout)")

    InvalidFieldValueError = _import_pge_module(
        'shared.exceptions').InvalidFieldValueError

    try:
        normalize(raw)
        motore_rifiuta = False
    except InvalidFieldValueError:
        motore_rifiuta = True

    ls_rifiuta = check_read_direction(raw) is not None

    assert ls_rifiuta == motore_rifiuta, (
        f"Drift su {raw!r}: motore "
        f"{'rifiuta' if motore_rifiuta else 'accetta'}, "
        f"LS {'rifiuta' if ls_rifiuta else 'accetta'}"
    )


def test_read_direction_in_schema_bridge(pge):
    """La chiave arriva dal bridge con i metadati che la issue prevedeva."""
    from granular_ls.schema_bridge import SchemaBridge
    from granular_ls.read_direction import READ_DIRECTION_PATH

    bridge = SchemaBridge.from_python_path(PGE_SRC)
    param = next((p for p in bridge.get_all_parameters()
                  if p.yaml_path == READ_DIRECTION_PATH), None)
    if param is None:
        pytest.skip("engine precede grain.read_direction (PGE #207)")

    assert param.exclusive_group == 'grain_direction'
    assert (param.min_val, param.max_val) == (-1, 1)
    assert param.variation_mode == 'negate'
    # La chiave deviation_probability e' propria: 'reverse' resta legata alla
    # sua, quindi un vecchio deviation_probability non ribalta read_direction.
    assert 'read_direction' in bridge.get_deviation_probability_keys()


def test_time_distribution_names_match(pge):
    """I nomi replicati sono quelli del registro, alias compresi."""
    from granular_ls.read_direction import TIME_DISTRIBUTION_NAMES
    from granular_ls.schema_bridge import _import_pge_module

    factory = _import_pge_module(
        'envelopes.time_distribution').TimeDistributionFactory
    assert set(TIME_DISTRIBUTION_NAMES) == set(factory._DISTRIBUTIONS)


# =============================================================================
# deviation_probability: il mirror decide come il motore (PGE #209)
# =============================================================================
#
# `granular_ls/deviation_probability.py` replica il criterio con cui il motore
# costruisce un envelope da quel corpo. Il rischio non è simmetrico: un mirror
# più permissivo tace su uno YAML che non renderà, un mirror più severo segnala
# uno YAML che rende — e quello è il modo peggiore in cui un language server
# può sbagliarsi. Il corpus tiene insieme le forme valide, quelle malformate, e
# i casi che distinguono questa chiave da `grain.read_direction`.
#
# Le stringhe restano fuori dal corpus di proposito: il motore le rifiuta, ma
# il Generator valuta `(50/2)` a 25 su tutto lo YAML prima che il gate le veda,
# e il LS non ha modo di distinguere l'espressione dal refuso.

DEVIATION_PROBABILITY_CORPUS = [
    # scalari e le cinque scritture che disattivano (o no) la deviazione
    50, 0, 100, 150, -5, False, True, None, {},
    # forme valide
    [[0, 50], [10, 100]], [[0, 50]], [[0, 50, 'linear'], [10, 100]],
    {'points': [[0, 50], [10, 100]]}, {'points': [[0, 50]]},
    {'points': [[0, 50], [10, 100]], 'type': 'linear'},
    {'points': [[0, 50], [10, 100]], 'type': 'cubic'},
    {'points': [[0, 50], [10, 100]], 'type': 'step'},
    {'points': [[0, 50], [10, 100]], 'time_unit': 'normalized'},
    {'points': [[0, 50, 'linear'], [10, 100]]},
    [[[0, 50], [10, 100]], 'linear'],
    [{'t': 0, 'v': 50}, {'t': 10, 'v': 100}],
    [[[0, 50], [100, 100]], 10.0, 4],
    [[[0, 50], [100, 100]], 10.0, 4, 'linear'],
    [[[0, 50], [100, 100]], 10.0, 4, None],
    [[[0, 50], [100, 100]], 10.0, 4, 'linear', 'linear', True],
    [[[0, 50], [100, 100]], 10.0, 1],
    [[[0, 50]], 10.0, 4],
    [[[0, 50, 'linear'], [100, 100]], 10.0, 4],
    [[0, 50], [[[0, 50], [100, 100]], 5.0, 2]],
    [[[[0, 50], [10, 100]], 'linear'], [20, 30]],
    # corpi che il motore rifiuta
    [], ['x'], {'punti': [[0, 50]]}, {'type': 'linear'}, [0, 50], [1, 2, 3],
    [[0, 50], 'x'], {'points': 'x'}, {'points': []}, {'points': [[0, 50], 'x']},
    [[0, 50], [10, 'x']], [['x', 50], [10, 100]],
    [[0, 50, 'bogus'], [10, 100]], [[[0, 50], [10, 100]], 'bogus'],
    {'points': [[0, 50], [10, 100]], 'type': 'bogus'},
    [[[0, 50]], 'linear'], [[[0, 50], [10, 100]], None],
    [[[0, 50], [100, 100]], 10.0, 0], [[[0, 50], [100, 100]], 0, 4],
    [[[0, 50], [100, 100]], 10.0, 4, 'bogus'],
    [[[0, 50], [100, 100]], 'x', 4], [[[0, 50], [100, 100]], 10.0, 'x'],
    [[[0, 50], [100, 100]], 10.0, 2.5], [[[0, 50], [100, 100]], 10.0, -3],
    [[[0, 'x'], [100, 100]], 10.0, 4], [['x', [100, 100]], 10.0, 4],
    [[], 10.0, 4], [[[0, 50, 'bogus'], [100, 100]], 10.0, 4],
    [{'t': 0}], [{'t': 'x', 'v': 50}], [{'t': 0, 'v': 50, 'type': 'bogus'}],
    [[[0, 50], [10, 100]]],
    # i casi che questa chiave accetta e read_direction no: i guard di PGE #208
    # sono semantica del verso, non del formato envelope
    [[[0, 50], [150, 100]], 10.0, 4],
    [[[100, 50], [0, 100]], 10.0, 4],
    [[[0, 50], [100, 100]], 10.0, True],
    [[[0, 50], [100, 100]], True, 4],
    [[0, -50], [10, 500]],
    # distribuzione temporale del ciclo
    [[[0, 50], [100, 100]], 10.0, 4, 'linear', 'exp'],
    [[[0, 50], [100, 100]], 10.0, 4, 'linear', 'bogus'],
    [[[0, 50], [100, 100]], 10.0, 4, 'linear', None],
    [[[0, 50], [100, 100]], 10.0, 4, 'linear', {'type': 'geometric', 'ratio': 1.5}],
    [[[0, 50], [100, 100]], 10.0, 4, 'linear', {'type': 'geometric', 'ratio': 0}],
    [[[0, 50], [100, 100]], 10.0, 4, 'linear', {'type': 'exponential', 'rate': 0}],
    [[[0, 50], [100, 100]], 10.0, 4, 'linear', {'type': 'logarithmic', 'base': 1}],
    [[[0, 50], [100, 100]], 10.0, 4, 'linear', {'type': 'power', 'exponent': 2}],
    [[[0, 50], [100, 100]], 10.0, 4, 'linear', {'ratio': 1.5}],
    [[[0, 50], [100, 100]], 10.0, 4, 'linear', {'type': 5}],
]


@pytest.mark.parametrize('raw', DEVIATION_PROBABILITY_CORPUS,
                         ids=lambda v: repr(v)[:60])
def test_deviation_probability_mirror_matches_engine(pge, raw, tmp_path,
                                                     monkeypatch):
    from granular_ls.deviation_probability import check_envelope_body
    from granular_ls.schema_bridge import _import_pge_module

    try:
        GateFactory = _import_pge_module('parameters.gate_factory').GateFactory
    except Exception:
        pytest.skip("engine senza GateFactory importabile in questo checkout")

    # Il logger del motore apre un `logs/envelope_clips_*.log` relativo alla
    # cwd al primo gate costruito: senza questo chdir la parità lascerebbe
    # quei file dentro il repo del language server.
    monkeypatch.chdir(tmp_path)

    try:
        GateFactory.create_gate(deviation_probability={'volume': raw},
                                param_key='volume', duration=10.0)
        motore_rifiuta = False
    except Exception:
        motore_rifiuta = True

    ls_rifiuta = check_envelope_body(raw) is not None

    assert ls_rifiuta == motore_rifiuta, (
        f"Drift su {raw!r}: motore "
        f"{'rifiuta' if motore_rifiuta else 'accetta'}, "
        f"LS {'rifiuta' if ls_rifiuta else 'accetta'}"
    )


# =============================================================================
# Il tetto della banda sotto `range_anchor: min` (issue #37)
# =============================================================================
#
# Il motore rifiuta al parse una banda `[base, base + range]` che sfora il
# tetto del parametro, ma solo dove il massimo della somma è calcolabile da un
# solo lato. La diagnostica ne replica i confini: qui si verifica che
# accetti e rifiuti le stesse coppie, non che il messaggio coincida.

BAND_CEILING_CORPUS = [
    # (base, range) su `volume`, bounds [-120, 12] e range [0, 24]
    (-6, 24), (0, 12), (0, 12.5), (-120, 24), (6, 12), (5, 6), (-6, 18),
    (12, 0), (11.9, 0.05), (None, 24), (-6, None),
    ([[0, -60], [10, 6]], 12), (6, [[0, 1], [10, 20]]),
    ([[0, -60], [10, 6]], [[0, 1], [10, 20]]),
    ([[0, -60], [10, -20]], 12),
]


def _band_ceiling_bridge():
    """Bridge con la coppia volume / volume_range e i bound veri del motore."""
    from granular_ls.schema_bridge import SchemaBridge

    def spec(name, yaml_path):
        return {'name': name, 'yaml_path': yaml_path, 'default': 0.0,
                'is_smart': True, 'exclusive_group': None, 'group_priority': 0,
                'range_path': None, 'deviation_probability_key': None,
                'is_internal': False}

    def bounds(mn, mx):
        return {'min_val': mn, 'max_val': mx, 'min_range': 0.0,
                'max_range': 0.0, 'default_jitter': 0.0,
                'variation_mode': 'additive'}

    return SchemaBridge({
        'specs': [spec('volume', 'volume'), spec('volume_range', 'volume_range')],
        'bounds': {'volume': bounds(-120.0, 12.0),
                   'volume_range': bounds(0.0, 24.0)},
    })


@pytest.mark.parametrize('base,mod_range', BAND_CEILING_CORPUS,
                         ids=lambda v: repr(v)[:40])
def test_band_ceiling_mirror_matches_engine(pge, base, mod_range, tmp_path,
                                            monkeypatch):
    from granular_ls.providers.diagnostic_provider import DiagnosticProvider
    from granular_ls.schema_bridge import _import_pge_module

    parser_mod = _import_pge_module('parameters.parser')
    GranularParser = getattr(parser_mod, 'GranularParser', None)
    ANCHOR_MIN = getattr(
        _import_pge_module('shared.distribution_strategy'), 'ANCHOR_MIN', None)
    if GranularParser is None or ANCHOR_MIN is None:
        pytest.skip("engine precede range_anchor / GranularParser")

    ParameterBoundError = _import_pge_module(
        'shared.exceptions').ParameterBoundError

    class _Ctx:
        sample_dur_sec = 10.0
        output_sr = 48000
        stream_id = 's1'
        rng_id = 'r1'
        duration = 10.0

    class _Cfg:
        context = _Ctx()
        time_mode = 'absolute'
        distribution_mode = 'uniform'
        range_anchor = ANCHOR_MIN
        duration = 10.0
        seed = None

    # Il logger del motore apre un file relativo alla cwd al primo parse.
    monkeypatch.chdir(tmp_path)

    # `base is None` è la chiave assente nello YAML: al parser non arriva mai
    # None, arriva il default della spec — è l'orchestrator a sostituirlo.
    base_engine = 0.0 if base is None else base

    try:
        GranularParser(_Cfg()).parse_parameter('volume', base_engine, mod_range)
        motore_rifiuta = False
    except ParameterBoundError:
        motore_rifiuta = True

    body = "    range_anchor: min\n"
    if base is not None:
        body += f"    volume: {base}\n"
    if mod_range is not None:
        body += f"    volume_range: {mod_range}\n"
    yaml = ("streams:\n  - stream_id: s1\n    duration: 10.0\n"
            "    sample: f.wav\n" + body)

    provider = DiagnosticProvider(_band_ceiling_bridge())
    ls_rifiuta = any(
        'banda' in d.message.lower() and 'range_anchor' in d.message
        for d in provider.get_diagnostics(yaml)
    )

    assert ls_rifiuta == motore_rifiuta, (
        f"Drift su base={base!r} range={mod_range!r}: motore "
        f"{'rifiuta' if motore_rifiuta else 'accetta'}, "
        f"LS {'rifiuta' if ls_rifiuta else 'accetta'}"
    )
