# tests/test_diagnostic_provider.py
"""
Suite TDD - FASE RED per diagnostic_provider.py

Modulo sotto test (non ancora esistente):
    granular_ls/providers/diagnostic_provider.py

Responsabilita' del modulo:
    Analizzare un documento YAML completo e produrre una lista di
    Diagnostic LSP che segnalano errori e avvisi all'utente.

Differenza rispetto agli altri provider:
    CompletionProvider e HoverProvider lavorano su un singolo punto
    del documento (posizione cursore). DiagnosticProvider lavora
    sull'intero documento e viene chiamato ogni volta che il testo cambia.
    Non riceve un YamlContext: analizza il documento da solo.

Tipi di diagnostica implementati:

    1. EXCLUSIVE_GROUP VIOLATION (Warning)
       Due o piu' parametri dello stesso exclusive_group sono presenti
       nello stesso documento. Es. 'fill_factor' e 'density' insieme.
       Severita': Warning (l'utente potrebbe volerlo, ma e' sospetto).

    2. VALUE OUT OF BOUNDS (Error)
       Un valore numerico e' fuori dal range [min_val, max_val] del parametro.
       Es. density: -5  (min e' 0.01)
       Severita': Error (il motore granulare rifiutera' il valore).

Struttura Diagnostic LSP attesa:
    range    : Range(start=Position(line, char), end=Position(line, char))
    message  : stringa descrittiva del problema
    severity : DiagnosticSeverity.Warning o DiagnosticSeverity.Error
    source   : 'granular-ls' (identifica il nostro server)

Parsing del documento:
    Il DiagnosticProvider fa il suo parsing interno riga per riga,
    simile a YamlAnalyzer ma con obiettivo diverso: estrarre coppie
    chiave-valore con le loro posizioni di riga per costruire il Range.

Organizzazione:
    1.  DiagnosticProvider - costruzione
    2.  get_diagnostics - documento vuoto ritorna lista vuota
    3.  get_diagnostics - documento valido senza problemi ritorna vuoto
    4.  get_diagnostics - exclusive_group violation (Warning)
    5.  get_diagnostics - valore fuori bounds (Error)
    6.  get_diagnostics - struttura Diagnostic (range, message, severity, source)
    7.  get_diagnostics - valore non numerico ignorato (no crash)
    8.  get_diagnostics - parametro senza bounds ignorato
    9.  get_diagnostics - multipli problemi nello stesso documento
    10. Edge cases
"""

import pytest
from lsprotocol.types import Diagnostic, DiagnosticSeverity

from granular_ls.schema_bridge import SchemaBridge
from granular_ls.providers.diagnostic_provider import DiagnosticProvider


# =============================================================================
# FIXTURES
# =============================================================================

def make_raw_spec(name, yaml_path, default=0.0, is_smart=True,
                  exclusive_group=None, group_priority=99):
    return {
        'name': name, 'yaml_path': yaml_path, 'default': default,
        'is_smart': is_smart, 'exclusive_group': exclusive_group,
        'group_priority': group_priority, 'range_path': None, 'dephase_key': None,
    }

def make_raw_bounds(min_val, max_val, variation_mode='additive'):
    return {
        'min_val': min_val, 'max_val': max_val,
        'min_range': 0.0, 'max_range': 0.0,
        'default_jitter': 0.0, 'variation_mode': variation_mode,
    }


@pytest.fixture
def bridge():
    raw = {
        'specs': [
            make_raw_spec('density', 'density', default=None,
                          exclusive_group='density_mode', group_priority=2),
            make_raw_spec('fill_factor', 'fill_factor', default=2,
                          exclusive_group='density_mode', group_priority=1),
            make_raw_spec('distribution', 'distribution', default=0.0),
            make_raw_spec('volume', 'volume', default=-6.0),
            make_raw_spec('grain_duration', 'grain.duration', default=0.05),
            make_raw_spec('pitch_semitones', 'pitch.semitones', default=None,
                          exclusive_group='pitch_mode', group_priority=1),
            make_raw_spec('pitch_ratio', 'pitch.ratio', default=1.0,
                          exclusive_group='pitch_mode', group_priority=2),
            make_raw_spec('effective_density', '_internal_calc_',
                          default=0.0, is_smart=False),
        ],
        'bounds': {
            'density':        make_raw_bounds(0.01, 4000.0),
            'fill_factor':    make_raw_bounds(0.001, 50.0),
            'distribution':   make_raw_bounds(0.0, 1.0),
            'volume':         make_raw_bounds(-60.0, 0.0),
            'grain_duration': make_raw_bounds(0.001, 10.0),
            'pitch_semitones':make_raw_bounds(-48.0, 48.0,
                                              variation_mode='quantized'),
            'pitch_ratio':    make_raw_bounds(0.01, 10.0),
        },
    }
    return SchemaBridge(raw)


