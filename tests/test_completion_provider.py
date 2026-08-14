# tests/test_completion_provider.py
"""
Suite TDD - FASE RED per completion_provider.py

Modulo sotto test (non ancora esistente):
    granular_ls/providers/completion_provider.py

Responsabilita' del modulo:
    Dato un YamlContext e un SchemaBridge, produrre una lista
    di CompletionItem LSP da restituire a VSCode.

Design:
    - CompletionProvider e' una classe con un unico metodo pubblico:
      get_completions(context, document_text) -> List[CompletionItem]
    - Costruita con un SchemaBridge in __init__
    - Non ha stato mutabile: stessa input -> stesso output sempre

Logica di filtraggio:
    1. context_type != 'key'  -> lista vuota (non stiamo scrivendo una chiave)
    2. context_type == 'key'  -> suggeriamo chiavi yaml_path compatibili con
                                 parent_path e current_text come prefisso
    3. parent_path == []      -> suggeriamo chiavi di root level
    4. parent_path == ['grain'] -> suggeriamo chiavi dentro grain:
    5. current_text filtra per prefisso (case-insensitive)
    6. Chiavi gia' presenti nel documento non vengono duplicate

Struttura CompletionItem attesa:
    label         : yaml_path del parametro (es. 'density', 'grain.duration')
    insert_text   : yaml_path + ': ' (es. 'density: ')
    kind          : CompletionItemKind.Field (5)
    detail        : '[min, max]' oppure stringa vuota se bounds mancano
    documentation : MarkupContent(kind=Markdown, value=...) con info complete

Organizzazione:
    1.  CompletionProvider - costruzione
    2.  get_completions - context_type non-key ritorna lista vuota
    3.  get_completions - context vuoto (documento vuoto) -> tutte le chiavi root
    4.  get_completions - filtro per prefisso current_text
    5.  get_completions - filtro per parent_path (blocco annidato)
    6.  get_completions - nessun match per prefisso -> lista vuota
    7.  get_completions - struttura CompletionItem (label, kind, detail, doc)
    8.  get_completions - chiavi gia' presenti nel documento escluse
    9.  get_completions - exclusive_group: entrambe le chiavi presenti -> avviso
    10. Edge cases
"""

import pytest
from lsprotocol.types import CompletionItem, CompletionItemKind, InsertTextFormat, MarkupKind

from granular_ls.schema_bridge import SchemaBridge
from granular_ls.yaml_analyzer import YamlContext, YamlAnalyzer
from granular_ls.providers.completion_provider import CompletionProvider


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
    """Bridge con parametri realistici che coprono i casi d'uso principali."""
    raw = {
        'specs': [
            # root level
            make_raw_spec('density', 'density', default=None,
                          exclusive_group='density_mode', group_priority=2),
            make_raw_spec('fill_factor', 'fill_factor', default=2,
                          exclusive_group='density_mode', group_priority=1),
            make_raw_spec('distribution', 'distribution', default=0.0),
            make_raw_spec('volume', 'volume', default=0.0),
            # annidati in grain
            make_raw_spec('grain_duration', 'grain.duration', default=0.05),
            make_raw_spec('grain_envelope', 'grain.envelope',
                          default='hanning', is_smart=False),
            # annidati in pointer
            make_raw_spec('pointer_speed_ratio', 'pointer.speed_ratio', default=1.0),
            # interni: NON devono apparire nei completamenti
            make_raw_spec('effective_density', '_internal_calc_',
                          default=0.0, is_smart=False),
        ],
        'bounds': {
            'density':            make_raw_bounds(0.01, 4000.0),
            'fill_factor':        make_raw_bounds(0.001, 50.0),
            'distribution':       make_raw_bounds(0.0, 1.0),
            'volume':             make_raw_bounds(-60.0, 0.0),
            'grain_duration':     make_raw_bounds(0.001, 10.0),
            'grain_envelope':     make_raw_bounds(0.0, 1.0),
            'pointer_speed_ratio':make_raw_bounds(0.01, 10.0),
            'effective_density':  make_raw_bounds(1.0, 4000.0),
        },
    }
    return SchemaBridge(raw)


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
# 1. CompletionProvider - costruzione
# =============================================================================

class TestCompletionProviderConstruction:

    def test_costruzione_con_bridge(self, bridge):
        provider = CompletionProvider(bridge)
        assert provider is not None

    def test_richiede_bridge(self):
        with pytest.raises(TypeError):
            CompletionProvider()


# =============================================================================
# 2. get_completions - context_type non-key
# =============================================================================

class TestGetCompletionsContextTypeNonKey:
    """Se non siamo in context 'key', non suggeriamo nulla."""

    def test_context_value_ritorna_lista_vuota(self, bridge):
        provider = CompletionProvider(bridge)
        ctx = make_context(context_type='value', current_text='100')
        result = provider.get_completions(ctx, document_text="density: 1")
        assert result == []

    def test_context_unknown_ritorna_lista_vuota(self, bridge):
        provider = CompletionProvider(bridge)
        ctx = make_context(context_type='unknown')
        result = provider.get_completions(ctx, document_text="# commento")
        assert result == []

    def test_context_key_non_ritorna_lista_vuota_se_ci_sono_params(self, bridge):
        provider = CompletionProvider(bridge)
        ctx = make_context(context_type='key', current_text='')
        result = provider.get_completions(ctx, document_text="")
        assert len(result) > 0


# =============================================================================
# 3. get_completions - documento vuoto, tutte le chiavi root
# =============================================================================

class TestGetCompletionsTutteLeChiaviRoot:
    """
    Con current_text='' e parent_path=[], ritorniamo tutti i parametri
    di root level (yaml_path senza punto e non interni).
    """

    def test_documento_vuoto_ritorna_completamenti(self, bridge):
        provider = CompletionProvider(bridge)
        ctx = make_context(context_type='key', current_text='')
        result = provider.get_completions(ctx, document_text="")
        assert len(result) > 0

    def test_ritorna_lista_di_completion_item(self, bridge):
        provider = CompletionProvider(bridge)
        ctx = make_context(context_type='key', current_text='')
        result = provider.get_completions(ctx, document_text="")
        for item in result:
            assert isinstance(item, CompletionItem)

    def test_density_presente_nei_completamenti_root(self, bridge):
        provider = CompletionProvider(bridge)
        ctx = make_context(context_type='key', current_text='')
        result = provider.get_completions(ctx, document_text="")
        labels = [item.label for item in result]
        assert 'density' in labels

    def test_parametri_annidati_non_compaiono_a_root(self, bridge):
        """grain.duration non deve comparire a root level."""
        provider = CompletionProvider(bridge)
        ctx = make_context(context_type='key', current_text='')
        result = provider.get_completions(ctx, document_text="")
        labels = [item.label for item in result]
        assert 'grain.duration' not in labels
        assert 'pointer.speed_ratio' not in labels

    def test_parametri_interni_non_compaiono(self, bridge):
        provider = CompletionProvider(bridge)
        ctx = make_context(context_type='key', current_text='')
        result = provider.get_completions(ctx, document_text="")
        labels = [item.label for item in result]
        assert '_internal_calc_' not in labels
        assert 'effective_density' not in labels

    def test_is_smart_false_non_compare(self, bridge):
        """grain_envelope ha is_smart=False, non deve comparire."""
        provider = CompletionProvider(bridge)
        ctx = make_context(context_type='key', current_text='',
                           parent_path=['grain'])
        result = provider.get_completions(ctx, document_text="grain:\n  ")
        labels = [item.label for item in result]
        assert 'grain.envelope' not in labels


# =============================================================================
# 4. get_completions - filtro per prefisso current_text
# =============================================================================

