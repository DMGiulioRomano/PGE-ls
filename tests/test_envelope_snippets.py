# tests/test_envelope_snippets.py
"""
Suite TDD - EnvelopeSnippetProvider

Modulo sotto test:
    granular_ls/envelope_snippets.py

Responsabilita':
    Dato uno yaml_path di un parametro numerico, produce una lista di
    CompletionItem LSP con snippet per tutti i formati envelope supportati:
        - Standard lineare (2 e 3 punti)
        - Dict con tipo esplicito (cubic, step, linear)
        - Compact loop (base, con interp, con time_dist stringa, con time_dist dict)
        - Misto (breakpoints + compact)

Organizzazione:
    1.  Costruzione
    2.  get_snippets() - formato standard lineare
    3.  get_snippets() - formato dict (cubic, step, linear)
    4.  get_snippets() - formato compact base
    5.  get_snippets() - compact con interp e time_dist
    6.  get_snippets() - formato misto
    7.  Struttura CompletionItem (label, insert_text, documentation)
    8.  get_snippets_for_parameter() - lookup per yaml_path
    9.  Edge cases
"""

import pytest
from lsprotocol.types import CompletionItem, CompletionItemKind, InsertTextFormat

from granular_ls.schema_bridge import SchemaBridge
from granular_ls.envelope_snippets import EnvelopeSnippetProvider


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
            make_raw_spec('density', 'density', default=None),
            make_raw_spec('volume', 'volume', default=-6.0),
            make_raw_spec('grain_duration', 'grain.duration', default=0.05),
            make_raw_spec('grain_envelope', 'grain.envelope',
                          default='hanning', is_smart=False),
            make_raw_spec('effective_density', '_internal_calc_',
                          default=0.0, is_smart=False),
        ],
        'bounds': {
            'density':        make_raw_bounds(0.01, 4000.0),
            'volume':         make_raw_bounds(-120.0, 12.0),
            'grain_duration': make_raw_bounds(0.001, 10.0),
        },
    }
    return SchemaBridge(raw)

@pytest.fixture
def provider(bridge):
    return EnvelopeSnippetProvider(bridge)


# =============================================================================
# 1. COSTRUZIONE
# =============================================================================

class TestEnvelopeSnippetProviderConstruction:

    def test_costruzione_con_bridge(self, bridge):
        p = EnvelopeSnippetProvider(bridge)
        assert p is not None

    def test_costruzione_con_bridge_vuoto(self):
        b = SchemaBridge({'specs': [], 'bounds': {}})
        p = EnvelopeSnippetProvider(b)
        assert p is not None


# =============================================================================
# 2. FORMATO STANDARD LINEARE
# =============================================================================

class TestGetSnippetsLinear:
    """Snippet per breakpoints standard [[t, v], ...]."""

    def test_ritorna_lista(self, provider):
        result = provider.get_snippets()
        assert isinstance(result, list)

    def test_lista_non_vuota(self, provider):
        assert len(provider.get_snippets()) > 0

    def test_contiene_snippet_2_punti(self, provider):
        labels = [i.label for i in provider.get_snippets()]
        assert any('2' in l or 'lineare' in l.lower() or 'standard' in l.lower()
                   for l in labels)

    def test_contiene_snippet_3_punti(self, provider):
        labels = [i.label for i in provider.get_snippets()]
        assert any('3' in l or 'ramp' in l.lower() or 'standard' in l.lower()
                   for l in labels)

    def test_snippet_2_punti_insert_text_e_lista(self, provider):
        """Il testo inserito deve aprire una lista YAML."""
        items = [i for i in provider.get_snippets()
                 if '2' in i.label or 'lineare' in i.label.lower()]
        if items:
            assert '[' in items[0].insert_text or '-' in items[0].insert_text

    def test_snippet_contiene_tab_stops(self, provider):
        """Almeno uno snippet deve avere tab stops ${n:...}."""
        has_tab_stop = any(
            '${' in item.insert_text
            for item in provider.get_snippets()
        )
        assert has_tab_stop

    def test_tutti_items_sono_completionitem(self, provider):
        for item in provider.get_snippets():
            assert isinstance(item, CompletionItem)


# =============================================================================
# 3. FORMATO DICT (cubic, step, linear)
# =============================================================================

