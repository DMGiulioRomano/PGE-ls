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

    for getter in ('get_dephase_keys', 'get_stream_context_keys',
                   'get_grain_envelope_names', 'get_distribution_modes'):
        assert set(getattr(snap_bridge, getter)()) == set(getattr(src_bridge, getter)()), (
            f"Lo snapshot perde superficie su {getter}()"
        )
    # Regressione del bug snapshot: le dephase keys non devono essere vuote.
    assert snap_bridge.get_dephase_keys()
