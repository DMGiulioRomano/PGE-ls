# tests/test_schema_bridge.py
"""
Suite TDD - FASE RED per schema_bridge.py

Modulo sotto test (non ancora esistente):
    granular_ls/schema_bridge.py

Responsabilita' del modulo:
    Caricare i dati da parameter_schema.py e parameter_definitions.py
    del progetto granulare e trasformarli in strutture interne al LS,
    indipendenti dai moduli sorgente.

Design scelto:
    - ParameterInfo: dataclass che fonde ParameterSpec + ParameterBounds
    - SchemaBridge: classe principale, costruita da raw_data (dict)
    - SchemaBridge.from_python_path(): factory che importa i moduli reali
    - SchemaBridge.from_snapshot(): factory che carica da JSON (offline)

Organizzazione:
    1.  ParameterInfo - costruzione, campi, proprieta' derivate
    2.  SchemaBridge - costruzione da raw_data fixture
    3.  get_all_parameters() - tutti i parametri inclusi interni
    4.  get_completion_parameters() - solo smart e non-interni
    5.  get_parameter() - accesso singolo per nome
    6.  get_exclusive_groups() - mappa gruppo -> lista ParameterInfo
    7.  get_yaml_keys() - chiavi YAML per completion
    8.  from_python_path() - factory da path reale (integration)
    9.  from_snapshot() - factory da JSON
    10. generate_snapshot() - serializzazione a JSON
    11. Documentazione generata - formato e contenuto
    12. Filtri interni - parametri con yaml_path che inizia con '_'
    13. Edge cases - schema vuoto, bounds mancanti, nome duplicato
"""

import json
import sys
import pytest
from pathlib import Path
from dataclasses import fields

from granular_ls.schema_bridge import ParameterInfo, SchemaBridge


# =============================================================================
# FIXTURES
# =============================================================================

def make_raw_spec(
    name,
    yaml_path,
    default=0.0,
    is_smart=True,
    exclusive_group=None,
    group_priority=99,
    range_path=None,
    dephase_key=None,
):
    return {
        'name': name,
        'yaml_path': yaml_path,
        'default': default,
        'is_smart': is_smart,
        'exclusive_group': exclusive_group,
        'group_priority': group_priority,
        'range_path': range_path,
        'dephase_key': dephase_key,
    }


def make_raw_bounds(
    min_val,
    max_val,
    min_range=0.0,
    max_range=0.0,
    default_jitter=0.0,
    variation_mode='additive',
):
    return {
        'min_val': min_val,
        'max_val': max_val,
        'min_range': min_range,
        'max_range': max_range,
        'default_jitter': default_jitter,
        'variation_mode': variation_mode,
    }


@pytest.fixture
def minimal_raw_data():
    return {
        'specs': [
            make_raw_spec('density', 'density', default=None),
            make_raw_spec('grain_duration', 'grain.duration', default=0.05),
        ],
        'bounds': {
            'density': make_raw_bounds(0.01, 4000.0, variation_mode='additive'),
            'grain_duration': make_raw_bounds(0.001, 10.0, 0.0, 1.0, 0.01),
        },
    }


@pytest.fixture
def exclusive_group_raw_data():
    return {
        'specs': [
            make_raw_spec('fill_factor', 'fill_factor', default=2,
                          exclusive_group='density_mode', group_priority=1),
            make_raw_spec('density', 'density', default=None,
                          exclusive_group='density_mode', group_priority=2),
            make_raw_spec('distribution', 'distribution', default=0.0),
        ],
        'bounds': {
            'fill_factor': make_raw_bounds(0.001, 50.0),
            'density': make_raw_bounds(0.01, 4000.0),
            'distribution': make_raw_bounds(0.0, 1.0),
        },
    }


@pytest.fixture
def internal_params_raw_data():
    return {
        'specs': [
            make_raw_spec('density', 'density', default=None),
            make_raw_spec('effective_density', '_internal_calc_',
                          default=0.0, is_smart=False),
            make_raw_spec('pointer_deviation', '_dummy_fixed_zero_',
                          default=0.0, range_path='offset_range'),
        ],
        'bounds': {
            'density': make_raw_bounds(0.01, 4000.0),
            'effective_density': make_raw_bounds(1.0, 4000.0),
            'pointer_deviation': make_raw_bounds(0.0, 1.0),
        },
    }


