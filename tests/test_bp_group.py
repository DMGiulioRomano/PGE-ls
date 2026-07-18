# tests/test_bp_group.py
"""
Test per il supporto BP group [points, interp] (PGE issue #64 / PR #165,
PGE-ls issue #32).

Copre:
  1. envelope_shapes: riconoscimento strutturale (is_bp_group e affini),
     regole discriminanti identiche a EnvelopeBuilder in PGE.
  2. DiagnosticProvider: interp invalido, gruppo con meno di 2 punti,
     punti malformati; bounds Y dentro i gruppi (forma diretta, mista
     inline e block-style).
  3. EnvelopeSnippetProvider: snippet per la forma BP group.
  4. HoverProvider: hover sull'interp di un BP group.
  5. server.py: round-trip parse (_parse_envelope_value / _try_parse_mixed).
  6. envelope_gui: emit (to_bp_group / to_misto_format) e stabilita'
     YAML -> parse -> emit.
"""

import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest
from lsprotocol.types import DiagnosticSeverity

from granular_ls.envelope_shapes import (
    VALID_INTERP_TYPES,
    is_bp_group,
    is_bp_group_candidate,
    is_loop_block,
)
from granular_ls.schema_bridge import SchemaBridge
from granular_ls.providers.diagnostic_provider import DiagnosticProvider
from granular_ls.providers.hover_provider import HoverProvider
from granular_ls.yaml_analyzer import YamlAnalyzer

from tests.test_diagnostic_provider import bridge, make_raw_spec, make_raw_bounds  # noqa: F401

import server as srv
from envelope_gui import to_bp_group, to_misto_format


# =============================================================================
# 1. envelope_shapes - riconoscimento strutturale
# =============================================================================

class TestIsBPGroup:

    def test_gruppo_valido_due_punti(self):
        assert is_bp_group([[[0.0, 0], [0.5, 30]], 'cubic'])

    def test_gruppo_valido_con_punto_3tuple(self):
        assert is_bp_group([[[0.0, 0], [0.2, 12, 'step'], [0.4, 8]], 'cubic'])

    def test_gruppo_interp_qualunque_stringa(self):
        # Il check e' strutturale: interp invalido riconosciuto comunque
        # come gruppo (l'errore preciso arriva dalla diagnostica).
        assert is_bp_group([[[0.0, 0], [1.0, 5]], 'sinusoidal'])

    def test_gruppo_vuoto_strutturalmente_valido(self):
        # Come in PGE: points vuoto passa il check strutturale,
        # il vincolo "almeno 2 punti" e' della validazione.
        assert is_bp_group([[], 'cubic'])

    def test_breakpoint_nudo_non_e_gruppo(self):
        assert not is_bp_group([0.5, 30])

    def test_3tuple_non_e_gruppo(self):
        assert not is_bp_group([0.5, 30, 'cubic'])

    def test_loop_block_non_e_gruppo(self):
        assert not is_bp_group([[[0, 8], [50, 18], [100, 8]], 0.7, 4, 'linear'])

    def test_legacy_marker_non_e_gruppo(self):
        # [[t, v], 'marker']: elem[0] e' UN punto, non lista di punti.
        assert not is_bp_group([[0.5, 30], 'marker'])

    def test_punti_malformati_non_gruppo_strutturale(self):
        assert not is_bp_group([[[0.0], [1.0, 5]], 'cubic'])
        assert not is_bp_group([[[0.0, 'x'], [1.0, 5]], 'cubic'])

    def test_non_liste(self):
        assert not is_bp_group('cubic')
        assert not is_bp_group({'type': 'cubic'})
        assert not is_bp_group([['cubic'], [0, 1]])


class TestIsBPGroupCandidate:

    def test_gruppo_con_punti_malformati_e_candidato(self):
        assert is_bp_group_candidate([[[0.0], [1.0, 5]], 'cubic'])

    def test_gruppo_vuoto_e_candidato(self):
        assert is_bp_group_candidate([[], 'cubic'])

    def test_legacy_marker_non_candidato(self):
        assert not is_bp_group_candidate([[0.5, 30], 'marker'])

    def test_loop_block_non_candidato(self):
        assert not is_bp_group_candidate(
            [[[0, 8], [50, 18], [100, 8]], 0.7, 4, 'linear'])


