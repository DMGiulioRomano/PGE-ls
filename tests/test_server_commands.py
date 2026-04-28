# tests/test_server_commands.py
"""
Test dei command handler di server.py.

Perche' esistono questi test
----------------------------
I test dei provider (test_completion_provider.py, ecc.) coprono la logica
interna ma non toccano mai server.py ne' pygls. Due bug recenti sono sfuggiti
proprio in questo layer:

  Bug 1 — Decorator sbagliato:
      @server.feature(WORKSPACE_EXECUTE_COMMAND, ...) non registra il handler
      in fm.commands -> KeyError: 'pge.buildEnvelope' a runtime.
      Fix: @server.command('pge.buildEnvelope')

  Bug 2 — Firma del handler:
      pygls con @server.command passa params.arguments direttamente come lista,
      NON un oggetto ExecuteCommandParams. Il handler che fa params.arguments
      esplode con AttributeError: 'list' object has no attribute 'arguments'.
      Fix: def handle_build_envelope(ls, args) dove args e' gia' la lista.

Questi test importano i handler da server.py e li chiamano con la firma
esatta che pygls usa, catturando entrambe le classi di bug prima del deploy.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import server as srv
from tests.test_completion_provider import make_bridge_with_stream_keys
from granular_ls.providers.completion_provider import CompletionProvider


# =============================================================================
# HELPERS
# =============================================================================

def _make_ls(document_text: str = '') -> MagicMock:
    """
    Mock minimale di LanguageServer: solo workspace.get_text_document.
    Simula cio' che il server riceve a runtime da pygls.
    """
    ls = MagicMock()
    doc = MagicMock()
    doc.source = document_text
    ls.workspace.get_text_document.return_value = doc
    return ls


def _inject_providers(bridge=None) -> None:
    """Inietta _completion_provider nel modulo server come farebbe _init_providers."""
    if bridge is None:
        bridge = make_bridge_with_stream_keys()
    srv._completion_provider = CompletionProvider(bridge)


@pytest.fixture(autouse=True)
def reset_providers():
    """Ogni test parte con provider puliti e li azzera dopo."""
    _inject_providers()
    yield
    srv._completion_provider = None


# =============================================================================
# FIRMA DEL HANDLER (Bug 2)
# =============================================================================

class TestHandleBuildEnvelopeFirma:
    """
    Verifica che handle_build_envelope accetti (ls, args_list) come pygls lo chiama.

    pygls con @server.command chiama il handler cosi':
        handler(ls, params.arguments)   <- una lista grezza

    Se il handler fa 'params.arguments' su questo argomento esplode:
        AttributeError: 'list' object has no attribute 'arguments'
    """

    def test_accetta_lista_non_execute_command_params(self):
        """La firma deve essere (ls, args) dove args e' una lista, non un oggetto."""
        ls = _make_ls()
        args = ['file:///fake.yml', 0, 10, 3]
        # AttributeError se il handler fa args.arguments
        result = srv.handle_build_envelope(ls, args)
        assert result is not None

    def test_lista_vuota_non_crasha(self):
        """Argomenti mancanti: usa defaults senza KeyError/IndexError."""
        ls = _make_ls()
        result = srv.handle_build_envelope(ls, [])
        assert result is not None

    def test_args_none_non_crasha(self):
        """None come args: usa defaults."""
        ls = _make_ls()
        result = srv.handle_build_envelope(ls, None)
        assert result is not None


# =============================================================================
# OUTPUT DEL HANDLER
# =============================================================================

