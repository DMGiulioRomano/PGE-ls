"""
tests/test_maps.py

Le mappe caotiche di PGE (#307) viste dal language server (issue #73, #74):
`maps:` e le sue regole, le espressioni del motore, `grain.iot`,
`pointer.position`, e la regola delle parentesi esterne (PGE #309).

YAML che il motore rende non ha diagnostiche; cio' che il motore ferma al
caricamento e' un Error, con il messaggio del motore.
"""
import pytest
from lsprotocol.types import DiagnosticSeverity

from granular_ls import engine_expressions as EX
from granular_ls import maps as M
from granular_ls.providers.completion_provider import CompletionProvider
from granular_ls.providers.diagnostic_provider import DiagnosticProvider
from granular_ls.providers.hover_provider import HoverProvider
from granular_ls.schema_bridge import SchemaBridge
from granular_ls.yaml_analyzer import YamlContext


def _spec(name, yaml_path, default=0.0, group=None, priority=99):
    return {'name': name, 'yaml_path': yaml_path, 'default': default,
            'is_smart': True, 'range_path': None, 'dephase_key': None,
            'exclusive_group': group, 'group_priority': priority}


def _bounds(lo, hi):
    return {'min_val': lo, 'max_val': hi, 'min_range': 0.0, 'max_range': 0.0,
            'default_jitter': 0.0, 'variation_mode': 'additive'}


@pytest.fixture
def bridge():
    """Un bridge con la superficie dei modi nuovi, com'e' nel motore."""
    return SchemaBridge({
        'specs': [
            _spec('density', 'density', None, 'density_mode', 2),
            _spec('fill_factor', 'fill_factor', 2, 'density_mode', 1),
            _spec('grain_iot', 'grain.iot', None, 'density_mode', 0),
            _spec('distribution', 'distribution'),
            _spec('volume', 'volume'),
            _spec('pan', 'pan'),
            _spec('grain_duration', 'grain.duration', 0.05),
            _spec('pointer_position', 'pointer.position', None),
            _spec('pointer_speed_ratio', 'pointer.speed_ratio', 1.0),
        ],
        'bounds': {
            'density': _bounds(0.01, None),
            'fill_factor': _bounds(0.001, 50.0),
            'grain_iot': _bounds(1 / 48000, 100.0),
            'distribution': _bounds(0.0, 1.0),
            'volume': _bounds(-120.0, 12.0),
            'pan': _bounds(-3600.0, 3600.0),
            'grain_duration': _bounds(0.001, 10.0),
            'pointer_position': _bounds(0.0, 1.0),
            'pointer_speed_ratio': _bounds(-100.0, 100.0),
        },
        'wrapping_parameters': ['pointer_position'],
    })


HEAD = "streams:\n  - stream_id: s1\n    sample: x.wav\n    duration: 4\n"


def diags(bridge, body, head=HEAD):
    return DiagnosticProvider(bridge).get_diagnostics(head + body)


def errors(bridge, body, head=HEAD):
    return [d for d in diags(bridge, body, head)
            if d.severity == DiagnosticSeverity.Error]


# =============================================================================
# YAML valido del motore: zero diagnostiche (le tabelle di #73 e #74)
# =============================================================================

