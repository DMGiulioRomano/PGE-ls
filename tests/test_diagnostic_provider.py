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
        'group_priority': group_priority, 'range_path': None, 'deviation_probability_key': None,
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
            make_raw_spec('volume', 'volume', default=0.0),
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

    def test_pitch_semitones_e_ratio_insieme_produce_error(self, bridge):
        # Superficie unit-driven: piu' chiavi-unità nello stesso blocco
        # pitch e' un Error del motore (non piu' Warning da pitch_mode).
        provider = DiagnosticProvider(bridge)
        yaml = (
            "streams:\n"
            "  - stream_id: s1\n"
            "    pitch:\n"
            "      semitones: 12\n"
            "      ratio: 2.0\n"
        )
        result = provider.get_diagnostics(yaml)
        errors = [d for d in result
                  if d.severity == DiagnosticSeverity.Error
                  and 'sola unit' in d.message.lower()]
        assert len(errors) == 2  # uno per ciascuna chiave-unità

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
    I campi obbligatori di ogni stream sono tre: stream_id, onset, sample.
    Se uno manca, il provider deve produrre un Warning con il nome del campo.

    `duration` non e' fra questi (PGE #205): se assente, il motore usa la
    durata del file audio dichiarato in `sample`.
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

    def test_stream_senza_duration_non_produce_diagnostiche(self, bridge):
        """PGE #205: `duration` e' opzionale — assente vale la durata del
        sample. Segnalarla sarebbe un falso positivo su ogni YAML valido che
        sfrutta il default."""
        provider = DiagnosticProvider(bridge)
        yaml = (
            "streams:\n"
            "  - stream_id: s1\n"
            "    onset: 0.0\n"
            "    sample: file.wav\n"
        )
        result = provider.get_diagnostics(yaml)
        assert result == []

    def test_duration_senza_valore_non_produce_diagnostiche(self, bridge):
        """`duration:` senza valore e' `duration: null`, che per il motore
        vale come chiave assente (PGE #205)."""
        provider = DiagnosticProvider(bridge)
        yaml = (
            "streams:\n"
            "  - stream_id: s1\n"
            "    onset: 0.0\n"
            "    duration:\n"
            "    sample: file.wav\n"
        )
        result = provider.get_diagnostics(yaml)
        assert result == []

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