class TestHandleBuildEnvelopeOutput:

    def test_ritorna_stringa(self):
        ls = _make_ls()
        result = srv.handle_build_envelope(ls, ['file:///fake.yml', 0, 10, 3])
        assert isinstance(result, str)

    def test_formato_envelope_inline(self):
        """Output deve essere ' [[t, v], ..., [t, v]]'."""
        ls = _make_ls()
        result = srv.handle_build_envelope(ls, ['file:///fake.yml', 0, 10, 3])
        stripped = result.strip()
        assert stripped.startswith('[[')
        assert stripped.endswith(']]')

    def test_n_punti_2(self):
        ls = _make_ls()
        result = srv.handle_build_envelope(ls, ['file:///fake.yml', 0, 10, 2])
        # ' [[t, v], [t, v]]' -> due coppie interne
        inner = result.strip()[1:-1]  # rimuove [ e ] esterni
        assert inner.count('[') == 2

    def test_n_punti_5(self):
        ls = _make_ls()
        result = srv.handle_build_envelope(ls, ['file:///fake.yml', 0, 10, 5])
        inner = result.strip()[1:-1]
        assert inner.count('[') == 5

    def test_n_punti_minimo_2(self):
        """n=1 non e' valido: viene silenziosamente portato a 2."""
        ls = _make_ls()
        result = srv.handle_build_envelope(ls, ['file:///fake.yml', 0, 10, 1])
        inner = result.strip()[1:-1]
        assert inner.count('[') >= 2

    def test_end_time_da_duration(self):
        """L'ultimo breakpoint deve avere t == duration dello stream."""
        yaml_text = (
            'streams:\n'
            '  - stream_id: s1\n'
            '    onset: 0.0\n'
            '    duration: 42.0\n'
            '    sample: file.wav\n'
            '    density: '
        )
        ls = _make_ls(document_text=yaml_text)
        line = yaml_text.count('\n')
        result = srv.handle_build_envelope(ls, ['file:///fake.yml', line, 14, 2])
        assert '42.0' in result

    def test_documento_vuoto_usa_end_time_default(self):
        """Se il documento e' vuoto, end_time default (non crasha)."""
        ls = _make_ls(document_text='')
        result = srv.handle_build_envelope(ls, ['file:///fake.yml', 0, 0, 2])
        assert result is not None
        assert '[[' in result

    def test_documento_non_trovato_usa_default(self):
        """Se get_text_document lancia eccezione, usa defaults."""
        ls = MagicMock()
        ls.workspace.get_text_document.side_effect = Exception('not found')
        result = srv.handle_build_envelope(ls, ['file:///fake.yml', 0, 0, 3])
        assert result is not None


# =============================================================================
# REGISTRAZIONE COMANDO (Bug 1)
# =============================================================================

class TestComandoRegistrato:
    """
    Verifica che 'pge.buildEnvelope' sia registrato in server.fm.commands.

    Bug 1: @server.feature(WORKSPACE_EXECUTE_COMMAND, ...) non registra
    il handler in fm.commands -> KeyError a runtime quando pygls dispatch.
    @server.command('pge.buildEnvelope') lo registra correttamente.
    """

    def test_pge_build_envelope_in_fm_commands(self):
        assert 'pge.buildEnvelope' in srv.server.lsp.fm.commands, (
            "'pge.buildEnvelope' non trovato in server.fm.commands. "
            "Usare @server.command() non @server.feature(WORKSPACE_EXECUTE_COMMAND, ...)."
        )

    def test_handler_e_callable(self):
        handler = srv.server.lsp.fm.commands.get('pge.buildEnvelope')
        assert handler is not None
        assert callable(handler)


# =============================================================================
# pge.getEnvelopeContext
# =============================================================================

class TestHandleGetEnvelopeContext:
    """
    Verifica handle_get_envelope_context(ls, args) -> dict con y_min, y_max, end_time.
    """

    def test_ritorna_dizionario(self):
        ls = _make_ls()
        result = srv.handle_get_envelope_context(ls, ['file:///fake.yml', 0, 0])
        assert isinstance(result, dict)

    def test_chiavi_presenti(self):
        ls = _make_ls()
        result = srv.handle_get_envelope_context(ls, ['file:///fake.yml', 0, 0])
        assert 'y_min' in result
        assert 'y_max' in result
        assert 'end_time' in result

    def test_valori_sono_float(self):
        ls = _make_ls()
        result = srv.handle_get_envelope_context(ls, ['file:///fake.yml', 0, 0])
        assert isinstance(result['y_min'], float)
        assert isinstance(result['y_max'], float)
        assert isinstance(result['end_time'], float)

    def test_args_vuoti_non_crashano(self):
        ls = _make_ls()
        result = srv.handle_get_envelope_context(ls, [])
        assert result is not None

    def test_args_none_non_crashano(self):
        ls = _make_ls()
        result = srv.handle_get_envelope_context(ls, None)
        assert result is not None

    def test_end_time_da_duration_stream(self):
        yaml_text = (
            'streams:\n'
            '  - stream_id: s1\n'
            '    onset: 0.0\n'
            '    duration: 42.0\n'
            '    sample: file.wav\n'
            '    density: '
        )
        ls = _make_ls(document_text=yaml_text)
        line = yaml_text.count('\n')
        result = srv.handle_get_envelope_context(ls, ['file:///fake.yml', line, 14])
        assert result['end_time'] == 42.0

    def test_documento_non_trovato_usa_defaults(self):
        ls = _make_ls.__func__ if hasattr(_make_ls, '__func__') else _make_ls
        ls = MagicMock()
        ls.workspace.get_text_document.side_effect = Exception('not found')
        result = srv.handle_get_envelope_context(ls, ['file:///fake.yml', 0, 0])
        assert result is not None
        assert 'y_min' in result

    def test_registrato_in_fm_commands(self):
        assert 'pge.getEnvelopeContext' in srv.server.lsp.fm.commands


