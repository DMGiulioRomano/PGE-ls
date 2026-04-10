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
