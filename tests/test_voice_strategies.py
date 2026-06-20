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

    def test_voices_block_doc_cita_eredita_time_mode(self):
        # PGE #144: gli envelope sotto voices.* ereditano il time_mode dello stream.
        assert 'time_mode' in VOICES_BLOCK_DOC
        assert 'normalized' in VOICES_BLOCK_DOC


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

    def test_range_ha_kwarg_pitch_range(self):
        spec = VOICE_STRATEGY_REGISTRY['pitch']['range']
        assert 'pitch_range' in spec.kwargs
        kwarg = spec.kwargs['pitch_range']
        assert kwarg.required is True
        assert kwarg.min_val == 0.0

    def test_range_non_ha_piu_semitone_range(self):
        # Hard break PGE: semitone_range rinominato in pitch_range
        spec = VOICE_STRATEGY_REGISTRY['pitch']['range']
        assert 'semitone_range' not in spec.kwargs

    def test_chord_ha_kwarg_chord_con_enum(self):
        spec = VOICE_STRATEGY_REGISTRY['pitch']['chord']
        assert 'chord' in spec.kwargs
        kwarg = spec.kwargs['chord']
        assert kwarg.type == 'enum'
        assert kwarg.enum_values is not None
        assert 'maj' in kwarg.enum_values
        assert 'dom7' in kwarg.enum_values
        assert 'min7' in kwarg.enum_values

    def test_chord_enum_speculare_a_chord_intervals(self):
        from granular_ls.voice_strategies import CHORD_INTERVALS
        kwarg = VOICE_STRATEGY_REGISTRY['pitch']['chord'].kwargs['chord']
        assert set(kwarg.enum_values) == set(CHORD_INTERVALS.keys())

    def test_chord_enum_contiene_accordi_estesi(self):
        # Registry PGE esteso fino a 7 voci
        kwarg = VOICE_STRATEGY_REGISTRY['pitch']['chord'].kwargs['chord']
        for nome in ('dom9', 'maj9', 'min11', 'dom13', 'altered', '9sus4'):
            assert nome in kwarg.enum_values

    def test_chord_ha_kwarg_inversion_opzionale(self):
        spec = VOICE_STRATEGY_REGISTRY['pitch']['chord']
        assert 'inversion' in spec.kwargs
        kwarg = spec.kwargs['inversion']
        assert kwarg.required is False
        assert kwarg.type == 'int'
        assert kwarg.min_val == 0.0

    def test_stochastic_ha_kwarg_pitch_range(self):
        spec = VOICE_STRATEGY_REGISTRY['pitch']['stochastic']
        assert 'pitch_range' in spec.kwargs
        kwarg = spec.kwargs['pitch_range']
        assert kwarg.required is True
        assert kwarg.min_val == 0.0

    def test_stochastic_non_ha_piu_semitone_range(self):
        spec = VOICE_STRATEGY_REGISTRY['pitch']['stochastic']
        assert 'semitone_range' not in spec.kwargs

    def test_stochastic_descrizione_non_deterministica(self):
        desc = VOICE_STRATEGY_REGISTRY['pitch']['stochastic'].description.lower()
        assert 'deterministici' not in desc
        assert 'riproducib' not in desc

    def test_strategy_spectral_esiste(self):
        assert 'spectral' in VOICE_STRATEGY_REGISTRY['pitch']

    def test_spectral_ha_kwarg_max_partial(self):
        spec = VOICE_STRATEGY_REGISTRY['pitch']['spectral']
        assert 'max_partial' in spec.kwargs
        kwarg = spec.kwargs['max_partial']
        assert kwarg.required is False
        assert kwarg.type == 'int'
        assert kwarg.min_val == 1.0

    def test_spectral_descrizione_contiene_serie_armonica(self):
        desc = VOICE_STRATEGY_REGISTRY['pitch']['spectral'].description
        assert 'armonica' in desc.lower()


class TestPitchUnitKwarg:
    """Il kwarg `unit` delle strategy pitch unit-agnostiche (issue #9/#10)."""

    @pytest.mark.parametrize('strategy', ['step', 'range', 'stochastic'])
    def test_strategy_agnostiche_hanno_unit(self, strategy):
        spec = VOICE_STRATEGY_REGISTRY['pitch'][strategy]
        assert 'unit' in spec.kwargs
        kwarg = spec.kwargs['unit']
        assert kwarg.type == 'pitch_unit'
        assert kwarg.required is False

    @pytest.mark.parametrize('strategy', ['chord', 'spectral'])
    def test_strategy_semitone_locked_non_espongono_unit(self, strategy):
        # chord/spectral accettano solo semitones: unit non viene suggerito
        spec = VOICE_STRATEGY_REGISTRY['pitch'][strategy]
        assert 'unit' not in spec.kwargs

    def test_unit_doc_menziona_geometria_ratio(self):
        kwarg = VOICE_STRATEGY_REGISTRY['pitch']['step'].kwargs['unit']
        assert 'ratio' in kwarg.description
        assert 'geometric' in kwarg.description.lower()

    def test_descrizione_step_geometrica_con_ratio(self):
        desc = VOICE_STRATEGY_REGISTRY['pitch']['step'].description
        assert 'step^i' in desc
        assert 'geometric' in desc.lower()

    def test_descrizione_stochastic_simmetrica_con_ratio(self):
        desc = VOICE_STRATEGY_REGISTRY['pitch']['stochastic'].description
        assert 'ratio' in desc

    def test_solo_pitch_ha_kwarg_unit(self):
        # unit appartiene alla dimensione pitch, non alle altre
        for dim in ('onset_offset', 'pointer', 'pan'):
            for spec in VOICE_STRATEGY_REGISTRY[dim].values():
                assert 'unit' not in spec.kwargs


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

    def test_linear_ha_kwarg_normalized(self):
        spec = VOICE_STRATEGY_REGISTRY['pointer']['linear']
        assert 'normalized' in spec.kwargs
        kwarg = spec.kwargs['normalized']
        assert kwarg.type == 'bool'
        assert kwarg.required is False

    def test_stochastic_ha_kwarg_normalized(self):
        spec = VOICE_STRATEGY_REGISTRY['pointer']['stochastic']
        assert 'normalized' in spec.kwargs
        kwarg = spec.kwargs['normalized']
        assert kwarg.type == 'bool'
        assert kwarg.required is False

    def test_normalized_descritto_in_top_level_doc(self):
        doc = get_top_level_doc('pointer')
        assert doc is not None
        assert 'normalized' in doc

    def test_find_kwarg_normalized_in_pointer(self):
        kwarg = find_kwarg_in_dimension('pointer', 'normalized')
        assert kwarg is not None
        assert kwarg.type == 'bool'

    def test_normalized_non_in_pitch_dim(self):
        assert find_kwarg_in_dimension('pitch', 'normalized') is None

    def test_normalized_non_in_pan_dim(self):
        assert find_kwarg_in_dimension('pan', 'normalized') is None


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

    def test_num_voices_doc_riporta_max_256(self):
        # PGE #145: il bound massimo di num_voices e' 256, non piu' 64.
        doc = get_top_level_doc('num_voices')
        assert '256' in doc
        assert '64' not in doc

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