class TestIsLoopBlock:

    def test_loop_block_base(self):
        assert is_loop_block([[[0, 8], [50, 18], [100, 8]], 0.7, 4])

    def test_loop_block_con_interp(self):
        assert is_loop_block([[[0, 8], [100, 8]], 0.7, 4, 'linear'])

    def test_bp_group_non_e_loop_block(self):
        assert not is_loop_block([[[0.0, 0], [0.5, 30]], 'cubic'])


# =============================================================================
# 2. DiagnosticProvider - validazione BP group
# =============================================================================

class TestBPGroupDiagnostics:

    def _msgs(self, bridge, doc):
        provider = DiagnosticProvider(bridge)
        return [d for d in provider.get_diagnostics(doc)
                if 'BP group' in d.message]

    DOC_HEADER = (
        "streams:\n"
        "  - id: s1\n"
        "    sample: v.wav\n"
        "    duration: 10.0\n"
        "    onset: 0.0\n"
    )

    def test_gruppo_valido_nessuna_diagnostica(self, bridge):
        doc = self.DOC_HEADER + \
            "    density: [[[0.0, 10], [0.5, 30], [1.0, 5]], 'cubic']\n"
        assert self._msgs(bridge, doc) == []

    def test_interp_invalido_errore(self, bridge):
        doc = self.DOC_HEADER + \
            "    density: [[[0.0, 10], [1.0, 5]], 'sinusoidal']\n"
        msgs = self._msgs(bridge, doc)
        assert len(msgs) == 1
        assert msgs[0].severity == DiagnosticSeverity.Error
        assert 'sinusoidal' in msgs[0].message
        assert 'linear' in msgs[0].message
        assert 'cubic' in msgs[0].message
        assert 'step' in msgs[0].message

    def test_meno_di_due_punti_errore(self, bridge):
        doc = self.DOC_HEADER + \
            "    density: [[[0.5, 30]], 'cubic']\n"
        msgs = self._msgs(bridge, doc)
        assert len(msgs) == 1
        assert msgs[0].severity == DiagnosticSeverity.Error
        assert 'almeno 2 punti' in msgs[0].message

    def test_gruppo_vuoto_errore(self, bridge):
        doc = self.DOC_HEADER + \
            "    density: [[], 'cubic']\n"
        msgs = self._msgs(bridge, doc)
        assert len(msgs) == 1
        assert 'almeno 2 punti' in msgs[0].message

    def test_punti_malformati_errore(self, bridge):
        doc = self.DOC_HEADER + \
            "    density: [[[0.0, 10], [1.0]], 'cubic']\n"
        msgs = self._msgs(bridge, doc)
        assert len(msgs) == 1
        assert msgs[0].severity == DiagnosticSeverity.Error
        assert 'malformat' in msgs[0].message

    def test_misto_inline_con_gruppo_invalido(self, bridge):
        doc = self.DOC_HEADER + (
            "    density: [[[[0.0, 0], [0.2, 12]], 'cubico'],"
            " [[[0, 8], [50, 18], [100, 8]], 0.7, 4, 'linear'],"
            " [[[0.75, 6], [1.0, 0]], 'step']]\n"
        )
        msgs = self._msgs(bridge, doc)
        assert len(msgs) == 1
        assert 'cubico' in msgs[0].message

    def test_block_style_item_gruppo_invalido(self, bridge):
        doc = self.DOC_HEADER + (
            "    density:\n"
            "      - [[[0.0, 0], [0.2, 12]], 'cubico']\n"
            "      - [[[0.75, 6], [1.0, 0]], 'step']\n"
        )
        msgs = self._msgs(bridge, doc)
        assert len(msgs) == 1
        assert 'cubico' in msgs[0].message

    def test_loop_block_non_segnalato(self, bridge):
        doc = self.DOC_HEADER + \
            "    density: [[[0, 8], [50, 18], [100, 8]], 0.7, 4, 'linear']\n"
        assert self._msgs(bridge, doc) == []

    def test_breakpoints_standard_non_segnalati(self, bridge):
        doc = self.DOC_HEADER + \
            "    density: [[0.0, 10], [1.0, 5]]\n"
        assert self._msgs(bridge, doc) == []

    def test_interp_non_quotato_yaml(self, bridge):
        # YAML flow ammette stringhe non quotate: la diagnostica deve
        # riconoscerle (parsing via yaml.safe_load, non ast).
        doc = self.DOC_HEADER + \
            "    density: [[[0.0, 10], [1.0, 5]], cubico]\n"
        msgs = self._msgs(bridge, doc)
        assert len(msgs) == 1