class TestGetSnippetsDictFormat:
    """Snippet per formato dict con tipo esplicito."""

    def test_contiene_snippet_cubic(self, provider):
        labels = [i.label for i in provider.get_snippets()]
        assert any('cubic' in l.lower() for l in labels)

    def test_contiene_snippet_step(self, provider):
        labels = [i.label for i in provider.get_snippets()]
        assert any('step' in l.lower() for l in labels)

    def test_snippet_cubic_contiene_type_field(self, provider):
        cubic = next(
            (i for i in provider.get_snippets() if 'cubic' in i.label.lower()),
            None
        )
        assert cubic is not None
        assert 'type' in cubic.insert_text
        assert 'cubic' in cubic.insert_text

    def test_snippet_step_contiene_type_step(self, provider):
        step = next(
            (i for i in provider.get_snippets() if 'step' in i.label.lower()),
            None
        )
        assert step is not None
        assert 'step' in step.insert_text

    def test_snippet_dict_contiene_points(self, provider):
        dict_items = [i for i in provider.get_snippets()
                      if ('cubic' in i.label.lower() or 'step' in i.label.lower()) and 'loop' not in i.label.lower()]
        for item in dict_items:
            assert 'points' in item.insert_text

    def test_snippet_dict_format_e_snippet(self, provider):
        dict_items = [i for i in provider.get_snippets()
                      if ('cubic' in i.label.lower() or 'step' in i.label.lower()) and 'loop' not in i.label.lower()]
        for item in dict_items:
            assert item.insert_text_format == InsertTextFormat.Snippet


# =============================================================================
# 4. FORMATO COMPACT BASE
# =============================================================================

class TestGetSnippetsCompactBase:
    """Snippet per formato compact loop base."""

    def test_contiene_snippet_compact(self, provider):
        labels = [i.label for i in provider.get_snippets()]
        assert any('loop' in l.lower() or 'compact' in l.lower()
                   or 'cicl' in l.lower() for l in labels)

    def test_snippet_compact_contiene_pattern_points(self, provider):
        """Il formato compact deve avere pattern a percentuale."""
        compact = next(
            (i for i in provider.get_snippets()
             if 'loop' in i.label.lower() or 'compact' in i.label.lower()
             or 'cicl' in i.label.lower()),
            None
        )
        assert compact is not None
        # Deve contenere la struttura del formato compatto
        text = compact.insert_text
        assert '100' in text  # percentuale massima del pattern

    def test_snippet_compact_contiene_n_reps(self, provider):
        compact_items = [i for i in provider.get_snippets()
                         if 'loop' in i.label.lower() or 'compact' in i.label.lower()
                         or 'cicl' in i.label.lower()]
        assert len(compact_items) > 0
        # Almeno uno deve avere n_reps
        assert any('reps' in i.insert_text or 'rep' in i.insert_text.lower()
                   or any(c.isdigit() for c in i.insert_text)
                   for i in compact_items)

    def test_snippet_compact_format_e_snippet(self, provider):
        compact_items = [i for i in provider.get_snippets()
                         if 'loop' in i.label.lower() or 'compact' in i.label.lower()
                         or 'cicl' in i.label.lower()]
        for item in compact_items:
            assert item.insert_text_format == InsertTextFormat.Snippet


# =============================================================================
# 5. COMPACT CON INTERP E TIME_DIST
# =============================================================================

class TestGetSnippetsCompactAdvanced:
    """Snippet compact con interpolazione e distribuzione temporale."""

    def test_contiene_snippet_compact_con_interp(self, provider):
        """Deve esserci uno snippet compact con tipo interpolazione."""
        labels = [i.label for i in provider.get_snippets()]
        assert any(
            ('loop' in l.lower() or 'cicl' in l.lower()) and
            ('cubic' in l.lower() or 'step' in l.lower() or 'interp' in l.lower())
            for l in labels
        ) or any(
            'cubic' in i.insert_text and
            ('loop' in i.label.lower() or 'cicl' in i.label.lower())
            for i in provider.get_snippets()
        )

    def test_contiene_snippet_compact_con_time_dist_string(self, provider):
        """Deve esserci uno snippet compact con distribuzione temporale."""
        items = provider.get_snippets()
        has_time_dist = any(
            'exponential' in i.insert_text or
            'logarithmic' in i.insert_text or
            'geometric' in i.insert_text
            for i in items
        )
        assert has_time_dist

    def test_contiene_snippet_compact_con_time_dist_geometric(self, provider):
        items = provider.get_snippets()
        has_geometric = any('geometric' in i.insert_text for i in items)
        assert has_geometric

    def test_contiene_snippet_compact_con_time_dist_power(self, provider):
        items = provider.get_snippets()
        has_power = any('power' in i.insert_text for i in items)
        assert has_power


