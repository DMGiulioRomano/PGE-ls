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


def _range_unit_definitions():
    """Il modulo del motore che dichiara le unita' del `_range` (PGE #267)."""
    from granular_ls.schema_bridge import _import_pge_module
    defs = _import_pge_module('parameters.parameter_definitions')
    if not hasattr(defs, 'RANGE_UNITS'):
        pytest.skip("engine precede RANGE_UNITS (PGE #267)")
    return defs


def test_range_units_letti_dal_vivo(pge):
    """Vocabolario e dominio vengono dal motore, non dal fallback statico.

    L'ordine conta: la prima grafia e' il default che l'hover dichiara e che
    la completion propone per primo, e il motore la chiama canonica.
    """
    from granular_ls.schema_bridge import SchemaBridge
    defs = _range_unit_definitions()
    bridge = SchemaBridge.from_python_path(PGE_SRC)
    assert bridge.get_range_units() == list(defs.RANGE_UNITS)
    assert bridge.get_range_units()[0] == defs.RANGE_UNIT_DEFAULT
    assert bridge.get_relative_range_bounds() == tuple(
        defs.RELATIVE_RANGE_BOUNDS)


def test_la_grafia_relativa_e_quella_del_motore(pge):
    """`relative` e' l'unica grafia che il LS confronta per nome.

    Il vocabolario arriva dal vivo, ma quale delle sue voci voglia dire
    «frazione della base» e' semantica, non un elenco: la decide
    `range_unit_is_relative`, e qui si pretende che dica la stessa cosa.
    """
    from granular_ls.range_unit import RANGE_UNIT_RELATIVE, is_relative
    defs = _range_unit_definitions()
    assert RANGE_UNIT_RELATIVE == defs.RANGE_UNIT_RELATIVE
    for grafia in (*defs.RANGE_UNITS, None, '', 'Relative', 1):
        assert is_relative(grafia) == defs.range_unit_is_relative(grafia), grafia


def test_range_unit_path_letto_dal_vivo(pge):
    """Ogni `ParameterSpec.range_unit_path` del motore diventa un legame.

    E' il meccanismo dichiarativo di #267: cablare un secondo parametro nel
    motore e' una riga nello schema, e qui deve bastare la stessa riga.
    """
    from granular_ls.schema_bridge import SchemaBridge, _import_pge_module
    _range_unit_definitions()
    schema = _import_pge_module('parameters.parameter_schema')
    attesi = {
        spec.range_unit_path: spec.name
        for specs in schema.ALL_SCHEMAS.values() for spec in specs
        if getattr(spec, 'range_unit_path', None)
    }
    assert attesi, "il motore dichiara RANGE_UNITS ma nessun range_unit_path"
    bridge = SchemaBridge.from_python_path(PGE_SRC)
    letti = {l.unit_path: l.base.name for l in bridge.get_range_unit_bindings()}
    assert letti == attesi


def _literal_from_engine_source(relpath: str, name: str):
    """Legge un letterale di modulo dal sorgente PGE senza importarlo.

    `core/stream.py` tira dentro soundfile e numpy, che la CI del language
    server non installa (installa solo pygls, lsprotocol, PyYAML e pytest, per
    restare veloce). Importarlo qui farebbe fallire la parità proprio dove
    dovrebbe girare. L'AST basta: la costante è un letterale, e leggerla così
    non esegue nulla del motore.

    Legge `NOME = ...` e anche `NOME: T = ...`: l'annotazione è lo stile di
    casa nel motore, e un lettore che la ignora fa skippare il patto invece
    di farlo fallire.

    Ritorna None se il file o il nome non esistono — engine più vecchio.
    """
    import ast
    source = Path(PGE_SRC) / relpath
    if not source.exists():
        return None
    tree = ast.parse(source.read_text(encoding='utf-8'))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets = [node.target]
        else:
            continue
        for target in targets:
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


def _pointer_controller_literal(name: str):
    """Un letterale di `controllers/pointer_controller.py`, via AST.

    Il modulo importa `pge.envelopes.envelope`, cioe' numpy: stessa ragione
    di `core/stream.py` per non importarlo nella CI del language server.
    """
    for relpath in ('pge/controllers/pointer_controller.py',
                    'controllers/pointer_controller.py'):
        value = _literal_from_engine_source(relpath, name)
        if value is not None:
            return value
    return None


def test_il_lettore_ast_vede_l_assegnazione_annotata(tmp_path, monkeypatch):
    """`NOME: T = ...` e' lo stesso letterale di `NOME = ...`.

    L'annotazione e' lo stile di casa nel motore (`GRANULAR_PARAMETERS`,
    `PITCH_UNIT_PRESETS`). Se il lettore conosce solo `ast.Assign`, il giorno
    che `LOOP_UNITS` prende un tipo il patto non fallisce: skippa, dicendo
    che il motore «precede PGE #222» proprio mentre lo segue.
    """
    modulo = tmp_path / 'm.py'
    modulo.write_text(
        "from typing import Tuple\n"
        "ANNOTATA: Tuple[str, ...] = ('a', 'b')\n"
        "SOLO_TIPO: int\n",
        encoding='utf-8')
    monkeypatch.setattr(sys.modules[__name__], 'PGE_SRC', str(tmp_path))
    assert _literal_from_engine_source('m.py', 'ANNOTATA') == ('a', 'b')
    assert _literal_from_engine_source('m.py', 'SOLO_TIPO') is None


def test_loop_units_match(pge):
    """Il vocabolario di `pointer.loop_unit` (PGE #222) e' ricopiato a mano.

    Prima di #222 non c'era niente da confrontare: «tutto cio' che non e'
    normalized» valeva assoluto. Ora un'unita' fuori da `LOOP_UNITS` ferma il
    render, e il LS la segnala, la completa e ne decide i bounds: un valore
    che il motore aggiunge e il LS no diventerebbe un errore rosso su YAML
    valido — il drift di #36 su `duration_unit`, spostato di un blocco.

    L'ordine conta: la prima grafia e' la canonica, ed e' il default che la
    completion propone per primo.
    """
    from granular_ls.loop_unit import LOOP_UNITS, LOOP_UNIT_DEFAULT
    engine_units = _pointer_controller_literal('LOOP_UNITS')
    if engine_units is None:
        pytest.skip("engine precede LOOP_UNITS (PGE #222)")
    assert tuple(LOOP_UNITS) == tuple(engine_units)
    assert LOOP_UNIT_DEFAULT == engine_units[0]


def test_loop_unit_scope_match(pge):
    """Le chiavi che `loop_unit` interpreta sono le posizioni dei bounds.

    `_check_pointer_param_bounds`, l'hover e i semantic token applicano la
    stessa unita' a queste quattro chiavi, e l'avviso di migrazione le nomina
    nell'ordine del motore. Se il motore ne aggiungesse una (o togliesse
    `start`), il LS la misurerebbe nella scala sbagliata.
    """
    from granular_ls.loop_unit import LOOP_UNIT_SCOPE
    engine_scope = _pointer_controller_literal('_LOOP_UNIT_SCOPE')
    if engine_scope is None:
        pytest.skip("engine precede _LOOP_UNIT_SCOPE (PGE #222)")
    assert tuple(LOOP_UNIT_SCOPE) == tuple(engine_scope)


