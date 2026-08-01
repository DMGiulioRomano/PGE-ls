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
