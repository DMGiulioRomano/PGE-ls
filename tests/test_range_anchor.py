"""
test_range_anchor.py

Supporto LSP per la chiave per-stream `range_anchor` (center | min): la chiave
va suggerita, i suoi valori completati, la chiave documentata in hover, e un
valore fuori dall'enum va segnalato come errore — rispecchiando l'engine, che
solleva InvalidFieldValueError.

Riferimento: DMGiulioRomano/PythonGranularEngine#173, issue PGE-ls #37.

`range_anchor` ha la stessa forma di `distribution_mode` — chiave per-stream con
enum chiuso — ma con una differenza di severità: l'engine rifiuta un valore non
ammesso (errore), quindi anche la diagnostica è un Error, non un semplice
"valore mancante".
"""

import pytest

from lsprotocol.types import DiagnosticSeverity

from granular_ls.schema_bridge import SchemaBridge
from granular_ls.yaml_analyzer import YamlContext
from granular_ls.providers.completion_provider import CompletionProvider
from granular_ls.providers.hover_provider import HoverProvider
from granular_ls.providers.diagnostic_provider import DiagnosticProvider


def _bridge():
    """Bridge minimale: usa i fallback statici per stream keys e range anchors."""
    return SchemaBridge({'specs': [], 'bounds': {}})


@pytest.fixture
def bridge():
    """Bridge con le coppie base/_range che l'ancora governa.

    I bound sono quelli veri del motore: `volume` arriva a 12 e il suo range a
    24, ed è proprio quella sproporzione a rendere possibile una coppia valida
    da centrata e fuori tetto da ancorata.
    """
    def spec(name, yaml_path):
        return {'name': name, 'yaml_path': yaml_path, 'default': 0.0,
                'is_smart': True, 'exclusive_group': None,
                'group_priority': 0, 'range_path': None,
                'deviation_probability_key': None, 'is_internal': False}

    def bounds(min_val, max_val):
        return {'min_val': min_val, 'max_val': max_val, 'min_range': 0.0,
                'max_range': 0.0, 'default_jitter': 0.0,
                'variation_mode': 'additive'}

    return SchemaBridge({
        'specs': [
            spec('volume', 'volume'), spec('volume_range', 'volume_range'),
            spec('pan', 'pan'), spec('pan_range', 'pan_range'),
            spec('grain_duration', 'grain.duration'),
            spec('grain_duration_range', 'grain.duration_range'),
        ],
        'bounds': {
            'volume': bounds(-120.0, 12.0),
            'volume_range': bounds(0.0, 24.0),
            'pan': bounds(-3600.0, 3600.0),
            'pan_range': bounds(0.0, 360.0),
            'grain_duration': bounds(0.001, 10.0),
            'grain_duration_range': bounds(0.0, 1.0),
        },
    })


def _bridge_con_default_grain_duration():
    """Come la fixture `bridge`, ma con il default vero di `grain_duration`.

    La fixture tiene tutti i default a 0.0, che sulla base assente rende il
    conto insensibile all'unita'. Qui il default e' quello del motore — 0.05
    **secondi**, qualunque `duration_unit` sia dichiarata — perche' e' la
    condizione in cui la conversione della base si vede.
    """
    def spec(name, yaml_path, default=0.0):
        return {'name': name, 'yaml_path': yaml_path, 'default': default,
                'is_smart': True, 'exclusive_group': None,
                'group_priority': 0, 'range_path': None,
                'deviation_probability_key': None, 'is_internal': False}

    def bounds(min_val, max_val):
        return {'min_val': min_val, 'max_val': max_val, 'min_range': 0.0,
                'max_range': 0.0, 'default_jitter': 0.0,
                'variation_mode': 'additive'}

    return SchemaBridge({
        'specs': [
            spec('grain_duration', 'grain.duration', default=0.05),
            spec('grain_duration_range', 'grain.duration_range'),
        ],
        'bounds': {
            'grain_duration': bounds(0.001, 10.0),
            'grain_duration_range': bounds(0.0, 1.0),
        },
    })