def test_loop_unit_non_eredita_piu_da_time_mode(pge):
    """La premessa di tutto il lato LS di #222, letta nel metodo che la fa.

    `_get_effective_unit_mode` era un mirror verbatim di
    `params.get('loop_unit') or config.time_mode`: se quella riga tornasse
    nel motore, il LS misurerebbe di nuovo in secondi posizioni che il motore
    scala. Si guarda il corpo di `_pre_normalize_loop_params` senza i
    docstring — dove `time_mode` resta nominato per spiegare perche' no.
    """
    import ast

    tree = None
    for relpath in ('pge/controllers/pointer_controller.py',
                    'controllers/pointer_controller.py'):
        path = Path(PGE_SRC) / relpath
        if path.exists():
            tree = ast.parse(path.read_text(encoding='utf-8'))
            break
    if tree is None:
        pytest.skip("pointer_controller.py non trovato in questo checkout")
    metodo = next((n for n in ast.walk(tree)
                   if isinstance(n, ast.FunctionDef)
                   and n.name == '_pre_normalize_loop_params'), None)
    if metodo is None:
        pytest.skip("engine senza _pre_normalize_loop_params")
    nomi = {n.attr for n in ast.walk(metodo) if isinstance(n, ast.Attribute)}
    nomi |= {n.id for n in ast.walk(metodo) if isinstance(n, ast.Name)}
    assert 'time_mode' not in nomi, (
        "_pre_normalize_loop_params legge di nuovo time_mode: l'unita' delle "
        "posizioni nel sample non e' piu' indipendente")


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


def test_output_sr_matches(pge):
    """`_OUTPUT_SR` è una costante del motore ricopiata a mano, e da essa
    dipendono tutti i conti sulla durata del grano: il fattore di `samples`,
    il minimo in ogni unità, i tetti nei messaggi. Se PGE cambia sample rate
    e questo numero resta indietro, il LS misura durate in un'unità che il
    motore non usa più — senza dirlo."""
    from granular_ls.providers.diagnostic_provider import _OUTPUT_SR
    from granular_ls.schema_bridge import _import_pge_module
    engine_sr = getattr(
        _import_pge_module('shared.constants'), 'DEFAULT_OUTPUT_SR', None)
    if engine_sr is None:
        pytest.skip("engine precede DEFAULT_OUTPUT_SR")
    assert _OUTPUT_SR == engine_sr


def test_grain_duration_min_e_un_campione(pge):
    """Il motore **sostituisce** il minimo di `grain_duration` col campione.

    `parse_parameter` chiama `get_parameter_definition(..., output_sr=...)`, e
    `output_sr` arriva da `StreamContext`, dove è una costante globale del
    motore e non una chiave dello YAML: l'override è quindi sempre in vigore,
    e il `min_val` del registro non è mai quello applicato. È la premessa di
    `_ENGINE_MIN_OVERRIDES`, e se il motore la togliesse il LS diventerebbe
    più permissivo del motore senza accorgersene.
    """
    from granular_ls.providers.diagnostic_provider import (
        _ENGINE_MIN_OVERRIDES, _OUTPUT_SR,
    )
    from granular_ls.schema_bridge import _import_pge_module

    definizioni = _import_pge_module('parameters.parameter_definitions')
    get_bounds = getattr(definizioni, 'get_parameter_definition', None)
    if get_bounds is None:
        pytest.skip("engine senza get_parameter_definition")

    dichiarato = get_bounds('grain_duration')
    applicato = get_bounds('grain_duration', output_sr=_OUTPUT_SR)

    assert applicato.min_val == 1.0 / _OUTPUT_SR
    assert applicato.min_val < dichiarato.min_val, (
        "l'override non abbassa più il minimo: se il registro è sceso sotto "
        "il campione, _ENGINE_MIN_OVERRIDES ora allarga invece di stringere"
    )
    assert _ENGINE_MIN_OVERRIDES['grain.duration'] == applicato.min_val
    assert applicato.max_val == dichiarato.max_val


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

def test_le_chiavi_scalate_dall_unita(pge):
    """`duration_unit` scala `duration` e `duration_range`, il range se assoluto.

    `_pre_normalize_grain_params` porta in secondi le chiavi nell'unita'
    dichiarata, e il parser le valida poi ciascuna coi suoi bound. Il language
    server converte i bound delle stesse chiavi (`_check_grain_duration_unit`),
    e da PGE #267 lascia fuori il range relativo, che e' una frazione della
    base: se il motore smettesse di scalarne una, ne aggiungesse una terza o
    cambiasse la regola del range, qui i due lati si direbbero cose diverse.

    Prima era una regex sul sorgente, e #267 l'ha rotta proprio come doveva:
    la tupla fissa e' diventata una scelta. Ora il metodo vero si esegue e si
    guarda che cosa sposta; la sola cosa letta dal sorgente e' l'insieme delle
    chiavi che il metodo nomina, perche' una terza chiave scalata non si
    vedrebbe da un dizionario di prova che non la contiene.
    """
    import ast

    pre_normalize = _load_pre_normalize_grain_params()

    def scalate(grain):
        dopo = pre_normalize({'grain': dict(grain)})['grain']
        return {k for k in grain if dopo[k] != grain[k]}

    grain = {'duration_unit': 'milliseconds', 'duration': 50,
             'duration_range': 5}
    assert scalate(grain) == {'duration', 'duration_range'}
    assert scalate({**grain, 'duration_range_unit': 'absolute'}) == {
        'duration', 'duration_range'}
    assert scalate({**grain, 'duration_range_unit': 'relative'}) == {
        'duration'}

    fn, _ = _pre_normalize_grain_params_ast()
    nominate = {
        elt.value
        for node in ast.walk(fn) if isinstance(node, ast.Tuple)
        for elt in node.elts
        if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
    }
    assert nominate == {'duration', 'duration_range'}, (
        "le chiavi scalate da grain.duration_unit non sono piu' quelle che "
        "_check_grain_duration_unit converte")


def test_bound_del_range_di_grain_duration(pge):
    """`grain.duration_range` porta i bound del range del padre.

    È la premessa della conversione: il LS divide per il fattore dell'unità i
    bound di `grain.duration_range`, non quelli di `grain.duration`, perché il
    motore valida il range contro `min_range`/`max_range`. Se il bridge
    smettesse di risolverli, dividerebbe i numeri sbagliati.
    """
    from granular_ls.schema_bridge import SchemaBridge

    bridge = SchemaBridge.from_python_path(PGE_SRC)
    per_path = {p.yaml_path: p for p in bridge.get_all_parameters()}
    base = per_path.get('grain.duration')
    rng = per_path.get('grain.duration_range')
    if base is None or rng is None:
        pytest.skip("engine senza grain.duration/duration_range nello schema")
    assert (rng.min_val, rng.max_val) == (base.min_range, base.max_range)


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
                   'get_range_anchors', 'get_range_units'):
        assert set(getattr(snap_bridge, getter)()) == set(getattr(src_bridge, getter)()), (
            f"Lo snapshot perde superficie su {getter}()"
        )
    # PGE #267: il legame fra una chiave `_range_unit` e i suoi due parametri.
    def legami(bridge):
        return {(l.unit_path, l.base.name, l.range.name)
                for l in bridge.get_range_unit_bindings()}
    assert legami(snap_bridge) == legami(src_bridge)
    assert (snap_bridge.get_relative_range_bounds()
            == src_bridge.get_relative_range_bounds())
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


