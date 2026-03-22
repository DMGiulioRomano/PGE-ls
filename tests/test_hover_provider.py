# tests/test_hover_provider.py
"""
Suite TDD - FASE RED per hover_provider.py

Modulo sotto test (non ancora esistente):
    granular_ls/providers/hover_provider.py

Responsabilita' del modulo:
    Dato un YamlContext e il nome della chiave sotto il cursore,
    produrre un oggetto Hover LSP con documentazione Markdown,
    oppure None se il cursore non e' su una chiave conosciuta.

Struttura Hover LSP attesa:
    contents : MarkupContent(kind=Markdown, value=...)
    range    : opzionale, non richiesto per ora

Logica:
    - context_type == 'unknown'          -> None
    - context_type == 'value'            -> None (cursore sul valore)
    - context_type == 'key', chiave nota -> Hover con documentazione
    - context_type == 'key', chiave ignota -> None
    - chiave nota con exclusive_group    -> documentazione menziona il gruppo
    - chiave senza bounds                -> documentazione generica senza crash

Il nome della chiave viene risolto cosi':
    1. current_text della riga del cursore, se non vuoto
    2. Se current_text e' vuoto (cursore su spazio dopo chiave completa),
       usiamo il testo della riga passato come argomento separato

Organizzazione:
    1.  HoverProvider - costruzione
    2.  get_hover - context_type non-key ritorna None
    3.  get_hover - chiave sconosciuta ritorna None
    4.  get_hover - chiave nota ritorna Hover
    5.  get_hover - struttura Hover (MarkupContent, Markdown)
    6.  get_hover - contenuto documentazione (min/max, variation_mode)
    7.  get_hover - exclusive_group nella documentazione
    8.  get_hover - parametro senza bounds non solleva
    9.  get_hover - chiave in blocco annidato (grain.duration)
    10. Edge cases
"""

import pytest
from lsprotocol.types import Hover, MarkupKind

from granular_ls.schema_bridge import SchemaBridge
from granular_ls.yaml_analyzer import YamlContext
from granular_ls.providers.hover_provider import HoverProvider


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
            # volume con dephase_key per testare hover nel blocco dephase
            {
                'name': 'volume', 'yaml_path': 'volume', 'default': -6.0,
                'is_smart': True, 'exclusive_group': None, 'group_priority': 99,
                'range_path': None, 'dephase_key': 'volume',
            },
            make_raw_spec('grain_duration', 'grain.duration', default=0.05),
            # pitch con dephase_key
            {
                'name': 'pitch_semitones', 'yaml_path': 'pitch.semitones',
                'default': None, 'is_smart': True,
                'exclusive_group': 'pitch_mode', 'group_priority': 1,
                'range_path': None, 'dephase_key': 'pitch',
            },
            make_raw_spec('orphan', 'orphan'),  # nessun bounds
            make_raw_spec('effective_density', '_internal_calc_',
                          default=0.0, is_smart=False),
        ],
        'bounds': {
            'density':        make_raw_bounds(0.01, 4000.0),
            'fill_factor':    make_raw_bounds(0.001, 50.0),
            'volume':         make_raw_bounds(-60.0, 0.0),
            'grain_duration': make_raw_bounds(0.001, 10.0),
            'pitch_semitones':make_raw_bounds(-48.0, 48.0,
                                              variation_mode='quantized'),
        },
    }
    return SchemaBridge(raw)


def make_context(context_type='key', current_text='',
                 parent_path=None, indent_level=0):
    return YamlContext(
        context_type=context_type,
        current_text=current_text,
        parent_path=parent_path or [],
        indent_level=indent_level,
    )


# =============================================================================
# 1. HoverProvider - costruzione
# =============================================================================

class TestHoverProviderConstruction:

    def test_costruzione_con_bridge(self, bridge):
        provider = HoverProvider(bridge)
        assert provider is not None

    def test_richiede_bridge(self):
        with pytest.raises(TypeError):
            HoverProvider()


# =============================================================================
# 2. get_hover - context_type non-key ritorna None
# =============================================================================

class TestGetHoverContextTypeNonKey:

    def test_context_value_ritorna_none(self, bridge):
        provider = HoverProvider(bridge)
        ctx = make_context(context_type='value', current_text='100')
        assert provider.get_hover(ctx) is None

    def test_context_unknown_ritorna_none(self, bridge):
        provider = HoverProvider(bridge)
        ctx = make_context(context_type='unknown')
        assert provider.get_hover(ctx) is None


