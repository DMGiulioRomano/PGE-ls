"""
test_range_unit.py

`grain.duration_range_unit` (PGE #267, issue PGE-ls #50).

Il motore ha dato ai `_range` un terzo asse, accanto a `distribution_mode`
(come la banda si riempie) e a `range_anchor` (dove cade la base): **quanto la
banda e' larga**.

| `duration_range_unit` | `duration_range` e' | dominio | scalato da `duration_unit` |
|---|---|---|---|
| assente / `absolute` | una durata, nell'unita' della base | `[min_range, max_range]` | si' |
| `relative` | una frazione della base | `RELATIVE_RANGE_BOUNDS` | no |

Il meccanismo e' dichiarativo: `ParameterSpec.range_unit_path` dice quale
chiave governa l'unita' del `_range` di quel parametro. Il language server lo
legge dal campo, non dal nome della chiave, e il vocabolario dal registry
`RANGE_UNITS`, non da una copia: un secondo parametro cablato domani entra
senza toccare questo codice.

Il motore rifiuta al parse due cose che qui si dicono mentre si scrive: una
grafia fuori vocabolario (vuota compresa) e `relative` senza il range che
governa — senza, varrebbe il jitter implicito, che e' assoluto.
"""

import json

import pytest

from granular_ls.schema_bridge import ParameterInfo, SchemaBridge


# =============================================================================
# FIXTURES
# =============================================================================

UNIT_PATH = 'grain.duration_range_unit'


def _spec(name, yaml_path, default=0.0, range_path=None, range_unit_path=None):
    return {'name': name, 'yaml_path': yaml_path, 'default': default,
            'is_smart': True, 'exclusive_group': None, 'group_priority': 99,
            'range_path': range_path, 'range_unit_path': range_unit_path,
            'deviation_probability_key': None}


def _bounds(min_val, max_val, min_range=0.0, max_range=0.0):
    return {'min_val': min_val, 'max_val': max_val, 'min_range': min_range,
            'max_range': max_range, 'default_jitter': 0.0,
            'variation_mode': 'additive'}


def _raw(range_unit_path=UNIT_PATH, **extra):
    """Lo schema del motore per la coppia che ha la banda relativa.

    I bound sono quelli veri: `grain_duration` in [0.001, 10] s, il suo range
    in [0, 1] s. Che il dominio relativo sia anch'esso [0, 1] e' una
    coincidenza numerica — e' proprio per questo che i test che vogliono
    vedere quale dei due domini viene usato alzano quello relativo.
    """
    raw = {
        'specs': [
            _spec('volume', 'volume', range_path='volume_range'),
            _spec('grain_duration', 'grain.duration', default=0.05,
                  range_path='grain.duration_range',
                  range_unit_path=range_unit_path),
        ],
        'bounds': {
            'volume': _bounds(-120.0, 12.0, 0.0, 24.0),
            'grain_duration': _bounds(0.001, 10.0, 0.0, 1.0),
        },
    }
    raw.update(extra)
    return raw


@pytest.fixture
def bridge():
    return SchemaBridge(_raw())


# =============================================================================
# 1. SchemaBridge: il campo, il vocabolario, il dominio, i legami
# =============================================================================

class TestParameterInfoRangeUnitPath:

    def test_il_campo_ha_un_default(self):
        """Chi costruisce un ParameterInfo senza il campo nuovo non si rompe."""
        info = ParameterInfo(
            name='density', yaml_path='density', default=None,
            is_smart=True, exclusive_group=None, group_priority=99,
            min_val=0.01, max_val=4000.0, min_range=0.0, max_range=0.0,
            variation_mode='additive', is_internal=False,
        )
        assert info.range_unit_path is None

    def test_il_bridge_lo_porta_dallo_spec(self, bridge):
        assert bridge.get_parameter('grain_duration').range_unit_path == UNIT_PATH

    def test_senza_campo_resta_none(self, bridge):
        assert bridge.get_parameter('volume').range_unit_path is None
        # Il `_range` espanso non governa niente: e' governato.
        assert bridge.get_parameter('grain_duration_range').range_unit_path is None