# La coppia che trabocca sotto `grain.read_direction`: il motore valida il
# corpo intero in `normalize_read_direction` e trabocca solo dopo, costruendo
# l'envelope. Qui si percorre la stessa strada, e si confronta anche *quale*
# errore arriva per primo — con un verso sbagliato piu' avanti nella lista e'
# quello, non la coppia.
_CICLO_RD = [[0, 1], [100, -1]]

READ_DIRECTION_OVERFLOW_CORPUS = [
    ([_CICLO_RD, 10.0, 400, 'step', {'type': 'geometric', 'ratio': 10}], 'overflow'),
    ([_CICLO_RD, 10.0, 309, 'step', {'type': 'geometric', 'ratio': 10}], None),
    ([_CICLO_RD, 10.0, 309, 'step', {'type': 'geometric', 'ratio': 10.0}], 'overflow'),
    ({'points': [_CICLO_RD, 10.0, 400, 'step', {'type': 'geometric', 'ratio': 10}]},
     'overflow'),
    ([[0, 1], [_CICLO_RD, 20.0, 400, 'step', {'type': 'power', 'exponent': 150.0}]],
     'overflow'),
    ([[_CICLO_RD, 10.0, 400, 'step', {'type': 'geometric', 'ratio': 10}], [20, 0.5]],
     'valore'),
]


@pytest.mark.parametrize('raw, atteso', READ_DIRECTION_OVERFLOW_CORPUS,
                         ids=lambda v: repr(v)[:50])
def test_read_direction_overflow_come_il_motore(pge, raw, atteso, tmp_path,
                                               monkeypatch):
    from granular_ls.read_direction import check_read_direction
    from granular_ls.schema_bridge import _import_pge_module

    try:
        normalize = _import_pge_module(
            'parameters.read_direction').normalize_read_direction
        create_scaled_envelope = _import_pge_module(
            'envelopes.envelope').create_scaled_envelope
    except Exception:
        pytest.skip("engine senza read_direction o Envelope importabili")

    eccezioni = _import_pge_module('shared.exceptions')
    monkeypatch.chdir(tmp_path)  # il logger del builder scrive in logs/

    try:
        create_scaled_envelope(normalize(raw), 10.0, 'absolute')
        motore = None
    except eccezioni.ParameterBoundError:
        motore = 'overflow'
    except eccezioni.InvalidFieldValueError:
        motore = 'valore'

    issue = check_read_direction(raw)
    if issue is None:
        ls = None
    else:
        ls = 'overflow' if 'coppia a esplodere' in issue.hint else 'valore'

    assert motore == atteso, f"il motore non da' piu' {atteso!r} su {raw!r}"
    assert ls == motore


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
# La coppia (parametro, n_reps) che trabocca (PGE #212, issue #45)
# =============================================================================
#
# `time_distributions.check_time_distribution` non stima la soglia: rifà le
# operazioni del motore sugli stessi tipi, quindi i due lati devono rifiutare
# esattamente gli stessi `n_reps` — senza la banda di tolleranza che un mirror
# in un altro linguaggio deve accettare. Il bordo si cerca sul motore per
# bisezione e si confronta su una finestra attorno: abbastanza larga da
# contenere i due tratti che precedono il rifiuto pulito, il `ZeroDivisionError`
# di `geometric` (errore anche lui) e il collasso muto di `exponential` e
# `power` (che errore non è).

# (spec, n_reps oltre il bordo). Le grafie int e float dello stesso numero
# stanno entrambe: e' l'unico punto dove il motore le distingue.
OVERFLOW_PROBES = [
    ('geometric', 2000),
    ({'type': 'geometric', 'ratio': 10}, 400),
    ({'type': 'geometric', 'ratio': 10.0}, 400),
    ({'type': 'geo', 'ratio': 2}, 1100),
    ({'type': 'geometric', 'ratio': 2.0}, 1100),
    ({'type': 'geometric', 'ratio': 1.1}, 8000),
    ({'type': 'exponential', 'rate': 0.5}, 1100),
    ({'type': 'exp', 'rate': 0.9}, 7000),
    ({'type': 'power', 'exponent': 150.0}, 200),
    ({'type': 'power', 'exponent': 100.5}, 1200),
    # un `rate` intero fuori dai float: il motore trabocca da `n_reps: 2`
    ({'type': 'exponential', 'rate': 10 ** 400}, 2),
]


def _engine_rejects(factory, spec, n_reps) -> bool:
    """True se il motore non costruisce la distribuzione per questo `n_reps`.

    Qualunque eccezione, non solo `ParameterBoundError`: nel tratto che
    precede il bordo `geometric` alza un `ZeroDivisionError` nudo, e il render
    fallisce lo stesso.
    """
    try:
        factory.create(spec).calculate_distribution(10.0, n_reps)
    except Exception:
        return True
    return False


def _ls_rejects(spec, n_reps) -> bool:
    from granular_ls.time_distributions import check_time_distribution
    issue = check_time_distribution(spec, n_reps)
    return issue is not None and issue.kind == 'overflow'


@pytest.mark.parametrize('spec, oltre', OVERFLOW_PROBES,
                         ids=lambda v: repr(v)[:40])
def test_overflow_bordo_coincide_col_motore(pge, spec, oltre):
    from granular_ls.schema_bridge import _import_pge_module

    factory = _import_pge_module(
        'envelopes.time_distribution').TimeDistributionFactory

    # Il sondaggio stesso: se la sonda non trabocca piu', va aggiornata, non
    # confrontata su una finestra dove non succede niente.
    assert _engine_rejects(factory, spec, oltre), \
        f"il motore non rifiuta piu' {spec!r} a n_reps={oltre}"

    basso, alto = 1, oltre  # il motore accetta `basso`, rifiuta `alto`
    while alto - basso > 1:
        medio = (basso + alto) // 2
        if _engine_rejects(factory, spec, medio):
            alto = medio
        else:
            basso = medio

    for n_reps in range(max(1, alto - 40), alto + 4):
        motore = _engine_rejects(factory, spec, n_reps)
        assert _ls_rejects(spec, n_reps) == motore, (
            f"{spec!r} a n_reps={n_reps}: il motore "
            f"{'rifiuta' if motore else 'accetta'}, il language server no"
        )


@pytest.mark.parametrize('spec', [
    {'type': 'power', 'exponent': 150},
    {'type': 'exponential', 'rate': 2},
    {'type': 'geometric', 'ratio': 0.5},
    'logarithmic', 'linear',
], ids=repr)
def test_overflow_cio_che_non_trabocca_mai(pge, spec):
    """Le grafie per cui il motore non ha soglia: nessun rifiuto su entrambi."""
    from granular_ls.schema_bridge import _import_pge_module

    factory = _import_pge_module(
        'envelopes.time_distribution').TimeDistributionFactory
    for n_reps in (1, 2, 400, 1100, 3000):
        assert not _engine_rejects(factory, spec, n_reps)
        assert not _ls_rejects(spec, n_reps)


@pytest.mark.parametrize('spec, n_reps', [
    ({'type': 'geometric', 'ratio': 10}, 400),
    ('geometric', 2000),
    ({'type': 'exponential', 'rate': 0.5}, 1100),
    ({'type': 'power', 'exponent': 150.0}, 200),
    ({'type': 'exponential', 'rate': 10 ** 400}, 2),
], ids=lambda v: repr(v)[:40])
def test_overflow_la_coppia_e_quella_che_nomina_il_motore(pge, spec, n_reps):
    """Parametro, valore e formula del messaggio sono quelli dell'errore del
    motore: chi legge la diagnostica e poi il render deve riconoscerli."""
    from granular_ls.schema_bridge import _import_pge_module
    from granular_ls.time_distributions import check_time_distribution

    modulo = _import_pge_module('envelopes.time_distribution')
    bound_error = _import_pge_module('shared.exceptions').ParameterBoundError

    with pytest.raises(bound_error) as preso:
        modulo.TimeDistributionFactory.create(spec).calculate_distribution(
            10.0, n_reps)
    errore = preso.value

    issue = check_time_distribution(spec, n_reps)
    assert issue.param == errore.param_name
    assert issue.param_value == errore.value
    assert issue.formula in errore.hint


