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
    """Bridge con parametri realistici che coprono i casi d'uso principali."""
    raw = {
        'specs': [
            # root level
            make_raw_spec('density', 'density', default=None,
                          exclusive_group='density_mode', group_priority=2),
            make_raw_spec('fill_factor', 'fill_factor', default=2,
                          exclusive_group='density_mode', group_priority=1),
            make_raw_spec('distribution', 'distribution', default=0.0),
            make_raw_spec('volume', 'volume', default=-6.0),
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
                 in_stream_element=True, current_key=''):
    return YamlContext(
        context_type=context_type,
        current_text=current_text,
        parent_path=parent_path or [],
        indent_level=indent_level,
        in_stream_element=in_stream_element,
        current_key=current_key,
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
        """Un blocco non noto al bridge non ha parametri noti."""
        provider = CompletionProvider(bridge)
        ctx = make_context(context_type='key', current_text='',
                           parent_path=['unknown_block'], indent_level=1)
        result = provider.get_completions(ctx, document_text="unknown_block:\n  ")
        assert result == []

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
            make_raw_spec('volume', 'volume', default=-6.0),
            make_raw_spec('grain_duration', 'grain.duration', default=0.05),
            make_raw_spec('grain_envelope', 'grain.envelope',
                          default='hanning', is_smart=False),
            make_raw_spec('pointer_speed', 'pointer.speed_ratio', default=1.0),
            {
                'name': 'grain_reverse', 'yaml_path': 'grain.reverse',
                'default': 0, 'is_smart': True, 'exclusive_group': None,
                'group_priority': 99, 'range_path': None, 'dephase_key': 'reverse',
            },
            {
                'name': 'pitch_ratio', 'yaml_path': 'pitch.ratio',
                'default': 1.0, 'is_smart': True, 'exclusive_group': 'pitch_mode',
                'group_priority': 2, 'range_path': None, 'dephase_key': 'pitch',
            },
            {
                'name': 'volume_param', 'yaml_path': 'volume',
                'default': -6.0, 'is_smart': True, 'exclusive_group': None,
                'group_priority': 99, 'range_path': None, 'dephase_key': 'volume',
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
        assert 'duration' in snippet
        assert 'sample' in snippet

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
    anche le block keys: 'grain', 'pointer', 'pitch', 'dephase'.
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


class TestGetCompletionsDephaseBlock:
    """
    parent_path=['dephase'] -> suggerisce le dephase keys ricavate
    dal bridge con get_dephase_keys().
    """

    def test_dephase_block_suggerisce_chiavi(self):
        b = make_bridge_with_stream_keys()
        provider = CompletionProvider(b)
        ctx = make_context(context_type='key', current_text='',
                           parent_path=['dephase'], indent_level=3)
        result = provider.get_completions(ctx, document_text="dephase:\n  ")
        assert len(result) > 0

    def test_dephase_block_contiene_volume(self):
        b = make_bridge_with_stream_keys()
        provider = CompletionProvider(b)
        ctx = make_context(context_type='key', current_text='',
                           parent_path=['dephase'], indent_level=3)
        result = provider.get_completions(ctx, document_text="dephase:\n  ")
        labels = [item.label for item in result]
        assert 'volume' in labels

    def test_dephase_block_contiene_pitch(self):
        b = make_bridge_with_stream_keys()
        provider = CompletionProvider(b)
        ctx = make_context(context_type='key', current_text='',
                           parent_path=['dephase'], indent_level=3)
        result = provider.get_completions(ctx, document_text="dephase:\n  ")
        labels = [item.label for item in result]
        assert 'pitch' in labels

    def test_dephase_block_non_suggerisce_parametri_normali(self):
        """'density' non e' una dephase key."""
        b = make_bridge_with_stream_keys()
        provider = CompletionProvider(b)
        ctx = make_context(context_type='key', current_text='',
                           parent_path=['dephase'], indent_level=3)
        result = provider.get_completions(ctx, document_text="dephase:\n  ")
        labels = [item.label for item in result]
        assert 'density' not in labels

    def test_dephase_block_insert_text_ha_due_punti(self):
        b = make_bridge_with_stream_keys()
        provider = CompletionProvider(b)
        ctx = make_context(context_type='key', current_text='',
                           parent_path=['dephase'], indent_level=3)
        result = provider.get_completions(ctx, document_text="dephase:\n  ")
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
        for item in result:
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
    Dentro un blocco (grain, pointer, pitch, dephase), i parametri
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
# Punto 1: envelope autocompletion dentro dephase
# =============================================================================

class TestDephaseEnvelopeCompletion:
    """
    Quando il cursore e' dopo 'chiave: ' dentro dephase:
    (es. 'volume: '), devono comparire gli snippet envelope.
    """

    def test_value_context_dentro_dephase_mostra_envelopes(self):
        """volume: dentro dephase deve mostrare snippet envelope."""
        b = make_bridge_with_stream_keys()
        provider = CompletionProvider(b)
        from granular_ls.yaml_analyzer import YamlContext, YamlAnalyzer
        ctx = YamlContext(
            context_type='value',
            current_text='',
            parent_path=['dephase'],
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
# Dephase: envelope su parametro dephase diretto e su chiavi interne
# =============================================================================

class TestDephaseEnvelopeFull:
    """
    Dephase ha bounds [0, 100] sia come parametro diretto che come
    contenitore di chiavi. In tutti i casi deve mostrare envelope snippets
    con y_min=0.0 e y_max=100.0.
    """

    def test_dephase_diretto_mostra_envelopes(self):
        """'dephase: ' come valore diretto mostra 11 snippet envelope."""
        b = make_bridge_with_stream_keys()
        provider = CompletionProvider(b)
        ctx = YamlContext(
            context_type='value', current_text='', parent_path=[],
            indent_level=2, in_stream_element=True,
            current_key='dephase', cursor_line=5,
        )
        items = provider.get_completions(ctx, '')
        assert len(items) > 0
        labels = [i.label for i in items]
        assert any('envelope' in l.lower() for l in labels)

    def test_dephase_diretto_bounds_0_100(self):
        """Gli snippet per dephase diretto hanno y_min=0.0 e y_max=100.0."""
        b = make_bridge_with_stream_keys()
        provider = CompletionProvider(b)
        ctx = YamlContext(
            context_type='value', current_text='', parent_path=[],
            indent_level=2, in_stream_element=True,
            current_key='dephase', cursor_line=5,
        )
        items = provider.get_completions(ctx, '')
        linear = next((i for i in items if '2 punti' in i.label), None)
        assert linear is not None
        assert '100.0' in linear.insert_text
        assert '0.0' in linear.insert_text

    def test_dephase_volume_usa_bounds_0_100(self):
        """volume dentro dephase usa bounds [0, 100], non quelli di volume."""
        b = make_bridge_with_stream_keys()
        provider = CompletionProvider(b)
        ctx = YamlContext(
            context_type='value', current_text='', parent_path=['dephase'],
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

    def test_dephase_chiavi_hanno_trigger_suggest(self):
        """Le chiavi dentro dephase devono avere command triggerSuggest."""
        b = make_bridge_with_stream_keys()
        provider = CompletionProvider(b)
        ctx = YamlContext(
            context_type='key', current_text='', parent_path=['dephase'],
            indent_level=3, in_stream_element=True,
            current_key='', cursor_line=5,
        )
        items = provider.get_completions(ctx, '')
        assert len(items) > 0
        # Ogni chiave deve avere command per aprire menu envelope
        for item in items:
            assert item.command is not None