# =============================================================================
# 3. get_hover - chiave sconosciuta ritorna None
# =============================================================================

class TestGetHoverChiaveSconosciuta:

    def test_chiave_inesistente_ritorna_none(self, bridge):
        provider = HoverProvider(bridge)
        ctx = make_context(context_type='key', current_text='nonexistent_key')
        assert provider.get_hover(ctx) is None

    def test_chiave_parziale_non_corrisponde(self, bridge):
        """'den' non e' un parametro completo, non ha hover."""
        provider = HoverProvider(bridge)
        ctx = make_context(context_type='key', current_text='den')
        assert provider.get_hover(ctx) is None

    def test_chiave_vuota_ritorna_none(self, bridge):
        provider = HoverProvider(bridge)
        ctx = make_context(context_type='key', current_text='')
        assert provider.get_hover(ctx) is None

    def test_parametro_interno_ritorna_none(self, bridge):
        """I parametri interni non hanno hover: non sono scrivibili dall'utente."""
        provider = HoverProvider(bridge)
        ctx = make_context(context_type='key',
                           current_text='_internal_calc_')
        assert provider.get_hover(ctx) is None


# =============================================================================
# 4. get_hover - chiave nota ritorna Hover
# =============================================================================

class TestGetHoverChiavaNota:

    def test_density_ritorna_hover(self, bridge):
        provider = HoverProvider(bridge)
        ctx = make_context(context_type='key', current_text='density')
        result = provider.get_hover(ctx)
        assert result is not None

    def test_volume_ritorna_hover(self, bridge):
        provider = HoverProvider(bridge)
        ctx = make_context(context_type='key', current_text='volume')
        result = provider.get_hover(ctx)
        assert result is not None

    def test_ritorna_oggetto_hover_lsp(self, bridge):
        provider = HoverProvider(bridge)
        ctx = make_context(context_type='key', current_text='density')
        result = provider.get_hover(ctx)
        assert isinstance(result, Hover)


# =============================================================================
# 5. get_hover - struttura Hover
# =============================================================================

class TestGetHoverStruttura:

    def test_contents_e_markup_content(self, bridge):
        provider = HoverProvider(bridge)
        ctx = make_context(context_type='key', current_text='density')
        result = provider.get_hover(ctx)
        assert result.contents is not None
        assert result.contents.kind == MarkupKind.Markdown

    def test_contents_value_e_stringa_non_vuota(self, bridge):
        provider = HoverProvider(bridge)
        ctx = make_context(context_type='key', current_text='density')
        result = provider.get_hover(ctx)
        assert isinstance(result.contents.value, str)
        assert len(result.contents.value) > 0


# =============================================================================
# 6. get_hover - contenuto documentazione
# =============================================================================

class TestGetHoverContenuto:

    def test_documentazione_contiene_min_val(self, bridge):
        provider = HoverProvider(bridge)
        ctx = make_context(context_type='key', current_text='density')
        result = provider.get_hover(ctx)
        assert '0.01' in result.contents.value

    def test_documentazione_contiene_max_val(self, bridge):
        provider = HoverProvider(bridge)
        ctx = make_context(context_type='key', current_text='density')
        result = provider.get_hover(ctx)
        assert '4000' in result.contents.value

    def test_documentazione_contiene_variation_mode(self, bridge):
        provider = HoverProvider(bridge)
        ctx = make_context(context_type='key', current_text='density')
        result = provider.get_hover(ctx)
        assert 'additive' in result.contents.value

    def test_documentazione_volume_contiene_range_corretto(self, bridge):
        provider = HoverProvider(bridge)
        ctx = make_context(context_type='key', current_text='volume')
        result = provider.get_hover(ctx)
        assert '-60' in result.contents.value
        assert '0.0' in result.contents.value

    def test_documentazione_semitones_variation_mode_quantized(self, bridge):
        provider = HoverProvider(bridge)
        ctx = make_context(context_type='key',
                           current_text='pitch.semitones',
                           parent_path=['pitch'], indent_level=1)
        result = provider.get_hover(ctx)
        assert result is not None
        assert 'quantized' in result.contents.value

    def test_documentazione_contiene_nome_parametro(self, bridge):
        """Il nome del parametro deve apparire nella documentazione."""
        provider = HoverProvider(bridge)
        ctx = make_context(context_type='key', current_text='volume')
        result = provider.get_hover(ctx)
        assert 'volume' in result.contents.value