def test_overflow_rimedi_come_il_motore(pge):
    """I rimedi per parametro (PGE #216) sono quelli del motore."""
    from granular_ls.schema_bridge import _import_pge_module
    from granular_ls.time_distributions import OVERFLOW_REMEDIES

    modulo = _import_pge_module('envelopes.time_distribution')
    rimedi = getattr(modulo, '_RIMEDI_OVERFLOW', None)
    if rimedi is None:
        pytest.skip("engine precede i rimedi per parametro (PGE #216)")
    assert OVERFLOW_REMEDIES == rimedi


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
# Le stringhe che il `Generator` lascia intatte stanno qui: su quelle chiamare
# `create_gate` direttamente e' fedele, perche' l'evaluazione non le tocca.
# Quelle che invece trasforma — espressioni fra parentesi e stringhe numeriche
# — stanno in `EVALUATED_STRING_CORPUS`, che le fa passare prima dalla
# funzione vera del motore.

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
    # stringhe che l'evaluazione lascia intatte: il motore le vede come sono
    # scritte e le rifiuta, quindi la decisione torna al mirror.
    'abc', '', '1e3', 'true', '50%',
    [[0, 'abc'], [10, 100]], {'points': [[0, 'abc']]},
    # i casi che questa chiave accetta e read_direction no: i guard di PGE #208
    # sono semantica del verso, non del formato envelope
    [[[0, 50], [150, 100]], 10.0, 4],
    [[[100, 50], [0, 100]], 10.0, 4],
    [[[0, 50], [100, 100]], 10.0, True],
    [[[0, 50], [100, 100]], True, 4],
    # ...e i due che il motore RIFIUTA, perche' `false` vale `0`: sono
    # esattamente i casi che i guard sul segno e sull'arita' esistono per
    # prendere. Il sondaggio aveva misurato un bool e generalizzato a due.
    [[[0, 50], [100, 100]], 10.0, False],
    [[[0, 50], [100, 100]], False, 4],
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
    # la coppia (parametro, n_reps) che trabocca (PGE #212): il bordo dipende
    # dalla grafia, il collasso muto di `exponential` non e' un rifiuto, e il
    # `ZeroDivisionError` di `geometric` si'
    [[[0, 50], [100, 100]], 10.0, 400, 'linear', {'type': 'geometric', 'ratio': 10}],
    [[[0, 50], [100, 100]], 10.0, 309, 'linear', {'type': 'geometric', 'ratio': 10}],
    [[[0, 50], [100, 100]], 10.0, 309, 'linear', {'type': 'geometric', 'ratio': 10.0}],
    [[[0, 50], [100, 100]], 10.0, 1024, 'linear', {'type': 'exponential', 'rate': 0.5}],
    [[[0, 50], [100, 100]], 10.0, 1025, 'linear', {'type': 'exponential', 'rate': 0.5}],
    [[[0, 50], [100, 100]], 10.0, 1749, 'linear', 'geometric'],
    [[0, 50], [[[0, 50], [100, 100]], 20.0, 400, 'linear', {'type': 'geometric', 'ratio': 10}]],
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
# Le chiavi che il motore consulta davvero (review PR #48, rilievo 2)
# =============================================================================
#
# Nella mappa per-parametro il motore guarda solo le chiavi dello schema
# (`if param_key in deviation_probability`, gate_factory): tutte le altre
# cadono nel gate range-only senza passare dalla costruzione dell'envelope.
# Un corpo malformato sotto una chiave che non esiste non e' un errore per
# nessuno, e segnalarlo era un Error rosso su uno YAML che rende.

DEVIATION_PROBABILITY_MAP_CORPUS = [
    # chiavi che il motore non legge: qualunque corpo, nessuno dei due parla
    {'chiave_inesistente': []},
    {'durations': []},
    {'speed_ratio': 'abc'},
    {'volume_range': [[0, 50], 'x']},
    # chiavi vere: il corpo torna a contare, da entrambi i lati
    {'volume': []},
    {'volume': 'abc'},
    {'volume': [[0, 50], [10, 100]]},
    {'volume': 50},
    # miste: la chiave vera decide, quella ignota non aggiunge niente
    {'chiave_inesistente': [], 'volume': [[0, 50], [10, 100]]},
    {'chiave_inesistente': [[0, 50], [10, 100]], 'volume': []},
]


@pytest.mark.parametrize('mappa', DEVIATION_PROBABILITY_MAP_CORPUS,
                         ids=lambda v: repr(v)[:60])
def test_deviation_probability_map_mirror_matches_engine(pge, mappa, tmp_path,
                                                         monkeypatch):
    """Parita' sulla **mappa**, non sul singolo corpo: e' li' che si decide
    quali chiavi vengono lette."""
    from granular_ls.deviation_probability import check_global_value
    from granular_ls.schema_bridge import SchemaBridge, _import_pge_module

    try:
        GateFactory = _import_pge_module('parameters.gate_factory').GateFactory
    except Exception:
        pytest.skip("engine senza GateFactory importabile in questo checkout")

    monkeypatch.chdir(tmp_path)
    known = SchemaBridge.from_python_path(PGE_SRC).get_deviation_probability_keys()

    # Il motore costruisce un gate per parametro: rifiuta la mappa se rifiuta
    # per almeno uno dei parametri che consulta.
    motore_rifiuta = False
    for param_key in known:
        try:
            GateFactory.create_gate(deviation_probability=mappa,
                                    param_key=param_key, duration=10.0)
        except Exception:
            motore_rifiuta = True
            break

    ls_rifiuta = check_global_value(mappa, known_keys=known) is not None

    assert ls_rifiuta == motore_rifiuta, (
        f"Drift su {mappa!r}: motore "
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
    # sfora anche da centrata (11 + 12 = 23 > 12): il motore la rifiuta lo
    # stesso, ma li' cambiare ancora non sarebbe la via d'uscita.
    (11, 24),
    ([[0, -60], [10, 6]], 12), (6, [[0, 1], [10, 20]]),
    ([[0, -60], [10, 6]], [[0, 1], [10, 20]]),
    ([[0, -60], [10, -20]], 12),
    # macro-forme: le Y stanno dentro l'elemento 0, e l'elemento 1 e'
    # l'`end_time` del ciclo o l'interp del gruppo. Il motore espande e prende
    # il picco vero; leggere l'elemento 1 come Y darebbe 10.0 al posto di 0.
    ([[[0, 0], [100, 0]], 10.0, 2], 4),
    ([[0, -60], [[[0, 0], [100, 0]], 10.0, 2]], 4),
    ([[0, -60], [[[0, 9], [100, 9]], 10.0, 2]], 4),
    ([[[0, 0], [10, 0]], 'linear'], 4),
    ([[0, -60], [[[0, 0], [10, 0]], 'linear']], 4),
    # stringhe numeriche: il Generator le converte prima che il parser le
    # veda, quindi la banda sfora davvero. Il mirror deve normalizzare come
    # lui invece di rinunciare al confronto.
    ('-6', 24), (-6, '24'), ('-6', '24'),
    ([[0, '-6'], [10, '-6']], 24), (-6, [[0, '24'], [10, '24']]),
    # e le stesse coppie sotto il tetto, per non scambiare la conversione con
    # un Error a prescindere
    ('-60', 24), (-6, '6'),
]