# =============================================================================
# 1. DiagnosticProvider - costruzione
# =============================================================================

class TestDiagnosticProviderConstruction:

    def test_costruzione_con_bridge(self, bridge):
        provider = DiagnosticProvider(bridge)
        assert provider is not None

    def test_richiede_bridge(self):
        with pytest.raises(TypeError):
            DiagnosticProvider()


# =============================================================================
# 2. get_diagnostics - documento vuoto
# =============================================================================

class TestGetDiagnosticsDocumentoVuoto:

    def test_documento_vuoto_ritorna_lista_vuota(self, bridge):
        provider = DiagnosticProvider(bridge)
        result = provider.get_diagnostics("")
        assert result == []

    def test_documento_solo_commenti_ritorna_lista_vuota(self, bridge):
        provider = DiagnosticProvider(bridge)
        result = provider.get_diagnostics("# commento\n# altro commento\n")
        assert result == []

    def test_documento_solo_newline_ritorna_lista_vuota(self, bridge):
        provider = DiagnosticProvider(bridge)
        result = provider.get_diagnostics("\n\n\n")
        assert result == []

    def test_ritorna_sempre_lista(self, bridge):
        provider = DiagnosticProvider(bridge)
        assert isinstance(provider.get_diagnostics(""), list)
        assert isinstance(provider.get_diagnostics("density: 100"), list)


# =============================================================================
# 3. get_diagnostics - documento valido senza problemi
# =============================================================================