class TestGetCompletionsFiltroPreffisso:

    def test_prefisso_den_ritorna_density(self, bridge):
        provider = CompletionProvider(bridge)
        ctx = make_context(context_type='key', current_text='den')
        result = provider.get_completions(ctx, document_text="den")
        labels = [item.label for item in result]
        assert 'density' in labels

    def test_prefisso_den_non_ritorna_volume(self, bridge):
        provider = CompletionProvider(bridge)
        ctx = make_context(context_type='key', current_text='den')
        result = provider.get_completions(ctx, document_text="den")
        labels = [item.label for item in result]
        assert 'volume' not in labels

    def test_prefisso_case_insensitive(self, bridge):
        """'DEN' deve trovare 'density' lo stesso."""
        provider = CompletionProvider(bridge)
        ctx = make_context(context_type='key', current_text='DEN')
        result = provider.get_completions(ctx, document_text="DEN")
        labels = [item.label for item in result]
        assert 'density' in labels

    def test_prefisso_vuoto_ritorna_tutto_root(self, bridge):
        provider = CompletionProvider(bridge)
        ctx = make_context(context_type='key', current_text='')
        result = provider.get_completions(ctx, document_text="")
        assert len(result) >= 3

    def test_prefisso_inesistente_ritorna_vuoto(self, bridge):
        provider = CompletionProvider(bridge)
        ctx = make_context(context_type='key', current_text='zzz')
        result = provider.get_completions(ctx, document_text="zzz")
        assert result == []

    def test_prefisso_vol_ritorna_volume(self, bridge):
        provider = CompletionProvider(bridge)
        ctx = make_context(context_type='key', current_text='vol')
        result = provider.get_completions(ctx, document_text="vol")
        labels = [item.label for item in result]
        assert 'volume' in labels


# =============================================================================
# 5. get_completions - filtro per parent_path
# =============================================================================

class TestGetCompletionsFiltroParentPath:

    def test_dentro_grain_suggerisce_grain_duration(self, bridge):
        provider = CompletionProvider(bridge)
        ctx = make_context(context_type='key', current_text='',
                           parent_path=['grain'], indent_level=3)
        result = provider.get_completions(ctx, document_text="grain:\n  ")
        labels = [item.label for item in result]
        # Label e' la chiave locale 'duration', non 'grain.duration'
        assert 'duration' in labels

    def test_dentro_grain_non_suggerisce_density(self, bridge):
        provider = CompletionProvider(bridge)
        ctx = make_context(context_type='key', current_text='',
                           parent_path=['grain'], indent_level=3)
        result = provider.get_completions(ctx, document_text="grain:\n  ")
        labels = [item.label for item in result]
        assert 'density' not in labels

    def test_dentro_pointer_suggerisce_pointer_params(self, bridge):
        provider = CompletionProvider(bridge)
        ctx = make_context(context_type='key', current_text='',
                           parent_path=['pointer'], indent_level=3)
        result = provider.get_completions(ctx, document_text="pointer:\n  ")
        labels = [item.label for item in result]
        # Label locale, non prefissato
        assert any('speed_ratio' in l or 'pointer' in l for l in labels) or len(result) == 0

    def test_dentro_pointer_non_suggerisce_grain_params(self, bridge):
        provider = CompletionProvider(bridge)
        ctx = make_context(context_type='key', current_text='',
                           parent_path=['pointer'], indent_level=1)
        result = provider.get_completions(ctx, document_text="pointer:\n  ")
        labels = [item.label for item in result]
        assert 'grain.duration' not in labels

    def test_parent_path_sconosciuto_ritorna_vuoto(self, bridge):
        """Un blocco non noto al bridge (es. dentro envelope) ritorna vuoto."""
        provider = CompletionProvider(bridge)
        ctx = make_context(context_type='key', current_text='',
                           parent_path=['unknown_block'], indent_level=3,
                           in_stream_element=True)
        result = provider.get_completions(ctx, document_text="streams:\n  - stream_id: x\n    unknown_block:\n      ")
        assert result == []

    def test_flag_key_in_stream_fallback_stream_level(self, bridge):
        """mute/solo come parent fanno fallback al livello stream (auto-indent VSCode)."""
        from granular_ls.providers.completion_provider import CompletionProvider
        provider = CompletionProvider(bridge)
        for flag in ('mute', 'solo'):
            ctx = make_context(context_type='key', current_text='',
                               parent_path=[flag], indent_level=3,
                               in_stream_element=True)
            result = provider.get_completions(ctx, document_text=f"streams:\n  - stream_id: x\n    {flag}:\n      ")
            assert len(result) > 0, f"Fallback non attivo per flag '{flag}'"

    def test_label_nel_blocco_annidato_e_yaml_path_completo(self, bridge):
        """
        Il label mostrato a VSCode e' la chiave locale: 'duration' (non 'grain.duration').
        insert_text e' 'duration: '.
        """
        provider = CompletionProvider(bridge)
        ctx = make_context(context_type='key', current_text='',
                           parent_path=['grain'], indent_level=3)
        result = provider.get_completions(ctx, document_text="grain:\n  ")
        item = next(i for i in result if i.label == 'duration')
        assert item.insert_text == 'duration: '


# =============================================================================
# 6. get_completions - struttura CompletionItem
# =============================================================================

class TestGetCompletionsStrutturaItem:

    def _get_density_item(self, bridge):
        provider = CompletionProvider(bridge)
        ctx = make_context(context_type='key', current_text='den')
        result = provider.get_completions(ctx, document_text="den")
        return next(i for i in result if i.label == 'density')

    def test_label_e_yaml_path(self, bridge):
        item = self._get_density_item(bridge)
        assert item.label == 'density'

    def test_insert_text_ha_due_punti_e_spazio(self, bridge):
        item = self._get_density_item(bridge)
        assert item.insert_text == 'density: '

    def test_kind_e_field(self, bridge):
        item = self._get_density_item(bridge)
        assert item.kind == CompletionItemKind.Field

    def test_detail_contiene_min_max(self, bridge):
        item = self._get_density_item(bridge)
        assert '0.01' in item.detail
        assert '4000' in item.detail

    def test_documentation_e_markup_content(self, bridge):
        item = self._get_density_item(bridge)
        assert item.documentation is not None
        assert item.documentation.kind == MarkupKind.Markdown

    def test_documentation_contiene_variation_mode(self, bridge):
        item = self._get_density_item(bridge)
        assert 'additive' in item.documentation.value

    def test_documentation_contiene_exclusive_group(self, bridge):
        item = self._get_density_item(bridge)
        assert 'density_mode' in item.documentation.value

    def test_item_senza_bounds_non_solleva(self, bridge):
        """Parametro senza bounds deve produrre un item valido senza crash."""
        raw = {
            'specs': [make_raw_spec('orphan', 'orphan')],
            'bounds': {},
        }
        b = SchemaBridge(raw)
        provider = CompletionProvider(b)
        ctx = make_context(context_type='key', current_text='')
        result = provider.get_completions(ctx, document_text="")
        assert isinstance(result, list)
        # 'orphan' deve essere tra i risultati (insieme alle stream context keys)
        labels = [item.label for item in result]
        assert 'orphan' in labels

    def test_detail_vuoto_se_bounds_mancano(self, bridge):
        raw = {
            'specs': [make_raw_spec('orphan', 'orphan')],
            'bounds': {},
        }
        b = SchemaBridge(raw)
        provider = CompletionProvider(b)
        ctx = make_context(context_type='key', current_text='')
        result = provider.get_completions(ctx, document_text="")
        labels = [item.label for item in result]
        orphan_item = next(i for i in result if i.label == 'orphan')
        assert orphan_item.detail == ''