# =============================================================================
# _parse_envelope_value — time_unit (Step 1)
# =============================================================================

class TestParseEnvelopeValueTimeUnit:

    def test_dict_con_time_unit_normalized_estratto(self):
        """time_unit: normalized nel dict viene estratto nel risultato."""
        result = srv._parse_envelope_value(
            '{type: linear, points: [[0, 0], [1, 1]], time_unit: normalized}'
        )
        assert result is not None
        assert result.get('time_unit') == 'normalized'

    def test_dict_senza_time_unit_non_ha_chiave(self):
        """Dict senza time_unit: la chiave non deve essere nel risultato."""
        result = srv._parse_envelope_value('{type: cubic, points: [[0, 0], [10, 1]]}')
        assert result is not None
        assert 'time_unit' not in result

    def test_lista_piatta_non_ha_time_unit(self):
        """Lista piatta non può avere time_unit."""
        result = srv._parse_envelope_value('[[0, 0], [10, 1]]')
        assert result is not None
        assert 'time_unit' not in result

    def test_compact_loop_non_ha_time_unit(self):
        """Compact loop non può avere time_unit."""
        result = srv._parse_envelope_value('[[[0, 0.5], [1, 1.0]], 10.0, 3]')
        assert result is not None
        assert 'time_unit' not in result


# =============================================================================
# _resolve_envelope_context — param_time_unit (Step 2)
# =============================================================================

class TestResolveEnvelopeContextTimeUnit:

    def test_param_time_unit_normalized_sovrascrive_stream_absolute(self):
        """time_unit: normalized nel dict ha precedenza su stream absolute."""
        yaml_text = (
            'streams:\n'
            '  - stream_id: s1\n'
            '    duration: 42.0\n'
            '    density: '
        )
        line = yaml_text.count('\n')
        result = srv._resolve_envelope_context(
            yaml_text, line, 14,
            param_time_unit='normalized'
        )
        assert result['end_time'] == 1.0

    def test_param_time_unit_normalized_con_stream_normalized(self):
        """Entrambi normalized: end_time == 1.0."""
        yaml_text = (
            'streams:\n'
            '  - stream_id: s1\n'
            '    time_mode: normalized\n'
            '    density: '
        )
        line = yaml_text.count('\n')
        result = srv._resolve_envelope_context(
            yaml_text, line, 14,
            param_time_unit='normalized'
        )
        assert result['end_time'] == 1.0

    def test_param_time_unit_none_fallback_a_stream_normalized(self):
        """Senza param_time_unit, time_mode: normalized dello stream governa."""
        yaml_text = (
            'streams:\n'
            '  - stream_id: s1\n'
            '    time_mode: normalized\n'
            '    density: '
        )
        line = yaml_text.count('\n')
        result = srv._resolve_envelope_context(
            yaml_text, line, 14,
            param_time_unit=None
        )
        assert result['end_time'] == 1.0

    def test_param_time_unit_none_fallback_a_duration(self):
        """Senza param_time_unit e time_mode absolute, usa duration."""
        yaml_text = (
            'streams:\n'
            '  - stream_id: s1\n'
            '    duration: 42.0\n'
            '    density: '
        )
        line = yaml_text.count('\n')
        result = srv._resolve_envelope_context(
            yaml_text, line, 14
        )
        assert result['end_time'] == 42.0


# =============================================================================
# handle_get_envelope_at_cursor — propagazione time_unit (Step 3)
# =============================================================================