def make_context(context_type='key', current_text='',
                 parent_path=None, indent_level=2,
                 in_stream_element=True, current_key='',
                 leading_spaces=None, cursor_line=0):
    return YamlContext(
        context_type=context_type,
        current_text=current_text,
        parent_path=parent_path or [],
        indent_level=indent_level,
        in_stream_element=in_stream_element,
        current_key=current_key,
        leading_spaces=leading_spaces if leading_spaces is not None else indent_level * 2,
        cursor_line=cursor_line,
    )


# =============================================================================
# 1. SchemaBridge — enum dei valori
# =============================================================================

class TestBridgeRangeAnchors:

    def test_static_fallback_is_center_min(self):
        assert _bridge().get_range_anchors() == ['center', 'min']

    def test_range_anchor_is_a_stream_context_key(self):
        assert 'range_anchor' in _bridge().get_stream_context_keys()

    def test_reads_from_raw_data_when_present(self):
        bridge = SchemaBridge({'specs': [], 'bounds': {},
                               'range_anchors': ['center', 'min', 'max']})
        assert bridge.get_range_anchors() == ['center', 'min', 'max']


# =============================================================================
# 2. Completion — chiave e valori
# =============================================================================

class TestRangeAnchorCompletion:

    def test_key_suggested_at_stream_level(self):
        provider = CompletionProvider(_bridge())
        ctx = make_context(context_type='key', current_text='',
                           parent_path=[], indent_level=2, leading_spaces=4)
        labels = [i.label for i in provider.get_completions(
            ctx, document_text="streams:\n  - stream_id: s\n    ")]
        assert 'range_anchor' in labels

    def test_value_completions_center_and_min(self):
        provider = CompletionProvider(_bridge())
        ctx = make_context(context_type='value', current_text='',
                           current_key='range_anchor', parent_path=[],
                           indent_level=2, leading_spaces=4)
        labels = [i.label for i in provider.get_completions(
            ctx, document_text="streams:\n  - range_anchor: ")]
        assert 'center' in labels
        assert 'min' in labels

    def test_value_completion_prefix_filter(self):
        provider = CompletionProvider(_bridge())
        ctx = make_context(context_type='value', current_text='mi',
                           current_key='range_anchor', parent_path=[],
                           indent_level=2, leading_spaces=4)
        labels = [i.label for i in provider.get_completions(
            ctx, document_text="streams:\n  - range_anchor: mi")]
        assert labels == ['min']

    def test_value_completions_quote_the_value(self):
        provider = CompletionProvider(_bridge())
        ctx = make_context(context_type='value', current_text='',
                           current_key='range_anchor', parent_path=[],
                           indent_level=2, leading_spaces=4)
        items = provider.get_completions(
            ctx, document_text="streams:\n  - range_anchor: ")
        by_label = {i.label: i for i in items}
        assert by_label['min'].insert_text == '"min"'


# =============================================================================
# 3. Hover
# =============================================================================

class TestRangeAnchorHover:

    def test_hover_present_for_key(self):
        provider = HoverProvider(_bridge())
        ctx = make_context(context_type='key', current_text='range_anchor',
                           parent_path=[])
        hover = provider.get_hover(ctx)
        assert hover is not None

    def test_hover_names_both_values(self):
        provider = HoverProvider(_bridge())
        ctx = make_context(context_type='key', current_text='range_anchor',
                           parent_path=[])
        text = provider.get_hover(ctx).contents.value
        assert 'center' in text
        assert 'min' in text

    def test_hover_warns_about_gaussian(self):
        """La coesistenza con gaussian è l'unica sorpresa: l'hover deve dirlo."""
        provider = HoverProvider(_bridge())
        ctx = make_context(context_type='key', current_text='range_anchor',
                           parent_path=[])
        text = provider.get_hover(ctx).contents.value.lower()
        assert 'gaussian' in text or 'larghezza' in text


# =============================================================================
# 4. Diagnostica — valore fuori enum è ERRORE (rispecchia l'engine)
# =============================================================================