# =============================================================================
# 6. FORMATO MISTO
# =============================================================================

class TestGetSnippetsMixed:
    """Snippet per formato misto breakpoints + compact."""

    def test_contiene_snippet_misto(self, provider):
        labels = [i.label for i in provider.get_snippets()]
        assert any('mist' in l.lower() or 'mixed' in l.lower()
                   or 'hybrid' in l.lower() for l in labels)

    def test_snippet_misto_ha_breakpoints_standard_e_compact(self, provider):
        mixed = next(
            (i for i in provider.get_snippets()
             if 'mist' in i.label.lower() or 'mixed' in i.label.lower()
             or 'hybrid' in i.label.lower()),
            None
        )
        assert mixed is not None
        text = mixed.insert_text
        # Deve contenere sia breakpoints standard che compact
        assert '100' in text  # parte compact


# =============================================================================
# 7. STRUTTURA CompletionItem
# =============================================================================

class TestSnippetItemStructure:
    """Ogni snippet deve avere label, insert_text, documentation corretti."""

    def test_tutti_items_hanno_label_non_vuoto(self, provider):
        for item in provider.get_snippets():
            assert item.label and len(item.label) > 0

    def test_tutti_items_hanno_insert_text_non_vuoto(self, provider):
        for item in provider.get_snippets():
            assert item.insert_text and len(item.insert_text) > 0

    def test_tutti_items_hanno_documentazione(self, provider):
        for item in provider.get_snippets():
            assert item.documentation is not None

    def test_tutti_items_format_e_snippet(self, provider):
        for item in provider.get_snippets():
            assert item.insert_text_format == InsertTextFormat.Snippet

    def test_tutti_items_kind_e_value(self, provider):
        """Kind deve essere Value (enum 12) per indicare valori di parametri."""
        for item in provider.get_snippets():
            assert item.kind == CompletionItemKind.Value

    def test_nessun_duplicato_label(self, provider):
        labels = [i.label for i in provider.get_snippets()]
        assert len(labels) == len(set(labels))


# =============================================================================
# 8. get_snippets_for_parameter()
# =============================================================================

class TestGetSnippetsForParameter:
    """Lookup snippet per yaml_path specifico del parametro."""

    def test_ritorna_lista_per_param_noto(self, provider):
        result = provider.get_snippets_for_parameter('density')
        assert isinstance(result, list)
        assert len(result) > 0

    def test_stessi_snippet_per_qualsiasi_param_numerico(self, provider):
        """Tutti i parametri numerici ottengono gli stessi snippet."""
        d = provider.get_snippets_for_parameter('density')
        v = provider.get_snippets_for_parameter('volume')
        assert len(d) == len(v)

    def test_yaml_path_annidato(self, provider):
        """grain.duration e' un parametro valido."""
        result = provider.get_snippets_for_parameter('grain.duration')
        assert len(result) > 0

    def test_yaml_path_sconosciuto_ritorna_lista_vuota(self, provider):
        result = provider.get_snippets_for_parameter('parametro_inesistente_xyz')
        assert result == []

    def test_yaml_path_interno_ritorna_lista_vuota(self, provider):
        """Parametri con yaml_path che inizia con '_' non ottengono snippet."""
        result = provider.get_snippets_for_parameter('_internal_calc_')
        assert result == []

    def test_parametro_non_smart_ritorna_lista_vuota(self, provider):
        """grain_envelope (is_smart=False) non ottiene snippet envelope."""
        result = provider.get_snippets_for_parameter('grain.envelope')
        assert result == []

    def test_yaml_path_vuoto_ritorna_lista_vuota(self, provider):
        result = provider.get_snippets_for_parameter('')
        assert result == []


# =============================================================================
# 9. EDGE CASES
# =============================================================================

