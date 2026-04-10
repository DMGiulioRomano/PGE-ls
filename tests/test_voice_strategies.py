# tests/test_voice_strategies.py
"""
Test per il registry voice_strategies.

Verifica completezza del registry, correttezza dei metadati
e funzionamento delle funzioni di accesso.
"""

import pytest
from granular_ls.voice_strategies import (
    VOICE_STRATEGY_REGISTRY,
    VOICE_DIMENSIONS,
    VOICE_TOP_LEVEL_KEYS,
    VOICES_BLOCK_DOC,
    VoiceKwargSpec,
    VoiceStrategySpec,
    get_strategies_for_dimension,
    get_strategy_spec,
    get_kwarg_spec,
    find_kwarg_in_dimension,
    get_top_level_doc,
)


class TestRegistryStructure:
    """Verifica la struttura complessiva del registry."""

    def test_tutte_le_dimensioni_presenti(self):
        assert set(VOICE_STRATEGY_REGISTRY.keys()) == {
            'pitch', 'onset_offset', 'pointer', 'pan'
        }

    def test_voice_dimensions_list(self):
        assert VOICE_DIMENSIONS == ['pitch', 'onset_offset', 'pointer', 'pan']

    def test_voice_top_level_keys(self):
        assert 'num_voices' in VOICE_TOP_LEVEL_KEYS
        for dim in VOICE_DIMENSIONS:
            assert dim in VOICE_TOP_LEVEL_KEYS

    def test_voices_block_doc_non_vuoto(self):
        assert VOICES_BLOCK_DOC
        assert 'voices' in VOICES_BLOCK_DOC.lower()


class TestPitchStrategies:
    """Verifica le strategy della dimensione pitch."""

    def test_strategy_step_esiste(self):
        assert 'step' in VOICE_STRATEGY_REGISTRY['pitch']

    def test_strategy_range_esiste(self):
        assert 'range' in VOICE_STRATEGY_REGISTRY['pitch']

    def test_strategy_chord_esiste(self):
        assert 'chord' in VOICE_STRATEGY_REGISTRY['pitch']

    def test_strategy_stochastic_esiste(self):
        assert 'stochastic' in VOICE_STRATEGY_REGISTRY['pitch']

    def test_step_ha_kwarg_step(self):
        spec = VOICE_STRATEGY_REGISTRY['pitch']['step']
        assert 'step' in spec.kwargs
        kwarg = spec.kwargs['step']
        assert kwarg.required is True
        assert kwarg.type == 'float'

    def test_range_ha_kwarg_semitone_range(self):
        spec = VOICE_STRATEGY_REGISTRY['pitch']['range']
        assert 'semitone_range' in spec.kwargs
        kwarg = spec.kwargs['semitone_range']
        assert kwarg.required is True
        assert kwarg.min_val == 0.0

    def test_chord_ha_kwarg_chord_con_enum(self):
        spec = VOICE_STRATEGY_REGISTRY['pitch']['chord']
        assert 'chord' in spec.kwargs
        kwarg = spec.kwargs['chord']
        assert kwarg.type == 'enum'
        assert kwarg.enum_values is not None
        assert 'maj' in kwarg.enum_values
        assert 'dom7' in kwarg.enum_values
        assert 'min7' in kwarg.enum_values

    def test_chord_enum_contiene_tutti_gli_accordi(self):
        kwarg = VOICE_STRATEGY_REGISTRY['pitch']['chord'].kwargs['chord']
        expected = {'maj', 'min', 'dom7', 'maj7', 'min7', 'dim', 'aug',
                    'sus2', 'sus4', 'dim7', 'minmaj7'}
        assert set(kwarg.enum_values) == expected

    def test_stochastic_ha_kwarg_semitone_range(self):
        spec = VOICE_STRATEGY_REGISTRY['pitch']['stochastic']
        assert 'semitone_range' in spec.kwargs
        kwarg = spec.kwargs['semitone_range']
        assert kwarg.required is True
        assert kwarg.min_val == 0.0


class TestOnsetOffsetStrategies:
    """Verifica le strategy della dimensione onset_offset."""

    def test_strategy_linear_esiste(self):
        assert 'linear' in VOICE_STRATEGY_REGISTRY['onset_offset']

    def test_strategy_geometric_esiste(self):
        assert 'geometric' in VOICE_STRATEGY_REGISTRY['onset_offset']

    def test_strategy_stochastic_esiste(self):
        assert 'stochastic' in VOICE_STRATEGY_REGISTRY['onset_offset']

    def test_linear_ha_kwarg_step(self):
        spec = VOICE_STRATEGY_REGISTRY['onset_offset']['linear']
        assert 'step' in spec.kwargs

    def test_geometric_ha_due_kwargs(self):
        spec = VOICE_STRATEGY_REGISTRY['onset_offset']['geometric']
        assert 'step' in spec.kwargs
        assert 'base' in spec.kwargs

    def test_stochastic_ha_kwarg_max_offset(self):
        spec = VOICE_STRATEGY_REGISTRY['onset_offset']['stochastic']
        assert 'max_offset' in spec.kwargs
        assert spec.kwargs['max_offset'].min_val == 0.0