VALIDI = {
    '308': "    maps: {m1: {type: logistic, r: 3.5}}\n"
           "    grain:\n      duration: (0.02 + 0.08*m1)\n",
    '310': "    maps:\n"
           "      m1: {type: logistic, r: [[0, 3.0], [4, 4.0]], clock: [[0, 2], [4, 20]]}\n"
           "      m2: {type: tent, r: 3.2, iterate: 8, x0: 0.4}\n",
    '311': "    maps: {m1: {type: logistic, r: 3.5}}\n"
           "    grain:\n      iot: (0.020 + 0.180*m1)\n",
    '311-ms': "    grain:\n      iot: 500\n      duration: 50\n"
              "      duration_unit: milliseconds\n",
    '312': "    pointer:\n      position: 0.5\n",
    '312-wrap': "    pointer:\n      position: 1.25\n",
    '312-env': "    pointer:\n      position: [[0, 0], [4, 3]]\n",
    '313-henon': "    maps: {m1: {type: henon, r: 1.4}}\n"
                 "    volume: (-18 + 4*m1)\n    pan: (100*m1.y)\n",
    '313-custom': "    maps: {m2: {type: custom, x: sin(r*x)*cos(x), r: 3}}\n"
                  "    pitch:\n      semitones: (12*m2)\n",
    '313-rd': "    maps: {m1: {type: logistic, r: 3.5}}\n"
              "    grain:\n      read_direction: (sign(m1 - 0.6))\n",
    '309': "    volume: (max(-12, -6))\n    grain:\n      duration: (pow(10, -2))\n",
    '309-gia-numero': "    pan: -(90/2)\n",
    'titolo': "    volume: -6\n",
}


@pytest.mark.parametrize('name', sorted(VALIDI))
def test_yaml_valido_non_ha_diagnostiche(bridge, name):
    assert diags(bridge, VALIDI[name]) == []


def test_un_titolo_non_e_un_espressione_a_meta(bridge):
    head = "title: 11 - (2026)\n" + HEAD
    assert diags(bridge, "    volume: -6\n", head) == []


# =============================================================================
# Le regole di `maps:`
# =============================================================================

ERRORI = {
    'tipo': "    maps: {m1: {type: lorenz, r: 3.5}}\n",
    'chiave': "    maps: {m1: {type: logistic, r: 3.5, clok: 2}}\n",
    'manca-tipo': "    maps: {m1: {r: 3.5}}\n",
    'manca-r': "    maps: {m1: {type: logistic}}\n",
    'x-catalogo': "    maps: {m1: {type: logistic, r: 3.5, x: r*x}}\n",
    'y0': "    maps: {m1: {type: logistic, r: 3.5, y0: 0.1}}\n",
    'custom-senza-x': "    maps: {m1: {type: custom, r: 3}}\n",
    'custom-legge-y': "    maps: {m1: {type: custom, x: x + y, r: 1}}\n",
    'custom-sintassi': "    maps: {m1: {type: custom, x: 'r*(x', r: 1}}\n",
    'clock-iterate': "    maps: {m1: {type: logistic, r: 3.5, clock: 2, iterate: 3}}\n",
    'iterate-41': "    maps: {m1: {type: logistic, r: 3.5, iterate: 41}}\n",
    'iterate-transient': "    maps: {m1: {type: logistic, r: 3.5, iterate: 4, transient: 1}}\n",
    'transient-tetto': "    maps: {m1: {type: logistic, r: 3.5, transient: 100001}}\n",
    'clock-zero': "    maps: {m1: {type: logistic, r: 3.5, clock: 0}}\n",
    'clock-tetto': "    maps: {m1: {type: logistic, r: 3.5, clock: 48001}}\n",
    'nome-riservato': "    maps: {sin: {type: logistic, r: 3.5}}\n",
    'vuota': "    maps: {}\n",
}


@pytest.mark.parametrize('name', sorted(ERRORI))
def test_cio_che_il_motore_rifiuta_di_una_mappa(bridge, name):
    assert len(errors(bridge, ERRORI[name])) >= 1


def test_tetti_inclusi(bridge):
    for body in ("    maps: {m1: {type: logistic, r: 3.5, iterate: 40}}\n",
                 "    maps: {m1: {type: logistic, r: 3.5, transient: 100000}}\n",
                 "    maps: {m1: {type: logistic, r: 3.5, clock: 48000}}\n"):
        assert diags(bridge, body) == [], body


def test_ogni_tipo_del_catalogo_e_valido(bridge):
    for tipo in M.MAP_TYPES:
        r = M.MAP_KINDS[tipo].r_range[1]
        assert diags(bridge, f"    maps: {{m1: {{type: {tipo}, r: {r}}}}}\n") == []