class TestBPGroupEnvelopeBounds:

    def _bounds_msgs(self, bridge, doc):
        provider = DiagnosticProvider(bridge)
        return [d for d in provider.get_diagnostics(doc)
                if 'fuori dai bounds' in d.message]

    DOC_HEADER = TestBPGroupDiagnostics.DOC_HEADER

    def test_forma_diretta_y_fuori_bounds(self, bridge):
        # density max 4000
        doc = self.DOC_HEADER + \
            "    density: [[[0.0, 10], [1.0, 9999]], 'cubic']\n"
        msgs = self._bounds_msgs(bridge, doc)
        assert len(msgs) == 1
        assert '9999' in msgs[0].message

    def test_misto_inline_y_fuori_bounds_dentro_gruppo(self, bridge):
        doc = self.DOC_HEADER + (
            "    density: [[[[0.0, 1], [0.2, 9999]], 'cubic'],"
            " [[[0, 8], [100, 8]], 0.7, 4, 'linear']]\n"
        )
        msgs = self._bounds_msgs(bridge, doc)
        assert len(msgs) == 1
        assert '9999' in msgs[0].message

    def test_misto_inline_y_fuori_bounds_dentro_loop_block(self, bridge):
        doc = self.DOC_HEADER + (
            "    density: [[[[0.0, 1], [0.2, 12]], 'cubic'],"
            " [[[0, 8], [100, 9999]], 0.7, 4, 'linear']]\n"
        )
        msgs = self._bounds_msgs(bridge, doc)
        assert len(msgs) == 1

    def test_forma_diretta_y_dentro_bounds_ok(self, bridge):
        doc = self.DOC_HEADER + \
            "    density: [[[0.0, 10], [1.0, 20]], 'cubic']\n"
        assert self._bounds_msgs(bridge, doc) == []


# =============================================================================
# 3. Snippet
# =============================================================================

class TestBPGroupSnippets:

    def test_snippet_bp_group_presente(self, bridge):
        from granular_ls.envelope_snippets import EnvelopeSnippetProvider
        provider = EnvelopeSnippetProvider(bridge)
        items = provider.get_snippets_for_parameter_with_context(
            'density', 10.0)
        labels = [i.label for i in items]
        bp_group_labels = [l for l in labels if 'BP group' in l]
        assert len(bp_group_labels) >= 2  # forma diretta + zone miste

    def test_snippet_bp_group_inserisce_forma(self, bridge):
        from granular_ls.envelope_snippets import EnvelopeSnippetProvider
        provider = EnvelopeSnippetProvider(bridge)
        items = provider.get_snippets_for_parameter_with_context(
            'density', 10.0)
        group_items = [i for i in items if 'BP group' in i.label]
        assert any('cubic' in i.insert_text for i in group_items)


# =============================================================================
# 4. Hover
# =============================================================================