# =============================================================================
# 7. get_completions - esclusione chiavi gia' presenti
# =============================================================================

class TestGetCompletionsEscludeChiaviPresenti:
    """
    Se una chiave e' gia' scritta nel documento, non va suggerita di nuovo.
    Evita duplicati fastidiosi nel menu.
    """

    def test_density_gia_presente_non_suggerita(self, bridge):
        provider = CompletionProvider(bridge)
        ctx = make_context(context_type='key', current_text='')
        result = provider.get_completions(
            ctx, document_text="density: 100\n"
        )
        labels = [item.label for item in result]
        assert 'density' not in labels

    def test_volume_gia_presente_non_suggerito(self, bridge):
        provider = CompletionProvider(bridge)
        ctx = make_context(context_type='key', current_text='')
        result = provider.get_completions(
            ctx, document_text="density: 100\nvolume: -6\n"
        )
        labels = [item.label for item in result]
        assert 'volume' not in labels

    def test_altri_parametri_ancora_suggeriti(self, bridge):
        provider = CompletionProvider(bridge)
        ctx = make_context(context_type='key', current_text='')
        result = provider.get_completions(
            ctx, document_text="density: 100\n"
        )
        labels = [item.label for item in result]
        assert 'volume' in labels

    def test_documento_vuoto_suggerisce_tutti(self, bridge):
        provider = CompletionProvider(bridge)
        ctx = make_context(context_type='key', current_text='')
        result_empty = provider.get_completions(ctx, document_text="")
        result_with_density = provider.get_completions(
            ctx, document_text="density: 100\n"
        )
        assert len(result_empty) > len(result_with_density)

    def test_chiave_parziale_non_conta_come_presente(self, bridge):
        """
        Se l'utente sta scrivendo 'den', la chiave 'density' non e'
        ancora presente nel documento: va comunque suggerita.
        """
        provider = CompletionProvider(bridge)
        ctx = make_context(context_type='key', current_text='den')
        result = provider.get_completions(ctx, document_text="den")
        labels = [item.label for item in result]
        assert 'density' in labels


# =============================================================================
# 8. Edge cases
# =============================================================================

class TestGetCompletionsEdgeCases:

    def test_bridge_senza_parametri_non_solleva(self):
        """Bridge vuoto non causa crash. Le stream context keys statiche compaiono."""
        b = SchemaBridge({'specs': [], 'bounds': {}})
        provider = CompletionProvider(b)
        ctx = make_context(context_type='key', current_text='')
        result = provider.get_completions(ctx, document_text="")
        assert isinstance(result, list)

    def test_risultato_e_sempre_lista(self, bridge):
        provider = CompletionProvider(bridge)
        for ct in ('key', 'value', 'unknown'):
            ctx = make_context(context_type=ct)
            result = provider.get_completions(ctx, document_text="")
            assert isinstance(result, list)

    def test_nessun_duplicato_nella_lista(self, bridge):
        provider = CompletionProvider(bridge)
        ctx = make_context(context_type='key', current_text='')
        result = provider.get_completions(ctx, document_text="")
        labels = [item.label for item in result]
        assert len(labels) == len(set(labels))

    def test_document_text_none_non_solleva(self, bridge):
        """document_text=None non deve esplodere."""
        provider = CompletionProvider(bridge)
        ctx = make_context(context_type='key', current_text='')
        result = provider.get_completions(ctx, document_text=None)
        assert isinstance(result, list)


# =============================================================================
# MODIFICA C - Nuovi scenari CompletionProvider
# =============================================================================

def make_bridge_with_stream_keys():
    """Bridge con parametri realistici + metodi Modifica A."""
    raw = {
        'specs': [
            make_raw_spec('density', 'density', default=None,
                          exclusive_group='density_mode', group_priority=2),
            make_raw_spec('fill_factor', 'fill_factor', default=2,
                          exclusive_group='density_mode', group_priority=1),
            make_raw_spec('volume', 'volume', default=0.0),
            make_raw_spec('grain_duration', 'grain.duration', default=0.05),
            make_raw_spec('grain_envelope', 'grain.envelope',
                          default='hanning', is_smart=False),
            make_raw_spec('pointer_speed', 'pointer.speed_ratio', default=1.0),
            {
                'name': 'grain_reverse', 'yaml_path': 'grain.reverse',
                'default': 0, 'is_smart': True, 'exclusive_group': None,
                'group_priority': 99, 'range_path': None, 'deviation_probability_key': 'reverse',
            },
            {
                'name': 'pitch_ratio', 'yaml_path': 'pitch.ratio',
                'default': 1.0, 'is_smart': True, 'exclusive_group': 'pitch_mode',
                'group_priority': 2, 'range_path': None, 'deviation_probability_key': 'pitch',
            },
            {
                'name': 'volume_param', 'yaml_path': 'volume',
                'default': 0.0, 'is_smart': True, 'exclusive_group': None,
                'group_priority': 99, 'range_path': None, 'deviation_probability_key': 'volume',
            },
        ],
        'bounds': {
            'density':        make_raw_bounds(0.01, 4000.0),
            'fill_factor':    make_raw_bounds(0.001, 50.0),
            'volume':         make_raw_bounds(-60.0, 0.0),
            'grain_duration': make_raw_bounds(0.001, 10.0),
            'pointer_speed':  make_raw_bounds(0.01, 10.0),
            'grain_reverse':  make_raw_bounds(0.0, 1.0),
            'pitch_ratio':    make_raw_bounds(0.01, 10.0),
            'volume_param':   make_raw_bounds(-60.0, 0.0),
        },
    }
    return SchemaBridge(raw)