class TestPointerStrategies:
    """Verifica le strategy della dimensione pointer."""

    def test_strategy_linear_esiste(self):
        assert 'linear' in VOICE_STRATEGY_REGISTRY['pointer']

    def test_strategy_stochastic_esiste(self):
        assert 'stochastic' in VOICE_STRATEGY_REGISTRY['pointer']

    def test_stochastic_ha_kwarg_pointer_range(self):
        spec = VOICE_STRATEGY_REGISTRY['pointer']['stochastic']
        assert 'pointer_range' in spec.kwargs


class TestPanStrategies:
    """Verifica le strategy della dimensione pan."""

    def test_strategy_linear_esiste(self):
        assert 'linear' in VOICE_STRATEGY_REGISTRY['pan']

    def test_strategy_random_esiste(self):
        assert 'random' in VOICE_STRATEGY_REGISTRY['pan']

    def test_strategy_additive_esiste(self):
        assert 'additive' in VOICE_STRATEGY_REGISTRY['pan']

    def test_tutte_le_pan_strategies_hanno_spread(self):
        for strategy_name, spec in VOICE_STRATEGY_REGISTRY['pan'].items():
            assert 'spread' in spec.kwargs, (
                f"Pan strategy '{strategy_name}' manca del kwarg 'spread'"
            )

    def test_linear_spread_min_val(self):
        kwarg = VOICE_STRATEGY_REGISTRY['pan']['linear'].kwargs['spread']
        assert kwarg.min_val == 0.0


class TestAccessFunctions:
    """Verifica le funzioni di accesso al registry."""

    def test_get_strategies_for_pitch(self):
        strategies = get_strategies_for_dimension('pitch')
        assert 'step' in strategies
        assert 'chord' in strategies

    def test_get_strategies_for_dimensione_sconosciuta(self):
        assert get_strategies_for_dimension('unknown') == []

    def test_get_strategy_spec_esistente(self):
        spec = get_strategy_spec('pitch', 'chord')
        assert spec is not None
        assert isinstance(spec, VoiceStrategySpec)
        assert spec.name == 'chord'

    def test_get_strategy_spec_inesistente(self):
        assert get_strategy_spec('pitch', 'nonexistent') is None
        assert get_strategy_spec('unknown_dim', 'step') is None

    def test_get_kwarg_spec(self):
        kwarg = get_kwarg_spec('pitch', 'chord', 'chord')
        assert kwarg is not None
        assert kwarg.name == 'chord'
        assert kwarg.type == 'enum'

    def test_get_kwarg_spec_inesistente(self):
        assert get_kwarg_spec('pitch', 'step', 'chord') is None
        assert get_kwarg_spec('pitch', 'nonexistent', 'step') is None

    def test_find_kwarg_in_dimension_trovato(self):
        # 'step' appare in pitch.step, onset_offset.linear, onset_offset.geometric, pointer.linear
        kwarg = find_kwarg_in_dimension('pitch', 'step')
        assert kwarg is not None
        assert kwarg.name == 'step'

    def test_find_kwarg_in_dimension_non_trovato(self):
        assert find_kwarg_in_dimension('pan', 'step') is None

    def test_find_kwarg_chord_solo_in_pitch(self):
        assert find_kwarg_in_dimension('pitch', 'chord') is not None
        assert find_kwarg_in_dimension('pan', 'chord') is None

    def test_get_top_level_doc_num_voices(self):
        doc = get_top_level_doc('num_voices')
        assert doc is not None
        assert 'num_voices' in doc.lower() or 'voci' in doc.lower()

    def test_get_top_level_doc_dimensioni(self):
        for dim in VOICE_DIMENSIONS:
            doc = get_top_level_doc(dim)
            assert doc is not None, f"Manca doc per dimensione '{dim}'"

    def test_get_top_level_doc_chiave_sconosciuta(self):
        assert get_top_level_doc('nonexistent') is None


class TestKwargSpecProperties:
    """Verifica le proprieta' dei VoiceKwargSpec."""

    def test_kwarg_spec_e_frozen(self):
        kwarg = get_kwarg_spec('pitch', 'step', 'step')
        with pytest.raises((AttributeError, TypeError)):
            kwarg.name = 'changed'  # type: ignore

    def test_strategy_spec_e_frozen(self):
        spec = get_strategy_spec('pitch', 'step')
        with pytest.raises((AttributeError, TypeError)):
            spec.name = 'changed'  # type: ignore

    def test_tutti_i_kwargs_hanno_description_non_vuota(self):
        for dim, strategies in VOICE_STRATEGY_REGISTRY.items():
            for strategy_name, spec in strategies.items():
                for kwarg_name, kwarg in spec.kwargs.items():
                    assert kwarg.description, (
                        f"Kwarg '{kwarg_name}' di {dim}.{strategy_name} "
                        f"ha description vuota"
                    )

    def test_tutte_le_strategy_hanno_description_non_vuota(self):
        for dim, strategies in VOICE_STRATEGY_REGISTRY.items():
            for strategy_name, spec in strategies.items():
                assert spec.description, (
                    f"Strategy '{strategy_name}' di {dim} ha description vuota"
                )