def test_l_errore_sta_sulla_riga_della_mappa(bridge):
    body = "    maps:\n      m1:\n        type: logistic\n        r: 3.5\n        iterate: 41\n"
    (d,) = errors(bridge, body)
    assert d.range.start.line == (HEAD + body).split('\n').index('        iterate: 41')


# =============================================================================
# Le espressioni
# =============================================================================

def test_nome_sconosciuto(bridge):
    (d,) = errors(bridge, "    maps: {m1: {type: logistic, r: 3.5}}\n"
                          "    grain:\n      duration: (0.02 + 0.08*m9)\n")
    assert 'm9' in d.message and 'm1' in d.message


def test_m1_y_solo_con_due_variabili(bridge):
    assert errors(bridge, "    maps: {m1: {type: logistic, r: 3.5}}\n"
                          "    pan: (100*m1.y)\n")


def test_senza_parentesi(bridge):
    (d,) = errors(bridge, "    maps: {m1: {type: logistic, r: 3.5}}\n"
                          "    grain:\n      iot: 0.02 + t\n")
    assert '(0.02 + t)' in d.message


def test_una_parola_non_e_un_espressione(bridge):
    assert diags(bridge, "    density: not_a_number\n") == []


@pytest.mark.parametrize('campo', sorted(EX.EXPRESSION_REFUSALS))
def test_rifiutata_dove_il_motore_la_rifiuta(bridge, campo):
    blocco, _, chiave = campo.partition('.')
    body = (f"    maps: {{m1: {{type: logistic, r: 3.5}}}}\n"
            f"    {blocco}:\n      {chiave}: (m1)\n")
    msgs = [d.message for d in errors(bridge, body)]
    assert any(EX.EXPRESSION_REFUSALS[campo][:40] in m for m in msgs)


def test_deviation_probability_la_rifiuta(bridge):
    msgs = [d.message for d in errors(
        bridge, "    maps: {m1: {type: logistic, r: 3.5}}\n"
                "    deviation_probability: {volume: (m1*100)}\n")]
    assert any("probabilita'" in m for m in msgs)


def test_costante_del_caricamento_vale_ovunque(bridge):
    assert diags(bridge, "    pointer:\n      speed_ratio: (10/2)\n") == []


# =============================================================================
# PGE #309: l'espressione sta tutta fra le parentesi esterne
# =============================================================================

@pytest.mark.parametrize('scritto, proposta', [
    ('(10/2)/10', '((10/2)/10)'),
    ('2*(3+4)', '(2*(3+4))'),
    ('(pi)*2', '((pi)*2)'),
])
def test_meta_valutata(bridge, scritto, proposta):
    (d,) = errors(bridge, f"    volume: {scritto}\n")
    assert proposta in d.message


def test_meta_valutata_sul_seed(bridge):
    head = "seed: (1000)+441\n" + HEAD
    (d,) = errors(bridge, "    volume: -6\n", head)
    assert d.range.start.line == 0


def test_stream_id_e_sample_sono_testo(bridge):
    head = "streams:\n  - stream_id: 1-(2)\n    sample: voce (1).wav\n    duration: 4\n"
    assert diags(bridge, "    volume: -6\n", head) == []


@pytest.mark.parametrize('testo, atteso', [
    ('(10 + 5)', 15), ('(10/2)', 5.0), ('(max(3, 7))', 7), ('(pow(2, 3))', 8),
    ('((10/2)/10)', 0.5), ('(10/2)/10', '5.0/10'), ('voce (1).wav', 'voce 1.wav'),
    ('(1,2)', '(1,2)'), ('(abs)', '(abs)'), ('-(90/2)', -45.0), ('(7//2)', 3),
    ('(m1)', '(m1)'), ('(2**100000)', '(2**100000)'),
])
def test_load_time_value(testo, atteso):
    valore = EX.load_time_value(testo)
    assert valore == atteso and type(valore) is type(atteso)