# =============================================================================
# 7. get_hover - exclusive_group nella documentazione
# =============================================================================

class TestGetHoverExclusiveGroup:

    def test_density_menziona_exclusive_group(self, bridge):
        provider = HoverProvider(bridge)
        ctx = make_context(context_type='key', current_text='density')
        result = provider.get_hover(ctx)
        assert 'density_mode' in result.contents.value

    def test_fill_factor_menziona_exclusive_group(self, bridge):
        provider = HoverProvider(bridge)
        ctx = make_context(context_type='key', current_text='fill_factor')
        result = provider.get_hover(ctx)
        assert 'density_mode' in result.contents.value

    def test_volume_non_menziona_exclusive_group(self, bridge):
        """volume non ha exclusive_group, non deve apparire nella doc."""
        provider = HoverProvider(bridge)
        ctx = make_context(context_type='key', current_text='volume')
        result = provider.get_hover(ctx)
        assert 'exclusive' not in result.contents.value.lower()

    def test_pitch_semitones_menziona_pitch_mode(self, bridge):
        provider = HoverProvider(bridge)
        ctx = make_context(context_type='key',
                           current_text='pitch.semitones',
                           parent_path=['pitch'])
        result = provider.get_hover(ctx)
        assert result is not None
        assert 'pitch_mode' in result.contents.value


# =============================================================================
# 8. get_hover - parametro senza bounds
# =============================================================================

class TestGetHoverSenzaBounds:

    def test_parametro_senza_bounds_ritorna_hover(self, bridge):
        """'orphan' non ha bounds ma deve ritornare Hover senza crash."""
        provider = HoverProvider(bridge)
        ctx = make_context(context_type='key', current_text='orphan')
        result = provider.get_hover(ctx)
        assert result is not None
        assert isinstance(result, Hover)

    def test_parametro_senza_bounds_contents_non_vuoto(self, bridge):
        provider = HoverProvider(bridge)
        ctx = make_context(context_type='key', current_text='orphan')
        result = provider.get_hover(ctx)
        assert len(result.contents.value) > 0


# =============================================================================
# 9. get_hover - chiave in blocco annidato
# =============================================================================

class TestGetHoverBloccoAnnidato:
    """
    Per i parametri annidati (grain.duration), il current_text del cursore
    puo' essere sia 'duration' (parte locale) che 'grain.duration' (path completo).
    Il provider deve riconoscere entrambi.
    """

    def test_chiave_locale_dentro_blocco_grain(self, bridge):
        """'duration' dentro grain: deve trovare grain.duration."""
        provider = HoverProvider(bridge)
        ctx = make_context(context_type='key', current_text='duration',
                           parent_path=['grain'], indent_level=1)
        result = provider.get_hover(ctx)
        assert result is not None

    def test_yaml_path_completo_funziona(self, bridge):
        """'grain.duration' come current_text deve funzionare."""
        provider = HoverProvider(bridge)
        ctx = make_context(context_type='key', current_text='grain.duration')
        result = provider.get_hover(ctx)
        assert result is not None

    def test_chiave_locale_errata_ritorna_none(self, bridge):
        """'nonexistent' dentro grain: non corrisponde a nulla."""
        provider = HoverProvider(bridge)
        ctx = make_context(context_type='key', current_text='nonexistent',
                           parent_path=['grain'], indent_level=1)
        result = provider.get_hover(ctx)
        assert result is None

    def test_documentazione_grain_duration_contiene_bounds(self, bridge):
        provider = HoverProvider(bridge)
        ctx = make_context(context_type='key', current_text='duration',
                           parent_path=['grain'], indent_level=1)
        result = provider.get_hover(ctx)
        assert '0.001' in result.contents.value
        assert '10.0' in result.contents.value


# =============================================================================
# 10. Edge cases
# =============================================================================