@pytest.fixture
def full_raw_data():
    return {
        'specs': [
            make_raw_spec('fill_factor', 'fill_factor', default=2,
                          exclusive_group='density_mode', group_priority=1),
            make_raw_spec('density', 'density', default=None,
                          exclusive_group='density_mode', group_priority=2),
            make_raw_spec('distribution', 'distribution', default=0.0),
            make_raw_spec('effective_density', '_internal_calc_',
                          default=0.0, is_smart=False),
            make_raw_spec('pitch_ratio', 'ratio', default=1.0,
                          range_path='range', dephase_key='pitch',
                          exclusive_group='pitch_mode', group_priority=2),
            make_raw_spec('pitch_semitones', 'semitones', default=None,
                          range_path='range', dephase_key='pitch',
                          exclusive_group='pitch_mode', group_priority=1),
            make_raw_spec('loop_end', 'loop_end', default=None,
                          exclusive_group='loop_bounds', group_priority=1),
            make_raw_spec('loop_dur', 'loop_dur', default=None,
                          exclusive_group='loop_bounds', group_priority=99),
            make_raw_spec('grain_duration', 'grain.duration', default=0.05),
            make_raw_spec('volume', 'volume', default=-6.0),
            make_raw_spec('pointer_deviation', '_dummy_fixed_zero_',
                          default=0.0, range_path='offset_range'),
        ],
        'bounds': {
            'fill_factor': make_raw_bounds(0.001, 50.0),
            'density': make_raw_bounds(0.01, 4000.0),
            'distribution': make_raw_bounds(0.0, 1.0),
            'effective_density': make_raw_bounds(1.0, 4000.0),
            'pitch_ratio': make_raw_bounds(0.01, 10.0),
            'pitch_semitones': make_raw_bounds(-48.0, 48.0,
                                               variation_mode='quantized'),
            'loop_end': make_raw_bounds(0.0, 1.0),
            'loop_dur': make_raw_bounds(0.0, 1.0),
            'grain_duration': make_raw_bounds(0.001, 10.0, 0.0, 1.0, 0.01),
            'volume': make_raw_bounds(-60.0, 0.0),
            'pointer_deviation': make_raw_bounds(0.0, 1.0),
        },
    }


# =============================================================================
# 1. ParameterInfo
# =============================================================================

class TestParameterInfoConstruction:

    def test_is_dataclass(self):
        info = ParameterInfo(
            name='density',
            yaml_path='density',
            default=None,
            is_smart=True,
            exclusive_group=None,
            group_priority=99,
            min_val=0.01,
            max_val=4000.0,
            min_range=0.0,
            max_range=0.0,
            variation_mode='additive',
            is_internal=False,
        )
        assert info.name == 'density'

    def test_is_frozen(self):
        from dataclasses import FrozenInstanceError
        info = ParameterInfo(
            name='density', yaml_path='density', default=None,
            is_smart=True, exclusive_group=None, group_priority=99,
            min_val=0.01, max_val=4000.0, min_range=0.0, max_range=0.0,
            variation_mode='additive', is_internal=False,
        )
        with pytest.raises(FrozenInstanceError):
            info.name = 'altro'

    def test_has_all_expected_fields(self):
        expected = {
            'name', 'yaml_path', 'default', 'is_smart',
            'exclusive_group', 'group_priority',
            'min_val', 'max_val', 'min_range', 'max_range',
            'variation_mode', 'is_internal',
        }
        actual = {f.name for f in fields(ParameterInfo)}
        assert expected == actual

    def test_is_internal_true_for_underscore_path(self):
        info = ParameterInfo(
            name='effective_density', yaml_path='_internal_calc_',
            default=0.0, is_smart=False, exclusive_group=None,
            group_priority=99, min_val=1.0, max_val=4000.0,
            min_range=0.0, max_range=0.0, variation_mode='additive',
            is_internal=True,
        )
        assert info.is_internal is True

    def test_is_internal_false_for_normal_path(self):
        info = ParameterInfo(
            name='density', yaml_path='density', default=None,
            is_smart=True, exclusive_group=None, group_priority=99,
            min_val=0.01, max_val=4000.0, min_range=0.0, max_range=0.0,
            variation_mode='additive', is_internal=False,
        )
        assert info.is_internal is False


# =============================================================================
# 2. SchemaBridge - costruzione
# =============================================================================

class TestSchemaBridgeConstruction:

    def test_costruzione_da_raw_data(self, minimal_raw_data):
        bridge = SchemaBridge(minimal_raw_data)
        assert bridge is not None

    def test_raw_data_vuoto_non_solleva(self):
        bridge = SchemaBridge({'specs': [], 'bounds': {}})
        assert bridge is not None

    def test_raw_data_senza_specs_solleva_key_error(self):
        with pytest.raises(KeyError):
            SchemaBridge({'bounds': {}})

    def test_raw_data_senza_bounds_solleva_key_error(self):
        with pytest.raises(KeyError):
            SchemaBridge({'specs': []})


# =============================================================================
# 3. get_all_parameters()
# =============================================================================