class TestEdgeCases:

    def test_get_snippets_idempotente(self, provider):
        """Chiamate multiple a get_snippets() ritornano lo stesso numero di item."""
        a = provider.get_snippets()
        b = provider.get_snippets()
        assert len(a) == len(b)

    def test_bridge_vuoto_get_snippets_ritorna_comunque(self):
        """Con bridge vuoto get_snippets() ritorna gli snippet generici."""
        b = SchemaBridge({'specs': [], 'bounds': {}})
        p = EnvelopeSnippetProvider(b)
        result = p.get_snippets()
        assert isinstance(result, list)
        assert len(result) > 0

    def test_bridge_vuoto_for_parameter_ritorna_vuoto(self):
        b = SchemaBridge({'specs': [], 'bounds': {}})
        p = EnvelopeSnippetProvider(b)
        assert p.get_snippets_for_parameter('density') == []


# =============================================================================
# Snippet con end_time dinamico
# =============================================================================

class TestGetSnippetsWithContext:
    """
    get_snippets_with_end_time(end_time) genera snippet con end_time
    personalizzato al posto del default hardcoded 10.0.

    Se time_mode == 'normalized' -> end_time = 1.0
    Altrimenti -> end_time = onset dello stream corrente
    """

    def test_ritorna_lista(self, provider):
        result = provider.get_snippets_with_end_time(5.0)
        assert isinstance(result, list)
        assert len(result) > 0

    def test_end_time_personalizzato_compare_nel_compact(self, provider):
        result = provider.get_snippets_with_end_time(7.5)
        compact = next(
            (i for i in result
             if 'loop' in i.label.lower() or 'compact' in i.label.lower()),
            None
        )
        assert compact is not None
        assert '7.5' in compact.insert_text

    def test_end_time_normalized_usa_1_punto_0(self, provider):
        result = provider.get_snippets_with_end_time(1.0)
        compact = next(
            (i for i in result
             if 'loop' in i.label.lower() or 'compact' in i.label.lower()),
            None
        )
        assert compact is not None
        assert '1.0' in compact.insert_text

    def test_end_time_onset_grande(self, provider):
        result = provider.get_snippets_with_end_time(120.0)
        compact = next(
            (i for i in result
             if 'loop' in i.label.lower() or 'compact' in i.label.lower()),
            None
        )
        assert compact is not None
        assert '120.0' in compact.insert_text

    def test_tutti_items_sono_completionitem(self, provider):
        for item in provider.get_snippets_with_end_time(5.0):
            assert isinstance(item, CompletionItem)

    def test_stesso_numero_di_snippet_rispetto_a_get_snippets(self, provider):
        base = provider.get_snippets()
        dyn = provider.get_snippets_with_end_time(5.0)
        assert len(base) == len(dyn)


# =============================================================================
# Snippet con bounds dinamici del parametro
# =============================================================================