class TestRangeAnchorDiagnostic:

    def _diags(self, doc):
        return DiagnosticProvider(_bridge()).get_diagnostics(doc)

    def test_invalid_value_is_error(self):
        doc = ("streams:\n"
               "  - stream_id: s\n"
               "    onset: 0\n"
               "    duration: 5\n"
               "    sample: a.wav\n"
               "    range_anchor: banana\n")
        diags = [d for d in self._diags(doc) if 'range_anchor' in d.message]
        assert len(diags) == 1
        assert diags[0].severity == DiagnosticSeverity.Error

    def test_center_is_valid(self):
        doc = ("streams:\n"
               "  - stream_id: s\n"
               "    onset: 0\n"
               "    duration: 5\n"
               "    sample: a.wav\n"
               "    range_anchor: center\n")
        assert [d for d in self._diags(doc) if 'range_anchor' in d.message] == []

    def test_min_is_valid(self):
        doc = ("streams:\n"
               "  - stream_id: s\n"
               "    onset: 0\n"
               "    duration: 5\n"
               "    sample: a.wav\n"
               "    range_anchor: min\n")
        assert [d for d in self._diags(doc) if 'range_anchor' in d.message] == []

    def test_quoted_value_is_accepted(self):
        doc = ('streams:\n'
               '  - stream_id: s\n'
               '    onset: 0\n'
               '    duration: 5\n'
               '    sample: a.wav\n'
               '    range_anchor: "min"\n')
        assert [d for d in self._diags(doc) if 'range_anchor' in d.message] == []

    def test_error_message_lists_allowed_values(self):
        doc = ("streams:\n"
               "  - stream_id: s\n"
               "    onset: 0\n"
               "    duration: 5\n"
               "    sample: a.wav\n"
               "    range_anchor: middle\n")
        diags = [d for d in self._diags(doc) if 'range_anchor' in d.message]
        assert 'center' in diags[0].message
        assert 'min' in diags[0].message


# =============================================================================
# Il tetto della banda sotto `range_anchor: min` (issue #37)
# =============================================================================