class TestGetAllParameters:

    def test_ritorna_lista(self, minimal_raw_data):
        bridge = SchemaBridge(minimal_raw_data)
        result = bridge.get_all_parameters()
        assert isinstance(result, list)

    def test_ritorna_parameter_info(self, minimal_raw_data):
        bridge = SchemaBridge(minimal_raw_data)
        for item in bridge.get_all_parameters():
            assert isinstance(item, ParameterInfo)

    def test_conteggio_corrisponde_agli_specs(self, minimal_raw_data):
        bridge = SchemaBridge(minimal_raw_data)
        # +2 per solo e mute sempre aggiunti
        assert len(bridge.get_all_parameters()) >= 2

    def test_include_parametri_interni(self, internal_params_raw_data):
        bridge = SchemaBridge(internal_params_raw_data)
        names = {p.name for p in bridge.get_all_parameters()}
        assert 'effective_density' in names

    def test_bounds_applicati_correttamente(self, minimal_raw_data):
        bridge = SchemaBridge(minimal_raw_data)
        params = {p.name: p for p in bridge.get_all_parameters()}
        assert params['density'].min_val == pytest.approx(0.01)
        assert params['density'].max_val == pytest.approx(4000.0)

    def test_variation_mode_applicato(self, minimal_raw_data):
        bridge = SchemaBridge(minimal_raw_data)
        params = {p.name: p for p in bridge.get_all_parameters()}
        assert params['density'].variation_mode == 'additive'

    def test_is_internal_calcolato_automaticamente(self, internal_params_raw_data):
        bridge = SchemaBridge(internal_params_raw_data)
        params = {p.name: p for p in bridge.get_all_parameters()}
        assert params['effective_density'].is_internal is True
        assert params['pointer_deviation'].is_internal is True
        assert params['density'].is_internal is False

    def test_parametro_senza_bounds_ha_none(self):
        raw = {
            'specs': [make_raw_spec('orphan_param', 'orphan')],
            'bounds': {},
        }
        bridge = SchemaBridge(raw)
        params = {p.name: p for p in bridge.get_all_parameters()}
        assert params['orphan_param'].min_val is None
        assert params['orphan_param'].max_val is None


# =============================================================================
# 4. get_completion_parameters()
# =============================================================================

class TestGetCompletionParameters:

    def test_esclude_parametri_is_smart_false(self, internal_params_raw_data):
        bridge = SchemaBridge(internal_params_raw_data)
        names = {p.name for p in bridge.get_completion_parameters()}
        assert 'effective_density' not in names

    def test_esclude_parametri_con_yaml_path_interno(self, internal_params_raw_data):
        bridge = SchemaBridge(internal_params_raw_data)
        names = {p.name for p in bridge.get_completion_parameters()}
        assert 'pointer_deviation' not in names

    def test_include_parametri_normali(self, internal_params_raw_data):
        bridge = SchemaBridge(internal_params_raw_data)
        names = {p.name for p in bridge.get_completion_parameters()}
        assert 'density' in names

    def test_lista_vuota_se_tutti_interni(self):
        raw = {
            'specs': [
                make_raw_spec('internal_a', '_path_a_', is_smart=False),
                make_raw_spec('internal_b', '_path_b_'),
            ],
            'bounds': {},
        }
        bridge = SchemaBridge(raw)
        # solo/mute sempre presenti, interni esclusi
        params = bridge.get_completion_parameters()
        names = {p.name for p in params}
        assert 'internal_a' not in names
        assert 'solo' in names

    def test_ritorna_parameter_info(self, minimal_raw_data):
        bridge = SchemaBridge(minimal_raw_data)
        for item in bridge.get_completion_parameters():
            assert isinstance(item, ParameterInfo)


# =============================================================================
# 5. get_parameter()
# =============================================================================

class TestGetParameter:

    def test_ritorna_parameter_info_per_nome_valido(self, minimal_raw_data):
        bridge = SchemaBridge(minimal_raw_data)
        result = bridge.get_parameter('density')
        assert isinstance(result, ParameterInfo)
        assert result.name == 'density'

    def test_ritorna_none_per_nome_sconosciuto(self, minimal_raw_data):
        bridge = SchemaBridge(minimal_raw_data)
        assert bridge.get_parameter('nonexistent') is None

    def test_case_sensitive(self, minimal_raw_data):
        bridge = SchemaBridge(minimal_raw_data)
        assert bridge.get_parameter('Density') is None
        assert bridge.get_parameter('DENSITY') is None

    def test_ritorna_parametro_interno(self, internal_params_raw_data):
        bridge = SchemaBridge(internal_params_raw_data)
        result = bridge.get_parameter('effective_density')
        assert result is not None
        assert result.is_internal is True


# =============================================================================
# 6. get_exclusive_groups()
# =============================================================================