class TestHandleGetEnvelopeAtCursorTimeUnit:

    def test_dict_time_unit_normalized_sovrascrive_duration_stream(self):
        """
        Parametro con {type: linear, points: [...], time_unit: normalized}
        in uno stream con duration: 42.0 → end_time deve essere 1.0, non 42.0.
        """
        yaml_text = (
            'streams:\n'
            '  - stream_id: s1\n'
            '    duration: 42.0\n'
            '    density: {type: linear, points: [[0, 0.5], [1, 1.0]], time_unit: normalized}'
        )
        ls = _make_ls(document_text=yaml_text)
        line = yaml_text.count('\n')
        result = srv.handle_get_envelope_at_cursor(ls, ['file:///fake.yml', line, 20])
        assert result is not None
        assert result['end_time'] == 1.0

    def test_dict_senza_time_unit_usa_duration_stream(self):
        """Dict senza time_unit: usa duration dello stream (comportamento invariato)."""
        yaml_text = (
            'streams:\n'
            '  - stream_id: s1\n'
            '    duration: 42.0\n'
            '    density: {type: linear, points: [[0, 0.5], [42.0, 1.0]]}'
        )
        ls = _make_ls(document_text=yaml_text)
        line = yaml_text.count('\n')
        result = srv.handle_get_envelope_at_cursor(ls, ['file:///fake.yml', line, 20])
        assert result is not None
        assert result['end_time'] == 42.0


# =============================================================================
# handle_get_envelope_at_cursor — formato block YAML
# =============================================================================

class TestHandleGetEnvelopeAtCursorBlockYaml:

    def test_block_yaml_con_time_unit_normalized(self):
        """
        Envelope scritto come block YAML con time_unit: normalized.
        Il cursore è sulla riga chiave; il valore è su righe successive.
        """
        yaml_text = (
            'streams:\n'
            '  - stream_id: s1\n'
            '    duration: 42.0\n'
            '    speed_ratio:\n'
            '      time_unit: normalized\n'
            '      points:\n'
            '        - [0.0, 0.04]\n'
            '        - [0.5, 0.006]\n'
        )
        ls = _make_ls(document_text=yaml_text)
        # cursore sulla riga 'speed_ratio:'
        line = 3
        result = srv.handle_get_envelope_at_cursor(ls, ['file:///fake.yml', line, 6])
        assert result is not None
        assert result['end_time'] == 1.0
        assert len(result['points']) == 2
        assert result['struttura'] == 'breakpoints'

    def test_block_yaml_senza_time_unit_usa_duration(self):
        """Block YAML senza time_unit: usa duration dello stream."""
        yaml_text = (
            'streams:\n'
            '  - stream_id: s1\n'
            '    duration: 42.0\n'
            '    density:\n'
            '      points:\n'
            '        - [0.0, 0.5]\n'
            '        - [42.0, 1.0]\n'
        )
        ls = _make_ls(document_text=yaml_text)
        line = 3
        result = srv.handle_get_envelope_at_cursor(ls, ['file:///fake.yml', line, 6])
        assert result is not None
        assert result['end_time'] == 42.0

    def test_block_yaml_replace_range_multi_riga(self):
        """Per block YAML, replace_range deve avere end_line != line."""
        yaml_text = (
            'streams:\n'
            '  - stream_id: s1\n'
            '    duration: 42.0\n'
            '    speed_ratio:\n'
            '      time_unit: normalized\n'
            '      points:\n'
            '        - [0.0, 0.04]\n'
            '        - [0.5, 0.006]\n'
        )
        ls = _make_ls(document_text=yaml_text)
        line = 3
        result = srv.handle_get_envelope_at_cursor(ls, ['file:///fake.yml', line, 6])
        assert result is not None
        rr = result['replace_range']
        assert rr['line'] == line
        assert 'end_line' in rr
        assert rr['end_line'] > line

    def test_inline_replace_range_senza_end_line(self):
        """Per envelope inline, replace_range NON deve avere end_line."""
        yaml_text = (
            'streams:\n'
            '  - stream_id: s1\n'
            '    duration: 42.0\n'
            '    density: {type: linear, points: [[0, 0.5], [42.0, 1.0]]}'
        )
        ls = _make_ls(document_text=yaml_text)
        line = yaml_text.count('\n')
        result = srv.handle_get_envelope_at_cursor(ls, ['file:///fake.yml', line, 20])
        assert result is not None
        assert 'end_line' not in result['replace_range']
