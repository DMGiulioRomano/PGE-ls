# tests/test_server_diagnostics.py
"""
Test del layer diagnostica di server.py: debounce + cache.

Cosa coprono
------------
handle_did_change non pubblica piu' in modo sincrono: con
DIAGNOSTICS_DEBOUNCE_S > 0 arma un threading.Timer per-uri
(cancel-and-reschedule), con <= 0 pubblica subito (percorso usato qui
per avere test deterministici). didOpen/didSave restano immediati e
BYPASSANO la cache (refresh autorevole); didClose cancella il timer
pendente ed evita la entry di cache.

Strategia di isolamento
-----------------------
- srv._get_document_text monkeypatchato (il Workspace pygls reale non
  serve): il testo arriva da un holder mutabile controllato dal test.
- srv.server.publish_diagnostics monkeypatchato con MagicMock.
- srv._diagnostic_provider sostituito da uno stub che conta le chiamate:
  e' il contatore che dimostra cache hit/miss.
- Stato globale (timer, cache, generation) azzerato prima e dopo.
"""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import server as srv

PGE_URI = 'file:///tmp/PGE_test.yaml'
NON_PGE_URI = 'file:///tmp/altro.yaml'


class CountingProvider:
    """Stub di DiagnosticProvider: conta le chiamate e registra il testo."""

    def __init__(self):
        self.calls = 0
        self.last_text = None
        self.result = []

    def get_diagnostics(self, text):
        self.calls += 1
        self.last_text = text
        return self.result


def _params(uri):
    """Mock minimale dei params LSP: serve solo text_document.uri."""
    return SimpleNamespace(text_document=SimpleNamespace(uri=uri))


def _clear_diag_state():
    for timer in srv._diag_timers.values():
        timer.cancel()
    srv._diag_timers.clear()
    srv._diag_cache.clear()
    srv._diag_generation.clear()


@pytest.fixture
def diag_env(monkeypatch):
    """
    Ambiente isolato: provider contatore, testo iniettabile, publish
    mockato, debounce sincrono (0) di default, stato globale pulito.
    """
    provider = CountingProvider()
    publish = MagicMock()
    holder = {'text': ''}

    monkeypatch.setattr(srv, '_diagnostic_provider', provider)
    monkeypatch.setattr(srv.server, 'publish_diagnostics', publish)
    monkeypatch.setattr(srv, '_get_document_text',
                        lambda _server, _uri: holder['text'])
    monkeypatch.setattr(srv, 'DIAGNOSTICS_DEBOUNCE_S', 0)
    _clear_diag_state()

    def set_text(t):
        holder['text'] = t

    yield SimpleNamespace(provider=provider, publish=publish,
                          set_text=set_text)

    _clear_diag_state()


# =============================================================================
# PERCORSO SINCRONO (debounce <= 0)
# =============================================================================

class TestDidChangeSincrono:

    def test_pubblica_subito_con_debounce_zero(self, diag_env):
        diag_env.set_text('density: 5\n')
        srv.handle_did_change(_params(PGE_URI))
        assert diag_env.provider.calls == 1
        assert diag_env.provider.last_text == 'density: 5\n'
        assert diag_env.publish.call_count == 1

    def test_uri_non_pge_ignorato(self, diag_env):
        srv.handle_did_change(_params(NON_PGE_URI))
        assert diag_env.provider.calls == 0
        assert diag_env.publish.call_count == 0
        assert srv._diag_timers == {}


# =============================================================================
# CACHE (testo -> diagnostics)
# =============================================================================