class TestGetExclusiveGroups:

    def test_ritorna_dict(self, exclusive_group_raw_data):
        bridge = SchemaBridge(exclusive_group_raw_data)
        result = bridge.get_exclusive_groups()
        assert isinstance(result, dict)

    def test_chiavi_sono_nomi_gruppi(self, exclusive_group_raw_data):
        bridge = SchemaBridge(exclusive_group_raw_data)
        groups = bridge.get_exclusive_groups()
        assert 'density_mode' in groups

    def test_valori_sono_liste_di_parameter_info(self, exclusive_group_raw_data):
        bridge = SchemaBridge(exclusive_group_raw_data)
        groups = bridge.get_exclusive_groups()
        for group_name, members in groups.items():
            assert isinstance(members, list)
            for m in members:
                assert isinstance(m, ParameterInfo)

    def test_membri_corretti_nel_gruppo(self, exclusive_group_raw_data):
        bridge = SchemaBridge(exclusive_group_raw_data)
        groups = bridge.get_exclusive_groups()
        names = {p.name for p in groups['density_mode']}
        assert names == {'fill_factor', 'density'}

    def test_parametri_senza_gruppo_non_compaiono(self, exclusive_group_raw_data):
        bridge = SchemaBridge(exclusive_group_raw_data)
        groups = bridge.get_exclusive_groups()
        all_grouped = {p.name for members in groups.values() for p in members}
        assert 'distribution' not in all_grouped

    def test_gruppi_multipli(self, full_raw_data):
        bridge = SchemaBridge(full_raw_data)
        groups = bridge.get_exclusive_groups()
        assert 'density_mode' in groups
        assert 'pitch_mode' in groups
        assert 'loop_bounds' in groups

    def test_dict_vuoto_se_nessun_gruppo(self, minimal_raw_data):
        bridge = SchemaBridge(minimal_raw_data)
        groups = bridge.get_exclusive_groups()
        # stream_mode (solo/mute) sempre presente
        assert 'stream_mode' in groups
        assert len(groups['stream_mode']) == 2

    def test_ordinati_per_group_priority(self, exclusive_group_raw_data):
        bridge = SchemaBridge(exclusive_group_raw_data)
        groups = bridge.get_exclusive_groups()
        members = groups['density_mode']
        priorities = [m.group_priority for m in members]
        assert priorities == sorted(priorities)


# =============================================================================
# 7. get_yaml_keys()
# =============================================================================

class TestGetYamlKeys:

    def test_ritorna_lista_di_stringhe(self, minimal_raw_data):
        bridge = SchemaBridge(minimal_raw_data)
        result = bridge.get_yaml_keys()
        assert isinstance(result, list)
        for k in result:
            assert isinstance(k, str)

    def test_contiene_yaml_path_dei_completion_params(self, minimal_raw_data):
        bridge = SchemaBridge(minimal_raw_data)
        keys = bridge.get_yaml_keys()
        assert 'density' in keys
        assert 'grain.duration' in keys

    def test_esclude_yaml_path_interni(self, internal_params_raw_data):
        bridge = SchemaBridge(internal_params_raw_data)
        keys = bridge.get_yaml_keys()
        assert '_internal_calc_' not in keys
        assert '_dummy_fixed_zero_' not in keys

    def test_nessun_duplicato(self, full_raw_data):
        bridge = SchemaBridge(full_raw_data)
        keys = bridge.get_yaml_keys()
        assert len(keys) == len(set(keys))


# =============================================================================
# 8. from_python_path() - integration
# =============================================================================

class TestFromPythonPath:

    @pytest.fixture
    def granular_project_path(self, tmp_path):
        src = tmp_path / 'src'
        src.mkdir()
        params_dir = src / 'parameters'
        params_dir.mkdir()
        (params_dir / '__init__.py').write_text('')

        (params_dir / 'parameter_definitions.py').write_text("""
from dataclasses import dataclass
from typing import Dict

@dataclass(frozen=True)
class ParameterBounds:
    min_val: float
    max_val: float
    min_range: float = 0.0
    max_range: float = 0.0
    default_jitter: float = 0.0
    variation_mode: str = 'additive'

GRANULAR_PARAMETERS = {
    'density': ParameterBounds(min_val=0.01, max_val=4000.0),
    'grain_duration': ParameterBounds(min_val=0.001, max_val=10.0),
}

def get_parameter_definition(name):
    if name not in GRANULAR_PARAMETERS:
        raise KeyError(name)
    return GRANULAR_PARAMETERS[name]
""")

        (params_dir / 'parameter_schema.py').write_text("""
from dataclasses import dataclass
from typing import Optional, Any, List

@dataclass(frozen=True)
class ParameterSpec:
    name: str
    yaml_path: str
    default: Any
    range_path: Optional[str] = None
    dephase_key: Optional[str] = None
    is_smart: bool = True
    exclusive_group: Optional[str] = None
    group_priority: int = 99

STREAM_PARAMETER_SCHEMA = [
    ParameterSpec(name='grain_duration', yaml_path='grain.duration', default=0.05),
]
POINTER_PARAMETER_SCHEMA = []
PITCH_PARAMETER_SCHEMA = []
DENSITY_PARAMETER_SCHEMA = [
    ParameterSpec(name='density', yaml_path='density', default=None),
]

ALL_SCHEMAS = {
    'stream': STREAM_PARAMETER_SCHEMA,
    'pointer': POINTER_PARAMETER_SCHEMA,
    'pitch': PITCH_PARAMETER_SCHEMA,
    'density': DENSITY_PARAMETER_SCHEMA,
}
""")
        return str(src)

    def test_from_python_path_ritorna_bridge(self, granular_project_path):
        bridge = SchemaBridge.from_python_path(granular_project_path)
        assert isinstance(bridge, SchemaBridge)

    def test_from_python_path_carica_parametri(self, granular_project_path):
        bridge = SchemaBridge.from_python_path(granular_project_path)
        names = {p.name for p in bridge.get_all_parameters()}
        assert 'density' in names
        assert 'grain_duration' in names

    def test_from_python_path_applica_bounds(self, granular_project_path):
        bridge = SchemaBridge.from_python_path(granular_project_path)
        p = bridge.get_parameter('density')
        assert p.min_val == pytest.approx(0.01)
        assert p.max_val == pytest.approx(4000.0)

    def test_from_python_path_path_inesistente_solleva(self):
        with pytest.raises((ImportError, FileNotFoundError, ModuleNotFoundError)):
            SchemaBridge.from_python_path('/percorso/inesistente/assoluto')