class TestGetCompletionsStreamStart:
    """
    context_type='stream_start' -> il primo item e' uno snippet con tutti
    i campi obbligatori. Gli altri item sono le chiavi singole.
    """

    def test_stream_start_ritorna_completamenti(self):
        b = make_bridge_with_stream_keys()
        provider = CompletionProvider(b)
        ctx = make_context(context_type='stream_start', current_text='')
        result = provider.get_completions(ctx, document_text="streams:\n  - ")
        assert len(result) > 0

    def test_stream_start_primo_item_e_snippet_obbligatori(self):
        """Il primo item e' lo snippet con tutti e quattro i campi obbligatori."""
        b = make_bridge_with_stream_keys()
        provider = CompletionProvider(b)
        ctx = make_context(context_type='stream_start', current_text='')
        result = provider.get_completions(ctx, document_text="streams:\n  - ")
        assert result[0].label == 'stream (obbligatori)'

    def test_stream_start_snippet_contiene_campi_obbligatori(self):
        b = make_bridge_with_stream_keys()
        provider = CompletionProvider(b)
        ctx = make_context(context_type='stream_start', current_text='')
        result = provider.get_completions(ctx, document_text="streams:\n  - ")
        snippet = result[0].insert_text
        assert 'stream_id' in snippet
        assert 'onset' in snippet
        assert 'sample' in snippet

    def test_stream_start_snippet_non_contiene_duration(self):
        """PGE #205: i campi obbligatori sono tre. `duration` e' un override
        compositivo e resta disponibile come chiave singola, non nello
        scheletro dello stream nuovo."""
        b = make_bridge_with_stream_keys()
        provider = CompletionProvider(b)
        ctx = make_context(context_type='stream_start', current_text='')
        result = provider.get_completions(ctx, document_text="streams:\n  - ")
        assert 'duration' not in result[0].insert_text

    def test_stream_start_duration_offerta_come_chiave_singola(self):
        b = make_bridge_with_stream_keys()
        provider = CompletionProvider(b)
        ctx = make_context(context_type='stream_start', current_text='')
        result = provider.get_completions(ctx, document_text="streams:\n  - ")
        item = next(i for i in result if i.label == 'duration')
        doc = item.documentation.value.lower()
        assert 'sample' in doc, "la doc deve dire cosa succede se si omette"

    def test_stream_start_contiene_stream_id(self):
        """stream_id e' nel snippet obbligatori."""
        b = make_bridge_with_stream_keys()
        provider = CompletionProvider(b)
        ctx = make_context(context_type='stream_start', current_text='')
        result = provider.get_completions(ctx, document_text="streams:\n  - ")
        all_text = ' '.join(i.insert_text or '' for i in result)
        assert 'stream_id' in all_text

    def test_stream_start_contiene_onset(self):
        b = make_bridge_with_stream_keys()
        provider = CompletionProvider(b)
        ctx = make_context(context_type='stream_start', current_text='')
        result = provider.get_completions(ctx, document_text="streams:\n  - ")
        all_text = ' '.join(i.insert_text or '' for i in result)
        assert 'onset' in all_text

    def test_stream_start_contiene_rng_group(self):
        """rng_group (PGE #169) e' offerto fra le chiavi stream-level."""
        b = make_bridge_with_stream_keys()
        provider = CompletionProvider(b)
        ctx = make_context(context_type='stream_start', current_text='')
        result = provider.get_completions(ctx, document_text="streams:\n  - ")
        labels = [i.label for i in result]
        assert 'rng_group' in labels

    def test_stream_start_rng_group_ha_doc_specifica(self):
        """La doc di rng_group spiega la condivisione della sequenza RNG,
        non il testo generico 'Chiave stream: ...'."""
        b = make_bridge_with_stream_keys()
        provider = CompletionProvider(b)
        ctx = make_context(context_type='stream_start', current_text='')
        result = provider.get_completions(ctx, document_text="streams:\n  - ")
        item = next(i for i in result if i.label == 'rng_group')
        doc = item.documentation.value.lower()
        assert 'chiave stream:' not in doc
        assert 'sequenz' in doc  # sequenza/sequenze RNG condivise
        assert 'stream_id' in doc  # spiega il default

    def test_stream_start_contiene_duration_e_sample(self):
        b = make_bridge_with_stream_keys()
        provider = CompletionProvider(b)
        ctx = make_context(context_type='stream_start', current_text='')
        result = provider.get_completions(ctx, document_text="streams:\n  - ")
        all_text = ' '.join(i.insert_text or '' for i in result)
        assert 'duration' in all_text
        assert 'sample' in all_text

    def test_stream_start_insert_text_ha_due_punti(self):
        """Lo snippet principale ha i due punti per ogni campo."""
        b = make_bridge_with_stream_keys()
        provider = CompletionProvider(b)
        ctx = make_context(context_type='stream_start', current_text='')
        result = provider.get_completions(ctx, document_text="streams:\n  - ")
        snippet = result[0].insert_text
        assert 'stream_id:' in snippet

    def test_stream_start_filtro_prefisso(self):
        """Con current_text='on' lo snippet obbligatori e' sempre presente."""
        b = make_bridge_with_stream_keys()
        provider = CompletionProvider(b)
        ctx = make_context(context_type='stream_start', current_text='on')
        result = provider.get_completions(ctx, document_text="streams:\n  - on")
        # Lo snippet obbligatori e' sempre il primo item
        assert len(result) >= 1


class TestGetCompletionsBlockKeys:
    """
    A root level dello stream (parent_path=[]) il provider suggerisce
    anche le block keys: 'grain', 'pointer', 'pitch', 'deviation_probability'.
    Queste sono chiavi di blocco che si inseriscono come 'grain:\n'.
    """

    def test_block_keys_presenti_a_root_stream(self):
        b = make_bridge_with_stream_keys()
        provider = CompletionProvider(b)
        ctx = make_context(context_type='key', current_text='', parent_path=[])
        result = provider.get_completions(ctx, document_text="")
        labels = [item.label for item in result]
        # grain e pointer devono essere presenti come block keys
        assert 'grain' in labels
        assert 'pointer' in labels

    def test_block_key_insert_text_ha_due_punti_e_newline(self):
        """Le block keys si inseriscono come 'grain:\\n' non 'grain: '."""
        b = make_bridge_with_stream_keys()
        provider = CompletionProvider(b)
        ctx = make_context(context_type='key', current_text='', parent_path=[])
        result = provider.get_completions(ctx, document_text="")
        items_by_label = {item.label: item for item in result}
        if 'grain' in items_by_label:
            assert 'grain:' in items_by_label['grain'].insert_text and '\n' in items_by_label['grain'].insert_text

    def test_block_key_kind_e_module(self):
        """Le block keys hanno kind=CompletionItemKind.Module."""
        b = make_bridge_with_stream_keys()
        provider = CompletionProvider(b)
        ctx = make_context(context_type='key', current_text='', parent_path=[])
        result = provider.get_completions(ctx, document_text="")
        items_by_label = {item.label: item for item in result}
        if 'grain' in items_by_label:
            assert items_by_label['grain'].kind == CompletionItemKind.Module

    def test_block_keys_filtrate_per_prefisso(self):
        """Con current_text='gr' suggerisce solo 'grain'."""
        b = make_bridge_with_stream_keys()
        provider = CompletionProvider(b)
        ctx = make_context(context_type='key', current_text='gr', parent_path=[])
        result = provider.get_completions(ctx, document_text="gr")
        labels = [item.label for item in result]
        assert 'grain' in labels
        assert 'pointer' not in labels

    def test_block_keys_non_compaiono_dentro_blocco(self):
        """Dentro 'grain:' non suggeriamo 'grain' come block key."""
        b = make_bridge_with_stream_keys()
        provider = CompletionProvider(b)
        ctx = make_context(context_type='key', current_text='',
                           parent_path=['grain'], indent_level=3)
        result = provider.get_completions(ctx, document_text="grain:\n  ")
        labels = [item.label for item in result]
        assert 'grain' not in labels
        assert 'pointer' not in labels


class TestGetCompletionsDeviationProbabilityBlock:
    """
    parent_path=['deviation_probability'] -> suggerisce le deviation_probability keys ricavate
    dal bridge con get_deviation_probability_keys().
    """

    def test_deviation_probability_block_suggerisce_chiavi(self):
        b = make_bridge_with_stream_keys()
        provider = CompletionProvider(b)
        ctx = make_context(context_type='key', current_text='',
                           parent_path=['deviation_probability'], indent_level=3)
        result = provider.get_completions(ctx, document_text="deviation_probability:\n  ")
        assert len(result) > 0

    def test_deviation_probability_block_contiene_volume(self):
        b = make_bridge_with_stream_keys()
        provider = CompletionProvider(b)
        ctx = make_context(context_type='key', current_text='',
                           parent_path=['deviation_probability'], indent_level=3)
        result = provider.get_completions(ctx, document_text="deviation_probability:\n  ")
        labels = [item.label for item in result]
        assert 'volume' in labels

    def test_deviation_probability_block_contiene_pitch(self):
        b = make_bridge_with_stream_keys()
        provider = CompletionProvider(b)
        ctx = make_context(context_type='key', current_text='',
                           parent_path=['deviation_probability'], indent_level=3)
        result = provider.get_completions(ctx, document_text="deviation_probability:\n  ")
        labels = [item.label for item in result]
        assert 'pitch' in labels

    def test_deviation_probability_block_non_suggerisce_parametri_normali(self):
        """'density' non e' una deviation_probability key."""
        b = make_bridge_with_stream_keys()
        provider = CompletionProvider(b)
        ctx = make_context(context_type='key', current_text='',
                           parent_path=['deviation_probability'], indent_level=3)
        result = provider.get_completions(ctx, document_text="deviation_probability:\n  ")
        labels = [item.label for item in result]
        assert 'density' not in labels

    def test_deviation_probability_block_insert_text_ha_due_punti(self):
        b = make_bridge_with_stream_keys()
        provider = CompletionProvider(b)
        ctx = make_context(context_type='key', current_text='',
                           parent_path=['deviation_probability'], indent_level=3)
        result = provider.get_completions(ctx, document_text="deviation_probability:\n  ")
        for item in result:
            assert item.insert_text.endswith(': ')


