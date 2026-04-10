# tests/test_envelope_gui.py
"""
Test TDD per le funzioni pure di envelope_gui.py.
Solo logica di conversione — non la GUI matplotlib (non unit-testabile).
"""

import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest
from envelope_gui import _fmt, sort_points, to_breakpoints, to_compact_loop, to_dict_type, format_output


# =============================================================================
# _fmt
# =============================================================================

class TestFmt:

    def test_intero_diventa_punto_zero(self):
        assert _fmt(0) == '0.0'
        assert _fmt(10) == '10.0'
        assert _fmt(-6) == '-6.0'

    def test_float_con_decimali(self):
        assert _fmt(0.5) == '0.5'
        assert _fmt(4000.0) == '4000.0'
        assert _fmt(-120.0) == '-120.0'

    def test_arrotondamento_4_decimali(self):
        result = _fmt(0.1 + 0.2)   # 0.30000000000000004
        assert '0.3' in result


# =============================================================================
# sort_points
# =============================================================================

class TestSortPoints:

    def test_ordina_per_t(self):
        pts = [(5.0, 1.0), (0.0, 0.0), (10.0, 0.5)]
        result = sort_points(pts)
        assert result[0][0] == 0.0
        assert result[1][0] == 5.0
        assert result[2][0] == 10.0

    def test_gia_ordinati_rimangono_uguali(self):
        pts = [(0.0, 0.0), (5.0, 1.0), (10.0, 0.0)]
        assert sort_points(pts) == pts

    def test_lista_vuota(self):
        assert sort_points([]) == []

    def test_non_modifica_originale(self):
        pts = [(5.0, 1.0), (0.0, 0.0)]
        sort_points(pts)
        assert pts[0][0] == 5.0


# =============================================================================
# to_breakpoints
# =============================================================================

class TestToBreakpoints:

    def test_2_punti_formato_corretto(self):
        pts = [(0.0, 0.0), (10.0, 1.0)]
        result = to_breakpoints(pts, 10.0)
        assert result == '[[0.0, 0.0], [10.0, 1.0]]'

    def test_inizia_con_doppia_aperta(self):
        pts = [(0.0, 0.0), (10.0, 1.0)]
        assert to_breakpoints(pts, 10.0).startswith('[[')

    def test_finisce_con_doppia_chiusa(self):
        pts = [(0.0, 0.0), (10.0, 1.0)]
        assert to_breakpoints(pts, 10.0).endswith(']]')

    def test_3_punti(self):
        pts = [(0.0, 0.0), (5.0, 1.0), (10.0, 0.0)]
        result = to_breakpoints(pts, 10.0)
        assert result == '[[0.0, 0.0], [5.0, 1.0], [10.0, 0.0]]'

    def test_no_newline(self):
        pts = [(0.0, 0.0), (5.0, 0.5), (10.0, 0.0)]
        assert '\n' not in to_breakpoints(pts, 10.0)

    def test_ordina_automaticamente(self):
        pts = [(10.0, 1.0), (0.0, 0.0)]
        result = to_breakpoints(pts, 10.0)
        assert result.startswith('[[0.0')

    def test_bounds_negativi(self):
        pts = [(0.0, -120.0), (10.0, 12.0)]
        result = to_breakpoints(pts, 10.0)
        assert '-120.0' in result
        assert '12.0' in result


# =============================================================================
# to_compact_loop
# =============================================================================