# =============================================================================
# 9. from_snapshot() e generate_snapshot()
# =============================================================================

class TestSnapshotRoundtrip:

    def test_generate_snapshot_ritorna_stringa_json(self, minimal_raw_data):
        bridge = SchemaBridge(minimal_raw_data)
        snapshot = bridge.generate_snapshot()
        assert isinstance(snapshot, str)
        parsed = json.loads(snapshot)
        assert isinstance(parsed, dict)

    def test_snapshot_contiene_tutti_i_parametri(self, minimal_raw_data):
        bridge = SchemaBridge(minimal_raw_data)
        snapshot = json.loads(bridge.generate_snapshot())
        names = {p['name'] for p in snapshot['parameters']}
        assert 'density' in names
        assert 'grain_duration' in names

    def test_from_snapshot_roundtrip(self, minimal_raw_data, tmp_path):
        bridge = SchemaBridge(minimal_raw_data)
        snapshot_path = tmp_path / 'schema.json'
        snapshot_path.write_text(bridge.generate_snapshot())
        bridge2 = SchemaBridge.from_snapshot(str(snapshot_path))
        names_original = {p.name for p in bridge.get_all_parameters()}
        names_loaded = {p.name for p in bridge2.get_all_parameters()}
        assert names_original == names_loaded

    def test_from_snapshot_preserva_bounds(self, minimal_raw_data, tmp_path):
        bridge = SchemaBridge(minimal_raw_data)
        snapshot_path = tmp_path / 'schema.json'
        snapshot_path.write_text(bridge.generate_snapshot())
        bridge2 = SchemaBridge.from_snapshot(str(snapshot_path))
        p = bridge2.get_parameter('density')
        assert p.min_val == pytest.approx(0.01)
        assert p.max_val == pytest.approx(4000.0)

    def test_from_snapshot_file_mancante_solleva(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            SchemaBridge.from_snapshot(str(tmp_path / 'non_esiste.json'))

    def test_from_snapshot_json_malformato_solleva(self, tmp_path):
        bad_file = tmp_path / 'bad.json'
        bad_file.write_text('{ questo non e json valido')
        with pytest.raises(json.JSONDecodeError):
            SchemaBridge.from_snapshot(str(bad_file))


# =============================================================================
# 10. Documentazione generata
# =============================================================================

class TestGeneratedDocumentation:

    def test_get_documentation_ritorna_stringa(self, minimal_raw_data):
        bridge = SchemaBridge(minimal_raw_data)
        p = bridge.get_parameter('density')
        doc = bridge.get_documentation(p)
        assert isinstance(doc, str)
        assert len(doc) > 0

    def test_documentazione_contiene_min_max(self, minimal_raw_data):
        bridge = SchemaBridge(minimal_raw_data)
        p = bridge.get_parameter('density')
        doc = bridge.get_documentation(p)
        assert '0.01' in doc
        assert '4000' in doc

    def test_documentazione_contiene_variation_mode(self, minimal_raw_data):
        bridge = SchemaBridge(minimal_raw_data)
        p = bridge.get_parameter('density')
        doc = bridge.get_documentation(p)
        assert 'additive' in doc

    def test_documentazione_contiene_exclusive_group(self, exclusive_group_raw_data):
        bridge = SchemaBridge(exclusive_group_raw_data)
        p = bridge.get_parameter('fill_factor')
        doc = bridge.get_documentation(p)
        assert 'density_mode' in doc

    def test_documentazione_parametro_senza_bounds(self):
        raw = {
            'specs': [make_raw_spec('orphan', 'orphan')],
            'bounds': {},
        }
        bridge = SchemaBridge(raw)
        p = bridge.get_parameter('orphan')
        doc = bridge.get_documentation(p)
        assert isinstance(doc, str)


# =============================================================================
# 11. Edge cases
# =============================================================================

class TestEdgeCases:

    def test_schema_completamente_vuoto(self):
        bridge = SchemaBridge({'specs': [], 'bounds': {}})
        # solo e mute sempre presenti
        params = bridge.get_all_parameters()
        assert len(params) == 2
        assert any(p.name == 'solo' for p in params)
        assert any(p.name == 'mute' for p in params)
        groups = bridge.get_exclusive_groups()
        assert 'stream_mode' in groups

    def test_parametro_con_bounds_mancanti_non_solleva(self):
        raw = {
            'specs': [make_raw_spec('orphan', 'orphan', default=0.0)],
            'bounds': {},
        }
        bridge = SchemaBridge(raw)
        params = bridge.get_all_parameters()
        # 1 orphan + 2 solo/mute
        assert len(params) == 3
        orphan = next(p for p in params if p.name == 'orphan')
        assert orphan.min_val is None

    def test_tutti_parametri_interni_completion_vuota(self):
        raw = {
            'specs': [
                make_raw_spec('a', '_path_a_', is_smart=False),
                make_raw_spec('b', '_path_b_'),
            ],
            'bounds': {},
        }
        bridge = SchemaBridge(raw)
        params = bridge.get_completion_parameters()
        names = {p.name for p in params}
        # a e b (interni/non-smart) non compaiono
        assert 'a' not in names and 'b' not in names
        # solo/mute sempre presenti
        assert 'solo' in names

    def test_gruppo_con_un_solo_membro(self):
        raw = {
            'specs': [
                make_raw_spec('solo', 'solo', exclusive_group='my_group'),
            ],
            'bounds': {'solo': make_raw_bounds(0.0, 1.0)},
        }
        bridge = SchemaBridge(raw)
        groups = bridge.get_exclusive_groups()
        assert 'my_group' in groups
        assert len(groups['my_group']) == 1


# =============================================================================
# MODIFICA A - get_stream_context_keys()
# =============================================================================

class TestGetStreamContextKeys:
    """
    get_stream_context_keys() ritorna le chiavi di contesto di ogni stream.

    Include i campi di StreamContext, StreamConfig (esclusi quelli interni),
    piu' i flag speciali del Generator: solo e mute.

    Fonte: caricata via from_python_path() dai dataclass reali,
    oppure dalla lista statica di fallback nello snapshot.
    """

    def test_ritorna_lista(self, minimal_raw_data):
        bridge = SchemaBridge(minimal_raw_data)
        result = bridge.get_stream_context_keys()
        assert isinstance(result, list)

    def test_lista_non_vuota(self, minimal_raw_data):
        bridge = SchemaBridge(minimal_raw_data)
        assert len(bridge.get_stream_context_keys()) > 0

    def test_tutti_elementi_sono_stringhe(self, minimal_raw_data):
        bridge = SchemaBridge(minimal_raw_data)
        for k in bridge.get_stream_context_keys():
            assert isinstance(k, str)

    def test_contiene_stream_context_fields(self, minimal_raw_data):
        """I campi di StreamContext sono presenti."""
        bridge = SchemaBridge(minimal_raw_data)
        keys = bridge.get_stream_context_keys()
        for expected in ['stream_id', 'onset', 'duration', 'sample']:
            assert expected in keys, f"'{expected}' mancante"

    def test_contiene_stream_config_fields(self, minimal_raw_data):
        """I campi di StreamConfig sono presenti."""
        bridge = SchemaBridge(minimal_raw_data)
        keys = bridge.get_stream_context_keys()
        for expected in ['time_mode', 'time_scale', 'range_always_active']:
            assert expected in keys, f"'{expected}' mancante"

    def test_contiene_dephase(self, minimal_raw_data):
        """dephase e' un campo di StreamConfig."""
        bridge = SchemaBridge(minimal_raw_data)
        assert 'dephase' in bridge.get_stream_context_keys()

    def test_contiene_solo(self, minimal_raw_data):
        """solo e' un flag Generator: mette lo stream in modalita' ascolto esclusivo."""
        bridge = SchemaBridge(minimal_raw_data)
        assert 'solo' in bridge.get_stream_context_keys()

    def test_contiene_mute(self, minimal_raw_data):
        """mute e' un flag Generator: silenzia lo stream senza rimuoverlo."""
        bridge = SchemaBridge(minimal_raw_data)
        assert 'mute' in bridge.get_stream_context_keys()

    def test_non_contiene_sample_dur_sec(self, minimal_raw_data):
        """sample_dur_sec e' calcolato internamente, non scritto dall'utente."""
        bridge = SchemaBridge(minimal_raw_data)
        assert 'sample_dur_sec' not in bridge.get_stream_context_keys()

    def test_non_contiene_context(self, minimal_raw_data):
        """context e' un campo interno di StreamConfig, non YAML."""
        bridge = SchemaBridge(minimal_raw_data)
        assert 'context' not in bridge.get_stream_context_keys()

    def test_nessun_duplicato(self, minimal_raw_data):
        bridge = SchemaBridge(minimal_raw_data)
        keys = bridge.get_stream_context_keys()
        assert len(keys) == len(set(keys))

    def test_from_python_path_carica_da_dataclass(self, tmp_path):
        """
        from_python_path() legge i campi reali da StreamContext e StreamConfig.
        I campi devono corrispondere a quelli dichiarati nei dataclass.
        """
        src = tmp_path / 'src'
        src.mkdir()
        core_dir = src / 'core'
        core_dir.mkdir()
        (core_dir / '__init__.py').write_text('')
        params_dir = src / 'parameters'
        params_dir.mkdir()
        (params_dir / '__init__.py').write_text('')

        (core_dir / 'stream_config.py').write_text("""
from dataclasses import dataclass
from typing import Optional

@dataclass(frozen=True)
class StreamContext:
    stream_id: str
    onset: float
    duration: float
    sample: str
    sample_dur_sec: float

@dataclass(frozen=True)
class StreamConfig:
    dephase: object = False
    range_always_active: bool = False
    time_mode: str = 'absolute'
    time_scale: float = 1.0
    context: Optional[object] = None
""")
        (params_dir / 'parameter_schema.py').write_text("""
from dataclasses import dataclass
from typing import Optional, Any, List

@dataclass(frozen=True)
class ParameterSpec:
    name: str
    yaml_path: str
    default: Any
    range_path: Optional[str] = None
    dephase_key: Optional[str] = None
    is_smart: bool = True
    exclusive_group: Optional[str] = None
    group_priority: int = 99

STREAM_PARAMETER_SCHEMA = []
POINTER_PARAMETER_SCHEMA = []
PITCH_PARAMETER_SCHEMA = []
DENSITY_PARAMETER_SCHEMA = []
ALL_SCHEMAS = {
    'stream': STREAM_PARAMETER_SCHEMA,
    'pointer': POINTER_PARAMETER_SCHEMA,
    'pitch': PITCH_PARAMETER_SCHEMA,
    'density': DENSITY_PARAMETER_SCHEMA,
}
""")
        (params_dir / 'parameter_definitions.py').write_text("""
from dataclasses import dataclass

@dataclass(frozen=True)
class ParameterBounds:
    min_val: float
    max_val: float
    min_range: float = 0.0
    max_range: float = 0.0
    default_jitter: float = 0.0
    variation_mode: str = 'additive'

GRANULAR_PARAMETERS = {}

def get_parameter_definition(name):
    raise KeyError(name)
""")

        bridge = SchemaBridge.from_python_path(str(src))
        keys = bridge.get_stream_context_keys()

        # Campi di StreamContext (escluso sample_dur_sec)
        assert 'stream_id' in keys
        assert 'onset' in keys
        assert 'duration' in keys
        assert 'sample' in keys
        assert 'sample_dur_sec' not in keys

        # Campi di StreamConfig (escluso context)
        assert 'time_mode' in keys
        assert 'dephase' in keys
        assert 'context' not in keys

        # Flag speciali sempre presenti
        assert 'solo' in keys
        assert 'mute' in keys


# =============================================================================
# MODIFICA A - get_block_keys()
# =============================================================================

class TestGetBlockKeys:
    """
    get_block_keys() ritorna i prefissi unici degli yaml_path annidati.

    Da 'grain.duration', 'grain.envelope' ricava 'grain'.
    Da 'pitch.ratio', 'pitch.semitones' ricava 'pitch'.
    I parametri root-level (senza punto) non generano block keys.
    """

    def test_ritorna_lista(self, minimal_raw_data):
        bridge = SchemaBridge(minimal_raw_data)
        result = bridge.get_block_keys()
        assert isinstance(result, list)

    def test_nessun_duplicato(self, full_raw_data):
        bridge = SchemaBridge(full_raw_data)
        keys = bridge.get_block_keys()
        assert len(keys) == len(set(keys))

    def test_grain_presente_se_ci_sono_params_grain(self, full_raw_data):
        bridge = SchemaBridge(full_raw_data)
        assert 'grain' in bridge.get_block_keys()

    def test_pointer_presente(self):
        raw = {
            'specs': [
                make_raw_spec('pointer_speed', 'pointer.speed_ratio'),
                make_raw_spec('pointer_start', 'pointer.start'),
            ],
            'bounds': {},
        }
        bridge = SchemaBridge(raw)
        assert 'pointer' in bridge.get_block_keys()

    def test_pitch_presente(self):
        raw = {
            'specs': [
                make_raw_spec('pitch_ratio', 'pitch.ratio'),
            ],
            'bounds': {},
        }
        bridge = SchemaBridge(raw)
        assert 'pitch' in bridge.get_block_keys()

    def test_root_level_params_non_generano_block_key(self):
        """Parametri senza punto nello yaml_path non diventano block keys."""
        raw = {
            'specs': [
                make_raw_spec('density', 'density'),
                make_raw_spec('volume', 'volume'),
            ],
            'bounds': {},
        }
        bridge = SchemaBridge(raw)
        assert bridge.get_block_keys() == []

    def test_parametri_interni_non_generano_block_key(self, internal_params_raw_data):
        """yaml_path che iniziano con '_' non generano block keys."""
        bridge = SchemaBridge(internal_params_raw_data)
        keys = bridge.get_block_keys()
        assert not any(k.startswith('_') for k in keys)

    def test_schema_vuoto_ritorna_lista_vuota(self):
        bridge = SchemaBridge({'specs': [], 'bounds': {}})
        assert bridge.get_block_keys() == []

    def test_tutti_elementi_sono_stringhe(self, full_raw_data):
        bridge = SchemaBridge(full_raw_data)
        for k in bridge.get_block_keys():
            assert isinstance(k, str)


# =============================================================================
# MODIFICA A - get_dephase_keys()
# =============================================================================

class TestGetDephaseKeys:
    """
    get_dephase_keys() ritorna le chiavi valide del blocco dephase:.

    Le chiavi sono derivate dai valori unici di dephase_key presenti
    negli ParameterSpec di tutti gli schema. Non sono hardcoded.

    Dai schema reali:
        volume (da volume), pan (da pan), duration (da grain_duration),
        envelope (da grain_envelope), reverse (da reverse),
        pointer (da pointer_deviation), pitch (da pitch_ratio/semitones)
    """

    def test_ritorna_lista(self, minimal_raw_data):
        bridge = SchemaBridge(minimal_raw_data)
        result = bridge.get_dephase_keys()
        assert isinstance(result, list)

    def test_nessun_duplicato(self, full_raw_data):
        """Nessuna chiave duplicata anche se piu' parametri hanno la stessa dephase_key."""
        bridge = SchemaBridge(full_raw_data)
        keys = bridge.get_dephase_keys()
        assert len(keys) == len(set(keys))

    def test_chiave_volume_presente(self):
        raw = {
            'specs': [make_raw_spec('volume', 'volume',
                                    **{'dephase_key': 'volume'})],
            'bounds': {},
        }
        # Nota: make_raw_spec non supporta dephase_key direttamente,
        # costruiamo manualmente
        raw2 = {
            'specs': [{
                'name': 'volume', 'yaml_path': 'volume', 'default': -6.0,
                'is_smart': True, 'exclusive_group': None, 'group_priority': 99,
                'range_path': None, 'dephase_key': 'volume',
            }],
            'bounds': {},
        }
        bridge = SchemaBridge(raw2)
        assert 'volume' in bridge.get_dephase_keys()

    def test_parametri_senza_dephase_key_non_contribuiscono(self):
        raw = {
            'specs': [{
                'name': 'density', 'yaml_path': 'density', 'default': None,
                'is_smart': True, 'exclusive_group': None, 'group_priority': 99,
                'range_path': None, 'dephase_key': None,
            }],
            'bounds': {},
        }
        bridge = SchemaBridge(raw)
        assert bridge.get_dephase_keys() == []

    def test_pitch_condiviso_da_piu_parametri_appare_una_volta(self):
        """pitch_ratio e pitch_semitones hanno entrambi dephase_key='pitch'."""
        raw = {
            'specs': [
                {
                    'name': 'pitch_ratio', 'yaml_path': 'pitch.ratio',
                    'default': 1.0, 'is_smart': True, 'exclusive_group': 'pitch_mode',
                    'group_priority': 2, 'range_path': None, 'dephase_key': 'pitch',
                },
                {
                    'name': 'pitch_semitones', 'yaml_path': 'pitch.semitones',
                    'default': None, 'is_smart': True, 'exclusive_group': 'pitch_mode',
                    'group_priority': 1, 'range_path': None, 'dephase_key': 'pitch',
                },
            ],
            'bounds': {},
        }
        bridge = SchemaBridge(raw)
        keys = bridge.get_dephase_keys()
        assert keys.count('pitch') == 1

    def test_schema_vuoto_ritorna_lista_vuota(self):
        bridge = SchemaBridge({'specs': [], 'bounds': {}})
        assert bridge.get_dephase_keys() == []

    def test_tutti_elementi_sono_stringhe(self):
        raw = {
            'specs': [
                {
                    'name': 'volume', 'yaml_path': 'volume', 'default': -6.0,
                    'is_smart': True, 'exclusive_group': None, 'group_priority': 99,
                    'range_path': None, 'dephase_key': 'volume',
                },
            ],
            'bounds': {},
        }
        bridge = SchemaBridge(raw)
        for k in bridge.get_dephase_keys():
            assert isinstance(k, str)