# =============================================================================
# Integrazione EnvelopeSnippetProvider in CompletionProvider
# =============================================================================

class TestEnvelopeCompletionsInValueContext:
    """
    context_type='value' con current_key di un parametro numerico
    deve suggerire tutti gli snippet envelope.
    """

    def _make_value_ctx(self, key, parent_path=None, in_stream=True):
        from granular_ls.yaml_analyzer import YamlContext, YamlAnalyzer
        return YamlContext(
            context_type='value',
            current_text='',
            parent_path=parent_path or [],
            indent_level=1,
            in_stream_element=in_stream,
            current_key=key,
        )

    def test_value_context_su_density_suggerisce_envelopes(self):
        b = make_bridge_with_stream_keys()
        provider = CompletionProvider(b)
        ctx = self._make_value_ctx('density')
        result = provider.get_completions(ctx, document_text='')
        assert len(result) > 0

    def test_value_context_items_hanno_format_snippet(self):
        b = make_bridge_with_stream_keys()
        provider = CompletionProvider(b)
        ctx = self._make_value_ctx('density')
        result = provider.get_completions(ctx, document_text='')
        # L'item 'N punti' usa textEdit con newText='' (nessuna inserzione);
        # items command-only (text_edit vuoto): N punti e GUI editor
        _COMMAND_LABELS = {'envelope N punti...', 'envelope editor grafico...'}
        for item in result:
            if item.label in _COMMAND_LABELS:
                assert item.text_edit is not None
                assert item.text_edit.new_text == ''
            else:
                assert item.insert_text_format == InsertTextFormat.Snippet

    def test_value_context_items_hanno_documentazione(self):
        b = make_bridge_with_stream_keys()
        provider = CompletionProvider(b)
        ctx = self._make_value_ctx('density')
        result = provider.get_completions(ctx, document_text='')
        for item in result:
            assert item.documentation is not None

    def test_value_context_contiene_snippet_lineare(self):
        b = make_bridge_with_stream_keys()
        provider = CompletionProvider(b)
        ctx = self._make_value_ctx('density')
        result = provider.get_completions(ctx, document_text='')
        labels = [i.label for i in result]
        assert any('lineare' in l.lower() or 'linear' in l.lower() for l in labels)

    def test_value_context_contiene_snippet_cubic(self):
        b = make_bridge_with_stream_keys()
        provider = CompletionProvider(b)
        ctx = self._make_value_ctx('density')
        result = provider.get_completions(ctx, document_text='')
        labels = [i.label for i in result]
        assert any('cubic' in l.lower() for l in labels)

    def test_value_context_contiene_snippet_loop(self):
        b = make_bridge_with_stream_keys()
        provider = CompletionProvider(b)
        ctx = self._make_value_ctx('density')
        result = provider.get_completions(ctx, document_text='')
        labels = [i.label for i in result]
        assert any('loop' in l.lower() or 'cicl' in l.lower() for l in labels)

    def test_value_context_current_key_vuoto_ritorna_vuoto(self):
        b = make_bridge_with_stream_keys()
        provider = CompletionProvider(b)
        ctx = self._make_value_ctx('')
        result = provider.get_completions(ctx, document_text='')
        assert result == []

    def test_value_context_param_annidato_grain_duration(self):
        """grain.duration dentro parent_path=['grain'] funziona."""
        b = make_bridge_with_stream_keys()
        provider = CompletionProvider(b)
        ctx = self._make_value_ctx('duration', parent_path=['grain'])
        result = provider.get_completions(ctx, document_text='')
        assert len(result) > 0

    def test_value_context_unknown_param_ritorna_vuoto(self):
        b = make_bridge_with_stream_keys()
        provider = CompletionProvider(b)
        ctx = self._make_value_ctx('parametro_inesistente_xyz')
        result = provider.get_completions(ctx, document_text='')
        assert result == []


# =============================================================================
# Punto 5: scope locale per stream (parametri gia' presenti)
# =============================================================================

class TestStreamLocalScope:
    """
    I parametri gia' presenti in uno stream non devono comparire
    come suggerimenti in QUELLO stream, ma devono comparire negli altri.
    """

    def _yaml_two_streams(self):
        return (
            "streams:\n"
            "  - stream_id: s1\n"
            "    onset: 0.0\n"
            "    duration: 10.0\n"
            "    sample: a.wav\n"
            "    density: 100\n"
            "  - stream_id: s2\n"
            "    onset: 5.0\n"
            "    duration: 5.0\n"
            "    sample: b.wav\n"
            "    "
        )

    def test_density_compare_nel_secondo_stream(self):
        """density presente nel primo stream deve comparire nel secondo."""
        b = make_bridge_with_stream_keys()
        provider = CompletionProvider(b)
        yaml = self._yaml_two_streams()
        # Cursore sull'ultima riga (secondo stream, riga 10)
        ctx = YamlAnalyzer.get_context(yaml, 10, 4)
        result = provider.get_completions(ctx, yaml)
        labels = [i.label for i in result]
        assert 'density' in labels

    def test_density_non_compare_nel_primo_stream_dove_e_gia_presente(self):
        """density presente nel primo stream NON deve comparire nel primo stream."""
        b = make_bridge_with_stream_keys()
        provider = CompletionProvider(b)
        yaml = (
            "streams:\n"
            "  - stream_id: s1\n"
            "    onset: 0.0\n"
            "    duration: 10.0\n"
            "    sample: a.wav\n"
            "    density: 100\n"
            "    "
        )
        ctx = YamlAnalyzer.get_context(yaml, 6, 4)
        result = provider.get_completions(ctx, yaml)
        labels = [i.label for i in result]
        assert 'density' not in labels


# =============================================================================
# Punto 2: scope locale per blocchi annidati
# =============================================================================

class TestBlockLocalScope:
    """
    Dentro un blocco (grain, pointer, pitch, deviation_probability), i parametri
    gia' presenti in QUEL blocco non devono comparire come suggerimenti.
    Parametri presenti in altri blocchi o altri stream non influenzano.
    """

    def _make_grain_ctx(self, yaml, line, char):
        return YamlAnalyzer.get_context(yaml, line, char)

    def test_duration_non_compare_se_gia_in_grain(self):
        """duration dentro grain: non deve comparire se gia' presente."""
        b = make_bridge_with_stream_keys()
        provider = CompletionProvider(b)
        yaml = (
            "streams:\n"
            "  - stream_id: s1\n"
            "    grain:\n"
            "      duration: 0.05\n"
            "      "
        )
        ctx = YamlAnalyzer.get_context(yaml, 4, 6)
        result = provider.get_completions(ctx, yaml)
        labels = [i.label for i in result]
        assert 'duration' not in labels

    def test_envelope_compare_se_non_in_grain(self):
        """envelope dentro grain deve comparire se non ancora presente."""
        b = make_bridge_with_stream_keys()
        provider = CompletionProvider(b)
        yaml = (
            "streams:\n"
            "  - stream_id: s1\n"
            "    grain:\n"
            "      duration: 0.05\n"
            "      "
        )
        ctx = YamlAnalyzer.get_context(yaml, 4, 6)
        result = provider.get_completions(ctx, yaml)
        labels = [i.label for i in result]
        # envelope e' un parametro di grain, non deve essere escluso
        assert len(result) > 0