class TestGetSnippetsWithBounds:
    """
    get_snippets_for_parameter_with_context(yaml_path, end_time) genera snippet
    con Y values derivati dai bounds del parametro specifico.

    density (0.01, 4000.0) -> y_min=0.01, y_max=4000.0
    pan (-3600.0, 3600.0)  -> y_min=-3600.0, y_max=3600.0
    volume (-120.0, 12.0)  -> y_min=-120.0, y_max=12.0
    """

    @pytest.fixture
    def rich_bridge(self):
        raw = {
            'specs': [
                {'name':'density','yaml_path':'density','default':None,'is_smart':True,
                 'exclusive_group':'density_mode','group_priority':2,
                 'range_path':None,'dephase_key':None},
                {'name':'pan','yaml_path':'pan','default':0.0,'is_smart':True,
                 'exclusive_group':None,'group_priority':99,
                 'range_path':None,'dephase_key':'pan'},
                {'name':'volume','yaml_path':'volume','default':-6.0,'is_smart':True,
                 'exclusive_group':None,'group_priority':99,
                 'range_path':None,'dephase_key':'volume'},
            ],
            'bounds': {
                'density': {'min_val':0.01,'max_val':4000.0,'min_range':0.0,
                            'max_range':0.0,'default_jitter':0.0,'variation_mode':'additive'},
                'pan':     {'min_val':-3600.0,'max_val':3600.0,'min_range':0.0,
                            'max_range':360.0,'default_jitter':30.0,'variation_mode':'additive'},
                'volume':  {'min_val':-120.0,'max_val':12.0,'min_range':0.0,
                            'max_range':24.0,'default_jitter':3.0,'variation_mode':'additive'},
            }
        }
        return SchemaBridge(raw)

    def test_metodo_esiste(self, rich_bridge):
        p = EnvelopeSnippetProvider(rich_bridge)
        assert hasattr(p, 'get_snippets_for_parameter_with_context')

    def test_density_y_max_e_4000(self, rich_bridge):
        """density ha max=4000.0 -> y_max nello snippet deve essere 4000.0."""
        p = EnvelopeSnippetProvider(rich_bridge)
        items = p.get_snippets_for_parameter_with_context('density', 10.0)
        # Cerca lo snippet lineare 2 punti
        linear = next(i for i in items if '2 punti' in i.label)
        assert '4000.0' in linear.insert_text

    def test_density_y_min_e_0_01(self, rich_bridge):
        """density ha min=0.01 -> y_min nello snippet deve essere 0.01."""
        p = EnvelopeSnippetProvider(rich_bridge)
        items = p.get_snippets_for_parameter_with_context('density', 10.0)
        linear = next(i for i in items if '2 punti' in i.label)
        assert '0.01' in linear.insert_text

    def test_pan_bounds_negativi(self, rich_bridge):
        """pan ha min=-3600.0 -> i bounds negativi devono comparire."""
        p = EnvelopeSnippetProvider(rich_bridge)
        items = p.get_snippets_for_parameter_with_context('pan', 10.0)
        linear = next(i for i in items if '2 punti' in i.label)
        assert '-3600.0' in linear.insert_text or '3600.0' in linear.insert_text

    def test_volume_y_max_e_12(self, rich_bridge):
        p = EnvelopeSnippetProvider(rich_bridge)
        items = p.get_snippets_for_parameter_with_context('volume', 10.0)
        linear = next(i for i in items if '2 punti' in i.label)
        assert '12.0' in linear.insert_text or '-120.0' in linear.insert_text

    def test_compact_usa_bounds_come_pattern(self, rich_bridge):
        """Nel formato compact i valori [0, y] e [100, y] usano min e max."""
        p = EnvelopeSnippetProvider(rich_bridge)
        items = p.get_snippets_for_parameter_with_context('density', 10.0)
        compact = next(i for i in items if 'loop' in i.label.lower() and 'base' in i.label.lower())
        assert '0.01' in compact.insert_text or '4000.0' in compact.insert_text

    def test_stesso_numero_di_snippet(self, rich_bridge):
        p = EnvelopeSnippetProvider(rich_bridge)
        base = p.get_snippets()
        ctx = p.get_snippets_for_parameter_with_context('density', 10.0)
        assert len(base) == len(ctx)

    def test_parametro_senza_bounds_usa_defaults(self, rich_bridge):
        """Parametro senza bounds -> usa 0.0 e 1.0 come y default."""
        raw = {'specs': [{'name':'x','yaml_path':'x','default':0.0,'is_smart':True,
                           'exclusive_group':None,'group_priority':99,
                           'range_path':None,'dephase_key':None}],
               'bounds': {}}
        b = SchemaBridge(raw)
        p = EnvelopeSnippetProvider(b)
        items = p.get_snippets_for_parameter_with_context('x', 10.0)
        assert len(items) > 0


# =============================================================================
# Punto medio a meta' dell'end_time negli snippet a 3 punti
# =============================================================================

class TestSnippet3PuntiMidpoint:
    """
    Nello snippet 'envelope lineare (3 punti)', il punto di mezzo deve avere
    X = end_time / 2. Deve essere calcolato dinamicamente dall'end_time.
    """

    def test_midpoint_x_e_meta_di_end_time(self, provider):
        items = provider.get_snippets_with_end_time(10.0)
        three = next(i for i in items if '3 punti' in i.label)
        # end_time=10.0 -> midpoint=5.0
        assert '5.0' in three.insert_text

    def test_midpoint_x_con_end_time_30(self, provider):
        items = provider.get_snippets_with_end_time(30.0)
        three = next(i for i in items if '3 punti' in i.label)
        assert '15.0' in three.insert_text

    def test_midpoint_x_con_end_time_1(self, provider):
        """Normalized: end_time=1.0 -> midpoint=0.5."""
        items = provider.get_snippets_with_end_time(1.0)
        three = next(i for i in items if '3 punti' in i.label)
        assert '0.5' in three.insert_text

    def test_snippet_2_punti_non_ha_midpoint(self, provider):
        """Lo snippet a 2 punti non deve cambiare per via del midpoint."""
        items = provider.get_snippets_with_end_time(10.0)
        two = next(i for i in items if '2 punti' in i.label)
        # Deve avere solo due righe di breakpoint, non tre
        assert two.insert_text.count('${') == 4  # 2 punti * 2 tab stops (x,y)