def _yaml_scalar(value) -> str:
    """Il valore come si scrive nello YAML.

    Le stringhe si riquotano: senza, `'-6'` finirebbe nel documento come il
    numero `-6` e il caso da verificare sparirebbe. Dentro le liste ci pensa
    gia' `repr` di Python, che e' flow YAML valido.
    """
    if isinstance(value, str):
        return f'"{value}"'
    return str(value)


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

    # Al parser i valori arrivano gia' passati dal Generator, non come sono
    # scritti: senza questa riga le stringhe numeriche verrebbero confrontate
    # con una pipeline che nel motore non esiste.
    evaluate, _ = _load_eval_math_expressions()
    base_engine = evaluate(base_engine)
    range_engine = evaluate(mod_range)

    try:
        GranularParser(_Cfg()).parse_parameter('volume', base_engine,
                                               range_engine)
        motore_rifiuta = False
    except ParameterBoundError:
        motore_rifiuta = True

    body = "    range_anchor: min\n"
    if base is not None:
        body += f"    volume: {_yaml_scalar(base)}\n"
    if mod_range is not None:
        body += f"    volume_range: {_yaml_scalar(mod_range)}\n"
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


# =============================================================================
# La banda relativa: `grain.duration_range_unit` (PGE #267, issue #50)
# =============================================================================
#
# La stessa domanda al motore e al language server: questo blocco grain rende
# o no? Il motore risponde con la sua catena vera, nell'ordine in cui la
# percorre — `Generator._eval_math_expressions` sulle stringhe,
# `Stream._pre_normalize_grain_params` sull'unita' della durata (che lascia
# fuori il range relativo), `ParameterOrchestrator._range_unit_from_spec` sul
# vocabolario e sul range mancante, `GranularParser.parse_parameter` sui bound
# e sul tetto della banda. Nessuno di questi pezzi importa numpy, e
# `stream.py` — che invece lo importa — non si importa: il metodo si estrae
# dal sorgente come `_eval_math_expressions`.
#
# Il confronto e' accetta/rifiuta, sull'intero blocco: i messaggi sono scritti
# per due superfici diverse.

GRAIN_RANGE_UNIT_CORPUS = [
    # (id, righe del blocco grain, range_anchor)
    ('assoluto', "duration: 0.05\nduration_range: 0.5", 'center'),
    ('assoluto-fuori', "duration: 0.05\nduration_range: 1.5", 'center'),
    ('relativo', "duration: 0.05\nduration_range: 0.5\n"
                 "duration_range_unit: relative", 'center'),
    ('relativo-fuori', "duration: 0.05\nduration_range: 1.5\n"
                       "duration_range_unit: relative", 'center'),
    ('relativo-stringa', 'duration: 0.05\nduration_range: "1.5"\n'
                         'duration_range_unit: relative', 'center'),
    ('relativo-envelope', "duration: 0.05\n"
                          "duration_range: [[0, 0.2], [10, 0.8]]\n"
                          "duration_range_unit: relative", 'center'),
    ('relativo-envelope-fuori', "duration: 0.05\n"
                                "duration_range: [[0, 0.2], [10, 1.5]]\n"
                                "duration_range_unit: relative", 'center'),
    # Il breakpoint dict `{t, v, type?}`: il motore lo normalizza in `[t, v]`.
    ('relativo-envelope-dict', "duration: 0.05\n"
                               "duration_range: [{t: 0, v: 0.2}, "
                               "{t: 10, v: 0.8}]\n"
                               "duration_range_unit: relative", 'center'),
    ('relativo-envelope-dict-fuori', "duration: 0.05\n"
                                     "duration_range: [{t: 0, v: 0.2}, "
                                     "{t: 10, v: 1.5}]\n"
                                     "duration_range_unit: relative",
     'center'),
    ('relativo-points-dict-fuori', "duration: 0.05\n"
                                   "duration_range: {type: linear, points: "
                                   "[{t: 0, v: 0.2}, {t: 10, v: 1.5}]}\n"
                                   "duration_range_unit: relative", 'center'),
    ('assoluto-esplicito', "duration: 0.05\nduration_range: 0.5\n"
                           "duration_range_unit: absolute", 'center'),
    # Il vocabolario, la chiave vuota e il range mancante.
    ('grafia-sconosciuta', "duration: 0.05\nduration_range: 0.5\n"
                           "duration_range_unit: relativo", 'center'),
    ('grafia-maiuscola', "duration: 0.05\nduration_range: 0.5\n"
                         "duration_range_unit: Relative", 'center'),
    ('grafia-null', "duration: 0.05\nduration_range: 0.5\n"
                    "duration_range_unit: null", 'center'),
    ('grafia-vuota', "duration: 0.05\nduration_range: 0.5\n"
                     "duration_range_unit:", 'center'),
    ('relativo-senza-range', "duration: 0.05\n"
                             "duration_range_unit: relative", 'center'),
    ('relativo-range-null', "duration: 0.05\nduration_range: null\n"
                            "duration_range_unit: relative", 'center'),
    ('assoluto-senza-range', "duration: 0.05\n"
                             "duration_range_unit: absolute", 'center'),
    # L'unita' della durata: scala il range assoluto, non la frazione.
    ('ms-relativo', "duration_unit: milliseconds\nduration: 50\n"
                    "duration_range: 0.5\nduration_range_unit: relative",
     'center'),
    ('ms-relativo-cinque', "duration_unit: milliseconds\nduration: 50\n"
                           "duration_range: 5\nduration_range_unit: relative",
     'center'),
    ('ms-assoluto-cinque', "duration_unit: milliseconds\nduration: 50\n"
                           "duration_range: 5", 'center'),
    ('ms-assoluto-fuori', "duration_unit: milliseconds\nduration: 50\n"
                          "duration_range: 1500", 'center'),
    ('campioni-relativo', "duration_unit: samples\nduration: 2400\n"
                          "duration_range: 0.9\nduration_range_unit: relative",
     'center'),
    ('campioni-relativo-fuori', "duration_unit: samples\nduration: 2400\n"
                                "duration_range: 2400\n"
                                "duration_range_unit: relative", 'center'),
    # Il tetto della banda sotto `min`: base + range * |base|.
    ('tetto-relativo', "duration: 8\nduration_range: 0.5\n"
                       "duration_range_unit: relative", 'min'),
    ('tetto-assoluto', "duration: 8\nduration_range: 0.5", 'min'),
    ('tetto-relativo-dentro', "duration: 6\nduration_range: 0.5\n"
                              "duration_range_unit: relative", 'min'),
    ('tetto-relativo-ms', "duration_unit: milliseconds\nduration: 8000\n"
                          "duration_range: 0.5\nduration_range_unit: relative",
     'min'),
    ('tetto-relativo-base-envelope', "duration: [[0, 1], [10, 8]]\n"
                                     "duration_range: 0.5\n"
                                     "duration_range_unit: relative", 'min'),
    ('tetto-relativo-base-dict', "duration: [{t: 0, v: 1}, {t: 10, v: 8}]\n"
                                 "duration_range: 0.5\n"
                                 "duration_range_unit: relative", 'min'),
    ('tetto-relativo-base-dict-dentro', "duration: [{t: 0, v: 1}, "
                                        "{t: 10, v: 6}]\n"
                                        "duration_range: 0.5\n"
                                        "duration_range_unit: relative",
     'min'),
    ('tetto-relativo-range-envelope', "duration: 8\n"
                                      "duration_range: [[0, 0.1], [10, 0.5]]\n"
                                      "duration_range_unit: relative", 'min'),
    ('tetto-relativo-base-default', "duration_range: 0.5\n"
                                    "duration_range_unit: relative", 'min'),
    ('tetto-relativo-center', "duration: 8\nduration_range: 0.5\n"
                              "duration_range_unit: relative", 'center'),
]


