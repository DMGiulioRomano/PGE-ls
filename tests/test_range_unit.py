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


# =============================================================================
# 2. Diagnostica
# =============================================================================

def _stream(grain_body: str, anchor: str = None) -> str:
    """Uno stream completo con un blocco grain (`grain_body` a indent 6)."""
    head = ("streams:\n"
            "  - stream_id: s1\n"
            "    onset: 0.0\n"
            "    duration: 10.0\n"
            "    sample: f.wav\n")
    if anchor is not None:
        head += f"    range_anchor: {anchor}\n"
    return head + "    grain:\n" + grain_body


def _line_of(text: str, needle: str) -> int:
    for n, line in enumerate(text.split('\n')):
        if needle in line:
            return n
    raise AssertionError(f'{needle!r} non trovato')


def _errors(bridge, text):
    from granular_ls.providers.diagnostic_provider import DiagnosticProvider
    from lsprotocol.types import DiagnosticSeverity
    return [d for d in DiagnosticProvider(bridge).get_diagnostics(text)
            if d.severity == DiagnosticSeverity.Error]


class TestVocabolario:
    """Grafia fuori da `RANGE_UNITS`: `InvalidFieldValueError` nel motore."""

    def test_grafia_sconosciuta(self, bridge):
        text = _stream("      duration: 0.05\n"
                       "      duration_range: 0.5\n"
                       "      duration_range_unit: relativo\n")
        errs = _errors(bridge, text)
        assert len(errs) == 1
        e = errs[0]
        assert e.range.start.line == _line_of(text, 'duration_range_unit')
        assert '`grain.duration_range_unit`' in e.message
        assert '`relativo`' in e.message
        assert 'absolute, relative' in e.message

    @pytest.mark.parametrize('unit', ['absolute', 'relative', '"relative"',
                                      "'absolute'", 'relative  # frazione'])
    def test_grafie_valide(self, bridge, unit):
        text = _stream("      duration: 0.05\n"
                       "      duration_range: 0.5\n"
                       f"      duration_range_unit: {unit}\n")
        assert _errors(bridge, text) == []

    @pytest.mark.parametrize('unit, label', [
        ('Relative', 'Relative'), ('null', 'null'), ('~', 'null'),
        ('1', '1'), ('true', 'true'), ('""', '""'),
    ])
    def test_valori_che_il_motore_rifiuta(self, bridge, unit, label):
        text = _stream("      duration: 0.05\n"
                       "      duration_range: 0.5\n"
                       f"      duration_range_unit: {unit}\n")
        errs = _errors(bridge, text)
        assert len(errs) == 1
        assert f'`{label}`' in errs[0].message

    def test_chiave_vuota_e_un_solo_errore(self, bridge):
        """Scritta e lasciata vuota non e' la chiave assente: il motore la
        rifiuta. La dice `_check_missing_values`, come le altre chiavi
        stringa; la fase del vocabolario non la ripete."""
        text = _stream("      duration: 0.05\n"
                       "      duration_range: 0.5\n"
                       "      duration_range_unit:\n")
        errs = _errors(bridge, text)
        assert len(errs) == 1
        assert errs[0].range.start.line == _line_of(text, 'duration_range_unit')

    def test_il_vocabolario_viene_dal_bridge(self):
        bridge = SchemaBridge(_raw(range_units=['absolute', 'relative', 'x']))
        text = _stream("      duration: 0.05\n"
                       "      duration_range: 0.5\n"
                       "      duration_range_unit: x\n")
        assert _errors(bridge, text) == []

    def test_fuori_dal_blocco_non_e_la_sua_chiave(self, bridge):
        """Il motore legge la chiave al path dichiarato, e solo li'."""
        text = _stream("      duration: 0.05\n").replace(
            "    grain:\n", "    duration_range_unit: relativo\n    grain:\n")
        assert [e for e in _errors(bridge, text)
                if 'relativo' in e.message] == []

    def test_motore_che_precede_267_non_conosce_la_chiave(self):
        bridge = SchemaBridge(_raw(range_unit_path=None))
        text = _stream("      duration: 0.05\n"
                       "      duration_range_unit: relativo\n")
        assert _errors(bridge, text) == []