# =============================================================================
# Sintassi single-line per tutti gli snippet
# =============================================================================

class TestSingleLineSyntax:
    """
    Tutti gli snippet envelope devono usare sintassi YAML flow (inline),
    senza newline nell'insert_text.

    - Dict (cubic, step): {type: X, points: [[t,v], ...]}
    - Compact loop singolo (diretto): [[[0, v], [100, v]], et, n]   (3 bracket)
    - Misto loop+bp / loop multipli:  [[loop_spec, ...], [t,v], ...]  (lista esterna)
    """

    def test_nessuno_snippet_ha_newline(self, provider):
        for item in provider.get_snippets():
            assert '\n' not in item.insert_text, (
                f"Snippet '{item.label}' contiene newline: {item.insert_text!r}"
            )

    def test_dict_cubic_usa_flow_syntax(self, provider):
        cubic = next(
            (i for i in provider.get_snippets()
             if 'cubic' in i.label.lower() and 'loop' not in i.label.lower()),
            None
        )
        assert cubic is not None
        text = cubic.insert_text
        assert '{' in text
        assert 'type' in text
        assert 'points' in text

    def test_dict_step_usa_flow_syntax(self, provider):
        step = next(
            (i for i in provider.get_snippets()
             if 'step' in i.label.lower() and 'loop' not in i.label.lower()),
            None
        )
        assert step is not None
        text = step.insert_text
        assert '{' in text
        assert 'type' in text
        assert 'points' in text

    def test_compact_loop_base_usa_formato_diretto(self, provider):
        """Loop singolo: formato diretto [[[...], et, n]] senza lista esterna."""
        base = next(
            (i for i in provider.get_snippets()
             if 'loop' in i.label.lower() and 'base' in i.label.lower()),
            None
        )
        assert base is not None
        assert base.insert_text.strip().startswith('[[['), (
            f"Loop base non usa formato diretto: {base.insert_text!r}"
        )

    def test_tutti_gli_snippet_senza_newline(self, provider):
        for item in provider.get_snippets_with_end_time(5.0):
            assert '\n' not in item.insert_text, f"'{item.label}' ha newline"


# =============================================================================
# Nuovi snippet: formato misto esteso
# =============================================================================

class TestNuoviSnippetMisti:
    """
    Nuovi snippet per formati misti avanzati:
    - loop → breakpoints standard
    - loop multipli in sequenza
    """

    def test_contiene_snippet_loop_poi_breakpoints(self, provider):
        labels = [i.label for i in provider.get_snippets()]
        assert any(
            'loop' in l.lower() and ('breakpoint' in l.lower() or '→' in l)
            for l in labels
        )

    def test_contiene_snippet_loop_multipli(self, provider):
        labels = [i.label for i in provider.get_snippets()]
        assert any('multipli' in l.lower() for l in labels)

    def test_loop_poi_breakpoints_struttura_corretta(self, provider):
        """[[loop_spec], [t,v], [t,v]] — lista esterna con compact + breakpoints."""
        item = next(
            (i for i in provider.get_snippets()
             if 'loop' in i.label.lower() and '→' in i.label),
            None
        )
        assert item is not None
        text = item.insert_text.strip()
        # lista esterna: [[[[...], et, n], [t, v], [t, v]]
        assert text.startswith('[[[['), (
            f"loop→bp non inizia con '[[[[': {item.insert_text!r}"
        )
        assert '\n' not in item.insert_text

    def test_loop_multipli_struttura_corretta(self, provider):
        """[[loop_spec1], [loop_spec2]] — due compact spec in sequenza."""
        item = next(
            (i for i in provider.get_snippets()
             if 'multipli' in i.label.lower()),
            None
        )
        assert item is not None
        text = item.insert_text.strip()
        assert text.startswith('[[[['), (
            f"loop multipli non inizia con '[[[[': {item.insert_text!r}"
        )
        assert '\n' not in item.insert_text

    def test_nuovi_snippet_sono_completionitem(self, provider):
        new_labels = [
            l for l in [i.label for i in provider.get_snippets()]
            if 'multipli' in l.lower() or '→' in l
        ]
        assert len(new_labels) == 2
        for item in provider.get_snippets():
            if item.label in new_labels:
                assert isinstance(item, CompletionItem)
                assert item.insert_text_format == InsertTextFormat.Snippet