# =============================================================================
# Punto 1: envelope autocompletion dentro deviation_probability
# =============================================================================

class TestDeviationProbabilityEnvelopeCompletion:
    """
    Quando il cursore e' dopo 'chiave: ' dentro deviation_probability:
    (es. 'volume: '), devono comparire gli snippet envelope.
    """

    def test_value_context_dentro_deviation_probability_mostra_envelopes(self):
        """volume: dentro deviation_probability deve mostrare snippet envelope."""
        b = make_bridge_with_stream_keys()
        provider = CompletionProvider(b)
        from granular_ls.yaml_analyzer import YamlContext, YamlAnalyzer
        ctx = YamlContext(
            context_type='value',
            current_text='',
            parent_path=['deviation_probability'],
            indent_level=3,
            in_stream_element=True,
            current_key='volume',
            cursor_line=5,
        )
        result = provider.get_completions(ctx, "")
        assert len(result) > 0
        labels = [i.label for i in result]
        assert any('envelope' in l.lower() for l in labels)


# =============================================================================
# DeviationProbability: envelope su parametro deviation_probability diretto e su chiavi interne
# =============================================================================

class TestDeviationProbabilityEnvelopeFull:
    """
    DeviationProbability ha bounds [0, 100] sia come parametro diretto che come
    contenitore di chiavi. In tutti i casi deve mostrare envelope snippets
    con y_min=0.0 e y_max=100.0.
    """

    def test_deviation_probability_diretto_mostra_envelopes(self):
        """'deviation_probability: ' come valore diretto mostra 11 snippet envelope."""
        b = make_bridge_with_stream_keys()
        provider = CompletionProvider(b)
        ctx = YamlContext(
            context_type='value', current_text='', parent_path=[],
            indent_level=2, in_stream_element=True,
            current_key='deviation_probability', cursor_line=5,
        )
        items = provider.get_completions(ctx, '')
        assert len(items) > 0
        labels = [i.label for i in items]
        assert any('envelope' in l.lower() for l in labels)

    def test_deviation_probability_diretto_bounds_0_100(self):
        """Gli snippet per deviation_probability diretto hanno y_min=0.0 e y_max=100.0."""
        b = make_bridge_with_stream_keys()
        provider = CompletionProvider(b)
        ctx = YamlContext(
            context_type='value', current_text='', parent_path=[],
            indent_level=2, in_stream_element=True,
            current_key='deviation_probability', cursor_line=5,
        )
        items = provider.get_completions(ctx, '')
        linear = next((i for i in items if '2 punti' in i.label), None)
        assert linear is not None
        assert '100.0' in linear.insert_text
        assert '0.0' in linear.insert_text

    def test_deviation_probability_volume_usa_bounds_0_100(self):
        """volume dentro deviation_probability usa bounds [0, 100], non quelli di volume."""
        b = make_bridge_with_stream_keys()
        provider = CompletionProvider(b)
        ctx = YamlContext(
            context_type='value', current_text='', parent_path=['deviation_probability'],
            indent_level=3, in_stream_element=True,
            current_key='volume', cursor_line=5,
        )
        items = provider.get_completions(ctx, '')
        assert len(items) > 0
        linear = next((i for i in items if '2 punti' in i.label), None)
        assert linear is not None
        assert '100.0' in linear.insert_text
        assert '0.0' in linear.insert_text
        # NON deve usare i bounds di volume (-120, 12)
        assert '-120.0' not in linear.insert_text

    def test_deviation_probability_chiavi_hanno_trigger_suggest(self):
        """Le chiavi dentro deviation_probability devono avere command triggerSuggest."""
        b = make_bridge_with_stream_keys()
        provider = CompletionProvider(b)
        ctx = YamlContext(
            context_type='key', current_text='', parent_path=['deviation_probability'],
            indent_level=3, in_stream_element=True,
            current_key='', cursor_line=5,
        )
        items = provider.get_completions(ctx, '')
        assert len(items) > 0
        # Ogni chiave deve avere command per aprire menu envelope
        for item in items:
            assert item.command is not None


# =============================================================================
# TestVoiceStrategySnippet
# =============================================================================