class TestGetDiagnosticsDocumentoValido:

    def test_solo_density_nessun_problema(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = "density: 100\n"
        result = provider.get_diagnostics(yaml)
        assert result == []

    def test_solo_fill_factor_nessun_problema(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = "fill_factor: 5\n"
        result = provider.get_diagnostics(yaml)
        assert result == []

    def test_volume_nel_range_nessun_problema(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = "volume: -6\n"
        result = provider.get_diagnostics(yaml)
        assert result == []

    def test_piu_parametri_validi_nessun_problema(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = "density: 100\nvolume: -6\ndistribution: 0.5\n"
        result = provider.get_diagnostics(yaml)
        assert result == []


# =============================================================================
# 4. get_diagnostics - exclusive_group violation
# =============================================================================

class TestGetDiagnosticsExclusiveGroup:

    def test_density_e_fill_factor_insieme_produce_warning(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = "fill_factor: 5\ndensity: 100\n"
        result = provider.get_diagnostics(yaml)
        assert len(result) >= 1

    def test_violation_e_warning_non_error(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = "fill_factor: 5\ndensity: 100\n"
        result = provider.get_diagnostics(yaml)
        warnings = [d for d in result
                    if d.severity == DiagnosticSeverity.Warning]
        assert len(warnings) >= 1

    def test_violation_message_menziona_il_gruppo(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = "fill_factor: 5\ndensity: 100\n"
        result = provider.get_diagnostics(yaml)
        warnings = [d for d in result
                    if d.severity == DiagnosticSeverity.Warning]
        assert any('density_mode' in d.message for d in warnings)

    def test_violation_message_menziona_entrambe_le_chiavi(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = "fill_factor: 5\ndensity: 100\n"
        result = provider.get_diagnostics(yaml)
        warnings = [d for d in result
                    if d.severity == DiagnosticSeverity.Warning]
        messages = ' '.join(d.message for d in warnings)
        assert 'fill_factor' in messages or 'density' in messages

    def test_pitch_semitones_e_ratio_insieme_produce_warning(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = "pitch:\n  semitones: 12\n  ratio: 2.0\n"
        result = provider.get_diagnostics(yaml)
        warnings = [d for d in result
                    if d.severity == DiagnosticSeverity.Warning]
        assert len(warnings) >= 1

    def test_un_solo_membro_del_gruppo_nessun_warning(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = "fill_factor: 5\n"
        result = provider.get_diagnostics(yaml)
        exclusive_warnings = [
            d for d in result
            if d.severity == DiagnosticSeverity.Warning
            and 'density_mode' in d.message
        ]
        assert exclusive_warnings == []


# =============================================================================
# 5. get_diagnostics - valore fuori bounds
# =============================================================================

class TestGetDiagnosticsValueOutOfBounds:

    def test_density_negativa_produce_error(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = "density: -5\n"
        result = provider.get_diagnostics(yaml)
        errors = [d for d in result
                  if d.severity == DiagnosticSeverity.Error]
        assert len(errors) >= 1

    def test_density_zero_produce_error(self, bridge):
        """density min_val e' 0.01, quindi 0 e' fuori range."""
        provider = DiagnosticProvider(bridge)
        yaml = "density: 0\n"
        result = provider.get_diagnostics(yaml)
        errors = [d for d in result
                  if d.severity == DiagnosticSeverity.Error]
        assert len(errors) >= 1

    def test_density_nel_range_nessun_error(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = "density: 100\n"
        result = provider.get_diagnostics(yaml)
        errors = [d for d in result
                  if d.severity == DiagnosticSeverity.Error]
        assert errors == []

    def test_volume_sopra_zero_produce_error(self, bridge):
        """volume max_val e' 0.0, quindi 5 e' fuori range."""
        provider = DiagnosticProvider(bridge)
        yaml = "volume: 5\n"
        result = provider.get_diagnostics(yaml)
        errors = [d for d in result
                  if d.severity == DiagnosticSeverity.Error]
        assert len(errors) >= 1

    def test_volume_sotto_meno60_produce_error(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = "volume: -100\n"
        result = provider.get_diagnostics(yaml)
        errors = [d for d in result
                  if d.severity == DiagnosticSeverity.Error]
        assert len(errors) >= 1

    def test_error_message_menziona_nome_parametro(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = "density: -5\n"
        result = provider.get_diagnostics(yaml)
        errors = [d for d in result
                  if d.severity == DiagnosticSeverity.Error]
        assert any('density' in d.message for d in errors)

    def test_error_message_menziona_il_range(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = "density: -5\n"
        result = provider.get_diagnostics(yaml)
        errors = [d for d in result
                  if d.severity == DiagnosticSeverity.Error]
        messages = ' '.join(d.message for d in errors)
        assert '0.01' in messages or '4000' in messages


# =============================================================================
# 6. get_diagnostics - struttura Diagnostic
# =============================================================================

class TestGetDiagnosticsStruttura:

    def _get_first_diagnostic(self, bridge, yaml):
        provider = DiagnosticProvider(bridge)
        result = provider.get_diagnostics(yaml)
        assert len(result) > 0
        return result[0]

    def test_diagnostic_e_oggetto_lsp(self, bridge):
        d = self._get_first_diagnostic(bridge, "density: -5\n")
        assert isinstance(d, Diagnostic)

    def test_diagnostic_ha_range(self, bridge):
        d = self._get_first_diagnostic(bridge, "density: -5\n")
        assert d.range is not None

    def test_diagnostic_range_line_corretto(self, bridge):
        """density e' alla riga 0 del documento."""
        d = self._get_first_diagnostic(bridge, "density: -5\n")
        assert d.range.start.line == 0

    def test_diagnostic_range_line_seconda_riga(self, bridge):
        """Se il problema e' alla riga 1, range.start.line deve essere 1."""
        provider = DiagnosticProvider(bridge)
        yaml = "volume: -6\ndensity: -5\n"
        result = provider.get_diagnostics(yaml)
        errors = [d for d in result
                  if d.severity == DiagnosticSeverity.Error]
        assert any(d.range.start.line == 1 for d in errors)

    def test_diagnostic_ha_message_non_vuoto(self, bridge):
        d = self._get_first_diagnostic(bridge, "density: -5\n")
        assert isinstance(d.message, str)
        assert len(d.message) > 0

    def test_diagnostic_source_e_granular_ls(self, bridge):
        d = self._get_first_diagnostic(bridge, "density: -5\n")
        assert d.source == 'granular-ls'

    def test_diagnostic_severity_e_impostata(self, bridge):
        d = self._get_first_diagnostic(bridge, "density: -5\n")
        assert d.severity in (DiagnosticSeverity.Error,
                               DiagnosticSeverity.Warning,
                               DiagnosticSeverity.Information,
                               DiagnosticSeverity.Hint)


# =============================================================================
# 7. get_diagnostics - valori non numerici ignorati
# =============================================================================

class TestGetDiagnosticsValoriNonNumerici:
    """
    Valori non numerici (stringhe, liste, None) non devono causare errori
    di bounds: non possiamo confrontarli con min/max numerici.
    """

    def test_valore_stringa_non_produce_bounds_error(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = "density: molto_denso\n"
        result = provider.get_diagnostics(yaml)
        errors = [d for d in result
                  if d.severity == DiagnosticSeverity.Error]
        assert errors == []

    def test_valore_lista_non_produce_bounds_error(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = "density: [[0, 100], [5, 200]]\n"
        result = provider.get_diagnostics(yaml)
        errors = [d for d in result
                  if d.severity == DiagnosticSeverity.Error]
        assert errors == []

    def test_valore_vuoto_non_produce_bounds_error(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = "density:\n"
        result = provider.get_diagnostics(yaml)
        errors = [d for d in result
                  if d.severity == DiagnosticSeverity.Error]
        assert errors == []

    def test_nessun_crash_su_yaml_malformato(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = "density: { broken: yaml\nvolume: -6\n"
        result = provider.get_diagnostics(yaml)
        assert isinstance(result, list)


# =============================================================================
# 8. get_diagnostics - parametro senza bounds ignorato
# =============================================================================

class TestGetDiagnosticsParametroSenzaBounds:

    def test_parametro_senza_bounds_non_produce_error(self):
        raw = {
            'specs': [make_raw_spec('orphan', 'orphan')],
            'bounds': {},
        }
        b = SchemaBridge(raw)
        provider = DiagnosticProvider(b)
        yaml = "orphan: 999\n"
        result = provider.get_diagnostics(yaml)
        assert result == []


# =============================================================================
# 9. get_diagnostics - multipli problemi
# =============================================================================

class TestGetDiagnosticsMultipliProblemi:

    def test_due_valori_fuori_range_produce_due_errori(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = "density: -5\nvolume: 10\n"
        result = provider.get_diagnostics(yaml)
        errors = [d for d in result
                  if d.severity == DiagnosticSeverity.Error]
        assert len(errors) >= 2

    def test_exclusive_group_e_bounds_insieme(self, bridge):
        """Entrambi i tipi di problema nello stesso documento."""
        provider = DiagnosticProvider(bridge)
        yaml = "fill_factor: 5\ndensity: -5\n"
        result = provider.get_diagnostics(yaml)
        warnings = [d for d in result
                    if d.severity == DiagnosticSeverity.Warning]
        errors = [d for d in result
                  if d.severity == DiagnosticSeverity.Error]
        assert len(warnings) >= 1
        assert len(errors) >= 1

    def test_tutti_i_diagnostici_hanno_source(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = "fill_factor: 5\ndensity: -5\nvolume: 10\n"
        result = provider.get_diagnostics(yaml)
        for d in result:
            assert d.source == 'granular-ls'


# =============================================================================
# 10. Edge cases
# =============================================================================

class TestGetDiagnosticsEdgeCases:

    def test_bridge_vuoto_sempre_lista_vuota(self):
        b = SchemaBridge({'specs': [], 'bounds': {}})
        provider = DiagnosticProvider(b)
        assert provider.get_diagnostics("qualsiasi: testo\n") == []

    def test_documento_none_non_solleva(self, bridge):
        provider = DiagnosticProvider(bridge)
        result = provider.get_diagnostics(None)
        assert isinstance(result, list)

    def test_chiamate_ripetute_stesso_risultato(self, bridge):
        """Idempotenza: stesso documento -> stessi diagnostici."""
        provider = DiagnosticProvider(bridge)
        yaml = "density: -5\n"
        r1 = provider.get_diagnostics(yaml)
        r2 = provider.get_diagnostics(yaml)
        assert len(r1) == len(r2)
        assert all(d1.message == d2.message for d1, d2 in zip(r1, r2))

    def test_parametro_interno_non_analizzato(self, bridge):
        """effective_density e' interno, non deve produrre diagnostici."""
        provider = DiagnosticProvider(bridge)
        yaml = "effective_density: -999\n"
        result = provider.get_diagnostics(yaml)
        assert result == []

    def test_chiave_sconosciuta_ignorata(self, bridge):
        """Chiavi non nel bridge vengono ignorate silenziosamente."""
        provider = DiagnosticProvider(bridge)
        yaml = "parametro_sconosciuto: 999\n"
        result = provider.get_diagnostics(yaml)
        assert result == []


# =============================================================================
# MODIFICA E - Parser lista nel DiagnosticProvider
# =============================================================================

class TestGetDiagnosticsListaStreams:
    """
    Il DiagnosticProvider deve analizzare correttamente la struttura
    con streams in lista. I parametri dentro ogni elemento lista
    devono essere valutati come se fossero a root level dello stream,
    non come figli del blocco 'streams:'.

    Struttura:
        streams:
          - stream_id: s1
            onset: 0.0
            density: -5        <- deve produrre Error (fuori bounds)
            fill_factor: 3
            density: 100       <- violation exclusive_group (density_mode)
    """

    def test_valore_fuori_bounds_dentro_stream_lista(self, bridge):
        """density: -5 dentro uno stream lista produce Error."""
        provider = DiagnosticProvider(bridge)
        yaml = "streams:\n  - stream_id: s1\n    density: -5\n"
        result = provider.get_diagnostics(yaml)
        errors = [d for d in result if d.severity == DiagnosticSeverity.Error]
        assert len(errors) >= 1

    def test_error_riga_corretta_dentro_lista(self, bridge):
        """L'errore punta alla riga corretta dentro la lista."""
        provider = DiagnosticProvider(bridge)
        yaml = "streams:\n  - stream_id: s1\n    density: -5\n"
        result = provider.get_diagnostics(yaml)
        errors = [d for d in result if d.severity == DiagnosticSeverity.Error]
        assert any(d.range.start.line == 2 for d in errors)

    def test_exclusive_group_violation_dentro_lista(self, bridge):
        """fill_factor e density insieme dentro uno stream producono Warning."""
        provider = DiagnosticProvider(bridge)
        yaml = (
            "streams:\n"
            "  - stream_id: s1\n"
            "    fill_factor: 3\n"
            "    density: 100\n"
        )
        result = provider.get_diagnostics(yaml)
        warnings = [d for d in result
                    if d.severity == DiagnosticSeverity.Warning]
        assert len(warnings) >= 1

    def test_stream_valido_nella_lista_nessun_errore(self, bridge):
        """Uno stream corretto dentro la lista non produce diagnostici."""
        provider = DiagnosticProvider(bridge)
        yaml = (
            "streams:\n"
            "  - stream_id: s1\n"
            "    onset: 0.0\n"
            "    duration: 10.0\n"
            "    sample: file.wav\n"
            "    density: 100\n"
            "    volume: -6\n"
        )
        result = provider.get_diagnostics(yaml)
        assert result == []

    def test_due_stream_nella_lista_errori_separati(self, bridge):
        """Errori in stream diversi vengono tutti rilevati."""
        provider = DiagnosticProvider(bridge)
        yaml = (
            "streams:\n"
            "  - stream_id: s1\n"
            "    density: -5\n"
            "  - stream_id: s2\n"
            "    volume: 10\n"
        )
        result = provider.get_diagnostics(yaml)
        errors = [d for d in result if d.severity == DiagnosticSeverity.Error]
        assert len(errors) >= 2

    def test_parametri_annidati_dentro_stream_lista(self, bridge):
        """Parametri in blocchi annidati (grain:) dentro la lista vengono analizzati."""
        provider = DiagnosticProvider(bridge)
        yaml = (
            "streams:\n"
            "  - stream_id: s1\n"
            "    grain:\n"
            "      duration: -0.5\n"
        )
        result = provider.get_diagnostics(yaml)
        errors = [d for d in result if d.severity == DiagnosticSeverity.Error]
        assert len(errors) >= 1

    def test_blocco_streams_senza_trattino_ignorato(self, bridge):
        """Un valore direttamente su 'streams:' non causa crash."""
        provider = DiagnosticProvider(bridge)
        yaml = "streams: null\n"
        result = provider.get_diagnostics(yaml)
        assert isinstance(result, list)


# =============================================================================
# Diagnostica campi obbligatori mancanti nello stream
# =============================================================================

class TestGetDiagnosticsMandatoryStreamFields:
    """
    I campi obbligatori di ogni stream sono: stream_id, onset, duration, sample.
    Se uno manca, il provider deve produrre un Warning con il nome del campo.
    """

    def test_stream_completo_nessun_warning(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = (
            "streams:\n"
            "  - stream_id: s1\n"
            "    onset: 0.0\n"
            "    duration: 10.0\n"
            "    sample: file.wav\n"
        )
        result = provider.get_diagnostics(yaml)
        mandatory_warnings = [
            d for d in result
            if d.severity == DiagnosticSeverity.Warning
            and any(f in d.message for f in ['stream_id','onset','duration','sample'])
        ]
        assert mandatory_warnings == []

    def test_stream_senza_stream_id_produce_warning(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = (
            "streams:\n"
            "  - onset: 0.0\n"
            "    duration: 10.0\n"
            "    sample: file.wav\n"
        )
        result = provider.get_diagnostics(yaml)
        warnings = [d for d in result if 'stream_id' in d.message]
        assert len(warnings) >= 1

    def test_stream_senza_onset_produce_warning(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = (
            "streams:\n"
            "  - stream_id: s1\n"
            "    duration: 10.0\n"
            "    sample: file.wav\n"
        )
        result = provider.get_diagnostics(yaml)
        warnings = [d for d in result if 'onset' in d.message]
        assert len(warnings) >= 1

    def test_stream_senza_duration_produce_warning(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = (
            "streams:\n"
            "  - stream_id: s1\n"
            "    onset: 0.0\n"
            "    sample: file.wav\n"
        )
        result = provider.get_diagnostics(yaml)
        warnings = [d for d in result if 'duration' in d.message]
        assert len(warnings) >= 1

    def test_stream_senza_sample_produce_warning(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = (
            "streams:\n"
            "  - stream_id: s1\n"
            "    onset: 0.0\n"
            "    duration: 10.0\n"
        )
        result = provider.get_diagnostics(yaml)
        warnings = [d for d in result if 'sample' in d.message]
        assert len(warnings) >= 1

    def test_warning_punta_alla_riga_del_trattino(self, bridge):
        """Il Warning deve puntare alla riga del marcatore '- ' dello stream."""
        provider = DiagnosticProvider(bridge)
        yaml = (
            "streams:\n"
            "  - onset: 0.0\n"
            "    duration: 10.0\n"
            "    sample: file.wav\n"
        )
        result = provider.get_diagnostics(yaml)
        warnings = [d for d in result if 'stream_id' in d.message]
        assert len(warnings) >= 1
        assert warnings[0].range.start.line == 1

    def test_due_stream_mancanze_separate(self, bridge):
        """Ogni stream segnala i propri campi mancanti indipendentemente."""
        provider = DiagnosticProvider(bridge)
        yaml = (
            "streams:\n"
            "  - onset: 0.0\n"        # manca stream_id
            "    duration: 10.0\n"
            "    sample: a.wav\n"
            "  - stream_id: s2\n"
            "    onset: 5.0\n"
            "    duration: 5.0\n"     # manca sample
        )
        result = provider.get_diagnostics(yaml)
        stream_id_warn = [d for d in result if 'stream_id' in d.message]
        sample_warn    = [d for d in result if 'sample' in d.message]
        assert len(stream_id_warn) >= 1
        assert len(sample_warn) >= 1

    def test_severity_e_warning_non_error(self, bridge):
        """Campo mancante e' Warning, non Error."""
        provider = DiagnosticProvider(bridge)
        yaml = "streams:\n  - onset: 0.0\n    duration: 10.0\n    sample: f.wav\n"
        result = provider.get_diagnostics(yaml)
        warnings = [d for d in result if 'stream_id' in d.message]
        assert all(d.severity == DiagnosticSeverity.Warning for d in warnings)


# =============================================================================
# Diagnostica valori envelope fuori bounds
# =============================================================================

class TestGetDiagnosticsEnvelopeBounds:
    """
    Il DiagnosticProvider deve controllare i valori Y dei breakpoints
    negli envelope e produrre Error se escono dai bounds del parametro.
    """

    def test_envelope_standard_valore_valido_nessun_errore(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = (
            "streams:\n"
            "  - stream_id: s1\n"
            "    onset: 0.0\n"
            "    duration: 10.0\n"
            "    sample: f.wav\n"
            "    density:\n"
            "      - [0.0, 100.0]\n"
            "      - [10.0, 500.0]\n"
        )
        result = provider.get_diagnostics(yaml)
        errors = [d for d in result if d.severity == DiagnosticSeverity.Error
                  and 'envelope' in d.message.lower()]
        assert errors == []

    def test_envelope_standard_valore_sopra_max_produce_errore(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = (
            "streams:\n"
            "  - stream_id: s1\n"
            "    onset: 0.0\n"
            "    duration: 10.0\n"
            "    sample: f.wav\n"
            "    density:\n"
            "      - [0.0, 100.0]\n"
            "      - [10.0, 9999.0]\n"   # 9999 > 4000 (max density)
        )
        result = provider.get_diagnostics(yaml)
        errors = [d for d in result if d.severity == DiagnosticSeverity.Error]
        assert len(errors) >= 1

    def test_envelope_standard_valore_sotto_min_produce_errore(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = (
            "streams:\n"
            "  - stream_id: s1\n"
            "    onset: 0.0\n"
            "    duration: 10.0\n"
            "    sample: f.wav\n"
            "    density:\n"
            "      - [0.0, -5.0]\n"    # -5 < 0.01 (min density)
            "      - [10.0, 100.0]\n"
        )
        result = provider.get_diagnostics(yaml)
        errors = [d for d in result if d.severity == DiagnosticSeverity.Error]
        assert len(errors) >= 1

    def test_errore_punta_alla_riga_del_breakpoint(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = (
            "streams:\n"
            "  - stream_id: s1\n"
            "    onset: 0.0\n"
            "    duration: 10.0\n"
            "    sample: f.wav\n"
            "    density:\n"
            "      - [0.0, 100.0]\n"
            "      - [10.0, 9999.0]\n"
        )
        result = provider.get_diagnostics(yaml)
        errors = [d for d in result if d.severity == DiagnosticSeverity.Error]
        # La riga del breakpoint fuori bounds e' la riga 7 (0-indexed)
        assert any(d.range.start.line == 7 for d in errors)

    def test_volume_valore_valido(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = (
            "streams:\n"
            "  - stream_id: s1\n"
            "    onset: 0.0\n"
            "    duration: 10.0\n"
            "    sample: f.wav\n"
            "    volume:\n"
            "      - [0.0, -6.0]\n"
            "      - [10.0, 0.0]\n"
        )
        result = provider.get_diagnostics(yaml)
        errors = [d for d in result if d.severity == DiagnosticSeverity.Error
                  and 'envelope' in d.message.lower()]
        assert errors == []


# =============================================================================
# Punto 3: exclusive group - errore su entrambe le righe con priorita'
# =============================================================================

class TestExclusiveGroupBothLines:
    """
    Quando due parametri mutuamente esclusivi sono presenti,
    il Warning deve comparire su ENTRAMBE le righe e indicare
    quale parametro ha la priorita' (group_priority piu' basso = priorita' alta).
    """

    def test_warning_su_entrambe_le_righe(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = (
            "streams:\n"
            "  - stream_id: s1\n"
            "    onset: 0.0\n"
            "    duration: 10.0\n"
            "    sample: f.wav\n"
            "    fill_factor: 2\n"   # riga 5, density_mode priority=1
            "    density: 100\n"    # riga 6, density_mode priority=2
        )
        result = provider.get_diagnostics(yaml)
        warnings = [d for d in result
                    if d.severity == DiagnosticSeverity.Warning
                    and 'density_mode' in d.message]
        lines_with_warning = {d.range.start.line for d in warnings}
        assert 5 in lines_with_warning
        assert 6 in lines_with_warning

    def test_messaggio_indica_quale_ha_priorita(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = (
            "streams:\n"
            "  - stream_id: s1\n"
            "    onset: 0.0\n"
            "    duration: 10.0\n"
            "    sample: f.wav\n"
            "    fill_factor: 2\n"
            "    density: 100\n"
        )
        result = provider.get_diagnostics(yaml)
        warnings = [d for d in result
                    if d.severity == DiagnosticSeverity.Warning
                    and 'density_mode' in d.message]
        assert len(warnings) >= 1
        # fill_factor ha group_priority=1 (piu' alta), deve essere indicato
        assert any('fill_factor' in d.message for d in warnings)

    def test_severity_e_warning_non_error(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = (
            "streams:\n"
            "  - stream_id: s1\n"
            "    onset: 0.0\n"
            "    duration: 10.0\n"
            "    sample: f.wav\n"
            "    fill_factor: 2\n"
            "    density: 100\n"
        )
        result = provider.get_diagnostics(yaml)
        warnings = [d for d in result
                    if 'density_mode' in d.message]
        assert all(d.severity == DiagnosticSeverity.Warning for d in warnings)


# =============================================================================
# Diagnostica chiavi duplicate dentro uno stream
# =============================================================================

class TestGetDiagnosticsDuplicateKeys:
    """
    Se una chiave appare due volte nello stesso stream, il provider
    produce un Error su ENTRAMBE le occorrenze.
    """

    def test_parametro_scalare_duplicato_produce_errore(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = (
            "streams:\n"
            "  - stream_id: s1\n"
            "    onset: 0.0\n"
            "    duration: 10.0\n"
            "    sample: f.wav\n"
            "    density: 100\n"
            "    density: 200\n"
        )
        result = provider.get_diagnostics(yaml)
        errors = [d for d in result if 'duplicat' in d.message.lower()
                  and 'density' in d.message]
        assert len(errors) == 2

    def test_blocco_duplicato_produce_errore(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = (
            "streams:\n"
            "  - stream_id: s1\n"
            "    onset: 0.0\n"
            "    duration: 10.0\n"
            "    sample: f.wav\n"
            "    pitch:\n"
            "      ratio: 1.5\n"
            "    pitch:\n"
            "      ratio: 2.0\n"
        )
        result = provider.get_diagnostics(yaml)
        errors = [d for d in result if 'duplicat' in d.message.lower()
                  and 'pitch' in d.message]
        assert len(errors) == 2

    def test_errore_su_entrambe_le_righe(self, bridge):
        """L'errore deve puntare alla riga di ogni occorrenza."""
        provider = DiagnosticProvider(bridge)
        yaml = (
            "streams:\n"
            "  - stream_id: s1\n"
            "    onset: 0.0\n"
            "    duration: 10.0\n"
            "    sample: f.wav\n"
            "    density: 100\n"
            "    density: 200\n"
        )
        result = provider.get_diagnostics(yaml)
        errors = [d for d in result if 'duplicat' in d.message.lower()
                  and 'density' in d.message]
        lines = {d.range.start.line for d in errors}
        assert 5 in lines
        assert 6 in lines

    def test_duplicato_in_stream_diversi_non_da_errore(self, bridge):
        """La stessa chiave in stream diversi non e' un duplicato."""
        provider = DiagnosticProvider(bridge)
        yaml = (
            "streams:\n"
            "  - stream_id: s1\n"
            "    onset: 0.0\n"
            "    duration: 10.0\n"
            "    sample: f.wav\n"
            "    density: 100\n"
            "  - stream_id: s2\n"
            "    onset: 5.0\n"
            "    duration: 5.0\n"
            "    sample: f.wav\n"
            "    density: 200\n"
        )
        result = provider.get_diagnostics(yaml)
        errors = [d for d in result if 'duplicat' in d.message.lower()]
        assert errors == []

    def test_chiave_singola_nessun_errore(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = (
            "streams:\n"
            "  - stream_id: s1\n"
            "    onset: 0.0\n"
            "    duration: 10.0\n"
            "    sample: f.wav\n"
            "    density: 100\n"
        )
        result = provider.get_diagnostics(yaml)
        errors = [d for d in result if 'duplicat' in d.message.lower()]
        assert errors == []

    def test_severity_e_error(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = (
            "streams:\n"
            "  - stream_id: s1\n"
            "    onset: 0.0\n"
            "    duration: 10.0\n"
            "    sample: f.wav\n"
            "    density: 100\n"
            "    density: 200\n"
        )
        result = provider.get_diagnostics(yaml)
        errors = [d for d in result if 'duplicat' in d.message.lower()]
        assert all(d.severity == DiagnosticSeverity.Error for d in errors)