class TestBPGroupHover:

    def _hover(self, bridge, doc, line, character):
        from granular_ls.yaml_analyzer import YamlContext
        provider = HoverProvider(bridge)
        base = YamlAnalyzer.get_context(doc, line, character)
        # server.py forza context_type='key' con la parola intera per hover
        # (YamlContext e' frozen: se ne costruisce uno nuovo, come fa
        # handle_hover).
        word = YamlAnalyzer.get_word_at_cursor(doc, line, character)
        context = YamlContext(
            context_type='key',
            current_text=word,
            parent_path=base.parent_path,
            indent_level=base.indent_level,
            in_stream_element=base.in_stream_element,
            cursor_line=line,
        )
        return provider.get_hover(context, doc)

    def test_hover_su_interp_di_bp_group(self, bridge):
        doc = (
            "streams:\n"
            "  - density: [[[0.0, 10], [1.0, 5]], 'cubic']\n"
        )
        line1 = doc.split('\n')[1]
        char = line1.index("'cubic'") + 2
        hover = self._hover(bridge, doc, 1, char)
        assert hover is not None
        assert 'macrozona' in hover.contents.value

    def test_hover_su_cubic_fuori_da_bp_group_nessun_hover_gruppo(self, bridge):
        # 'cubic' in un loop block: niente hover BP group.
        doc = (
            "streams:\n"
            "  - density: [[[0, 8], [100, 8]], 0.7, 4, 'cubic']\n"
        )
        line1 = doc.split('\n')[1]
        char = line1.index("'cubic'") + 2
        hover = self._hover(bridge, doc, 1, char)
        assert hover is None or 'macrozona' not in hover.contents.value


# =============================================================================
# 5. server.py - round-trip parse
# =============================================================================

class TestParseEnvelopeValueBPGroup:

    def test_forma_diretta(self):
        result = srv._parse_envelope_value(
            "[[[0.0, 0], [0.5, 30], [1.0, 5]], 'cubic']")
        assert result is not None
        assert result['struttura'] == 'breakpoints'
        assert result['interp'] == 'cubic'
        assert result.get('bp_group') is True
        assert result['points'] == [[0.0, 0.0], [0.5, 30.0], [1.0, 5.0]]

    def test_forma_diretta_linear(self):
        result = srv._parse_envelope_value(
            "[[[0.0, 0], [1.0, 5]], 'linear']")
        assert result is not None
        assert result['interp'] == 'linear'
        assert result.get('bp_group') is True

    def test_forma_diretta_interp_invalido_none(self):
        assert srv._parse_envelope_value(
            "[[[0.0, 0], [1.0, 5]], 'sinusoidal']") is None

    def test_misto_con_gruppi(self):
        result = srv._parse_envelope_value(
            "[[[[0.0, 0], [0.2, 12], [0.4, 8]], 'cubic'],"
            " [[[0, 8], [50, 18], [100, 8]], 0.7, 4, 'linear'],"
            " [[[0.75, 6], [0.9, 6], [1.0, 0]], 'step']]")
        assert result is not None
        assert result['struttura'] == 'misto'
        segs = result['segments']
        assert len(segs) == 3
        assert segs[0]['type'] == 'breakpoints'
        assert segs[0]['interp'] == 'cubic'
        assert segs[0].get('bp_group') is True
        assert segs[1]['type'] == 'loop'
        assert segs[2]['type'] == 'breakpoints'
        assert segs[2]['interp'] == 'step'

    def test_misto_bp_nudi_e_gruppo(self):
        result = srv._parse_envelope_value(
            "[[0.0, 1], [0.2, 3],"
            " [[[0.5, 6], [1.0, 0]], 'step']]")
        assert result is not None
        assert result['struttura'] == 'misto'
        segs = result['segments']
        assert len(segs) == 2
        assert segs[0]['type'] == 'breakpoints'
        assert segs[0].get('bp_group') is not True
        assert segs[1].get('bp_group') is True

    def test_misto_solo_gruppi_senza_loop(self):
        # Due zone BP group senza loop block: comunque misto valido.
        result = srv._parse_envelope_value(
            "[[[[0.0, 0], [0.4, 8]], 'cubic'],"
            " [[[0.75, 6], [1.0, 0]], 'step']]")
        assert result is not None
        assert result['struttura'] == 'misto'
        assert len(result['segments']) == 2


# =============================================================================
# 6. envelope_gui - emit e stabilita' round-trip
# =============================================================================

class TestToBPGroup:

    def test_emit_forma_diretta(self):
        out = to_bp_group([(0.0, 0.0), (0.5, 30.0), (1.0, 5.0)], 'cubic')
        assert out == '[[[0.0, 0.0], [0.5, 30.0], [1.0, 5.0]], "cubic"]'

    def test_emit_ordina_punti(self):
        out = to_bp_group([(1.0, 5.0), (0.0, 0.0)], 'step')
        assert out == '[[[0.0, 0.0], [1.0, 5.0]], "step"]'