def _pre_normalize_grain_params_ast():
    """Il nodo AST di `Stream._pre_normalize_grain_params` e il suo file."""
    import ast

    relpath = next((r for r in ('pge/core/stream.py', 'core/stream.py')
                    if (Path(PGE_SRC) / r).exists()), None)
    if relpath is None:
        pytest.skip("core/stream.py non trovato in questo checkout")
    tree = ast.parse((Path(PGE_SRC) / relpath).read_text(encoding='utf-8'))
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef)
               and n.name == '_pre_normalize_grain_params'), None)
    if fn is None:
        pytest.skip("engine senza _pre_normalize_grain_params")
    return fn, relpath


def _load_pre_normalize_grain_params():
    """`Stream._pre_normalize_grain_params`, il metodo vero, senza stream.py.

    `core/stream.py` importa numpy e soundfile; il metodo no. Si estrae dal
    sorgente e si esegue con i nomi che usa, presi dai moduli del motore che
    li definiscono (quelli si importano senza numpy) o, per i letterali di
    `stream.py`, riletti via AST.
    """
    import ast
    from granular_ls.schema_bridge import _import_pge_module

    fn, relpath = _pre_normalize_grain_params_ast()
    sorgente = Path(PGE_SRC) / relpath
    defs = _range_unit_definitions()
    exc = _import_pge_module('shared.exceptions')
    namespace = {
        'GRAIN_DURATION_UNITS': _literal_from_engine_source(
            relpath, 'GRAIN_DURATION_UNITS'),
        '_GRAIN_DURATION_UNIT_LABELS': _literal_from_engine_source(
            relpath, '_GRAIN_DURATION_UNIT_LABELS'),
        'InvalidFieldValueError': exc.InvalidFieldValueError,
        'MissingFieldError': exc.MissingFieldError,
        'SECONDS_PER_MILLISECOND': _import_pge_module(
            'shared.constants').SECONDS_PER_MILLISECOND,
        'range_unit_is_relative': defs.range_unit_is_relative,
        'scale_raw_param_values': _import_pge_module(
            'envelopes.envelope').scale_raw_param_values,
    }
    modulo = ast.Module(body=[fn], type_ignores=[])
    ast.fix_missing_locations(modulo)
    exec(compile(modulo, str(sorgente), 'exec'), namespace)
    grezza = namespace['_pre_normalize_grain_params']

    class _Stream:
        stream_id = 's1'

    return lambda params: grezza(_Stream(), params, 48000)


def _engine_rejects_grain(stream: dict, anchor: str) -> bool:
    """La catena del motore su `grain_duration`, dal Generator al parser."""
    from types import SimpleNamespace
    from granular_ls.schema_bridge import _import_pge_module

    evaluate, _ = _load_eval_math_expressions()
    pre_normalize = _load_pre_normalize_grain_params()
    exc = _import_pge_module('shared.exceptions')
    schema = _import_pge_module('parameters.parameter_schema')
    orchestrator = _import_pge_module(
        'parameters.parameter_orchestrator').ParameterOrchestrator
    GranularParser = _import_pge_module('parameters.parser').GranularParser
    spec = next(s for specs in schema.ALL_SCHEMAS.values() for s in specs
                if s.name == 'grain_duration')

    context = SimpleNamespace(sample_dur_sec=10.0, output_sr=48000,
                              stream_id='s1', rng_id='r1', duration=10.0)
    config = SimpleNamespace(context=context, time_mode='absolute',
                             distribution_mode='uniform', range_anchor=anchor,
                             duration=10.0, seed=None)
    try:
        params = pre_normalize(evaluate(stream))
        value = schema.resolve_yaml_path(params, spec.yaml_path, spec.default)
        range_val = schema.resolve_yaml_path(params, spec.range_path, None)
        unit = orchestrator._range_unit_from_spec(
            SimpleNamespace(_config=config), spec, params, range_val)
        GranularParser(config).parse_parameter(
            spec.name, value, range_val, range_unit=unit)
    except exc.ConfigError:
        return True
    return False


@pytest.mark.parametrize('caso,righe,anchor', GRAIN_RANGE_UNIT_CORPUS,
                         ids=[c[0] for c in GRAIN_RANGE_UNIT_CORPUS])
def test_range_unit_mirror_matches_engine(pge, caso, righe, anchor, tmp_path,
                                         monkeypatch):
    import yaml as pyyaml
    from granular_ls.providers.diagnostic_provider import DiagnosticProvider
    from granular_ls.schema_bridge import SchemaBridge
    from lsprotocol.types import DiagnosticSeverity

    # Il logger del motore apre un file relativo alla cwd al primo parse.
    monkeypatch.chdir(tmp_path)

    grain = ''.join(f"      {r}\n" for r in righe.split('\n'))
    testo = ("streams:\n  - stream_id: s1\n    onset: 0.0\n"
             "    duration: 10.0\n    sample: f.wav\n"
             f"    range_anchor: {anchor}\n    grain:\n" + grain)

    motore_rifiuta = _engine_rejects_grain(
        pyyaml.safe_load(testo)['streams'][0], anchor)

    bridge = SchemaBridge.from_python_path(PGE_SRC)
    ls_rifiuta = any(
        d.severity == DiagnosticSeverity.Error
        for d in DiagnosticProvider(bridge).get_diagnostics(testo)
    )

    assert ls_rifiuta == motore_rifiuta, (
        f"Drift su {caso}: motore "
        f"{'rifiuta' if motore_rifiuta else 'accetta'}, "
        f"LS {'rifiuta' if ls_rifiuta else 'accetta'}"
    )


# =============================================================================
# Le stringhe che il Generator trasforma: la pipeline reale, non il solo gate
# =============================================================================
#
# `Generator._eval_math_expressions` gira su **tutto** lo YAML prima che
# qualunque controller veda i valori, e ricorre dentro liste e dict. Trasforma
# due famiglie di stringhe: quelle con un'espressione fra parentesi (`(50/2)`
# arriva come `25`) e quelle che `int`/`float` accettano anche senza parentesi
# (`"50"` arriva come `50`), a qualunque profondita'. I due mirror la
# rispecchiano in due modi: tacciono sulle prime, il cui esito non e'
# prevedibile, e convertono le seconde come lui prima di guardare la forma.
#
# I corpus qui sopra non se ne accorgerebbero: interrogano `create_gate` e
# `normalize_read_direction`, cioè il gradino **a valle** dell'evaluazione, e
# le stringhe ne sono escluse di proposito. Questo corpus rimette davanti il
# gradino mancante, chiamando la funzione vera del motore.
#
# La parità qui è a senso unico, e va detto: si pretende che il motore accetti
# ⇒ il language server taccia. L'implicazione opposta no — su un'espressione
# che il motore rifiuta dopo averla valutata (`(abc)` resta stringa) il mirror
# tace lo stesso, perché prevederne l'esito vorrebbe dire rifare
# `_eval_math_expressions`. Tacere di troppo costa una diagnostica mancata;
# segnalare di troppo costa un errore rosso su uno YAML che rende.