class TestRelativoSenzaRange:
    """`relative` senza il range che governa: `MissingFieldError`."""

    def test_range_assente(self, bridge):
        text = _stream("      duration: 0.05\n"
                       "      duration_range_unit: relative\n")
        errs = _errors(bridge, text)
        assert len(errs) == 1
        e = errs[0]
        assert e.range.start.line == _line_of(text, 'duration_range_unit')
        assert '`grain.duration_range`' in e.message
        assert 'jitter implicito' in e.message

    def test_range_null(self, bridge):
        text = _stream("      duration: 0.05\n"
                       "      duration_range: null\n"
                       "      duration_range_unit: relative\n")
        errs = _errors(bridge, text)
        assert len(errs) == 1
        assert errs[0].range.start.line == _line_of(text, 'duration_range_unit')

    def test_range_scritto_vuoto_e_un_solo_errore(self, bridge):
        """Lo dice gia' `_check_missing_values` sulla riga del range."""
        text = _stream("      duration: 0.05\n"
                       "      duration_range:\n"
                       "      duration_range_unit: relative\n")
        errs = _errors(bridge, text)
        assert len(errs) == 1
        assert errs[0].range.start.line == _line_of(text, 'duration_range:')

    def test_assoluto_senza_range_e_valido(self, bridge):
        text = _stream("      duration: 0.05\n"
                       "      duration_range_unit: absolute\n")
        assert _errors(bridge, text) == []

    def test_range_envelope_block_style(self, bridge):
        text = _stream("      duration: 0.05\n"
                       "      duration_range:\n"
                       "        - [0, 0.1]\n"
                       "        - [10, 0.5]\n"
                       "      duration_range_unit: relative\n")
        assert _errors(bridge, text) == []

    def test_la_chiave_prima_del_range(self, bridge):
        text = _stream("      duration_range_unit: relative\n"
                       "      duration: 0.05\n"
                       "      duration_range: 0.5\n")
        assert _errors(bridge, text) == []


class TestDominioRelativo:
    """Il dominio del range relativo e' `RELATIVE_RANGE_BOUNDS`, non scalato."""

    def _range_errors(self, bridge, text):
        riga = _line_of(text, 'duration_range:')
        return [e for e in _errors(bridge, text) if e.range.start.line == riga]

    def test_frazione_oltre_uno(self, bridge):
        text = _stream("      duration: 0.05\n"
                       "      duration_range: 1.5\n"
                       "      duration_range_unit: relative\n")
        errs = self._range_errors(bridge, text)
        assert len(errs) == 1
        assert 'frazione' in errs[0].message
        assert '[0, 1]' in errs[0].message

    def test_frazione_dentro(self, bridge):
        text = _stream("      duration: 0.05\n"
                       "      duration_range: 0.5\n"
                       "      duration_range_unit: relative\n")
        assert _errors(bridge, text) == []

    def test_si_usa_il_dominio_relativo_non_quello_assoluto(self):
        """I due domini coincidono per caso su `grain_duration` ([0, 1]):
        allargando quello relativo si vede quale dei due viene letto."""
        bridge = SchemaBridge(_raw(relative_range_bounds=[0.0, 2.0]))
        rel = _stream("      duration: 0.05\n"
                      "      duration_range: 1.5\n"
                      "      duration_range_unit: relative\n")
        assert _errors(bridge, rel) == []
        ass = rel.replace('relative', 'absolute')
        assert len(self._range_errors(bridge, ass)) == 1

    @pytest.mark.parametrize('unit, dur', [('milliseconds', 50),
                                           ('samples', 2400)])
    def test_l_unita_della_base_non_scala_la_frazione(self, bridge, unit, dur):
        """Sotto `duration_unit` non-secondi il range assoluto si converte
        insieme alla base; quello relativo no — una frazione non ha unita'.
        `5` era dentro [0, 1000] ms, e il motore lo rifiuta come frazione."""
        text = _stream(f"      duration_unit: {unit}\n"
                       f"      duration: {dur}\n"
                       "      duration_range: 5\n"
                       "      duration_range_unit: relative\n")
        errs = self._range_errors(bridge, text)
        assert len(errs) == 1
        assert 'frazione' in errs[0].message

    def test_sotto_millisecondi_la_frazione_valida_passa(self, bridge):
        text = _stream("      duration_unit: milliseconds\n"
                       "      duration: 50\n"
                       "      duration_range: 0.5\n"
                       "      duration_range_unit: relative\n")
        assert _errors(bridge, text) == []

    def test_sotto_millisecondi_l_assoluto_resta_in_millisecondi(self, bridge):
        dentro = _stream("      duration_unit: milliseconds\n"
                         "      duration: 50\n"
                         "      duration_range: 5\n")
        assert _errors(bridge, dentro) == []
        fuori = dentro.replace('duration_range: 5', 'duration_range: 1500')
        assert len(self._range_errors(bridge, fuori)) == 1

    def test_envelope_con_una_y_fuori(self, bridge):
        text = _stream("      duration: 0.05\n"
                       "      duration_range: [[0, 0.2], [10, 1.5]]\n"
                       "      duration_range_unit: relative\n")
        errs = self._range_errors(bridge, text)
        assert len(errs) == 1
        assert '1.5' in errs[0].message

    def test_envelope_block_style_con_una_y_fuori(self, bridge):
        text = _stream("      duration: 0.05\n"
                       "      duration_range:\n"
                       "        - [0, 0.2]\n"
                       "        - [10, 1.5]\n"
                       "      duration_range_unit: relative\n")
        assert len(self._range_errors(bridge, text)) == 1

    def test_stringa_numerica(self, bridge):
        text = _stream("      duration: 0.05\n"
                       '      duration_range: "1.5"\n'
                       "      duration_range_unit: relative\n")
        assert len(self._range_errors(bridge, text)) == 1

    def test_espressione_non_si_valuta(self, bridge):
        text = _stream("      duration: 0.05\n"
                       '      duration_range: "(3/2)"\n'
                       "      duration_range_unit: relative\n")
        assert _errors(bridge, text) == []

    def test_unita_sconosciuta_non_misura_il_range(self, bridge):
        """Il motore rifiuta l'unita' prima di guardare il range: di errori
        ce n'e' uno, sulla riga dell'unita'."""
        text = _stream("      duration: 0.05\n"
                       "      duration_range: 1.5\n"
                       "      duration_range_unit: relativo\n")
        errs = _errors(bridge, text)
        assert len(errs) == 1
        assert errs[0].range.start.line == _line_of(text, 'duration_range_unit')