class TestGetHoverEdgeCases:

    def test_ritorna_sempre_hover_o_none(self, bridge):
        """Invariante: get_hover ritorna solo Hover o None, mai altro."""
        provider = HoverProvider(bridge)
        casi = [
            make_context(context_type='key', current_text='density'),
            make_context(context_type='key', current_text='den'),
            make_context(context_type='value', current_text='100'),
            make_context(context_type='unknown'),
            make_context(context_type='key', current_text=''),
            make_context(context_type='key', current_text='nonexistent'),
        ]
        for ctx in casi:
            result = provider.get_hover(ctx)
            assert result is None or isinstance(result, Hover), (
                f"get_hover({ctx}) ha ritornato {type(result)}"
            )

    def test_bridge_vuoto_ritorna_sempre_none(self):
        b = SchemaBridge({'specs': [], 'bounds': {}})
        provider = HoverProvider(b)
        ctx = make_context(context_type='key', current_text='density')
        assert provider.get_hover(ctx) is None

    def test_chiamate_ripetute_stesso_risultato(self, bridge):
        """Idempotenza: stessa chiamata -> stesso risultato."""
        provider = HoverProvider(bridge)
        ctx = make_context(context_type='key', current_text='density')
        result1 = provider.get_hover(ctx)
        result2 = provider.get_hover(ctx)
        assert result1.contents.value == result2.contents.value


# =============================================================================
# MODIFICA D - Stream context keys e dephase nel HoverProvider
# =============================================================================

class TestGetHoverStreamContextKeys:
    """
    Le stream context keys (stream_id, onset, solo, mute, etc.)
    devono avere hover anche se non sono in GRANULAR_PARAMETERS.
    """

    def test_onset_ritorna_hover(self, bridge):
        provider = HoverProvider(bridge)
        ctx = make_context(context_type='key', current_text='onset')
        result = provider.get_hover(ctx)
        assert result is not None
        assert isinstance(result, Hover)

    def test_stream_id_ritorna_hover(self, bridge):
        provider = HoverProvider(bridge)
        ctx = make_context(context_type='key', current_text='stream_id')
        result = provider.get_hover(ctx)
        assert result is not None

    def test_solo_ritorna_hover_con_documentazione(self, bridge):
        provider = HoverProvider(bridge)
        ctx = make_context(context_type='key', current_text='solo')
        result = provider.get_hover(ctx)
        assert result is not None
        # La documentazione deve spiegare cosa fa solo
        assert 'solo' in result.contents.value.lower()

    def test_mute_ritorna_hover_con_documentazione(self, bridge):
        provider = HoverProvider(bridge)
        ctx = make_context(context_type='key', current_text='mute')
        result = provider.get_hover(ctx)
        assert result is not None
        assert 'mute' in result.contents.value.lower()

    def test_solo_documentazione_menziona_esclusione(self, bridge):
        """La doc di solo deve spiegare che gli altri stream vengono ignorati."""
        provider = HoverProvider(bridge)
        ctx = make_context(context_type='key', current_text='solo')
        result = provider.get_hover(ctx)
        assert result is not None
        # Deve menzionare il concetto di isolamento/esclusione
        text = result.contents.value.lower()
        assert any(word in text for word in ['solo', 'esclusivo', 'ignor', 'render'])

    def test_mute_documentazione_menziona_silenziamento(self, bridge):
        """La doc di mute deve spiegare che lo stream viene silenziato."""
        provider = HoverProvider(bridge)
        ctx = make_context(context_type='key', current_text='mute')
        result = provider.get_hover(ctx)
        assert result is not None
        text = result.contents.value.lower()
        assert any(word in text for word in ['mute', 'silenz', 'disabil', 'render'])

    def test_time_mode_ritorna_hover(self, bridge):
        provider = HoverProvider(bridge)
        ctx = make_context(context_type='key', current_text='time_mode')
        result = provider.get_hover(ctx)
        assert result is not None

    def test_chiave_stream_context_inesistente_ritorna_none(self, bridge):
        provider = HoverProvider(bridge)
        ctx = make_context(context_type='key', current_text='chiave_inesistente_xyz')
        result = provider.get_hover(ctx)
        assert result is None


class TestGetHoverDephaseKeys:
    """
    Le dephase keys (volume, pan, duration, pitch, etc.) dentro
    il blocco dephase: devono avere hover con documentazione utile.
    """

    def test_dephase_volume_ritorna_hover(self, bridge):
        provider = HoverProvider(bridge)
        ctx = make_context(context_type='key', current_text='volume',
                           parent_path=['dephase'], indent_level=1)
        result = provider.get_hover(ctx)
        assert result is not None

    def test_dephase_key_contents_non_vuoto(self, bridge):
        provider = HoverProvider(bridge)
        ctx = make_context(context_type='key', current_text='volume',
                           parent_path=['dephase'], indent_level=1)
        result = provider.get_hover(ctx)
        assert result is not None
        assert len(result.contents.value) > 0