class TestRangeUnitsVocabulary:

    def test_fallback_statico(self):
        assert SchemaBridge({'specs': [], 'bounds': {}}).get_range_units() == [
            'absolute', 'relative']

    def test_letto_dal_raw_data(self):
        bridge = SchemaBridge(_raw(range_units=['absolute', 'relative', 'x']))
        assert bridge.get_range_units() == ['absolute', 'relative', 'x']

    def test_restituisce_una_copia(self, bridge):
        bridge.get_range_units().append('x')
        assert 'x' not in bridge.get_range_units()


class TestRelativeRangeBounds:

    def test_fallback_statico(self):
        assert SchemaBridge({'specs': [], 'bounds': {}}) \
            .get_relative_range_bounds() == (0.0, 1.0)

    def test_letto_dal_raw_data(self):
        bridge = SchemaBridge(_raw(relative_range_bounds=[0.0, 2.0]))
        assert bridge.get_relative_range_bounds() == (0.0, 2.0)


class TestRangeUnitBindings:

    def test_un_legame_per_parametro_cablato(self, bridge):
        legami = bridge.get_range_unit_bindings()
        assert len(legami) == 1
        legame = legami[0]
        assert legame.unit_path == UNIT_PATH
        assert legame.base.yaml_path == 'grain.duration'
        assert legame.range.yaml_path == 'grain.duration_range'

    def test_nessun_legame_su_un_motore_che_precede_267(self):
        bridge = SchemaBridge(_raw(range_unit_path=None))
        assert bridge.get_range_unit_bindings() == []

    def test_un_unita_senza_range_non_lega_niente(self):
        """`range_unit_path` ha senso solo insieme a `range_path` (motore)."""
        raw = _raw()
        raw['specs'][1]['range_path'] = None
        assert SchemaBridge(raw).get_range_unit_bindings() == []

    def test_dichiarativo_un_secondo_parametro_entra_da_solo(self):
        raw = _raw()
        raw['specs'][0]['range_unit_path'] = 'volume_range_unit'
        paths = {l.unit_path for l in SchemaBridge(raw).get_range_unit_bindings()}
        assert paths == {UNIT_PATH, 'volume_range_unit'}


class TestIsRelative:
    """Mirror di `range_unit_is_relative`: lettura pura, niente vocabolario."""

    @pytest.mark.parametrize('grafia, attesa', [
        ('relative', True),
        ('absolute', False),
        # Una grafia sbagliata legge come non-relativa: l'errore lo dice la
        # validazione del vocabolario, non questa lettura.
        ('Relative', False), ('relativo', False),
        (None, False), ('', False), (1, False), (True, False),
    ])
    def test_lettura(self, grafia, attesa):
        from granular_ls.range_unit import is_relative
        assert is_relative(grafia) is attesa


class TestSnapshotRoundTrip:
    """Il `.vsix` gira sullo snapshot: quel che non ci arriva non esiste."""

    def _round_trip(self, bridge, tmp_path):
        path = tmp_path / 'schema_snapshot.json'
        path.write_text(bridge.generate_snapshot())
        return SchemaBridge.from_snapshot(str(path))

    def test_il_legame_sopravvive(self, bridge, tmp_path):
        ricaricato = self._round_trip(bridge, tmp_path)
        legami = ricaricato.get_range_unit_bindings()
        assert [(l.unit_path, l.base.name, l.range.name) for l in legami] == [
            (UNIT_PATH, 'grain_duration', 'grain_duration_range')]

    def test_vocabolario_e_dominio_sopravvivono(self, tmp_path):
        bridge = SchemaBridge(_raw(range_units=['absolute', 'relative', 'x'],
                                   relative_range_bounds=[0.0, 2.0]))
        ricaricato = self._round_trip(bridge, tmp_path)
        assert ricaricato.get_range_units() == ['absolute', 'relative', 'x']
        assert ricaricato.get_relative_range_bounds() == (0.0, 2.0)

    def test_lo_snapshot_e_json_valido(self, bridge):
        data = json.loads(bridge.generate_snapshot())
        assert data['range_units'] == ['absolute', 'relative']
        assert data['relative_range_bounds'] == [0.0, 1.0]