class TestEnvelopeBoundsInline:
    """
    Envelope inline sulla riga della chiave: key: [[t, v], ...]

    Prima di questo check venivano validati solo i breakpoint block-style
    ('- [t, v]' su righe separate sotto una chiave nuda). L'estrazione dei
    valori Y riusa _extract_envelope_y_values (stessi formati del check
    pitch: breakpoints standard, breakpoint singolo [t, y], compact loop).
    """

    @staticmethod
    def _stream(body: str) -> str:
        return (
            "streams:\n"
            "  - stream_id: s1\n"
            "    onset: 0.0\n"
            "    duration: 10.0\n"
            "    sample: f.wav\n"
            + body
        )

    def test_inline_fuori_bounds_produce_errore(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = self._stream("    density: [[0.0, 100.0], [10.0, 9999.0]]\n")
        result = provider.get_diagnostics(yaml)
        errors = [d for d in result if d.severity == DiagnosticSeverity.Error
                  and 'fuori dai bounds' in d.message]
        assert len(errors) == 1
        assert "'density'" in errors[0].message
        assert '9999' in errors[0].message

    def test_inline_errore_punta_alla_riga_della_chiave(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = self._stream("    density: [[0.0, 100.0], [10.0, 9999.0]]\n")
        result = provider.get_diagnostics(yaml)
        errors = [d for d in result if d.severity == DiagnosticSeverity.Error
                  and 'fuori dai bounds' in d.message]
        assert errors[0].range.start.line == 5

    def test_inline_dentro_bounds_nessun_errore(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = self._stream("    density: [[0.0, 100.0], [10.0, 500.0]]\n")
        result = provider.get_diagnostics(yaml)
        errors = [d for d in result if d.severity == DiagnosticSeverity.Error]
        assert errors == []

    def test_inline_compact_loop_valida_y_del_pattern(self, bridge):
        provider = DiagnosticProvider(bridge)
        # Compact: [[[x_pct, y], ...], end_time, n_reps] — 9999 nel pattern
        yaml = self._stream(
            "    density: [[[0, 100.0], [50, 9999.0], [100, 100.0]], 10, 3]\n"
        )
        result = provider.get_diagnostics(yaml)
        errors = [d for d in result if d.severity == DiagnosticSeverity.Error
                  and 'fuori dai bounds' in d.message]
        assert len(errors) == 1
        assert '9999' in errors[0].message

    def test_inline_breakpoint_singolo(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = self._stream("    volume: [0.0, -80.0]\n")  # -80 < -60 (min)
        result = provider.get_diagnostics(yaml)
        errors = [d for d in result if d.severity == DiagnosticSeverity.Error
                  and 'fuori dai bounds' in d.message]
        assert len(errors) == 1
        assert "'volume'" in errors[0].message

    def test_inline_risoluzione_per_chiave_locale(self, bridge):
        provider = DiagnosticProvider(bridge)
        # 'duration' dentro grain: risolve grain.duration (0.001, 10) per
        # chiave locale, come gia' avviene per i breakpoint block-style.
        yaml = self._stream(
            "    grain:\n"
            "      duration: [[0.0, 0.05], [10.0, 99.0]]\n"
        )
        result = provider.get_diagnostics(yaml)
        errors = [d for d in result if d.severity == DiagnosticSeverity.Error
                  and 'fuori dai bounds' in d.message]
        assert len(errors) == 1
        assert "'grain.duration'" in errors[0].message

    def test_inline_lista_non_numerica_ignorata(self, bridge):
        provider = DiagnosticProvider(bridge)
        # Lista di nomi (grain.envelope) e lista non parseabile su chiave
        # con bounds: entrambe ignorate (tolleranza, come il block-style).
        yaml = self._stream(
            "    grain:\n"
            "      envelope: [hanning, hamming]\n"
            "    density: [alto, basso]\n"
        )
        result = provider.get_diagnostics(yaml)
        errors = [d for d in result if d.severity == DiagnosticSeverity.Error
                  and 'fuori dai bounds' in d.message]
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


# =============================================================================
# TestCheckMissingValues
# =============================================================================

class TestCheckMissingValues:
    """
    Parametri numerici (con bounds) scritti come 'chiave:' senza valore
    devono produrre un Error. Parametri flag (solo, mute) e chiavi di
    contesto stream (range_always_active, ecc.) non devono essere toccati.
    """

    def _missing_errors(self, result):
        return [d for d in result if 'richiede un valore' in d.message]

    def test_parametro_con_valore_scalare_nessun_errore(self, bridge):
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
        assert self._missing_errors(result) == []

    def test_parametro_senza_valore_produce_errore(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = (
            "streams:\n"
            "  - stream_id: s1\n"
            "    onset: 0.0\n"
            "    duration: 10.0\n"
            "    sample: f.wav\n"
            "    density:\n"
        )
        result = provider.get_diagnostics(yaml)
        errors = self._missing_errors(result)
        assert len(errors) == 1
        assert 'density' in errors[0].message

    def test_parametro_con_envelope_lista_nessun_errore(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = (
            "streams:\n"
            "  - stream_id: s1\n"
            "    onset: 0.0\n"
            "    duration: 10.0\n"
            "    sample: f.wav\n"
            "    density:\n"
            "      - [0, 100]\n"
            "      - [1, 200]\n"
        )
        result = provider.get_diagnostics(yaml)
        assert self._missing_errors(result) == []

    def test_parametro_con_envelope_dict_nessun_errore(self, bridge):
        # Formato dict envelope: type + points → non deve essere segnalato
        provider = DiagnosticProvider(bridge)
        yaml = (
            "streams:\n"
            "  - stream_id: s1\n"
            "    onset: 0.0\n"
            "    duration: 10.0\n"
            "    sample: f.wav\n"
            "    distribution:\n"
            "      type: compact\n"
            "      points:\n"
            "        - [0, 0.5]\n"
            "        - [1, 0.8]\n"
        )
        result = provider.get_diagnostics(yaml)
        assert self._missing_errors(result) == []

    def test_parametro_annidato_senza_valore_produce_errore(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = (
            "streams:\n"
            "  - stream_id: s1\n"
            "    onset: 0.0\n"
            "    duration: 10.0\n"
            "    sample: f.wav\n"
            "    grain:\n"
            "      duration:\n"
        )
        result = provider.get_diagnostics(yaml)
        errors = self._missing_errors(result)
        assert len(errors) == 1
        assert 'grain.duration' in errors[0].message

    def test_parametro_annidato_con_envelope_nessun_errore(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = (
            "streams:\n"
            "  - stream_id: s1\n"
            "    onset: 0.0\n"
            "    duration: 10.0\n"
            "    sample: f.wav\n"
            "    grain:\n"
            "      duration:\n"
            "        - [0, 0.05]\n"
        )
        result = provider.get_diagnostics(yaml)
        assert self._missing_errors(result) == []

    def test_severity_e_error(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = (
            "streams:\n"
            "  - stream_id: s1\n"
            "    onset: 0.0\n"
            "    duration: 10.0\n"
            "    sample: f.wav\n"
            "    density:\n"
        )
        result = provider.get_diagnostics(yaml)
        errors = self._missing_errors(result)
        assert errors[0].severity == DiagnosticSeverity.Error

    def test_errore_punta_alla_riga_del_parametro(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = (
            "streams:\n"
            "  - stream_id: s1\n"
            "    onset: 0.0\n"
            "    duration: 10.0\n"
            "    sample: f.wav\n"
            "    density:\n"
        )
        result = provider.get_diagnostics(yaml)
        errors = self._missing_errors(result)
        assert errors[0].range.start.line == 5  # riga 'density:' (0-based)

    def test_chiave_omonima_dentro_voices_non_da_errore(self, bridge):
        # 'distribution' dentro voices è un sub-blocco dimension, non il parametro
        # numerico 'distribution' dello stream → non deve essere segnalato
        provider = DiagnosticProvider(bridge)
        yaml = (
            "streams:\n"
            "  - stream_id: s1\n"
            "    onset: 0.0\n"
            "    duration: 10.0\n"
            "    sample: f.wav\n"
            "    voices:\n"
            "      num_voices: 4\n"
            "      pitch:\n"
            "        strategy: step\n"
            "        step: 3.0\n"
            "      distribution:\n"        # omonimo ma è voices.distribution (sub-blocco)
            "        strategy: linear\n"
        )
        result = provider.get_diagnostics(yaml)
        errors = self._missing_errors(result)
        assert errors == []

    def test_time_mode_senza_valore_produce_errore(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = (
            "streams:\n"
            "  - stream_id: s1\n"
            "    onset: 0.0\n"
            "    duration: 10.0\n"
            "    sample: f.wav\n"
            "    time_mode:\n"
        )
        result = provider.get_diagnostics(yaml)
        errors = [d for d in result if 'time_mode' in d.message]
        assert len(errors) == 1
        assert errors[0].severity == DiagnosticSeverity.Error

    def test_time_mode_con_valore_nessun_errore(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = (
            "streams:\n"
            "  - stream_id: s1\n"
            "    onset: 0.0\n"
            "    duration: 10.0\n"
            "    sample: f.wav\n"
            "    time_mode: normalized\n"
        )
        result = provider.get_diagnostics(yaml)
        errors = [d for d in result if 'time_mode' in d.message]
        assert errors == []

    # --- Campi obbligatori stream ---

    def test_duration_senza_valore_non_produce_errore(self, bridge):
        """PGE #205: `duration` non e' piu' un campo che richiede un valore.
        `duration:` nudo e' `duration: null` — durata del sample, non una
        dichiarazione lasciata a meta'."""
        provider = DiagnosticProvider(bridge)
        yaml = (
            "streams:\n"
            "  - stream_id: s1\n"
            "    onset: 0.0\n"
            "    duration:\n"
            "    sample: f.wav\n"
        )
        result = provider.get_diagnostics(yaml)
        errors = [d for d in result if "'duration'" in d.message and 'richiede' in d.message]
        assert errors == []

    def test_onset_senza_valore_produce_errore(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = (
            "streams:\n"
            "  - stream_id: s1\n"
            "    onset:\n"
            "    duration: 10.0\n"
            "    sample: f.wav\n"
        )
        result = provider.get_diagnostics(yaml)
        errors = [d for d in result if "'onset'" in d.message and 'richiede' in d.message]
        assert len(errors) == 1
        assert errors[0].severity == DiagnosticSeverity.Error

    def test_sample_senza_valore_produce_errore(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = (
            "streams:\n"
            "  - stream_id: s1\n"
            "    onset: 0.0\n"
            "    duration: 10.0\n"
            "    sample:\n"
        )
        result = provider.get_diagnostics(yaml)
        errors = [d for d in result if "'sample'" in d.message and 'richiede' in d.message]
        assert len(errors) == 1
        assert errors[0].severity == DiagnosticSeverity.Error

    # --- voices.num_voices e voices.scatter ---

    def test_num_voices_senza_valore_produce_errore(self, bridge):
        # Richiede bridge con raw_bounds per num_voices
        raw = {
            'specs': [],
            'bounds': {},
            'extra_bounds': {'num_voices': {'min_val': 1.0, 'max_val': 256.0,
                                            'min_range': 0.0, 'max_range': 0.0,
                                            'default_jitter': 0.0, 'variation_mode': 'additive'}},
        }
        from granular_ls.schema_bridge import SchemaBridge
        b = SchemaBridge({'specs': [], 'bounds': {'num_voices': {
            'min_val': 1.0, 'max_val': 256.0, 'min_range': 0.0,
            'max_range': 0.0, 'default_jitter': 0.0, 'variation_mode': 'additive'}}})
        provider = DiagnosticProvider(b)
        yaml = (
            "streams:\n"
            "  - stream_id: s1\n"
            "    onset: 0.0\n"
            "    duration: 10.0\n"
            "    sample: f.wav\n"
            "    voices:\n"
            "      num_voices:\n"
        )
        result = provider.get_diagnostics(yaml)
        errors = self._missing_errors(result)
        assert len(errors) == 1
        assert 'voices.num_voices' in errors[0].message

    # --- Voice kwargs ---

    def test_voice_kwarg_senza_valore_produce_errore(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = (
            "streams:\n"
            "  - stream_id: s1\n"
            "    onset: 0.0\n"
            "    duration: 10.0\n"
            "    sample: f.wav\n"
            "    voices:\n"
            "      num_voices: 4\n"
            "      pitch:\n"
            "        strategy: step\n"
            "        step:\n"          # kwarg senza valore
        )
        result = provider.get_diagnostics(yaml)
        errors = self._missing_errors(result)
        assert len(errors) == 1
        assert 'voices.pitch.step' in errors[0].message
        assert errors[0].severity == DiagnosticSeverity.Error

    def test_voice_strategy_senza_valore_produce_errore(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = (
            "streams:\n"
            "  - stream_id: s1\n"
            "    onset: 0.0\n"
            "    duration: 10.0\n"
            "    sample: f.wav\n"
            "    voices:\n"
            "      num_voices: 4\n"
            "      pitch:\n"
            "        strategy:\n"     # strategy senza valore
        )
        result = provider.get_diagnostics(yaml)
        errors = self._missing_errors(result)
        assert len(errors) == 1
        assert 'voices.pitch.strategy' in errors[0].message

    def test_voice_strategy_senza_valore_no_double_error(self, bridge):
        # Non deve esserci anche l'errore "Strategy `` non valida" da _check_voice_strategies
        provider = DiagnosticProvider(bridge)
        yaml = (
            "streams:\n"
            "  - stream_id: s1\n"
            "    onset: 0.0\n"
            "    duration: 10.0\n"
            "    sample: f.wav\n"
            "    voices:\n"
            "      num_voices: 4\n"
            "      pitch:\n"
            "        strategy:\n"
        )
        result = provider.get_diagnostics(yaml)
        # Non deve comparire "Strategy `` non valida"
        bogus = [d for d in result if 'non valida' in d.message and 'strategy' in d.message.lower()]
        assert bogus == []

    def test_voice_inline_dict_nessun_errore(self, bridge):
        # pan: {strategy: range, spread: 90} non deve produrre warning "richiede strategy"
        provider = DiagnosticProvider(bridge)
        yaml = (
            "streams:\n"
            "  - stream_id: s1\n"
            "    onset: 0.0\n"
            "    duration: 10.0\n"
            "    sample: f.wav\n"
            "    voices:\n"
            "      num_voices: 4\n"
            "      pan: {strategy: range, spread: 90}\n"
        )
        result = provider.get_diagnostics(yaml)
        strategy_warnings = [d for d in result if 'richiede la chiave `strategy`' in d.message]
        assert strategy_warnings == []

    def test_voice_kwarg_con_valore_nessun_errore(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = (
            "streams:\n"
            "  - stream_id: s1\n"
            "    onset: 0.0\n"
            "    duration: 10.0\n"
            "    sample: f.wav\n"
            "    voices:\n"
            "      num_voices: 4\n"
            "      pitch:\n"
            "        strategy: step\n"
            "        step: 3.0\n"
        )
        result = provider.get_diagnostics(yaml)
        errors = self._missing_errors(result)
        assert errors == []

    # --- voices.pointer normalized ---

    def test_pointer_normalized_true_nessun_errore(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = (
            "streams:\n"
            "  - stream_id: s1\n"
            "    onset: 0.0\n"
            "    duration: 10.0\n"
            "    sample: f.wav\n"
            "    voices:\n"
            "      num_voices: 4\n"
            "      pointer:\n"
            "        strategy: linear\n"
            "        step: 0.1\n"
            "        normalized: true\n"
        )
        result = provider.get_diagnostics(yaml)
        bool_errors = [d for d in result if 'normalized' in d.message]
        assert bool_errors == []

    def test_pointer_normalized_false_nessun_errore(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = (
            "streams:\n"
            "  - stream_id: s1\n"
            "    onset: 0.0\n"
            "    duration: 10.0\n"
            "    sample: f.wav\n"
            "    voices:\n"
            "      num_voices: 4\n"
            "      pointer:\n"
            "        strategy: stochastic\n"
            "        pointer_range: 0.05\n"
            "        normalized: false\n"
        )
        result = provider.get_diagnostics(yaml)
        bool_errors = [d for d in result if 'normalized' in d.message]
        assert bool_errors == []

    def test_rng_group_senza_valore_produce_errore(self, bridge):
        """rng_group vuoto (PGE #169) e' un silent no-op: l'engine ricade
        sull'identita' stream_id e la sequenza NON viene condivisa, ma
        l'utente crede di aver creato il gruppo."""
        provider = DiagnosticProvider(bridge)
        yaml = (
            "streams:\n"
            "  - stream_id: s1\n"
            "    onset: 0.0\n"
            "    duration: 10.0\n"
            "    sample: f.wav\n"
            "    rng_group:\n"
        )
        result = provider.get_diagnostics(yaml)
        errors = self._missing_errors(result)
        assert len(errors) == 1
        assert 'rng_group' in errors[0].message

    def test_rng_group_lista_produce_errore(self, bridge):
        """rng_group e' un'identita' testuale: una lista finirebbe all'engine
        come identita' \"['a', 'b']\" (f-string sul valore), silenziosamente.
        Parita' con la diagnostica rng-group-type di gl-ls."""
        provider = DiagnosticProvider(bridge)
        yaml = (
            "streams:\n"
            "  - stream_id: s1\n"
            "    onset: 0.0\n"
            "    duration: 10.0\n"
            "    sample: f.wav\n"
            "    rng_group: [a, b]\n"
        )
        result = provider.get_diagnostics(yaml)
        errors = [d for d in result if 'rng_group' in d.message]
        assert len(errors) == 1
        assert 'identit' in errors[0].message

    def test_rng_group_dict_inline_produce_errore(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = (
            "streams:\n"
            "  - stream_id: s1\n"
            "    onset: 0.0\n"
            "    duration: 10.0\n"
            "    sample: f.wav\n"
            "    rng_group: {values: [a, b]}\n"
        )
        result = provider.get_diagnostics(yaml)
        assert [d for d in result if 'rng_group' in d.message]

    def test_rng_group_stringa_quotata_nessun_errore(self, bridge):
        """Una stringa quotata che contiene parentesi non e' una lista."""
        provider = DiagnosticProvider(bridge)
        yaml = (
            "streams:\n"
            "  - stream_id: s1\n"
            "    onset: 0.0\n"
            "    duration: 10.0\n"
            "    sample: f.wav\n"
            '    rng_group: "[cugini]"\n'
        )
        result = provider.get_diagnostics(yaml)
        assert [d for d in result if 'rng_group' in d.message] == []

    def test_rng_group_con_valore_nessun_errore(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = (
            "streams:\n"
            "  - stream_id: s1\n"
            "    onset: 0.0\n"
            "    duration: 10.0\n"
            "    sample: f.wav\n"
            "    rng_group: cugini\n"
        )
        result = provider.get_diagnostics(yaml)
        assert self._missing_errors(result) == []

    def test_pointer_normalized_valore_invalido_produce_errore(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = (
            "streams:\n"
            "  - stream_id: s1\n"
            "    onset: 0.0\n"
            "    duration: 10.0\n"
            "    sample: f.wav\n"
            "    voices:\n"
            "      num_voices: 4\n"
            "      pointer:\n"
            "        strategy: linear\n"
            "        step: 0.1\n"
            "        normalized: yes\n"   # non è bool YAML-LSP valido
        )
        result = provider.get_diagnostics(yaml)
        bool_errors = [d for d in result if 'normalized' in d.message]
        assert len(bool_errors) == 1
        assert 'true' in bool_errors[0].message or 'false' in bool_errors[0].message

    def test_pointer_assente_normalized_nessun_warning_richiesto(self, bridge):
        # normalized è opzionale: non deve produrre warning se assente
        provider = DiagnosticProvider(bridge)
        yaml = (
            "streams:\n"
            "  - stream_id: s1\n"
            "    onset: 0.0\n"
            "    duration: 10.0\n"
            "    sample: f.wav\n"
            "    voices:\n"
            "      num_voices: 4\n"
            "      pointer:\n"
            "        strategy: linear\n"
            "        step: 0.1\n"
        )
        result = provider.get_diagnostics(yaml)
        normalized_warnings = [d for d in result if 'normalized' in d.message]
        assert normalized_warnings == []


# =============================================================================
# BLOCCO PITCH UNIT-DRIVEN (issue #9) - validazione strict
# =============================================================================

def _stream_yaml(body: str) -> str:
    """Costruisce uno stream completo con il corpo indicato (indent 4)."""
    return (
        "streams:\n"
        "  - stream_id: s1\n"
        "    onset: 0.0\n"
        "    duration: 10.0\n"
        "    sample: f.wav\n"
        + body
    )


def _pitch_errors(result):
    return [d for d in result
            if d.severity == DiagnosticSeverity.Error
            and 'pitch' in d.message.lower()]


class TestPitchBlockStrict:
    """Validazione strict del blocco pitch (speculare a _select_unit di PGE)."""

    def test_blocco_valido_semitones_nessun_errore(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = _stream_yaml("    pitch:\n      semitones: 12\n")
        assert _pitch_errors(provider.get_diagnostics(yaml)) == []

    def test_pitch_vuoto_produce_errore(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = _stream_yaml("    pitch:\n")
        errors = _pitch_errors(provider.get_diagnostics(yaml))
        assert len(errors) == 1
        assert 'ometti' in errors[0].message

    def test_pitch_scalare_non_mapping_produce_errore(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = _stream_yaml("    pitch: 3.0\n")
        errors = _pitch_errors(provider.get_diagnostics(yaml))
        assert len(errors) == 1
        assert 'mapping' in errors[0].message

    def test_pitch_lista_non_mapping_produce_errore(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = _stream_yaml("    pitch: [[0, -12], [10, 12]]\n")
        errors = _pitch_errors(provider.get_diagnostics(yaml))
        assert len(errors) == 1

    def test_pitch_mapping_vuoto_valido(self, bridge):
        # pitch: {} -> default semitoni neutro, indistinguibile da assente
        provider = DiagnosticProvider(bridge)
        yaml = _stream_yaml("    pitch: {}\n")
        assert _pitch_errors(provider.get_diagnostics(yaml)) == []

    def test_chiave_sconosciuta_produce_errore(self, bridge):
        # refuso tipico: semitone invece di semitones
        provider = DiagnosticProvider(bridge)
        yaml = _stream_yaml("    pitch:\n      semitone: 12\n")
        errors = _pitch_errors(provider.get_diagnostics(yaml))
        assert any('sconosciuta' in e.message.lower() for e in errors)
        assert any('semitone' in e.message for e in errors)

    def test_chiave_sconosciuta_inline_produce_errore(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = _stream_yaml("    pitch: {semitone: 12}\n")
        errors = _pitch_errors(provider.get_diagnostics(yaml))
        assert any('sconosciuta' in e.message.lower() for e in errors)

    def test_value_senza_edo_produce_errore(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = _stream_yaml("    pitch:\n      semitones: 3\n      value: 5\n")
        errors = _pitch_errors(provider.get_diagnostics(yaml))
        assert any('solo con `edo' in e.message for e in errors)

    def test_solo_range_senza_unita_valido(self, bridge):
        # 0 chiavi-unità + modificatore: default semitoni, valido
        provider = DiagnosticProvider(bridge)
        yaml = _stream_yaml("    pitch:\n      range: 6\n")
        assert _pitch_errors(provider.get_diagnostics(yaml)) == []


class TestPitchBlockEdo:
    """Grammatica appiattita: edo: N + value: X a fianco."""

    def test_edo_valido_nessun_errore(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = _stream_yaml("    pitch:\n      edo: 31\n      value: 18\n")
        assert _pitch_errors(provider.get_diagnostics(yaml)) == []

    def test_forma_annidata_inline_hard_break(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = _stream_yaml(
            "    pitch:\n      edo: {divisions: 31, value: 18}\n"
        )
        errors = _pitch_errors(provider.get_diagnostics(yaml))
        assert any('annidata' in e.message for e in errors)

    def test_forma_annidata_block_style_hard_break(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = _stream_yaml(
            "    pitch:\n      edo:\n        divisions: 31\n        value: 18\n"
        )
        errors = _pitch_errors(provider.get_diagnostics(yaml))
        assert any('annidata' in e.message for e in errors)

    def test_edo_non_intero_produce_errore(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = _stream_yaml("    pitch:\n      edo: 2.5\n      value: 1\n")
        errors = _pitch_errors(provider.get_diagnostics(yaml))
        assert any('intero > 0' in e.message for e in errors)

    def test_edo_zero_produce_errore(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = _stream_yaml("    pitch:\n      edo: 0\n      value: 1\n")
        errors = _pitch_errors(provider.get_diagnostics(yaml))
        assert any('intero > 0' in e.message for e in errors)

    def test_edo_senza_value_produce_errore(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = _stream_yaml("    pitch:\n      edo: 31\n")
        errors = _pitch_errors(provider.get_diagnostics(yaml))
        assert any('serve `value' in e.message for e in errors)

    def test_value_fuori_bounds_dinamici(self, bridge):
        # edo: 31 -> bounds ±93
        provider = DiagnosticProvider(bridge)
        yaml = _stream_yaml("    pitch:\n      edo: 31\n      value: 94\n")
        errors = _pitch_errors(provider.get_diagnostics(yaml))
        assert any('fuori range' in e.message
                   and '93' in e.message for e in errors)

    def test_value_envelope_dentro_bounds(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = _stream_yaml(
            "    pitch:\n      edo: 31\n      value: [[0, -93], [10, 93]]\n"
        )
        assert _pitch_errors(provider.get_diagnostics(yaml)) == []


class TestPitchBlockBounds:
    """Bounds per-unità: scalari ed envelope."""

    @pytest.mark.parametrize('chiave, valore, limite', [
        ('semitones', '37', '36'),
        ('semitones', '-37', '36'),
        ('cents', '3700', '3600'),
        ('quarter_tone', '73', '72'),
        ('eighth_tone', '-145', '144'),
        ('ratio', '9', '8'),
        ('ratio', '0.0005', '0.001'),
    ])
    def test_scalare_fuori_bounds(self, bridge, chiave, valore, limite):
        provider = DiagnosticProvider(bridge)
        yaml = _stream_yaml(f"    pitch:\n      {chiave}: {valore}\n")
        errors = _pitch_errors(provider.get_diagnostics(yaml))
        assert any('fuori range' in e.message and limite in e.message
                   for e in errors), errors

    @pytest.mark.parametrize('chiave, valore', [
        ('semitones', '36'),
        ('semitones', '-36'),
        ('cents', '50'),
        ('ratio', '1.5'),
        ('ratio', '0.05'),
    ])
    def test_scalare_dentro_bounds(self, bridge, chiave, valore):
        provider = DiagnosticProvider(bridge)
        yaml = _stream_yaml(f"    pitch:\n      {chiave}: {valore}\n")
        assert _pitch_errors(provider.get_diagnostics(yaml)) == []

    def test_envelope_inline_y_fuori_bounds(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = _stream_yaml(
            "    pitch:\n      semitones: [[0, -50], [10, 50]]\n"
        )
        errors = _pitch_errors(provider.get_diagnostics(yaml))
        assert len(errors) == 2  # entrambi gli Y fuori da [-36, 36]

    def test_envelope_block_style_y_fuori_bounds(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = _stream_yaml(
            "    pitch:\n"
            "      semitones:\n"
            "        - [0, -50]\n"
            "        - [10, 12]\n"
        )
        errors = _pitch_errors(provider.get_diagnostics(yaml))
        assert len(errors) == 1
        assert '-50' in errors[0].message

    def test_envelope_compact_y_fuori_bounds(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = _stream_yaml(
            "    pitch:\n      semitones: [[[0, 0], [50, -48], [100, 0]], 10, 3]\n"
        )
        errors = _pitch_errors(provider.get_diagnostics(yaml))
        assert any('-48' in e.message for e in errors)

    def test_unita_senza_valore_produce_errore(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = _stream_yaml("    pitch:\n      semitones:\n")
        errors = _pitch_errors(provider.get_diagnostics(yaml))
        assert any('richiede un valore' in e.message for e in errors)

    def test_range_con_ratio_fuori_bounds(self, bridge):
        # max_range dell'unità ratio: 2.0
        provider = DiagnosticProvider(bridge)
        yaml = _stream_yaml("    pitch:\n      ratio: 1.5\n      range: 3\n")
        errors = _pitch_errors(provider.get_diagnostics(yaml))
        assert any('pitch.range' in e.message for e in errors)

    def test_range_default_semitoni(self, bridge):
        # senza unità: default semitones, max_range 36
        provider = DiagnosticProvider(bridge)
        yaml = _stream_yaml("    pitch:\n      range: 40\n")
        errors = _pitch_errors(provider.get_diagnostics(yaml))
        assert any('pitch.range' in e.message and '36' in e.message
                   for e in errors)

    def test_legacy_snapshot_pitch_spec_ignorate(self, bridge):
        # il bridge fixture ha ancora pitch_semitones [-48, 48] (snapshot
        # legacy): i bounds validi sono quelli dell'unità ([-36, 36])
        provider = DiagnosticProvider(bridge)
        yaml = _stream_yaml("    pitch:\n      semitones: 40\n")
        errors = _pitch_errors(provider.get_diagnostics(yaml))
        assert any('36' in e.message for e in errors)


# =============================================================================
# VOICES.PITCH UNIT-AWARE (issue #10)
# =============================================================================

def _voices_yaml(body: str) -> str:
    return _stream_yaml(
        "    voices:\n      num_voices: 4\n      pitch:\n" + body
    )


class TestVoicePitchUnit:
    """Validazione di voices.pitch: unit, lock semitoni, pitch_range."""

    def test_semitone_range_produce_errore_rename(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = _voices_yaml(
            "        strategy: range\n        semitone_range: 12\n"
        )
        result = provider.get_diagnostics(yaml)
        errors = [d for d in result
                  if d.severity == DiagnosticSeverity.Error
                  and 'pitch_range' in d.message]
        assert len(errors) == 1
        # nessun warning ridondante 'richiede il kwarg pitch_range'
        warnings = [d for d in result
                    if d.severity == DiagnosticSeverity.Warning
                    and 'pitch_range' in d.message]
        assert warnings == []

    def test_pitch_range_valido_nessun_errore(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = _voices_yaml(
            "        strategy: range\n        pitch_range: 12\n"
        )
        result = provider.get_diagnostics(yaml)
        assert [d for d in result
                if d.severity == DiagnosticSeverity.Error] == []

    def test_range_con_unit_ratio_valido(self, bridge):
        # issue #10: range/stochastic VALIDI con unit: ratio
        provider = DiagnosticProvider(bridge)
        yaml = _voices_yaml(
            "        strategy: range\n"
            "        pitch_range: 2.0\n"
            "        unit: ratio\n"
        )
        result = provider.get_diagnostics(yaml)
        assert [d for d in result
                if d.severity == DiagnosticSeverity.Error] == []

    def test_ampiezza_zero_con_ratio_produce_warning(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = _voices_yaml(
            "        strategy: range\n"
            "        pitch_range: 0\n"
            "        unit: ratio\n"
        )
        result = provider.get_diagnostics(yaml)
        warnings = [d for d in result
                    if d.severity == DiagnosticSeverity.Warning
                    and 'identit' in d.message]
        assert len(warnings) == 1

    def test_step_negativo_con_ratio_produce_warning(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = _voices_yaml(
            "        strategy: step\n"
            "        step: -1.0\n"
            "        unit: ratio\n"
        )
        result = provider.get_diagnostics(yaml)
        warnings = [d for d in result
                    if d.severity == DiagnosticSeverity.Warning
                    and 'identit' in d.message]
        assert len(warnings) == 1

    def test_step_negativo_senza_ratio_nessun_warning(self, bridge):
        # con la famiglia EDO step negativo e' una progressione discendente
        provider = DiagnosticProvider(bridge)
        yaml = _voices_yaml(
            "        strategy: step\n        step: -1.0\n"
        )
        result = provider.get_diagnostics(yaml)
        warnings = [d for d in result
                    if d.severity == DiagnosticSeverity.Warning
                    and 'identit' in d.message]
        assert warnings == []

    @pytest.mark.parametrize('strategy, extra', [
        ('chord', '        chord: dom7\n'),
        ('spectral', ''),
    ])
    def test_semitone_locked_con_unit_diversa(self, bridge, strategy, extra):
        provider = DiagnosticProvider(bridge)
        yaml = _voices_yaml(
            f"        strategy: {strategy}\n{extra}        unit: cents\n"
        )
        result = provider.get_diagnostics(yaml)
        errors = [d for d in result
                  if d.severity == DiagnosticSeverity.Error
                  and 'semiton' in d.message]
        assert len(errors) == 1
        assert 'ometti `unit`' in errors[0].message

    def test_semitone_locked_con_edo_dict(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = _voices_yaml(
            "        strategy: chord\n"
            "        chord: maj\n"
            "        unit: {edo: 24}\n"
        )
        result = provider.get_diagnostics(yaml)
        errors = [d for d in result
                  if d.severity == DiagnosticSeverity.Error
                  and 'semiton' in d.message]
        assert len(errors) == 1

    def test_chord_con_unit_semitones_valido(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = _voices_yaml(
            "        strategy: chord\n"
            "        chord: dom7\n"
            "        unit: semitones\n"
        )
        result = provider.get_diagnostics(yaml)
        assert [d for d in result
                if d.severity == DiagnosticSeverity.Error] == []

    def test_unit_refuso_produce_errore(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = _voices_yaml(
            "        strategy: step\n        step: 3\n        unit: semitone\n"
        )
        result = provider.get_diagnostics(yaml)
        errors = [d for d in result
                  if d.severity == DiagnosticSeverity.Error
                  and 'voices.pitch.unit' in d.message]
        assert len(errors) == 1
        assert '{edo: N}' in errors[0].message

    def test_unit_edo_nudo_produce_errore(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = _voices_yaml(
            "        strategy: step\n        step: 3\n        unit: edo\n"
        )
        result = provider.get_diagnostics(yaml)
        errors = [d for d in result
                  if d.severity == DiagnosticSeverity.Error
                  and 'voices.pitch.unit' in d.message]
        assert len(errors) == 1

    def test_unit_edo_dict_valido(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = _voices_yaml(
            "        strategy: stochastic\n"
            "        pitch_range: 6\n"
            "        unit: {edo: 24}\n"
        )
        result = provider.get_diagnostics(yaml)
        assert [d for d in result
                if d.severity == DiagnosticSeverity.Error] == []

    def test_unit_edo_zero_produce_errore(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = _voices_yaml(
            "        strategy: step\n        step: 3\n        unit: {edo: 0}\n"
        )
        result = provider.get_diagnostics(yaml)
        errors = [d for d in result
                  if d.severity == DiagnosticSeverity.Error
                  and 'voices.pitch.unit' in d.message]
        assert len(errors) == 1

    def test_accordo_esteso_dom9_valido(self, bridge):
        # registry accordi allineato a PGE (22 accordi)
        provider = DiagnosticProvider(bridge)
        yaml = _voices_yaml(
            "        strategy: chord\n        chord: dom9\n"
        )
        result = provider.get_diagnostics(yaml)
        assert [d for d in result
                if d.severity == DiagnosticSeverity.Error] == []

    def test_inversion_valida_nessun_errore(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = _voices_yaml(
            "        strategy: chord\n"
            "        chord: dom7\n"
            "        inversion: 3\n"
        )
        result = provider.get_diagnostics(yaml)
        assert [d for d in result
                if d.severity == DiagnosticSeverity.Error] == []

    def test_inversion_fuori_range_produce_errore(self, bridge):
        # maj ha 3 note: inversion valida in [0, 2]
        provider = DiagnosticProvider(bridge)
        yaml = _voices_yaml(
            "        strategy: chord\n"
            "        chord: maj\n"
            "        inversion: 3\n"
        )
        result = provider.get_diagnostics(yaml)
        errors = [d for d in result
                  if d.severity == DiagnosticSeverity.Error
                  and 'inversion' in d.message]
        assert len(errors) == 1
        assert '[0, 2]' in errors[0].message

    def test_inversion_non_intera_produce_errore(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = _voices_yaml(
            "        strategy: chord\n"
            "        chord: maj\n"
            "        inversion: x\n"
        )
        result = provider.get_diagnostics(yaml)
        errors = [d for d in result
                  if d.severity == DiagnosticSeverity.Error
                  and 'inversion' in d.message]
        assert len(errors) == 1


# =============================================================================
# loop_end <= loop_start (finestra di loop degenere) — PGE commit ec61242
# =============================================================================

class TestLoopEndLeLoopStart:
    """Fase 10: loop_end <= loop_start (valori scalari) -> Error."""

    def _stream(self, pointer_body: str) -> str:
        return (
            "streams:\n"
            "  - stream_id: s1\n"
            "    duration: 10.0\n"
            "    sample: a.wav\n"
            "    pointer:\n"
            + pointer_body
        )

    def test_loop_end_minore_di_loop_start_errore(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = self._stream(
            "      loop_start: 2.0\n"
            "      loop_end: 1.0\n"
        )
        result = provider.get_diagnostics(yaml)
        errors = [d for d in result
                  if d.severity == DiagnosticSeverity.Error
                  and 'loop_end' in d.message]
        assert len(errors) == 1

    def test_loop_end_uguale_a_loop_start_errore(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = self._stream(
            "      loop_start: 2.0\n"
            "      loop_end: 2.0\n"
        )
        result = provider.get_diagnostics(yaml)
        errors = [d for d in result
                  if d.severity == DiagnosticSeverity.Error
                  and 'loop_end' in d.message]
        assert len(errors) == 1

    def test_loop_end_maggiore_di_loop_start_ok(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = self._stream(
            "      loop_start: 1.0\n"
            "      loop_end: 2.0\n"
        )
        result = provider.get_diagnostics(yaml)
        errors = [d for d in result
                  if d.severity == DiagnosticSeverity.Error
                  and 'loop_end' in d.message]
        assert len(errors) == 0

    def test_loop_dur_presente_nessun_errore_degenere(self, bridge):
        # loop_dur ha priorita': loop_end viene ignorato dal motore.
        provider = DiagnosticProvider(bridge)
        yaml = self._stream(
            "      loop_start: 2.0\n"
            "      loop_end: 1.0\n"
            "      loop_dur: 3.0\n"
        )
        result = provider.get_diagnostics(yaml)
        errors = [d for d in result
                  if d.severity == DiagnosticSeverity.Error
                  and 'degenere' in d.message]
        assert len(errors) == 0

    def test_loop_end_envelope_esente(self, bridge):
        # Endpoint dinamici (envelope): nessun controllo statico.
        provider = DiagnosticProvider(bridge)
        yaml = self._stream(
            "      loop_start: 2.0\n"
            "      loop_end: [[0.0, 1.0], [1.0, 0.5]]\n"
        )
        result = provider.get_diagnostics(yaml)
        errors = [d for d in result
                  if d.severity == DiagnosticSeverity.Error
                  and 'loop_end' in d.message and 'degenere' in d.message]
        assert len(errors) == 0


# =============================================================================
# VOICES.PITCH chord_progression (PGE issue #86 / PGE-ls #28)
# =============================================================================

class TestChordProgression:
    """Validazione della strategy pitch `chord_progression`."""

    @staticmethod
    def _errors(result):
        return [d for d in result if d.severity == DiagnosticSeverity.Error]

    def test_progression_valida_a_blocco(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = _voices_yaml(
            "        strategy: chord_progression\n"
            "        progression:\n"
            "          - [0, maj7]\n"
            "          - [8, min7, 1]\n"
            "          - [16, {chord: dom7, inversion: 0}]\n"
        )
        errors = self._errors(provider.get_diagnostics(yaml))
        assert errors == []

    def test_progression_valida_inline(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = _voices_yaml(
            "        strategy: chord_progression\n"
            "        progression: [[0, maj7], [8, min7]]\n"
        )
        errors = self._errors(provider.get_diagnostics(yaml))
        assert errors == []

    def test_progression_vuota_errore(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = _voices_yaml(
            "        strategy: chord_progression\n"
            "        progression:\n"
            "        interp: linear\n"
        )
        errors = self._errors(provider.get_diagnostics(yaml))
        assert any('vuota' in e.message for e in errors)

    def test_tempi_non_decrescenti(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = _voices_yaml(
            "        strategy: chord_progression\n"
            "        progression:\n"
            "          - [0, maj7]\n"
            "          - [4, min7]\n"
            "          - [2, dom7]\n"
        )
        errors = self._errors(provider.get_diagnostics(yaml))
        assert any('decrescenti' in e.message for e in errors)

    def test_accordo_non_valido(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = _voices_yaml(
            "        strategy: chord_progression\n"
            "        progression:\n"
            "          - [0, nonexistent]\n"
        )
        errors = self._errors(provider.get_diagnostics(yaml))
        assert any('nonexistent' in e.message and 'non valido' in e.message
                   for e in errors)

    def test_inversion_fuori_range(self, bridge):
        provider = DiagnosticProvider(bridge)
        # maj ha 3 note: inversion valido [0, 2]
        yaml = _voices_yaml(
            "        strategy: chord_progression\n"
            "        progression:\n"
            "          - [0, maj, 5]\n"
        )
        errors = self._errors(provider.get_diagnostics(yaml))
        assert any('inversion' in e.message for e in errors)

    def test_step_malformato(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = _voices_yaml(
            "        strategy: chord_progression\n"
            "        progression:\n"
            "          - [0]\n"
        )
        errors = self._errors(provider.get_diagnostics(yaml))
        assert any('tempo, accordo' in e.message for e in errors)

    def test_interp_enum_non_valido(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = _voices_yaml(
            "        strategy: chord_progression\n"
            "        progression:\n"
            "          - [0, maj7]\n"
            "        interp: parabolic\n"
        )
        errors = self._errors(provider.get_diagnostics(yaml))
        assert any('parabolic' in e.message for e in errors)

    def test_voice_leading_enum_non_valido(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = _voices_yaml(
            "        strategy: chord_progression\n"
            "        progression:\n"
            "          - [0, maj7]\n"
            "        voice_leading: random\n"
        )
        errors = self._errors(provider.get_diagnostics(yaml))
        assert any('random' in e.message for e in errors)

    def test_semitone_locked_unit_diversa(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = _voices_yaml(
            "        strategy: chord_progression\n"
            "        progression:\n"
            "          - [0, maj7]\n"
            "        unit: cents\n"
        )
        errors = [d for d in provider.get_diagnostics(yaml)
                  if d.severity == DiagnosticSeverity.Error
                  and 'semiton' in d.message]
        assert len(errors) == 1


# =============================================================================
# grain.duration_unit (PGE #158): unità samples/seconds del blocco grain
# =============================================================================

class TestGrainDurationUnit:
    """Diagnostica per grain.duration_unit (mirror di loop_unit del pointer)."""

    @staticmethod
    def _grain(body: str) -> str:
        return _stream_yaml("    grain:\n" + body)

    def _errors(self, provider, yaml):
        return [d for d in provider.get_diagnostics(yaml)
                if d.severity == DiagnosticSeverity.Error]

    def test_invalid_unit_flagged(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = self._grain(
            "      duration: 480\n"
            "      duration_unit: frames\n"
        )
        errs = self._errors(provider, yaml)
        assert any('duration_unit' in e.message and 'frames' in e.message
                   for e in errs)

    def test_valid_units_not_flagged(self, bridge):
        provider = DiagnosticProvider(bridge)
        for unit in ('seconds', 'samples'):
            body = "      duration: 0.05\n" if unit == 'seconds' else "      duration: 480\n"
            yaml = self._grain(body + f"      duration_unit: {unit}\n")
            errs = self._errors(provider, yaml)
            assert not any('duration_unit' in e.message for e in errs), unit

    def test_samples_requires_explicit_duration(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = self._grain(
            "      duration_range: 64\n"
            "      duration_unit: samples\n"
        )
        errs = self._errors(provider, yaml)
        assert any('duration' in e.message for e in errs)

    def test_samples_with_duration_ok(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = self._grain(
            "      duration: 480\n"
            "      duration_range: 64\n"
            "      duration_unit: samples\n"
        )
        errs = self._errors(provider, yaml)
        assert errs == []

    def test_samples_scalar_not_flagged_against_seconds_bound(self, bridge):
        provider = DiagnosticProvider(bridge)
        # 480 campioni: NON deve essere segnalato come > 10 (bound in secondi)
        yaml = self._grain(
            "      duration: 480\n"
            "      duration_unit: samples\n"
        )
        bad = [e for e in self._errors(provider, yaml)
               if 'grain.duration' in e.message
               and ('fuori range' in e.message or 'fuori dai bounds' in e.message)]
        assert bad == []

    def test_samples_envelope_not_flagged(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = self._grain(
            "      duration: [[0.0, 48], [10.0, 4800]]\n"
            "      duration_unit: samples\n"
        )
        bad = [e for e in self._errors(provider, yaml)
               if 'grain.duration' in e.message
               and ('fuori range' in e.message or 'fuori dai bounds' in e.message)]
        assert bad == []

    def test_samples_below_one_flagged(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = self._grain(
            "      duration: 0.5\n"
            "      duration_unit: samples\n"
        )
        errs = self._errors(provider, yaml)
        assert any('grain.duration' in e.message or 'campione' in e.message
                   for e in errs)

    def test_seconds_bound_still_enforced(self, bridge):
        """Nessuna regressione: in secondi il bound massimo resta attivo."""
        provider = DiagnosticProvider(bridge)
        yaml = self._grain("      duration: 999\n")  # default seconds, > 10
        bad = [e for e in self._errors(provider, yaml)
               if 'grain.duration' in e.message
               and ('fuori range' in e.message or 'fuori dai bounds' in e.message)]
        assert len(bad) == 1


# =============================================================================
# grain.read_direction (PGE #207)
# =============================================================================

@pytest.fixture
def rd_bridge():
    """Bridge con la coppia del gruppo 'grain_direction'.

    Il fixture `bridge` generale non ha le due chiavi del verso; qui servono
    entrambe, e con i metadati veri: `read_direction` ha bounds [-1, 1] e
    priorita' 2, `reverse` default 0 e priorita' 1. Sono esattamente i dati
    che rendono il check generico insufficiente — un bound continuo su un
    dominio di due valori, e un gruppo esclusivo che qui e' un errore e non
    una priorita'.
    """
    raw = {
        'specs': [
            make_raw_spec('density', 'density', default=None),
            make_raw_spec('reverse', 'grain.reverse', default=0,
                          exclusive_group='grain_direction', group_priority=1),
            make_raw_spec('read_direction', 'grain.read_direction',
                          default=None, exclusive_group='grain_direction',
                          group_priority=2),
        ],
        'bounds': {
            'density': make_raw_bounds(0.01, 4000.0),
            'reverse': make_raw_bounds(0, 1, variation_mode='invert'),
            'read_direction': make_raw_bounds(-1, 1, variation_mode='negate'),
        },
    }
    return SchemaBridge(raw)


class TestReadDirection:
    """Diagnostica di grain.read_direction: dominio, step imposto, gruppo."""

    @staticmethod
    def _grain(body: str) -> str:
        return _stream_yaml("    grain:\n" + body)

    def _errors(self, provider, yaml):
        return [d for d in provider.get_diagnostics(yaml)
                if d.severity == DiagnosticSeverity.Error]

    def _rd_errors(self, provider, yaml):
        return [d for d in self._errors(provider, yaml)
                if 'read_direction' in d.message]

    # --- dominio a due valori --------------------------------------------

    @pytest.mark.parametrize('valore', ['1', '-1', '1.0', '-1.0'])
    def test_i_due_versi_non_sono_segnalati(self, rd_bridge, valore):
        provider = DiagnosticProvider(rd_bridge)
        yaml = self._grain(f"      read_direction: {valore}\n")
        assert self._errors(provider, yaml) == []

    def test_zero_segnalato_pur_essendo_nei_bounds(self, rd_bridge):
        """Il caso che il solo check sui bounds [-1, 1] lascerebbe passare."""
        provider = DiagnosticProvider(rd_bridge)
        yaml = self._grain("      read_direction: 0\n")
        errs = self._rd_errors(provider, yaml)
        assert len(errs) == 1
        assert '-1' in errs[0].message and '+1' in errs[0].message

    def test_valore_intermedio_segnalato(self, rd_bridge):
        provider = DiagnosticProvider(rd_bridge)
        yaml = self._grain("      read_direction: 0.5\n")
        assert len(self._rd_errors(provider, yaml)) == 1

    def test_una_sola_diagnostica_per_valore_fuori_scala(self, rd_bridge):
        """Fuori dai bounds E fuori dal dominio: un messaggio, non due.

        Il check generico direbbe 'fuori range [-1, 1]', che e' vero ma
        insufficiente; il dedicato dice perche' il dominio ha due soli valori.
        Sentirsi dire due cose diverse sullo stesso errore e' rumore.
        """
        provider = DiagnosticProvider(rd_bridge)
        yaml = self._grain("      read_direction: 5\n")
        errs = self._rd_errors(provider, yaml)
        assert len(errs) == 1
        assert 'fuori range' not in errs[0].message

    def test_chiave_vuota_segnalata(self, rd_bridge):
        """A differenza di grain.reverse, la chiave vuota qui e' un errore."""
        provider = DiagnosticProvider(rd_bridge)
        yaml = self._grain("      read_direction:\n")
        assert len(self._rd_errors(provider, yaml)) == 1

    def test_reverse_vuota_resta_valida(self, rd_bridge):
        """Nessuna regressione sulla chiave storica: la sua sintassi e' quella."""
        provider = DiagnosticProvider(rd_bridge)
        yaml = self._grain("      reverse:\n")
        assert self._errors(provider, yaml) == []

    # --- step imposto -----------------------------------------------------

    def test_envelope_di_versi_non_segnalato(self, rd_bridge):
        provider = DiagnosticProvider(rd_bridge)
        yaml = self._grain("      read_direction: [[0, 1], [12, -1]]\n")
        assert self._errors(provider, yaml) == []

    def test_interp_diverso_da_step_segnalato(self, rd_bridge):
        provider = DiagnosticProvider(rd_bridge)
        yaml = self._grain(
            "      read_direction: {type: linear, points: [[0, 1], [12, -1]]}\n"
        )
        errs = self._rd_errors(provider, yaml)
        assert len(errs) == 1
        assert 'step' in errs[0].message

    def test_step_esplicito_e_ridondanza_valida(self, rd_bridge):
        provider = DiagnosticProvider(rd_bridge)
        yaml = self._grain(
            "      read_direction: {type: step, points: [[0, 1], [12, -1]]}\n"
        )
        assert self._errors(provider, yaml) == []

    def test_envelope_block_style_letto(self, rd_bridge):
        """Il valore va riletto come YAML: le forme envelope sono cinque."""
        provider = DiagnosticProvider(rd_bridge)
        yaml = self._grain(
            "      read_direction:\n"
            "        - [0, 1]\n"
            "        - [12, 0.3]\n"
        )
        errs = self._rd_errors(provider, yaml)
        assert len(errs) == 1
        assert '0.3' in errs[0].message

    def test_envelope_block_style_valido_non_segnalato(self, rd_bridge):
        provider = DiagnosticProvider(rd_bridge)
        yaml = self._grain(
            "      read_direction:\n"
            "        - [0, 1]\n"
            "        - [12, -1]\n"
        )
        assert self._errors(provider, yaml) == []

    def test_ancoraggio_alla_riga_della_chiave(self, rd_bridge):
        provider = DiagnosticProvider(rd_bridge)
        yaml = self._grain("      read_direction: 0\n")
        errs = self._rd_errors(provider, yaml)
        # streams(0) - stream_id(1) onset(2) duration(3) sample(4) grain(5)
        assert errs[0].range.start.line == 6

    # --- guard sulle macro-forme -----------------------------------------

    def test_distribuzione_temporale_ignota_segnalata(self, rd_bridge):
        provider = DiagnosticProvider(rd_bridge)
        yaml = self._grain(
            "      read_direction: [[[0, 1], [50, -1]], 2.0, 2, 'step', 'bogus']\n"
        )
        assert len(self._rd_errors(provider, yaml)) == 1

    def test_percentuale_del_pattern_fuori_scala_segnalata(self, rd_bridge):
        provider = DiagnosticProvider(rd_bridge)
        yaml = self._grain(
            "      read_direction: [[[0, 1], [150, -1]], 2.0, 2]\n"
        )
        assert len(self._rd_errors(provider, yaml)) == 1

    def test_ciclo_valido_non_segnalato(self, rd_bridge):
        provider = DiagnosticProvider(rd_bridge)
        yaml = self._grain(
            "      read_direction: [[[0, 1], [50, -1]], 2.0, 2]\n"
        )
        assert self._errors(provider, yaml) == []

    # --- gruppo esclusivo 'grain_direction' -------------------------------

    def test_reverse_e_read_direction_insieme_sono_errore(self, rd_bridge):
        """Errore, non warning: il motore rifiuta, non sceglie per priorita'."""
        provider = DiagnosticProvider(rd_bridge)
        yaml = self._grain(
            "      reverse:\n"
            "      read_direction: 1\n"
        )
        errs = self._errors(provider, yaml)
        assert len(errs) == 2
        assert all('grain_direction' in e.message for e in errs)

    def test_nessun_warning_di_priorita_sul_gruppo(self, rd_bridge):
        """Il messaggio generico direbbe 'vince quello a priorita' piu' alta',
        che qui e' falso: il render fallisce."""
        provider = DiagnosticProvider(rd_bridge)
        yaml = self._grain(
            "      reverse:\n"
            "      read_direction: 1\n"
        )
        warns = [d for d in provider.get_diagnostics(yaml)
                 if d.severity == DiagnosticSeverity.Warning
                 and 'grain_direction' in d.message]
        assert warns == []

    def test_le_due_righe_sono_entrambe_segnalate(self, rd_bridge):
        provider = DiagnosticProvider(rd_bridge)
        yaml = self._grain(
            "      reverse:\n"
            "      read_direction: 1\n"
        )
        righe = sorted(e.range.start.line for e in self._errors(provider, yaml))
        assert righe == [6, 7]

    def test_con_entrambe_non_si_segnala_anche_il_valore(self, rd_bridge):
        """Il motore rifiuta la coppia prima di guardare i valori: dire anche
        che 0 non e' un verso sposterebbe l'attenzione sul problema minore."""
        provider = DiagnosticProvider(rd_bridge)
        yaml = self._grain(
            "      reverse:\n"
            "      read_direction: 0\n"
        )
        errs = self._errors(provider, yaml)
        assert len(errs) == 2
        assert all('grain_direction' in e.message for e in errs)

    def test_gruppo_esclusivo_e_per_stream(self, rd_bridge):
        """Stream diversi possono usare chiavi diverse dello stesso gruppo."""
        provider = DiagnosticProvider(rd_bridge)
        yaml = (
            "streams:\n"
            "  - stream_id: s1\n"
            "    onset: 0.0\n"
            "    duration: 10.0\n"
            "    sample: f.wav\n"
            "    grain:\n"
            "      reverse:\n"
            "  - stream_id: s2\n"
            "    onset: 0.0\n"
            "    duration: 10.0\n"
            "    sample: f.wav\n"
            "    grain:\n"
            "      read_direction: -1\n"
        )
        assert self._errors(provider, yaml) == []

    # --- tolleranza -------------------------------------------------------

    def test_documento_a_meta_scrittura_non_segnalato(self, rd_bridge):
        """Frammento non interpretabile come YAML: si tace, non si indovina."""
        provider = DiagnosticProvider(rd_bridge)
        yaml = self._grain("      read_direction: [[0, 1], [12,\n")
        assert self._rd_errors(provider, yaml) == []

    def test_chiave_assente_non_produce_niente(self, rd_bridge):
        provider = DiagnosticProvider(rd_bridge)
        yaml = self._grain("      duration: 0.05\n")
        assert self._errors(provider, yaml) == []