class TestBandCeilingUnderAnchorMin:
    """
    In modalita' `min` la banda arriva a `base + range`, quindi una coppia che
    passa la validazione da centrata puo' sforare il tetto: `volume: -6` con
    `volume_range: 24` sta dentro i bounds da centrata (`[-18, 6]`) e li sfora
    da ancorata (`[-6, 18]` contro `max_val` 12).

    Il motore lo tratta come **errore al parse**, non come warning: la
    modalita' `min` promette una banda esatta, e prometterla per poi tagliarla
    col clamp e' peggio che rifiutarla subito. La diagnostica ne rispecchia la
    severita' e i confini — compreso quello che il motore NON controlla, cioe'
    base e range entrambi envelope, dove il massimo della somma non e' la
    somma dei massimi.
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

    def _ceiling_errors(self, provider, yaml):
        return [d for d in provider.get_diagnostics(yaml)
                if d.severity == DiagnosticSeverity.Error
                and 'range_anchor' in d.message and 'banda' in d.message.lower()]

    # --- il caso dell'issue ------------------------------------------------

    def test_volume_meno_sei_con_range_ventiquattro(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = self._stream(
            "    range_anchor: min\n"
            "    volume: -6\n"
            "    volume_range: 24\n"
        )
        assert len(self._ceiling_errors(provider, yaml)) == 1

    def test_la_stessa_coppia_da_centrata_e_valida(self, bridge):
        """Sotto `center` la banda arriva a base + range/2: nessun errore."""
        provider = DiagnosticProvider(bridge)
        yaml = self._stream(
            "    range_anchor: center\n"
            "    volume: -6\n"
            "    volume_range: 24\n"
        )
        assert self._ceiling_errors(provider, yaml) == []

    def test_ancora_assente_e_come_center(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = self._stream(
            "    volume: -6\n"
            "    volume_range: 24\n"
        )
        assert self._ceiling_errors(provider, yaml) == []

    # --- confini del controllo --------------------------------------------

    def test_banda_esattamente_al_tetto_e_valida(self, bridge):
        """volume: 0 + range 12 = 12 = max_val: il motore accetta."""
        provider = DiagnosticProvider(bridge)
        yaml = self._stream(
            "    range_anchor: min\n"
            "    volume: 0\n"
            "    volume_range: 12\n"
        )
        assert self._ceiling_errors(provider, yaml) == []

    def test_senza_range_niente_controllo(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = self._stream(
            "    range_anchor: min\n"
            "    volume: 6\n"
        )
        assert self._ceiling_errors(provider, yaml) == []

    def test_pan_ha_la_sua_coppia(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = self._stream(
            "    range_anchor: min\n"
            "    pan: 3500\n"
            "    pan_range: 200\n"
        )
        assert len(self._ceiling_errors(provider, yaml)) == 1

    def test_grain_duration_ha_la_sua_coppia(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = self._stream(
            "    range_anchor: min\n"
            "    grain:\n"
            "      duration: 9.5\n"
            "      duration_range: 0.8\n"
        )
        assert len(self._ceiling_errors(provider, yaml)) == 1

    # --- envelope ----------------------------------------------------------

    def test_base_envelope_e_range_scalare_usa_il_picco(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = self._stream(
            "    range_anchor: min\n"
            "    volume: [[0, -60], [10, 6]]\n"
            "    volume_range: 12\n"
        )
        assert len(self._ceiling_errors(provider, yaml)) == 1

    def test_base_scalare_e_range_envelope_usa_il_picco(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = self._stream(
            "    range_anchor: min\n"
            "    volume: 6\n"
            "    volume_range: [[0, 1], [10, 20]]\n"
        )
        assert len(self._ceiling_errors(provider, yaml)) == 1

    def test_entrambi_envelope_non_si_controlla(self, bridge):
        """I due picchi possono cadere in istanti diversi: il motore non
        controlla, e un falso positivo bloccherebbe un render valido."""
        provider = DiagnosticProvider(bridge)
        yaml = self._stream(
            "    range_anchor: min\n"
            "    volume: [[0, -60], [10, 6]]\n"
            "    volume_range: [[0, 1], [10, 20]]\n"
        )
        assert self._ceiling_errors(provider, yaml) == []

    # --- unita' di grain.duration -----------------------------------------

    def test_duration_in_millisecondi_convertita_prima_del_confronto(self, bridge):
        """9500 ms + 800 ms = 10.3 s: sfora, ma solo se si converte."""
        provider = DiagnosticProvider(bridge)
        yaml = self._stream(
            "    range_anchor: min\n"
            "    grain:\n"
            "      duration_unit: milliseconds\n"
            "      duration: 9500\n"
            "      duration_range: 800\n"
        )
        assert len(self._ceiling_errors(provider, yaml)) == 1

    def test_duration_in_millisecondi_dentro_il_tetto_non_segnalata(self, bridge):
        """50 ms + 4.5 ms non sfora niente: senza conversione sarebbero 54.5 s."""
        provider = DiagnosticProvider(bridge)
        yaml = self._stream(
            "    range_anchor: min\n"
            "    grain:\n"
            "      duration_unit: milliseconds\n"
            "      duration: 50\n"
            "      duration_range: 4.5\n"
        )
        assert self._ceiling_errors(provider, yaml) == []

    # --- messaggio e ancoraggio -------------------------------------------

    def test_messaggio_nomina_la_somma_e_il_tetto(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = self._stream(
            "    range_anchor: min\n"
            "    volume: -6\n"
            "    volume_range: 24\n"
        )
        msg = self._ceiling_errors(provider, yaml)[0].message
        assert '18' in msg and '12' in msg

    def test_messaggio_promette_la_centrata_solo_quando_e_vero(self, bridge):
        """`volume: -6` con `volume_range: 24` sta dentro da centrata
        (-6 + 12 = 6 <= 12): li' cambiare ancora e' davvero la via d'uscita."""
        provider = DiagnosticProvider(bridge)
        yaml = self._stream(
            "    range_anchor: min\n"
            "    volume: -6\n"
            "    volume_range: 24\n"
        )
        msg = self._ceiling_errors(provider, yaml)[0].message
        assert 'centrata la stessa coppia starebbe dentro' in msg

    def test_messaggio_non_promette_la_centrata_quando_sfora_anche_lei(self, bridge):
        """`volume: 11` con `volume_range: 24`: da centrata la banda arriva a
        23, sopra il tetto 12. Li' interviene il safety clamp invece del
        rifiuto al parse — cambiare ancora non risolve, e prometterlo manda
        l'utente a fare la modifica sbagliata."""
        provider = DiagnosticProvider(bridge)
        yaml = self._stream(
            "    range_anchor: min\n"
            "    volume: 11\n"
            "    volume_range: 24\n"
        )
        msg = self._ceiling_errors(provider, yaml)[0].message
        assert 'starebbe dentro' not in msg
        assert 'clamp' in msg

    # --- il messaggio parla l'unita' che l'utente ha scritto ---------------

    def test_messaggio_in_millisecondi_riporta_i_numeri_scritti(self, bridge):
        """9500 + 800 ms: il confronto si fa in secondi come il motore, ma il
        messaggio non puo' rispondere con numeri che l'utente non ha mai
        visto."""
        provider = DiagnosticProvider(bridge)
        yaml = self._stream(
            "    range_anchor: min\n"
            "    grain:\n"
            "      duration_unit: milliseconds\n"
            "      duration: 9500\n"
            "      duration_range: 800\n"
        )
        msg = self._ceiling_errors(provider, yaml)[0].message
        assert '10300' in msg and '10000' in msg
        assert 'millisecondi' in msg

    def test_messaggio_in_campioni_riporta_i_numeri_scritti(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = self._stream(
            "    range_anchor: min\n"
            "    grain:\n"
            "      duration_unit: samples\n"
            "      duration: 470000\n"
            "      duration_range: 20000\n"
        )
        msg = self._ceiling_errors(provider, yaml)[0].message
        assert '490000' in msg and '480000' in msg
        assert 'campioni' in msg

    def test_messaggio_in_secondi_resta_senza_etichetta(self, bridge):
        """Senza `duration_unit` i numeri sono gia' quelli scritti."""
        provider = DiagnosticProvider(bridge)
        yaml = self._stream(
            "    range_anchor: min\n"
            "    grain:\n"
            "      duration: 9.5\n"
            "      duration_range: 0.8\n"
        )
        msg = self._ceiling_errors(provider, yaml)[0].message
        assert 'millisecondi' not in msg and 'campioni' not in msg
        assert '10.3' in msg

    def test_base_dal_default_e_gia_in_secondi(self):
        """Chiave assente: vale il default della spec, che il motore tiene in
        secondi e non converte. Portarlo nell'unita' dichiarata (0.05 s = 50
        ms) e non moltiplicarlo per il fattore come se fosse gia' in
        millisecondi: li' varrebbe 0.05 ms, tre ordini di grandezza sotto, e
        la banda 50 + 9990 = 10040 ms non risulterebbe sopra il tetto."""
        provider = DiagnosticProvider(_bridge_con_default_grain_duration())
        yaml = self._stream(
            "    range_anchor: min\n"
            "    grain:\n"
            "      duration_unit: milliseconds\n"
            "      duration_range: 9990\n"
        )
        errors = self._ceiling_errors(provider, yaml)
        assert len(errors) == 1
        assert '10040' in errors[0].message

    def test_errore_ancorato_alla_riga_del_range(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = self._stream(
            "    range_anchor: min\n"
            "    volume: -6\n"
            "    volume_range: 24\n"
        )
        assert self._ceiling_errors(provider, yaml)[0].range.start.line == 7

    def test_stream_indipendenti(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = (
            "streams:\n"
            "  - stream_id: s1\n"
            "    duration: 10.0\n"
            "    sample: f.wav\n"
            "    range_anchor: min\n"
            "    volume: -6\n"
            "    volume_range: 24\n"
            "  - stream_id: s2\n"
            "    duration: 10.0\n"
            "    sample: f.wav\n"
            "    volume: -6\n"
            "    volume_range: 24\n"
        )
        assert len(self._ceiling_errors(provider, yaml)) == 1

    # --- base assente: vale il default ------------------------------------

    def test_range_da_solo_usa_il_default_della_base(self, bridge):
        """`volume_range: 24` senza `volume`: l'orchestrator passa il default
        0.0 al parser, e 0 + 24 sfora il tetto 12."""
        provider = DiagnosticProvider(bridge)
        yaml = self._stream(
            "    range_anchor: min\n"
            "    volume_range: 24\n"
        )
        assert len(self._ceiling_errors(provider, yaml)) == 1

    def test_range_da_solo_dentro_il_tetto_non_segnalato(self, bridge):
        provider = DiagnosticProvider(bridge)
        yaml = self._stream(
            "    range_anchor: min\n"
            "    volume_range: 12\n"
        )
        assert self._ceiling_errors(provider, yaml) == []