class TestMistoEmitBPGroup:

    def test_segmento_bp_group_emesso_come_gruppo(self):
        segments = [
            {'type': 'breakpoints', 'interp': 'cubic', 'bp_group': True,
             'points': [(0.0, 0.0), (0.4, 8.0)], 'end_time': 0.4},
            {'type': 'loop', 'loop_dist': 'base', 'n_reps': 4,
             'abs_start': 0.4, 'duration': 0.3,
             'points': [(0.0, 8.0), (100.0, 8.0)]},
        ]
        out = to_misto_format(segments)
        assert '[[[0.0, 0.0], [0.4, 8.0]], "cubic"]' in out

    def test_segmento_interp_non_lineare_emesso_come_gruppo(self):
        # Anche senza flag bp_group: l'interp non-lineare di una zona BP
        # in un misto e' esprimibile SOLO come BP group.
        segments = [
            {'type': 'breakpoints', 'interp': 'step',
             'points': [(0.0, 0.0), (0.4, 8.0)], 'end_time': 0.4},
            {'type': 'loop', 'loop_dist': 'base', 'n_reps': 4,
             'abs_start': 0.4, 'duration': 0.3,
             'points': [(0.0, 8.0), (100.0, 8.0)]},
        ]
        out = to_misto_format(segments)
        assert '"step"]' in out

    def test_segmento_linear_senza_flag_resta_bp_nudi(self):
        segments = [
            {'type': 'breakpoints', 'interp': 'linear',
             'points': [(0.0, 0.0), (0.4, 8.0)], 'end_time': 0.4},
            {'type': 'loop', 'loop_dist': 'base', 'n_reps': 4,
             'abs_start': 0.4, 'duration': 0.3,
             'points': [(0.0, 8.0), (100.0, 8.0)]},
        ]
        out = to_misto_format(segments)
        assert out.startswith('[[0.0, 0.0], [0.4, 8.0],')

    def test_segmento_linear_con_flag_resta_gruppo(self):
        segments = [
            {'type': 'breakpoints', 'interp': 'linear', 'bp_group': True,
             'points': [(0.0, 0.0), (0.4, 8.0)], 'end_time': 0.4},
            {'type': 'loop', 'loop_dist': 'base', 'n_reps': 4,
             'abs_start': 0.4, 'duration': 0.3,
             'points': [(0.0, 8.0), (100.0, 8.0)]},
        ]
        out = to_misto_format(segments)
        assert '[[[0.0, 0.0], [0.4, 8.0]], "linear"]' in out


class TestRoundTripStability:

    def _roundtrip_mixed(self, value_str):
        parsed = srv._parse_envelope_value(value_str)
        assert parsed is not None and parsed['struttura'] == 'misto'
        return to_misto_format(parsed['segments'])

    def test_parse_emit_parse_stabile(self):
        # Stabilita' semantica: emit -> parse riproduce la stessa struttura.
        # (Solo zone BP: la fedelta' del pattern % dei loop block e' una
        # questione pre-esistente separata dal BP group.)
        src = ("[[[[0.0, 0.0], [0.2, 12.0], [0.4, 8.0]], 'cubic'], "
               "[0.5, 3.0], [0.6, 4.0], "
               "[[[0.75, 6.0], [1.0, 0.0]], 'step']]")
        emitted = self._roundtrip_mixed(src)
        reparsed = srv._parse_envelope_value(emitted)
        assert reparsed is not None
        assert reparsed['struttura'] == 'misto'
        segs = reparsed['segments']
        assert [s['type'] for s in segs] == [
            'breakpoints', 'breakpoints', 'breakpoints']
        assert segs[0]['interp'] == 'cubic'
        assert segs[0].get('bp_group') is True
        assert segs[1].get('bp_group') is not True
        assert segs[2]['interp'] == 'step'
        # Secondo giro identico al primo (punto fisso).
        assert to_misto_format(segs) == emitted