# =============================================================================
# grain.iot e pointer.position
# =============================================================================

@pytest.mark.parametrize('altro', M.IOT_EXCLUSIVE_KEYS)
def test_iot_e_un_errore_non_una_priorita(bridge, altro):
    ds = diags(bridge, f"    {altro}: 1\n    grain:\n      iot: 0.1\n")
    assert [d.severity for d in ds] == [DiagnosticSeverity.Error]
    assert 'priorita' in ds[0].message  # «non una priorita'»


def test_iot_vuota_e_gia_un_modo(bridge):
    assert any('grain.iot' in d.message
               for d in errors(bridge, "    density: 20\n    grain:\n      iot:\n"))


def test_iot_in_millisecondi_fuori_dal_tetto(bridge):
    msgs = [d.message for d in errors(bridge, "    grain:\n      iot: 200000\n"
                                              "      duration: 50\n"
                                              "      duration_unit: milliseconds\n")]
    assert any('grain.iot' in m for m in msgs)


@pytest.mark.parametrize('altro', M.POSITION_EXCLUSIVE_KEYS)
def test_position_esclusiva(bridge, altro):
    (d,) = errors(bridge, f"    pointer:\n      position: 0.5\n      {altro}: 0\n")
    assert 'pointer.position' in d.message


@pytest.mark.parametrize('v', ['.nan', '.inf'])
def test_position_non_finita(bridge, v):
    assert errors(bridge, f"    pointer:\n      position: {v}\n")


def test_position_hover_dice_che_rientra(bridge):
    param = bridge.get_parameter('pointer_position')
    assert bridge.get_value_domain(param) is None
    assert 'rientra a capo' in bridge.get_documentation(param)


def test_lo_snapshot_porta_i_parametri_che_avvolgono(bridge):
    import json
    import tempfile
    with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False) as fh:
        fh.write(bridge.generate_snapshot())
    letto = SchemaBridge.from_snapshot(fh.name)
    assert letto.get_wrapping_parameters() == frozenset({'pointer_position'})


# =============================================================================
# Completion e hover
# =============================================================================

def _ctx(context_type, current_text, parent_path, current_key='', line=0,
         leading=6):
    return YamlContext(context_type=context_type, current_text=current_text,
                       parent_path=parent_path, indent_level=leading // 2,
                       in_stream_element=True, current_key=current_key,
                       leading_spaces=leading, cursor_line=line)


def test_completion_dei_tipi(bridge):
    items = CompletionProvider(bridge).get_completions(
        _ctx('value', '', ['maps', 'm1'], current_key='type'), '')
    assert [i.label for i in items] == list(M.MAP_TYPE_VALUES)


def test_completion_delle_chiavi_di_una_mappa(bridge):
    text = HEAD + "    maps:\n      m1:\n        type: logistic\n        \n"
    line = text.split('\n').index('        ')
    items = CompletionProvider(bridge).get_completions(
        _ctx('key', '', ['maps', 'm1'], line=line, leading=8), text)
    labels = [i.label for i in items]
    assert 'type' not in labels and set(labels) == set(M.MAP_KEYS) - {'type'}


def test_completion_di_maps_nello_stream(bridge):
    text = HEAD + "    \n"
    items = CompletionProvider(bridge).get_completions(
        _ctx('key', 'ma', [], line=4, leading=4), text)
    assert 'maps' in [i.label for i in items]


def test_hover_del_nome_di_una_mappa(bridge):
    text = HEAD + "    maps:\n      m1: {type: henon, r: 1.4}\n"
    line = text.split('\n').index('      m1: {type: henon, r: 1.4}')
    h = HoverProvider(bridge).get_hover(_ctx('key', 'm1', ['maps'], line=line), text)
    assert '-1.3' in h.contents.value and 'm1.y' in h.contents.value


def test_hover_di_una_chiave_di_mappa(bridge):
    h = HoverProvider(bridge).get_hover(_ctx('key', 'iterate', ['maps', 'm1']), '')
    assert str(M.MAP_ITERATE_MAX) in h.contents.value


