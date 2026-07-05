# tests/test_pitch_units.py
"""
Test per il registry statico delle unità pitch (granular_ls/pitch_units.py).

La superficie replicata è quella unit-driven di PGE:
src/controllers/pitch_controller.py (PITCH_BLOCK_KEYS) e
src/parameters/pitch_unit.py (PITCH_UNIT_PRESETS, value_bounds).
"""

import pytest

from granular_ls.pitch_units import (
    PITCH_UNIT_PRESETS,
    PITCH_UNIT_KEYS,
    PITCH_BLOCK_EXTRA_KEYS,
    PITCH_BLOCK_KEYS,
    VOICE_PITCH_UNIT_VALUES,
    SEMITONE_LOCKED_STRATEGIES,
    PITCH_KEY_DOCS,
    PITCH_BLOCK_DOC,
    edo_bounds,
    get_unit_info,
    parse_edo_divisions,
    parse_voice_unit_value,
)


class TestRegistry:
    """Struttura del registry e whitelist del blocco pitch."""

    def test_unit_keys_complete(self):
        assert PITCH_UNIT_KEYS == (
            'semitones', 'cents', 'quarter_tone', 'eighth_tone', 'edo', 'ratio',
        )

    def test_extra_keys(self):
        assert set(PITCH_BLOCK_EXTRA_KEYS) == {'range', 'value'}

    def test_block_keys_unione_di_unita_e_modificatori(self):
        assert PITCH_BLOCK_KEYS == (
            set(PITCH_UNIT_KEYS) | set(PITCH_BLOCK_EXTRA_KEYS)
        )

    def test_preset_speculari_al_motore(self):
        # (divisions, min_val, max_val) da PitchUnit.value_bounds()
        attesi = {
            'semitones':    (12, -36.0, 36.0),
            'cents':        (1200, -3600.0, 3600.0),
            'quarter_tone': (24, -72.0, 72.0),
            'eighth_tone':  (48, -144.0, 144.0),
        }
        for key, (div, lo, hi) in attesi.items():
            info = PITCH_UNIT_PRESETS[key]
            assert info.divisions == div
            assert info.min_val == lo
            assert info.max_val == hi
            assert info.variation_mode == 'quantized'
            assert info.max_range == hi
            assert info.neutral == 0.0

    def test_ratio_speculare_al_motore(self):
        info = PITCH_UNIT_PRESETS['ratio']
        assert info.divisions is None
        assert info.min_val == 0.001
        assert info.max_val == 8.0
        assert info.max_range == 2.0
        assert info.variation_mode == 'additive'
        assert info.neutral == 1.0

    def test_voice_unit_values_senza_edo_nudo(self):
        # nelle voci `edo` nudo non e' un preset valido: serve {edo: N}
        assert 'edo' not in VOICE_PITCH_UNIT_VALUES
        assert set(VOICE_PITCH_UNIT_VALUES) == {
            'semitones', 'cents', 'quarter_tone', 'eighth_tone', 'ratio',
        }

    def test_semitone_locked(self):
        assert SEMITONE_LOCKED_STRATEGIES == {
            'chord', 'chord_progression', 'spectral'
        }


class TestEdoBounds:
    """Bounds dinamici della griglia EDO: ±3 ottave."""

    @pytest.mark.parametrize('divisions, atteso', [
        (12, (-36.0, 36.0)),
        (31, (-93.0, 93.0)),
        (1, (-3.0, 3.0)),
    ])
    def test_edo_bounds(self, divisions, atteso):
        assert edo_bounds(divisions) == atteso

    def test_get_unit_info_edo_valido(self):
        info = get_unit_info('edo', divisions=31)
        assert info is not None
        assert info.min_val == -93.0
        assert info.max_val == 93.0
        assert info.symbol == 'edo31'

    @pytest.mark.parametrize('divisions', [None, 0, -5, 2.5, True, '31'])
    def test_get_unit_info_edo_invalido(self, divisions):
        assert get_unit_info('edo', divisions=divisions) is None

    def test_get_unit_info_preset(self):
        assert get_unit_info('semitones').divisions == 12

    def test_get_unit_info_sconosciuta(self):
        assert get_unit_info('semitone') is None


class TestParseEdoDivisions:
    """Grammatica di `edo: N`: intero scalare > 0."""

    @pytest.mark.parametrize('testo, atteso', [
        ('31', 31),
        ('  12 ', 12),
        ('+24', 24),
        ('0', None),
        ('-5', None),
        ('2.5', None),
        ('true', None),
        ('{divisions: 31, value: 18}', None),  # vecchia forma annidata
        ('', None),
    ])
    def test_parse(self, testo, atteso):
        assert parse_edo_divisions(testo) == atteso


class TestParseVoiceUnitValue:
    """Classificazione del valore di voices.pitch.unit."""

    @pytest.mark.parametrize('valore', list(VOICE_PITCH_UNIT_VALUES))
    def test_preset_validi(self, valore):
        assert parse_voice_unit_value(valore) == ('preset', valore)

    def test_preset_con_virgolette(self):
        assert parse_voice_unit_value('"cents"') == ('preset', 'cents')

    def test_forma_edo_valida(self):
        assert parse_voice_unit_value('{edo: 24}') == ('edo', 24)

    def test_forma_edo_spazi(self):
        assert parse_voice_unit_value('{ edo: 31 }') == ('edo', 31)

    @pytest.mark.parametrize('valore', [
        'edo',                 # nudo: non e' un preset
        '{edo: 0}',
        '{edo: -3}',
        '{edo: 2.5}',
        '{edo: 12, value: 3}',  # chiavi extra
        'semitone',             # refuso
        'Hz',
    ])
    def test_forme_invalide(self, valore):
        kind, _ = parse_voice_unit_value(valore)
        assert kind == 'invalid'


class TestDocs:
    """Documentazione hover presente e coerente."""

    def test_ogni_chiave_del_blocco_ha_doc(self):
        for key in PITCH_BLOCK_KEYS:
            assert key in PITCH_KEY_DOCS, f'doc mancante per {key}'
            assert PITCH_KEY_DOCS[key]

    def test_block_doc_elenca_le_unita(self):
        for key in PITCH_UNIT_KEYS:
            assert key in PITCH_BLOCK_DOC

    def test_doc_edo_menziona_forma_appiattita(self):
        # la vecchia forma annidata {divisions, value} e' un hard break
        assert 'value' in PITCH_KEY_DOCS['edo']
        assert 'annidata' in PITCH_KEY_DOCS['edo']