def _load_eval_math_expressions():
    """La funzione vera del motore, senza importarne il modulo.

    `_eval_math_expressions` vive dentro `Generator`, e importare
    `pge.engine.generator` tira dentro numpy e soundfile — dipendenze del
    render che la CI del language server non installa (vedi lo step `install
    deps` in .github/workflows). Il corpo della funzione però non ne usa
    nessuna: solo `re` e `math`. Si estrae dal sorgente con `ast` e si compila
    da sola, così la parità gira davvero invece di skippare.

    Returns:
        `(evaluate, pattern)`: la funzione applicata a un oggetto YAML, e la
        stringa del regex con cui il motore riconosce un'espressione.
    """
    import ast
    import math
    import re as _re

    root = Path(PGE_SRC)
    for cand in (root / 'pge' / 'engine' / 'generator.py',
                 root / 'engine' / 'generator.py'):
        if cand.exists():
            sorgente = cand
            break
    else:
        pytest.skip("generator.py non trovato in questo checkout del motore")

    tree = ast.parse(sorgente.read_text(encoding='utf-8'))
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef)
               and n.name == '_eval_math_expressions'), None)
    if fn is None:
        pytest.skip("il motore non valuta piu' le espressioni in questo punto")

    modulo = ast.Module(body=[fn], type_ignores=[])
    ast.fix_missing_locations(modulo)
    namespace = {'math': math, 're': _re}
    exec(compile(modulo, str(sorgente), 'exec'), namespace)
    grezza = namespace['_eval_math_expressions']

    # Il primo argomento e' `self`, e serve solo alla ricorsione: si passa un
    # oggetto che rimanda alla funzione stessa.
    class _Portatore:
        def _eval_math_expressions(self, obj):
            return grezza(self, obj)

    # Il pattern letterale, per confrontarlo col mirror.
    pattern = next(
        (n.value.value for n in ast.walk(fn)
         if isinstance(n, ast.Assign)
         and any(getattr(t, 'id', None) == 'pattern' for t in n.targets)
         and isinstance(n.value, ast.Constant)
         and isinstance(n.value.value, str)),
        None,
    )
    return _Portatore()._eval_math_expressions, pattern


def test_math_expression_pattern_matches_engine(pge):
    """Il regex del mirror e' quello del motore, carattere per carattere.

    Se il motore allarga o restringe cio' che valuta, il mirror tace su un
    insieme di stringhe diverso: da una parte diagnostiche mancate, dall'altra
    falsi positivi. E' una riga sola, ed e' quella che va inseguita.
    """
    from granular_ls.envelope_shapes import _MATH_EXPRESSION_RE

    _, pattern = _load_eval_math_expressions()
    if pattern is None:
        pytest.skip("pattern non estraibile dal sorgente del motore")
    assert _MATH_EXPRESSION_RE.pattern == pattern


# Stringhe su cui si confronta la conversione, non la forma: quella coda della
# funzione il mirror la riproduce invece di tacere, quindi deve dare lo stesso
# risultato carattere per carattere.
NUMERIC_STRING_CORPUS = [
    '50', ' 50 ', '1.5', '.5', '-3', '0', '1_000',
    'abc', '', '1e3', 'true', '50%', 'nan', '1,5', '+', '- 3',
]


@pytest.mark.parametrize('testo', NUMERIC_STRING_CORPUS,
                         ids=lambda v: repr(v)[:20])
def test_numeric_string_value_matches_engine(pge, testo):
    """`numeric_string_value` decide come la coda di `_eval_math_expressions`.

    E' la riga che separa la stringa che il motore vede da quella che gli
    arriva come numero: sbagliarla in un verso segnala uno YAML che rende,
    nell'altro tace su uno che non rende.
    """
    from granular_ls.envelope_shapes import numeric_string_value

    evaluate, _ = _load_eval_math_expressions()
    atteso = evaluate(testo)
    ottenuto = numeric_string_value(testo)

    if isinstance(atteso, str):
        assert ottenuto is None, (
            f"{testo!r}: il motore la lascia stringa, il mirror la converte a "
            f"{ottenuto!r}")
    else:
        assert ottenuto == atteso, (
            f"{testo!r}: il motore consegna {atteso!r}, il mirror {ottenuto!r}")


# Corpi con un'espressione o una stringa numerica, nei posti dove il mirror
# prima non le vedeva.
# Ogni voce e' scelta perche' dopo l'evaluazione il motore la **accetta**: e'
# lo YAML che rende e che il language server segnalava in rosso.
EVALUATED_STRING_CORPUS = [
    '(50/2)',
    [[0, '(50/2)'], [10, 100]],
    [['(5*2)', 50], [20, 100]],
    [[0, '(50/2)', 'linear'], [10, 100]],
    {'points': [[0, '(50/2)'], [10, 100]]},
    {'points': [[0, '(50/2)']], 'type': 'linear'},
    [[[0, '(50/2)'], [100, 100]], 'linear'],
    [[[0, '(50/2)'], [100, 100]], 10.0, 4],
    [[[0, 50], [100, 100]], '(5*2)', 4],
    [[[0, 50], [100, 100]], 10.0, '(2*2)'],
    [{'t': 0, 'v': '(50/2)'}, {'t': 10, 'v': 100}],
    [[0, '(50/2)'], [[[0, 50], [100, 100]], 5.0, 2]],
    # stringhe numeriche senza parentesi: la coda della funzione le converte
    # lo stesso, e chiamare `create_gate` sul grezzo qui non sarebbe fedele.
    '50', '1.5', '.5',
    [[0, '50'], [10, '100']], [['0', 50], ['10', 100]],
    {'points': [[0, '50'], [10, 100]]},
    [[[0, '50'], [100, 100]], '10.0', '4'],
    # dentro il dict della distribuzione temporale: `_eval_math_expressions`
    # ricorre sui valori, quindi anche qui la stringa non e' il valore.
    [[[0, 50], [100, 100]], 10.0, 4, 'linear',
     {'type': 'geometric', 'ratio': '(3/2)'}],
    [[0, '(pi*10)'], [10, 100]],
]


@pytest.mark.parametrize('raw', EVALUATED_STRING_CORPUS,
                         ids=lambda v: repr(v)[:60])
def test_deviation_probability_mirror_non_segnala_cio_che_il_motore_accetta(pge, raw,
                                                             tmp_path,
                                                             monkeypatch):
    """Il motore costruisce il gate dal corpo valutato: il mirror deve tacere."""
    from granular_ls.deviation_probability import check_envelope_body
    from granular_ls.schema_bridge import _import_pge_module

    try:
        GateFactory = _import_pge_module('parameters.gate_factory').GateFactory
    except Exception:
        pytest.skip("engine senza GateFactory importabile in questo checkout")

    evaluate, _ = _load_eval_math_expressions()
    monkeypatch.chdir(tmp_path)  # il logger del motore scrive relativo alla cwd

    valutato = evaluate(raw)
    try:
        GateFactory.create_gate(deviation_probability={'volume': valutato},
                                param_key='volume', duration=10.0)
        motore_accetta = True
    except Exception:
        motore_accetta = False

    if not motore_accetta:
        pytest.skip(f"il motore rifiuta {valutato!r} anche dopo l'evaluazione: "
                    f"il mirror tace lo stesso, e va bene cosi'")

    assert check_envelope_body(raw) is None, (
        f"Falso positivo su {raw!r}: il Generator lo valuta a {valutato!r} e "
        f"il motore ne costruisce il gate, ma il language server lo segnala."
    )