class TestCacheDiagnostica:

    def test_stesso_testo_non_ricalcola(self, diag_env):
        diag_env.set_text('density: 5\n')
        srv.handle_did_change(_params(PGE_URI))
        srv.handle_did_change(_params(PGE_URI))
        # Cache hit: un solo ricalcolo, ma la pubblicazione avviene comunque
        assert diag_env.provider.calls == 1
        assert diag_env.publish.call_count == 2

    def test_testo_diverso_ricalcola(self, diag_env):
        diag_env.set_text('density: 5\n')
        srv.handle_did_change(_params(PGE_URI))
        diag_env.set_text('density: 6\n')
        srv.handle_did_change(_params(PGE_URI))
        assert diag_env.provider.calls == 2

    def test_uri_diversi_non_condividono_la_cache(self, diag_env):
        other = 'file:///tmp/PGE_altro.yaml'
        diag_env.set_text('density: 5\n')
        srv.handle_did_change(_params(PGE_URI))
        srv.handle_did_change(_params(other))
        assert diag_env.provider.calls == 2

    def test_did_open_pubblica_subito(self, diag_env):
        diag_env.set_text('density: 5\n')
        srv.handle_did_open(_params(PGE_URI))
        assert diag_env.provider.calls == 1
        assert diag_env.publish.call_count == 1

    def test_did_save_bypassa_la_cache(self, diag_env):
        # Il salvataggio resta un refresh autorevole: ricalcola anche a
        # testo identico (es. durate WAV cambiate su disco).
        diag_env.set_text('density: 5\n')
        srv.handle_did_change(_params(PGE_URI))
        srv.handle_did_save(_params(PGE_URI))
        assert diag_env.provider.calls == 2
        assert diag_env.publish.call_count == 2

    def test_did_open_bypassa_la_cache(self, diag_env):
        diag_env.set_text('density: 5\n')
        srv.handle_did_change(_params(PGE_URI))
        srv.handle_did_open(_params(PGE_URI))
        assert diag_env.provider.calls == 2


# =============================================================================
# PERCORSO DEBOUNCED (timer reale)
# =============================================================================

class TestDebounceTimer:

    def test_due_change_ravvicinati_un_solo_ricalcolo(self, diag_env,
                                                      monkeypatch):
        # 0.2s di debounce: ampiamente oltre i microsecondi che separano
        # le due chiamate qui sotto — niente flakiness.
        monkeypatch.setattr(srv, 'DIAGNOSTICS_DEBOUNCE_S', 0.2)

        diag_env.set_text('density: 5\n')
        srv.handle_did_change(_params(PGE_URI))
        t1 = srv._diag_timers[PGE_URI]

        diag_env.set_text('density: 6\n')
        srv.handle_did_change(_params(PGE_URI))
        t2 = srv._diag_timers[PGE_URI]

        assert t1 is not t2  # il secondo change ha riarmato il timer
        t1.join(timeout=2)
        t2.join(timeout=2)

        # Un solo ricalcolo, col testo dell'ultimo evento
        assert diag_env.provider.calls == 1
        assert diag_env.provider.last_text == 'density: 6\n'
        assert diag_env.publish.call_count == 1

    def test_did_save_cancella_il_timer_pendente(self, diag_env, monkeypatch):
        monkeypatch.setattr(srv, 'DIAGNOSTICS_DEBOUNCE_S', 5)
        diag_env.set_text('density: 5\n')
        srv.handle_did_change(_params(PGE_URI))
        pending = srv._diag_timers[PGE_URI]

        srv.handle_did_save(_params(PGE_URI))
        # Pubblicazione immediata, timer rimosso: nessun secondo publish
        assert PGE_URI not in srv._diag_timers
        assert diag_env.publish.call_count == 1
        pending.join(timeout=2)
        assert diag_env.provider.calls == 1


# =============================================================================
# DID CLOSE
# =============================================================================

class TestDidClose:

    def test_cancella_timer_e_svuota_cache(self, diag_env, monkeypatch):
        diag_env.set_text('density: 5\n')
        srv.handle_did_open(_params(PGE_URI))   # popola la cache
        assert PGE_URI in srv._diag_cache

        monkeypatch.setattr(srv, 'DIAGNOSTICS_DEBOUNCE_S', 5)
        diag_env.set_text('density: 6\n')
        srv.handle_did_change(_params(PGE_URI))  # arma un timer
        assert PGE_URI in srv._diag_timers

        srv.handle_did_close(_params(PGE_URI))
        assert PGE_URI not in srv._diag_timers
        assert PGE_URI not in srv._diag_cache
        # Nessuna pubblicazione oltre quella del didOpen
        assert diag_env.publish.call_count == 1