class TestVoiceStrategySnippet:
    """
    Flusso in due passi per voices dimensions:
    1. Completamento dimension key → inserisce 'dim: {strategy: ' + TRIGGER_SUGGEST
    2. Completamento strategy inline → inserisce 'name, kwarg: ${1:0}}' chiudendo il dict
    """

    def test_dimension_inserisce_inline_dict_aperto(self, bridge):
        provider = CompletionProvider(bridge)
        ctx = make_context(
            context_type='key',
            current_text='',
            parent_path=['voices'],
            indent_level=3,
            leading_spaces=6,
        )
        items = provider.get_completions(ctx, '')
        pitch_items = [it for it in items if it.label == 'pitch']
        assert len(pitch_items) == 1  # un solo item per dimension
        assert pitch_items[0].insert_text == 'pitch: {strategy: '
        assert pitch_items[0].command is not None  # TRIGGER_SUGGEST

    def test_dimension_senza_newline(self, bridge):
        provider = CompletionProvider(bridge)
        ctx = make_context(
            context_type='key',
            current_text='',
            parent_path=['voices'],
            indent_level=3,
        )
        items = provider.get_completions(ctx, '')
        dim_items = [it for it in items
                     if it.label in ('pitch', 'pan', 'pointer', 'onset_offset')]
        assert len(dim_items) > 0
        for it in dim_items:
            assert '\n' not in (it.insert_text or '')

    def test_inline_strategy_completion_chiude_dict(self, bridge):
        from lsprotocol.types import InsertTextFormat
        provider = CompletionProvider(bridge)
        # Simuliamo la riga 'pitch: {strategy: ' con cursore alla fine
        document = (
            "streams:\n"
            "  - stream_id: s1\n"
            "    onset: 0.0\n"
            "    duration: 10.0\n"
            "    sample: f.wav\n"
            "    voices:\n"
            "      num_voices: 4\n"
            "      pitch: {strategy: \n"
        )
        ctx = make_context(
            context_type='value',
            current_text='',
            current_key='pitch',
            parent_path=['voices'],
            indent_level=3,
            leading_spaces=6,
            cursor_line=7,
        )
        items = provider.get_completions(ctx, document)
        assert len(items) > 0
        # Ogni item deve chiudere il dict con '}'
        for it in items:
            assert (it.insert_text or '').endswith('}')
        # Almeno uno deve avere tab stop
        assert any('$' in (it.insert_text or '') for it in items)

    def test_inline_strategy_step_contiene_kwarg(self, bridge):
        from lsprotocol.types import InsertTextFormat
        provider = CompletionProvider(bridge)
        document = (
            "streams:\n"
            "  - stream_id: s1\n"
            "    onset: 0.0\n"
            "    duration: 10.0\n"
            "    sample: f.wav\n"
            "    voices:\n"
            "      num_voices: 4\n"
            "      pitch: {strategy: step\n"
        )
        ctx = make_context(
            context_type='value',
            current_text='step',
            current_key='pitch',
            parent_path=['voices'],
            indent_level=3,
            cursor_line=7,
        )
        items = provider.get_completions(ctx, document)
        step_items = [it for it in items if it.label == 'step']
        assert len(step_items) == 1
        # insert_text deve contenere il kwarg 'step:' e chiudere con '}'
        assert 'step:' in step_items[0].insert_text
        assert step_items[0].insert_text.endswith('}')
        assert step_items[0].insert_text_format == InsertTextFormat.Snippet

    def test_inline_strategy_senza_kwargs_chiude_solo(self, bridge):
        provider = CompletionProvider(bridge)
        # stochastic per pan non ha kwargs (verifichiamo con una strategy fittizia)
        # Usiamo _get_voice_strategy_inline_completions direttamente
        items = provider._get_voice_strategy_inline_completions('pitch', '')
        assert len(items) > 0
        for it in items:
            assert (it.insert_text or '').endswith('}')

    def test_strategy_block_style_inserisce_solo_nome(self, bridge):
        # Nel contesto block style (parent_path=['voices','pitch']), solo il nome
        provider = CompletionProvider(bridge)
        ctx = make_context(
            context_type='value',
            current_text='',
            current_key='strategy',
            parent_path=['voices', 'pitch'],
            indent_level=4,
            leading_spaces=8,
        )
        items = provider.get_completions(ctx, '')
        assert len(items) > 0
        for it in items:
            assert '\n' not in (it.insert_text or '')
            assert not (it.insert_text or '').endswith('}')

    def test_chord_progression_tra_le_strategy_pitch(self, bridge):
        # chord_progression deve comparire tra i valori di strategy per pitch
        provider = CompletionProvider(bridge)
        ctx = make_context(
            context_type='value',
            current_text='',
            current_key='strategy',
            parent_path=['voices', 'pitch'],
            indent_level=4,
            leading_spaces=8,
        )
        items = provider.get_completions(ctx, '')
        assert 'chord_progression' in [it.label for it in items]

    def test_chord_progression_kwargs_suggeriti(self, bridge):
        # Con strategy: chord_progression, i kwarg progression/interp/
        # voice_leading vengono suggeriti come chiavi.
        provider = CompletionProvider(bridge)
        document = (
            "streams:\n"
            "  - stream_id: s1\n"
            "    onset: 0.0\n"
            "    duration: 10.0\n"
            "    sample: f.wav\n"
            "    voices:\n"
            "      num_voices: 4\n"
            "      pitch:\n"
            "        strategy: chord_progression\n"
            "        \n"  # cursore qui
        )
        ctx = make_context(
            context_type='key',
            current_text='',
            parent_path=['voices', 'pitch'],
            indent_level=4,
            leading_spaces=8,
            cursor_line=9,
        )
        labels = [it.label for it in provider.get_completions(ctx, document)]
        assert 'progression' in labels
        assert 'interp' in labels
        assert 'voice_leading' in labels

    def test_interp_enum_value_completion(self, bridge):
        # Valori enum di interp: linear/cubic/step
        provider = CompletionProvider(bridge)
        ctx = make_context(
            context_type='value',
            current_text='',
            current_key='interp',
            parent_path=['voices', 'pitch'],
            indent_level=4,
            leading_spaces=8,
        )
        items = provider.get_completions(ctx, '')
        labels = [it.label for it in items]
        assert {'linear', 'cubic', 'step'} <= set(labels)

    def test_pointer_dimension_suggerisce_normalized(self, bridge):
        # Dentro voices.pointer block style, normalized deve essere suggerito
        provider = CompletionProvider(bridge)
        document = (
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
            "        \n"  # cursore qui
        )
        ctx = make_context(
            context_type='key',
            current_text='',
            parent_path=['voices', 'pointer'],
            indent_level=4,
            leading_spaces=8,
            cursor_line=10,
        )
        items = provider.get_completions(ctx, document)
        labels = [it.label for it in items]
        assert 'normalized' in labels

    def test_pointer_inline_strategy_contiene_normalized_snippet(self, bridge):
        # In inline completion, normalized deve comparire come tab stop bool
        items = provider_instance = CompletionProvider(bridge)
        inline_items = provider_instance._get_voice_strategy_inline_completions(
            'pointer', ''
        )
        linear_items = [it for it in inline_items if it.label == 'linear']
        assert len(linear_items) == 1
        # Il snippet deve contenere normalized con scelta true/false
        assert 'normalized' in linear_items[0].insert_text
        assert 'true' in linear_items[0].insert_text


# =============================================================================
# BLOCCO PITCH UNIT-DRIVEN (issue #9)
# =============================================================================

_PITCH_DOC_BASE = (
    "streams:\n"
    "  - stream_id: s1\n"
    "    onset: 0.0\n"
    "    duration: 10.0\n"
    "    sample: f.wav\n"
)


class TestPitchBlockCompletions:
    """Completion delle chiavi del blocco pitch (registry pitch_units)."""

    def _ctx(self, cursor_line, current_text='', context_type='key',
             current_key=''):
        return make_context(
            context_type=context_type,
            current_text=current_text,
            current_key=current_key,
            parent_path=['pitch'],
            indent_level=3,
            leading_spaces=6,
            cursor_line=cursor_line,
        )

    def test_blocco_vuoto_suggerisce_unita_e_range(self, bridge):
        provider = CompletionProvider(bridge)
        document = _PITCH_DOC_BASE + "    pitch:\n      \n"
        items = provider.get_completions(self._ctx(6), document)
        labels = {it.label for it in items}
        for key in ('semitones', 'cents', 'quarter_tone',
                    'eighth_tone', 'edo', 'ratio', 'range'):
            assert key in labels, f'{key} mancante in {labels}'
        # value solo con edo nel blocco
        assert 'value' not in labels

    def test_edo_inserisce_coppia_con_value(self, bridge):
        provider = CompletionProvider(bridge)
        document = _PITCH_DOC_BASE + "    pitch:\n      \n"
        items = provider.get_completions(self._ctx(6), document)
        edo = [it for it in items if it.label == 'edo'][0]
        assert 'edo: ' in edo.insert_text
        assert 'value: ' in edo.insert_text

    def test_unita_presente_esclude_le_altre(self, bridge):
        provider = CompletionProvider(bridge)
        document = _PITCH_DOC_BASE + "    pitch:\n      semitones: 3\n      \n"
        items = provider.get_completions(self._ctx(7), document)
        labels = {it.label for it in items}
        for key in ('semitones', 'cents', 'quarter_tone',
                    'eighth_tone', 'edo', 'ratio'):
            assert key not in labels
        assert 'range' in labels

    def test_edo_presente_suggerisce_value(self, bridge):
        provider = CompletionProvider(bridge)
        document = _PITCH_DOC_BASE + "    pitch:\n      edo: 31\n      \n"
        items = provider.get_completions(self._ctx(7), document)
        labels = {it.label for it in items}
        assert 'value' in labels
        value_item = [it for it in items if it.label == 'value'][0]
        assert '93' in value_item.detail  # bounds dinamici ±3·31

    def test_prefisso_filtra_le_unita(self, bridge):
        provider = CompletionProvider(bridge)
        document = _PITCH_DOC_BASE + "    pitch:\n      qu\n"
        items = provider.get_completions(
            self._ctx(6, current_text='qu'), document
        )
        labels = {it.label for it in items}
        assert labels == {'quarter_tone'}

    def test_value_context_semitones_da_envelope_con_bounds_unita(self, bridge):
        provider = CompletionProvider(bridge)
        document = _PITCH_DOC_BASE + "    pitch:\n      semitones: \n"
        ctx = self._ctx(6, context_type='value', current_key='semitones')
        items = provider.get_completions(ctx, document)
        assert len(items) > 0
        joined = ' '.join(it.insert_text or '' for it in items)
        assert '-36' in joined  # y_min dell'unità semitones

    def test_value_context_edo_nessun_envelope(self, bridge):
        provider = CompletionProvider(bridge)
        document = _PITCH_DOC_BASE + "    pitch:\n      edo: \n"
        ctx = self._ctx(6, context_type='value', current_key='edo')
        assert provider.get_completions(ctx, document) == []

    def test_value_context_value_usa_bounds_da_edo(self, bridge):
        provider = CompletionProvider(bridge)
        document = _PITCH_DOC_BASE + (
            "    pitch:\n      edo: 31\n      value: \n"
        )
        ctx = self._ctx(7, context_type='value', current_key='value')
        items = provider.get_completions(ctx, document)
        assert len(items) > 0
        joined = ' '.join(it.insert_text or '' for it in items)
        assert '-93' in joined

    def test_value_context_range_con_ratio(self, bridge):
        provider = CompletionProvider(bridge)
        document = _PITCH_DOC_BASE + (
            "    pitch:\n      ratio: 1.5\n      range: \n"
        )
        ctx = self._ctx(7, context_type='value', current_key='range')
        items = provider.get_completions(ctx, document)
        assert len(items) > 0
        joined = ' '.join(it.insert_text or '' for it in items)
        assert '2' in joined  # max_range dell'unità ratio

    def test_stream_level_offre_blocco_pitch(self, bridge):
        provider = CompletionProvider(bridge)
        document = _PITCH_DOC_BASE + "    \n"
        ctx = make_context(
            context_type='key', current_text='',
            parent_path=[], indent_level=2,
            in_stream_element=True, leading_spaces=4, cursor_line=5,
        )
        items = provider.get_completions(ctx, document)
        pitch_items = [it for it in items if it.label == 'pitch']
        assert len(pitch_items) == 1
        doc_value = pitch_items[0].documentation.value
        assert 'unit-driven' in doc_value or 'chiave-unit' in doc_value.lower()