# =============================================================================
# Review della PR #80: cio' che il motore fa e il mirror non faceva
# =============================================================================

# Le chiavi di una mappa passano dalla valutazione al caricamento come ogni
# altra stringa dello stream (solo le equazioni `x:`/`y:` vanno al riparo):
# `r: (7/2)` arriva a `parse_maps` come 3.5. Il motore le rende tutte.
COSTANTI_DEL_CARICAMENTO = {
    'r': "    maps: {m1: {type: logistic, r: (7/2)}}\n",
    'clock': "    maps: {m1: {type: logistic, r: 3.5, clock: (10/2)}}\n",
    'iterate': "    maps: {m1: {type: logistic, r: 3.5, iterate: (2*2)}}\n",
    'transient': "    maps: {m1: {type: logistic, r: 3.5, transient: (100*3)}}\n",
    'x0': "    maps: {m1: {type: logistic, r: 3.5, x0: (1/4)}}\n",
    'y0': "    maps: {m1: {type: henon, r: 1.4, y0: (1/10)}}\n",
}


@pytest.mark.parametrize('name', sorted(COSTANTI_DEL_CARICAMENTO))
def test_una_costante_del_caricamento_in_una_mappa_e_un_numero(bridge, name):
    assert diags(bridge, COSTANTI_DEL_CARICAMENTO[name]) == []


def test_una_chiave_di_mappa_che_nomina_una_mappa_non_e_una_costante(bridge):
    # `(m2)` va al riparo (nomina una mappa dichiarata): resta un testo, e
    # `parse_maps` lo rifiuta.
    body = ("    maps:\n      m2: {type: logistic, r: 3.5}\n"
            "      m1: {type: logistic, r: (m2)}\n")
    assert any('maps.m1.r' in d.message for d in errors(bridge, body))


def test_una_mappa_che_si_chiama_come_una_funzione_del_caricamento(bridge):
    # `int` non e' un nome riservato: con una mappa `int` dichiarata,
    # `(int(2))` va al riparo, e l'interprete non conosce la funzione `int`.
    body = "    maps: {int: {type: logistic, r: 3.5}}\n    volume: (int(2))\n"
    msgs = [d.message for d in errors(bridge, body)]
    assert any('funzione sconosciuta' in m for m in msgs), msgs
    ok = "    maps: {float: {type: logistic, r: 3.5}}\n    volume: (-20 + float)\n"
    assert diags(bridge, ok) == []


@pytest.mark.parametrize('campo', ['duration', 'iot'])
def test_una_parola_nei_campi_del_grano_e_un_espressione(bridge, campo):
    # `_wrap_grain_expressions` porta l'unita' a ogni stringa di
    # grain.duration e grain.iot, non solo a quelle scritte come
    # un'espressione: `forte` lo compila l'interprete, e lo rifiuta.
    body = f"    grain:\n      {campo}: forte\n"
    msgs = [d.message for d in errors(bridge, body)]
    assert any('nome sconosciuto «forte»' in m for m in msgs), msgs


def test_una_parola_negli_altri_campi_non_e_un_espressione(bridge):
    # `_wrap_block_expressions` invece guarda `_written_as_expression`.
    assert not any('Espressione' in d.message
                   for d in diags(bridge, "    density: not_a_number\n"))


def test_la_meta_valutata_dentro_una_mappa(bridge):
    # `find_half_evaluated` scende anche in `maps:`; al riparo ci sono solo
    # le equazioni di una `custom`.
    body = "    maps: {m1: {type: logistic, r: (7/2)*1}}\n"
    msgs = [d.message for d in errors(bridge, body)]
    assert any('((7/2)*1)' in m for m in msgs), msgs
    eq = "    maps: {m1: {type: custom, x: (1+2)*x*(1-x)/3}}\n    volume: (-20 + m1)\n"
    assert diags(bridge, eq) == []