class TestTettoBandaRelativa:
    """Sotto `range_anchor: min` il tetto e' `base + range * |base|`.

    Sommare una frazione a una durata sommerebbe due grandezze diverse, e il
    controllo lascerebbe passare proprio le bande larghe: con base 8 e
    frazione 0.5 la somma da' 8.5, la banda arriva a 12.
    """

    def _band(self, bridge, text):
        return [e for e in _errors(bridge, text) if 'banda' in e.message]

    def test_la_frazione_moltiplica_la_base(self, bridge):
        text = _stream("      duration: 8\n"
                       "      duration_range: 0.5\n"
                       "      duration_range_unit: relative\n", anchor='min')
        errs = self._band(bridge, text)
        assert len(errs) == 1
        assert '12' in errs[0].message
        assert '|base|' in errs[0].message
        assert errs[0].range.start.line == _line_of(text, 'duration_range:')

    def test_la_stessa_coppia_assoluta_sta_dentro(self, bridge):
        text = _stream("      duration: 8\n"
                       "      duration_range: 0.5\n", anchor='min')
        assert self._band(bridge, text) == []

    def test_dentro_il_tetto(self, bridge):
        text = _stream("      duration: 6\n"
                       "      duration_range: 0.5\n"
                       "      duration_range_unit: relative\n", anchor='min')
        assert self._band(bridge, text) == []

    def test_sotto_center_nessun_controllo(self, bridge):
        text = _stream("      duration: 8\n"
                       "      duration_range: 0.5\n"
                       "      duration_range_unit: relative\n")
        assert self._band(bridge, text) == []

    def test_in_millisecondi_riporta_i_numeri_scritti(self, bridge):
        text = _stream("      duration_unit: milliseconds\n"
                       "      duration: 8000\n"
                       "      duration_range: 0.5\n"
                       "      duration_range_unit: relative\n", anchor='min')
        errs = self._band(bridge, text)
        assert len(errs) == 1
        assert '12000 millisecondi' in errs[0].message

    def test_base_envelope(self, bridge):
        text = _stream("      duration: [[0, 1], [10, 8]]\n"
                       "      duration_range: 0.5\n"
                       "      duration_range_unit: relative\n", anchor='min')
        assert len(self._band(bridge, text)) == 1

    def test_range_envelope(self, bridge):
        text = _stream("      duration: 8\n"
                       "      duration_range: [[0, 0.1], [10, 0.5]]\n"
                       "      duration_range_unit: relative\n", anchor='min')
        assert len(self._band(bridge, text)) == 1

    def test_base_assente_vale_il_default(self, bridge):
        text = _stream("      duration_range: 0.5\n"
                       "      duration_range_unit: relative\n", anchor='min')
        assert self._band(bridge, text) == []

    def test_da_centrata_starebbe_dentro(self, bridge):
        """Centrata la banda arriva a `base * (1 + r/2)`: 8 * 1.25 = 10."""
        text = _stream("      duration: 8\n"
                       "      duration_range: 0.5\n"
                       "      duration_range_unit: relative\n", anchor='min')
        assert 'Da centrata la stessa coppia starebbe dentro' in \
            self._band(bridge, text)[0].message

    def test_da_centrata_sforerebbe_anche_lei(self, bridge):
        text = _stream("      duration: 9\n"
                       "      duration_range: 0.5\n"
                       "      duration_range_unit: relative\n", anchor='min')
        assert 'sforerebbe anche lei' in self._band(bridge, text)[0].message