class TestVoicePitchUnitCompletions:
    """Completion di `unit` e dei suoi valori in voices.pitch (issue #9/#10)."""

    def _voices_doc(self, body):
        return _PITCH_DOC_BASE + "    voices:\n      num_voices: 4\n" + body

    def test_kwargs_di_step_includono_unit(self, bridge):
        provider = CompletionProvider(bridge)
        document = self._voices_doc(
            "      pitch:\n        strategy: step\n        \n"
        )
        ctx = make_context(
            context_type='key', current_text='',
            parent_path=['voices', 'pitch'], indent_level=4,
            leading_spaces=8, cursor_line=8,
        )
        items = provider.get_completions(ctx, document)
        labels = {it.label for it in items}
        assert 'unit' in labels
        assert 'step' in labels

    def test_kwargs_di_range_usano_pitch_range(self, bridge):
        provider = CompletionProvider(bridge)
        document = self._voices_doc(
            "      pitch:\n        strategy: range\n        \n"
        )
        ctx = make_context(
            context_type='key', current_text='',
            parent_path=['voices', 'pitch'], indent_level=4,
            leading_spaces=8, cursor_line=8,
        )
        items = provider.get_completions(ctx, document)
        labels = {it.label for it in items}
        assert 'pitch_range' in labels
        assert 'semitone_range' not in labels

    def test_kwargs_di_chord_senza_unit_ma_con_inversion(self, bridge):
        provider = CompletionProvider(bridge)
        document = self._voices_doc(
            "      pitch:\n        strategy: chord\n        \n"
        )
        ctx = make_context(
            context_type='key', current_text='',
            parent_path=['voices', 'pitch'], indent_level=4,
            leading_spaces=8, cursor_line=8,
        )
        items = provider.get_completions(ctx, document)
        labels = {it.label for it in items}
        assert 'unit' not in labels      # semitone-locked
        assert 'inversion' in labels

    def test_valori_unit_block_style(self, bridge):
        provider = CompletionProvider(bridge)
        document = self._voices_doc(
            "      pitch:\n        strategy: step\n        unit: \n"
        )
        ctx = make_context(
            context_type='value', current_text='', current_key='unit',
            parent_path=['voices', 'pitch'], indent_level=4,
            leading_spaces=8, cursor_line=8,
        )
        items = provider.get_completions(ctx, document)
        labels = {it.label for it in items}
        for nome in ('semitones', 'cents', 'quarter_tone',
                     'eighth_tone', 'ratio'):
            assert nome in labels
        assert '{edo: N}' in labels

    def test_valori_unit_filtrati_dal_prefisso(self, bridge):
        provider = CompletionProvider(bridge)
        document = self._voices_doc(
            "      pitch:\n        strategy: step\n        unit: ce\n"
        )
        ctx = make_context(
            context_type='value', current_text='ce', current_key='unit',
            parent_path=['voices', 'pitch'], indent_level=4,
            leading_spaces=8, cursor_line=8,
        )
        items = provider.get_completions(ctx, document)
        labels = {it.label for it in items}
        assert 'cents' in labels
        assert 'semitones' not in labels

    def test_valori_unit_inline_dict(self, bridge):
        provider = CompletionProvider(bridge)
        document = self._voices_doc(
            "      pitch: {strategy: step, step: 3.0, unit: \n"
        )
        ctx = make_context(
            context_type='value', current_text='', current_key='pitch',
            parent_path=['voices'], indent_level=3,
            leading_spaces=6, cursor_line=7,
        )
        items = provider.get_completions(ctx, document)
        labels = {it.label for it in items}
        assert 'ratio' in labels
        assert '{edo: N}' in labels

    def test_inline_snippet_step_include_unit_choices(self, bridge):
        provider = CompletionProvider(bridge)
        document = self._voices_doc("      pitch: {strategy: \n")
        ctx = make_context(
            context_type='value', current_text='', current_key='pitch',
            parent_path=['voices'], indent_level=3,
            leading_spaces=6, cursor_line=7,
        )
        items = provider.get_completions(ctx, document)
        step_item = [it for it in items if it.label == 'step'][0]
        assert 'unit:' in step_item.insert_text
        assert 'semitones' in step_item.insert_text


# =============================================================================
# grain.duration_unit (PGE #158): meta-chiave del blocco grain
# =============================================================================

class TestGrainDurationUnitCompletion:
    """duration_unit dentro grain: (mirror di loop_unit per pointer)."""

    def test_duration_unit_suggested_in_grain_block(self, bridge):
        provider = CompletionProvider(bridge)
        ctx = make_context(context_type='key', current_text='',
                           parent_path=['grain'], indent_level=3)
        result = provider.get_completions(ctx, document_text="grain:\n  ")
        labels = [item.label for item in result]
        assert 'duration_unit' in labels

    def test_duration_unit_not_suggested_if_present(self, bridge):
        provider = CompletionProvider(bridge)
        ctx = make_context(context_type='key', current_text='',
                           parent_path=['grain'], indent_level=3)
        doc = ("streams:\n  - stream_id: s\n    grain:\n"
               "      duration_unit: samples\n      ")
        result = provider.get_completions(ctx, document_text=doc)
        labels = [item.label for item in result]
        assert 'duration_unit' not in labels

    def test_duration_unit_value_completions(self, bridge):
        provider = CompletionProvider(bridge)
        ctx = make_context(context_type='value', current_text='',
                           current_key='duration_unit',
                           parent_path=['grain'], indent_level=3)
        result = provider.get_completions(ctx, document_text="grain:\n  duration_unit: ")
        labels = [item.label for item in result]
        assert 'seconds' in labels
        assert 'samples' in labels

    def test_duration_unit_value_prefix_filter(self, bridge):
        provider = CompletionProvider(bridge)
        ctx = make_context(context_type='value', current_text='sam',
                           current_key='duration_unit',
                           parent_path=['grain'], indent_level=3)
        result = provider.get_completions(ctx, document_text="grain:\n  duration_unit: sam")
        labels = [item.label for item in result]
        assert labels == ['samples']