# Gli stessi posti, per il verso di lettura: `(0-1)` e' -1, che e' un verso.
READ_DIRECTION_EVALUATED_CORPUS = [
    '(0-1)',
    '(1)',
    '1', '-1',
    [[0, '1'], [10, '-1']], [['0', 1], ['10', -1]],
    {'points': [[0, '1'], [10, '-1']]},
    [[0, '(0-1)'], [10, 1]],
    [['(5*2)', 1], [20, -1]],
    {'points': [[0, '(0-1)'], [10, 1]]},
    [[[0, '(0-1)'], [10, 1]], 'step'],
    [[[0, '(0-1)'], [50, 1]], 10.0, 4],
    [[[0, 1], [50, -1]], '(5*2)', 4],
    [{'t': 0, 'v': '(0-1)'}, {'t': 10, 'v': 1}],
]


@pytest.mark.parametrize('raw', READ_DIRECTION_EVALUATED_CORPUS,
                         ids=lambda v: repr(v)[:60])
def test_read_direction_mirror_non_segnala_cio_che_il_motore_accetta(pge, raw):
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

    evaluate, _ = _load_eval_math_expressions()
    valutato = evaluate(raw)
    try:
        normalize(valutato)
    except InvalidFieldValueError:
        pytest.skip(f"il motore rifiuta {valutato!r} anche dopo l'evaluazione")

    assert check_read_direction(raw) is None, (
        f"Falso positivo su {raw!r}: il Generator lo valuta a {valutato!r} e "
        f"il motore lo normalizza, ma il language server lo segnala."
    )


def test_il_silenzio_copre_anche_le_espressioni_che_il_motore_rifiuta(pge):
    """L'altra meta' della parita' a senso unico, dichiarata come tale.

    `(abc)` non si valuta: il motore stampa un warning e lascia la stringa,
    poi la rifiuta. Il mirror tace lo stesso — prevedere quali espressioni
    sopravvivono all'`eval` vorrebbe dire rifarlo. E' una diagnostica mancata,
    non un falso positivo: il costo sta dalla parte giusta.
    """
    from granular_ls.deviation_probability import check_envelope_body
    from granular_ls.read_direction import check_read_direction

    evaluate, _ = _load_eval_math_expressions()
    assert evaluate('(abc)') == '(abc)'
    assert check_envelope_body([[0, '(abc)'], [10, 100]]) is None
    assert check_read_direction([[0, '(abc)'], [10, 1]]) is None


# =============================================================================
# `pointer.start` non e' un numero (review PR #48, rilievo 6)
# =============================================================================
#
# Il guard del motore e' `not isinstance(self.start, (int, float))`
# (`PointerController._init_params`): qualunque cosa non sia un numero, non le
# sole strutture. Fra il testo e quel guard sta pero' il `Generator`, quindi la
# parita' si fa sul valore **valutato**, com'e' per gli altri due mirror.

POINTER_START_CORPUS = [
    'abc', '"1e3"', '"0.5"', '"(10/2)"', '"1"', '0.5', '-3', 'true', 'null',
    '', '[[0, 0], [10, 1]]', '{points: [[0, 0]]}', '[0, 1]',
]


def _pointer_start_engine_accetta(valore) -> bool:
    """Il motore costruisce un PointerController con questo `start`?

    Si intercetta il **solo** `InvalidFieldValueError`, che e' il rifiuto: un
    `except Exception` largo trasformerebbe in "rifiuta" anche il
    `ModuleNotFoundError` di numpy/soundfile, e la parita' misurerebbe
    l'assenza di una dipendenza invece del guard del motore.
    """
    from granular_ls.schema_bridge import _import_pge_module

    PointerController = _import_pge_module(
        'controllers.pointer_controller').PointerController
    stream_config = _import_pge_module('core.stream_config')
    InvalidFieldValueError = _import_pge_module(
        'shared.exceptions').InvalidFieldValueError
    contesto = stream_config.StreamContext.from_yaml(
        {'stream_id': 's1', 'sample': 'f.wav', 'duration': 10.0, 'onset': 0.0},
        sample_dur_sec=10.0,
    )
    try:
        PointerController({'start': valore},
                          stream_config.StreamConfig(context=contesto))
        return True
    except InvalidFieldValueError:
        return False


@pytest.mark.parametrize('scritto', POINTER_START_CORPUS,
                         ids=lambda v: repr(v)[:24])
def test_pointer_start_mirror_matches_engine(pge, scritto, tmp_path,
                                             monkeypatch):
    import yaml as _yaml

    from granular_ls.schema_bridge import SchemaBridge
    from granular_ls.providers.diagnostic_provider import DiagnosticProvider

    # Sonda su un valore che il motore accetta di sicuro: se qui vola
    # qualcosa, non e' il guard — sono le dipendenze che nella CI del
    # language server non ci sono.
    try:
        assert _pointer_start_engine_accetta(0.0)
    except ImportError:
        pytest.skip("PointerController non costruibile senza numpy/soundfile")

    monkeypatch.chdir(tmp_path)
    evaluate, _ = _load_eval_math_expressions()
    valutato = evaluate(_yaml.safe_load(f'v: {scritto}')['v'])
    motore_accetta = _pointer_start_engine_accetta(valutato)

    documento = (
        "streams:\n"
        "  - stream_id: s1\n"
        "    sample: f.wav\n"
        "    duration: 10.0\n"
        "    pointer:\n"
        f"      start: {scritto}\n"
    )
    provider = DiagnosticProvider(SchemaBridge.from_python_path(PGE_SRC))
    ls_segnala = any('pointer.start' in d.message
                     for d in provider.get_diagnostics(documento))

    assert ls_segnala == (not motore_accetta), (
        f"Drift su `start: {scritto}`: il Generator lo valuta a "
        f"{valutato!r}, il motore {'accetta' if motore_accetta else 'rifiuta'}, "
        f"il LS {'segnala' if ls_segnala else 'tace'}"
    )


def test_pointer_start_guard_del_motore_e_ancora_isinstance(pge):
    """La riga che il mirror insegue, letta dal sorgente.

    Il corpus qui sopra salta dove `numpy`/`soundfile` non ci sono — cioe'
    nella CI del language server. Questo controllo no: se il motore stringe o
    allarga il guard, la forma cambia qui prima che altrove.
    """
    import re
    from pathlib import Path

    sorgente = None
    for candidato in ('pge/controllers/pointer_controller.py',
                      'controllers/pointer_controller.py'):
        percorso = Path(PGE_SRC) / candidato
        if percorso.exists():
            sorgente = percorso.read_text()
            break
    if sorgente is None:
        pytest.skip("pointer_controller non trovato in questo checkout")

    assert re.search(
        r'if not isinstance\(\s*self\.start\s*,\s*\(\s*int\s*,\s*float\s*\)\s*\)',
        sorgente,
    ), ("il guard di pointer.start non e' piu' `not isinstance(self.start, "
        "(int, float))`: il mirror in _check_pointer_start_envelope va "
        "riallineato")