class TestToCompactLoop:

    def test_2_punti_base(self):
        pts = [(0.0, 0.01), (10.0, 4000.0)]
        result = to_compact_loop(pts, 10.0, 4)
        assert result == '[[[0, 0.01], [100, 4000.0]], 10.0, 4]'

    def test_3_punti_percentuale(self):
        pts = [(0.0, 0.0), (5.0, 1.0), (10.0, 0.0)]
        result = to_compact_loop(pts, 10.0, 3)
        assert result == '[[[0, 0.0], [50, 1.0], [100, 0.0]], 10.0, 3]'

    def test_inizia_con_triple_bracket(self):
        pts = [(0.0, 0.0), (10.0, 1.0)]
        assert to_compact_loop(pts, 10.0, 4).startswith('[[[')

    def test_contiene_end_time(self):
        pts = [(0.0, 0.0), (7.5, 1.0)]
        result = to_compact_loop(pts, 7.5, 3)
        assert '7.5' in result

    def test_contiene_n_reps(self):
        pts = [(0.0, 0.0), (10.0, 1.0)]
        result = to_compact_loop(pts, 10.0, 6)
        assert result.endswith(', 6]')

    def test_no_newline(self):
        pts = [(0.0, 0.0), (10.0, 1.0)]
        assert '\n' not in to_compact_loop(pts, 10.0, 4)

    def test_percentuale_primo_punto_e_zero(self):
        pts = [(0.0, 0.0), (10.0, 1.0)]
        result = to_compact_loop(pts, 10.0, 4)
        assert '[0,' in result or '[0, ' in result

    def test_percentuale_ultimo_punto_e_100(self):
        pts = [(0.0, 0.0), (10.0, 1.0)]
        result = to_compact_loop(pts, 10.0, 4)
        assert '[100,' in result or '[100, ' in result

    def test_bounds_negativi(self):
        pts = [(0.0, -120.0), (10.0, 12.0)]
        result = to_compact_loop(pts, 10.0, 4)
        assert '-120.0' in result
        assert '12.0' in result


# =============================================================================
# to_dict_type
# =============================================================================

class TestToDictType:

    def test_cubic_inizia_con_type_cubic(self):
        pts = [(0.0, 0.0), (5.0, 1.0), (10.0, 0.0)]
        result = to_dict_type(pts, 10.0, 'cubic')
        assert result.startswith('{type: cubic, points:')

    def test_step_contiene_step(self):
        pts = [(0.0, 0.0), (10.0, 1.0)]
        result = to_dict_type(pts, 10.0, 'step')
        assert 'step' in result

    def test_contiene_tempi_assoluti(self):
        pts = [(0.0, 0.0), (5.0, 1.0), (10.0, 0.0)]
        result = to_dict_type(pts, 10.0, 'cubic')
        assert '5.0' in result
        assert '10.0' in result

    def test_no_newline(self):
        pts = [(0.0, 0.0), (10.0, 1.0)]
        assert '\n' not in to_dict_type(pts, 10.0, 'cubic')

    def test_chiude_con_graffa(self):
        pts = [(0.0, 0.0), (10.0, 1.0)]
        assert to_dict_type(pts, 10.0, 'cubic').endswith('}')


# =============================================================================
# format_output
# =============================================================================

class TestFormatOutput:

    def test_breakpoints_default(self):
        pts = [(0.0, 0.0), (10.0, 1.0)]
        result = format_output(pts, 10.0, 'breakpoints')
        assert result.startswith('[[')

    def test_compact_usa_triple_bracket(self):
        pts = [(0.0, 0.0), (10.0, 1.0)]
        result = format_output(pts, 10.0, 'compact', n_reps=4)
        assert result.startswith('[[[')

    def test_cubic_usa_dict(self):
        pts = [(0.0, 0.0), (10.0, 1.0)]
        result = format_output(pts, 10.0, 'cubic')
        assert 'cubic' in result
        assert result.startswith('{')

    def test_step_usa_dict(self):
        pts = [(0.0, 0.0), (10.0, 1.0)]
        result = format_output(pts, 10.0, 'step')
        assert 'step' in result

    def test_nessun_formato_produce_newline(self):
        pts = [(0.0, 0.0), (5.0, 1.0), (10.0, 0.0)]
        for fmt in ('breakpoints', 'compact', 'cubic', 'step'):
            result = format_output(pts, 10.0, fmt, n_reps=4)
            assert '\n' not in result, f"Formato '{fmt}' produce newline"
