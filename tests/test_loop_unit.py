"""
test_loop_unit.py

`pointer.loop_unit` dopo PGE #222 (issue PGE-ls #49).

Il motore ha smesso di far ereditare `loop_unit` da `time_mode`: le due chiavi
governano assi diversi — `time_mode` l'asse X degli envelope sulla `duration`
dello stream, `loop_unit` il valore delle posizioni nel sample sulla durata
del file — e un solo keyword finiva per spostare anche la testina di lettura.

| | prima | dopo |
|---|---|---|
| default | eredita da `time_mode` | `seconds`, indipendente |
| vocabolario | implicito (`!= 'normalized'`) | `seconds` / `absolute` / `normalized` |
| unita' sconosciuta | silenzio, vale "assoluto" | `InvalidFieldValueError` |
| `loop_unit:` vuoto | `None` e' falsy, eredita | errore |

Il language server teneva un mirror verbatim della riga vecchia
(`_get_effective_unit_mode`) e ci decideva sopra i bounds delle quattro
posizioni: dopo #222 avrebbe dato falsi positivi (`loop_start: 2.0` su un
sample da 8 s, sotto `time_mode: normalized`) e falsi negativi (un valore oltre
la durata del sample, mai piu' confrontato col file). Questi test fissano la
lettura nuova in ogni provider che la usa: diagnostica, hover, completion e i
semantic token di server.py.
"""

import sys
import wave
from pathlib import Path

import pytest

from lsprotocol.types import DiagnosticSeverity

from granular_ls.schema_bridge import SchemaBridge
from granular_ls.yaml_analyzer import YamlContext
from granular_ls.providers.completion_provider import (
    CompletionProvider,
    TRIGGER_SUGGEST,
)
from granular_ls.providers.hover_provider import (
    HoverProvider,
    _get_effective_unit_mode,
)
from granular_ls.providers.diagnostic_provider import DiagnosticProvider

_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


# =============================================================================
# FIXTURES
# =============================================================================

def _bridge():
    """Bridge minimale: il blocco pointer non passa dallo schema."""
    return SchemaBridge({'specs': [], 'bounds': {}})


@pytest.fixture
def bridge():
    return _bridge()


@pytest.fixture
def pointer_bridge():
    """Bridge con un parametro del blocco pointer.

    La completion delle chiavi di un blocco parte solo se lo schema gli
    conosce almeno un parametro (altrimenti ripiega sul livello stream).
    """
    return SchemaBridge({
        'specs': [{
            'name': 'pointer_speed_ratio', 'yaml_path': 'pointer.speed_ratio',
            'default': 1.0, 'is_smart': True, 'exclusive_group': None,
            'group_priority': 99, 'range_path': None,
            'deviation_probability_key': None,
        }],
        'bounds': {'pointer_speed_ratio': {
            'min_val': -100.0, 'max_val': 100.0, 'min_range': 0.0,
            'max_range': 0.0, 'default_jitter': 0.0,
            'variation_mode': 'additive',
        }},
    })


SAMPLE_SECONDS = 8.0


@pytest.fixture
def refs_dir(tmp_path):
    """Una cartella refs/ con un WAV da 8 secondi (1000 Hz, mono, 16 bit).

    Il sample e' volutamente piu' corto dello stream (10 s in `_stream`): e'
    la differenza fra i due riferimenti che #222 separa, e un test che li
    avesse uguali non vedrebbe quale dei due viene usato.
    """
    refs = tmp_path / 'refs'
    refs.mkdir()
    with wave.open(str(refs / 'f.wav'), 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(1000)
        w.writeframes(b'\x00\x00' * int(SAMPLE_SECONDS * 1000))
    return str(refs)


def _stream(body: str, time_mode: str = None) -> str:
    """Uno stream completo; `body` e' il contenuto a indent 4."""
    head = (
        "streams:\n"
        "  - stream_id: s1\n"
        "    onset: 0.0\n"
        "    duration: 10.0\n"
        "    sample: f.wav\n"
    )
    if time_mode is not None:
        head += f"    time_mode: {time_mode}\n"
    return head + body


def _pointer(body: str, time_mode: str = None) -> str:
    return _stream("    pointer:\n" + body, time_mode=time_mode)


def _con_commento_su_sample(text: str) -> str:
    """Lo stesso stream, con il sample fra virgolette e un commento in coda."""
    return text.replace('    sample: f.wav\n',
                        '    sample: "f.wav"  # otto secondi\n')


def _line_of(text: str, needle: str) -> int:
    for n, line in enumerate(text.split('\n')):
        if needle in line:
            return n
    raise AssertionError(f'{needle!r} non trovato')


def make_context(context_type='key', current_text='', parent_path=None,
                 indent_level=0, current_key='', cursor_line=0):
    return YamlContext(
        context_type=context_type,
        current_text=current_text,
        parent_path=parent_path or [],
        indent_level=indent_level,
        current_key=current_key,
        cursor_line=cursor_line,
    )


# =============================================================================
# 1. Registry: vocabolario, default, scope
# =============================================================================

class TestRegistry:

    def test_vocabolario_chiuso_con_la_grafia_canonica_in_testa(self):
        from granular_ls.loop_unit import LOOP_UNITS
        assert LOOP_UNITS == ('seconds', 'absolute', 'normalized')

    def test_il_default_ha_un_nome_ed_e_seconds(self):
        from granular_ls.loop_unit import LOOP_UNITS, LOOP_UNIT_DEFAULT
        assert LOOP_UNIT_DEFAULT == 'seconds'
        assert LOOP_UNIT_DEFAULT == LOOP_UNITS[0]

    def test_lo_scope_comprende_start(self):
        """`start` loop non e', ma e' una posizione nel sample: stessa unita'."""
        from granular_ls.loop_unit import LOOP_UNIT_SCOPE
        assert LOOP_UNIT_SCOPE == ('start', 'loop_start', 'loop_end', 'loop_dur')

    def test_i_tre_insiemi_del_blocco_pointer_sono_uno_solo(self):
        """Hover, diagnostica e semantic token tenevano tre copie a mano
        dello stesso insieme: ora lo leggono tutti dal registry."""
        import server as srv
        from granular_ls.loop_unit import LOOP_UNIT_SCOPE
        from granular_ls.providers import hover_provider
        assert set(hover_provider._POINTER_UNIT_PARAMS) == set(LOOP_UNIT_SCOPE)
        assert set(DiagnosticProvider._POINTER_SCALAR_PARAMS) == set(LOOP_UNIT_SCOPE)
        assert set(srv._POINTER_UNIT_PARAMS) == set(LOOP_UNIT_SCOPE)

    @pytest.mark.parametrize('value, mode', [
        ('normalized', 'normalized'),
        ('seconds', 'absolute'),
        ('absolute', 'absolute'),
        # Fuori vocabolario: il motore alza, non ricade su "assoluto".
        ('normalised', 'invalid'),
        ('Normalized', 'invalid'),
        ('loop', 'invalid'),
        ('', 'invalid'),
        # `loop_unit:` scritto e lasciato vuoto: prima era falsy (eredita),
        # ora e' un valore fuori vocabolario come un altro.
        (None, 'invalid'),
        (1, 'invalid'),
        (True, 'invalid'),
        (['normalized'], 'invalid'),
    ])
    def test_modo_di_lettura(self, value, mode):
        from granular_ls.loop_unit import loop_unit_mode
        assert loop_unit_mode(value) == mode


class TestRescalingWouldChange:
    """Mirror di `_rescaling_would_change` (PointerController, PGE #222).

    Decide chi riceve l'avviso di migrazione: solo i valori che la vecchia
    conversione muoveva davvero. Uno zero resta zero sotto qualunque fattore
    di scala, e `start: 0` e' la forma piu' comune del corpus.
    """

    @pytest.mark.parametrize('value', [
        None, True, False, 0, 0.0, '0', [], 'abc', {'type': 'linear'},
        # Espressione: la valuta il Generator, qui non si decide.
        '(1/2)',
    ])
    def test_non_si_muove(self, value):
        from granular_ls.loop_unit import rescaling_would_change
        assert rescaling_would_change(value) is False

    @pytest.mark.parametrize('value', [
        2.0, 1, -0.5, '0.5',
        [[0, 0.1], [10, 0.5]],
        [[0, 0.1, 'step'], [10, 0.5]],
        [{'t': 0, 'v': 0.1}],
        {'points': [[0, 0.1], [10, 0.5]]},
        [[[0, 0.1], [50, 0.5]], 10, 4],          # formato compatto
        [[[0, 0.1], [10, 0.5]], 'cubic'],        # BP group diretto
    ])
    def test_si_muove(self, value):
        from granular_ls.loop_unit import rescaling_would_change
        assert rescaling_would_change(value) is True


class TestStreamSample:
    """`stream_sample`: il file la cui durata limita le posizioni in secondi.

    L'hover lo nomina nella nota d'unita' e la fase 9 della diagnostica ci
    misura i bounds: devono leggere la stessa riga allo stesso modo, e via
    YAML, per la ragione di `find_loop_unit` — a regex il commento in coda
    finiva nel nome del file, il file non si apriva e il limite spariva.
    """

    def _sample(self, text, needle='stream_id'):
        from granular_ls.loop_unit import stream_sample
        return stream_sample(text.split('\n'), _line_of(text, needle))

    @pytest.mark.parametrize('riga', [
        '    sample: f.wav',
        '    sample: "f.wav"',
        "    sample: 'f.wav'",
        '    sample: f.wav  # otto secondi',
        '    sample: "f.wav"  # otto secondi',
    ])
    def test_il_valore_come_lo_legge_yaml(self, riga):
        text = _stream('').replace('    sample: f.wav', riga)
        assert self._sample(text) == 'f.wav'

    def test_sulla_riga_del_trattino(self):
        text = ("streams:\n"
                "  - sample: f.wav  # otto secondi\n"
                "    stream_id: s1\n")
        assert self._sample(text) == 'f.wav'

    @pytest.mark.parametrize('riga', [
        '    sample:',
        '    sample:   # da scegliere',
        '    sample: null',
        '    sample: 3',
    ])
    def test_senza_un_nome_di_file(self, riga):
        text = _stream('').replace('    sample: f.wav', riga)
        assert self._sample(text) is None

    def test_un_sample_annidato_non_e_quello_dello_stream(self):
        text = _stream("    voices:\n"
                       "      sample: g.wav\n").replace(
            '    sample: f.wav\n', '')
        assert self._sample(text) is None

    def test_il_sample_di_un_altro_stream_non_conta(self):
        text = (_stream('')
                + "  - stream_id: s2\n"
                  "    duration: 4.0\n")
        assert self._sample(text, 's2') is None

    def test_hover_e_diagnostica_leggono_dallo_stesso_posto(self):
        from granular_ls import loop_unit
        from granular_ls.providers import diagnostic_provider, hover_provider
        assert hover_provider.stream_sample is loop_unit.stream_sample
        assert diagnostic_provider.stream_sample is loop_unit.stream_sample


# =============================================================================
# 2. _get_effective_unit_mode: niente piu' eredita' da time_mode
# =============================================================================

class TestEffectiveUnitMode:

    def _mode(self, text, needle='loop_'):
        return _get_effective_unit_mode(text, _line_of(text, needle))

    def test_time_mode_normalized_non_governa_piu_le_posizioni(self):
        """Il cuore di #222: il passo 2 del mirror vecchio non esiste piu'."""
        text = _pointer("      loop_start: 2.0\n", time_mode='normalized')
        assert self._mode(text) == ('absolute', 'default')

    def test_senza_niente_e_il_default(self):
        text = _pointer("      loop_start: 2.0\n")
        assert self._mode(text) == ('absolute', 'default')

    def test_loop_unit_normalized(self):
        text = _pointer("      loop_unit: normalized\n"
                        "      loop_start: 0.2\n")
        assert self._mode(text) == ('normalized', 'loop_unit')

    @pytest.mark.parametrize('unit', ['seconds', 'absolute'])
    def test_le_due_grafie_dell_assoluto(self, unit):
        text = _pointer(f"      loop_unit: {unit}\n"
                        "      loop_start: 2.0\n", time_mode='normalized')
        assert self._mode(text) == ('absolute', 'loop_unit')

    def test_refuso_non_e_un_sinonimo_di_assoluto(self):
        text = _pointer("      loop_unit: normalised\n"
                        "      loop_start: 0.2\n")
        assert self._mode(text) == ('invalid', 'loop_unit')

    def test_chiave_scritta_e_lasciata_vuota(self):
        text = _pointer("      loop_unit:\n"
                        "      loop_start: 0.2\n", time_mode='normalized')
        assert self._mode(text) == ('invalid', 'loop_unit')

    def test_valore_fra_virgolette(self):
        text = _pointer('      loop_unit: "normalized"\n'
                        "      loop_start: 0.2\n")
        assert self._mode(text) == ('normalized', 'loop_unit')

    def test_commento_inline_non_entra_nel_valore(self):
        """Il valore si legge come lo legge YAML: `normalized  # nota` e'
        `normalized`. Il mirror vecchio ci metteva dentro il commento, e
        l'unita' smetteva di essere riconosciuta."""
        text = _pointer("      loop_unit: normalized  # frazione del file\n"
                        "      loop_start: 0.2\n")
        assert self._mode(text) == ('normalized', 'loop_unit')

    def test_loop_unit_di_un_altro_stream_non_conta(self):
        text = (
            _pointer("      loop_unit: normalized\n")
            + "  - stream_id: s2\n"
              "    sample: f.wav\n"
              "    time_mode: normalized\n"
              "    pointer:\n"
              "      loop_start: 2.0\n"
        )
        lines = text.split('\n')
        riga = max(n for n, l in enumerate(lines) if 'loop_start' in l)
        assert _get_effective_unit_mode(text, riga) == ('absolute', 'default')


# =============================================================================
# 3. Diagnostica: bounds delle quattro posizioni
# =============================================================================

class TestPointerBoundsDopo222:

    def _errors(self, bridge, refs_dir, text):
        provider = DiagnosticProvider(bridge, refs_dir=refs_dir)
        return [d for d in provider.get_diagnostics(text)
                if d.severity == DiagnosticSeverity.Error]

    def test_niente_falso_positivo_in_secondi_sotto_time_mode_normalized(
            self, bridge, refs_dir):
        """`loop_start: 2.0` su un sample da 8 s e' valido per il motore:
        sotto `time_mode: normalized` il mirror vecchio lo misurava in
        [0, 1]."""
        text = _pointer("      loop_start: 2.0\n"
                        "      loop_dur: 1.5\n", time_mode='normalized')
        assert self._errors(bridge, refs_dir, text) == []

    def test_niente_falso_negativo_oltre_la_durata_del_sample(
            self, bridge, refs_dir):
        """9 s su un file da 8: prima la risposta era "fuori da [0, 1]", cioe'
        un confronto col riferimento sbagliato. Ora e' la durata del file."""
        text = _pointer("      loop_end: 9.0\n", time_mode='normalized')
        errs = self._errors(bridge, refs_dir, text)
        assert len(errs) == 1
        assert 'durata del sample' in errs[0].message
        assert '8.000' in errs[0].message

    def test_loop_unit_normalized_resta_in_zero_uno(self, bridge, refs_dir):
        text = _pointer("      loop_unit: normalized\n"
                        "      loop_end: 1.5\n")
        errs = self._errors(bridge, refs_dir, text)
        assert len(errs) == 1
        assert '[0.0, 1.0]' in errs[0].message

    def test_loop_unit_normalized_con_time_mode_absolute(self, bridge, refs_dir):
        """I due assi coesistono: `time_mode` non tocca la lettura."""
        text = _pointer("      loop_unit: normalized\n"
                        "      loop_end: 0.5\n", time_mode='absolute')
        assert self._errors(bridge, refs_dir, text) == []

    def test_loop_unit_seconds_sotto_time_mode_normalized(self, bridge, refs_dir):
        text = _pointer("      loop_unit: seconds\n"
                        "      start: 6.5\n", time_mode='normalized')
        assert self._errors(bridge, refs_dir, text) == []

    def test_il_commento_sulla_riga_di_sample_non_toglie_il_limite(
            self, bridge, refs_dir):
        """`sample: "f.wav"  # otto secondi` e' `f.wav`: il falso negativo di
        sopra, arrivato per un'altra strada. A regex il commento finiva nel
        nome del file, il file non si apriva e 9 s passavano in silenzio."""
        text = _con_commento_su_sample(_pointer("      loop_end: 9.0\n"))
        errs = self._errors(bridge, refs_dir, text)
        assert len(errs) == 1
        assert '8.000' in errs[0].message

    def test_unita_sconosciuta_non_misura_la_finestra(self, bridge, refs_dir):
        """Sotto un'unita' che il motore rifiuta non c'e' una scala in cui
        misurare: l'errore vero e' l'unita', e lo dice la sua riga."""
        text = _pointer("      loop_unit: normalised\n"
                        "      loop_end: 9.0\n")
        errs = self._errors(bridge, refs_dir, text)
        assert len(errs) == 1
        assert 'loop_unit' in errs[0].message
        assert errs[0].range.start.line == _line_of(text, 'loop_unit')


# =============================================================================
# 4. Diagnostica: il vocabolario del valore
# =============================================================================

class TestLoopUnitValue:
    """Gemello di `TestGrainDurationUnit`: stesso vocabolario chiuso, stesso
    errore sull'unita' ignota, stessa forma del messaggio."""

    def _loop_unit_diags(self, bridge, text):
        provider = DiagnosticProvider(bridge)
        return [d for d in provider.get_diagnostics(text)
                if 'loop_unit' in d.message]

    def test_invalid_unit_flagged(self, bridge):
        text = _pointer("      loop_unit: normalised\n")
        diags = self._loop_unit_diags(bridge, text)
        assert len(diags) == 1
        d = diags[0]
        assert d.severity == DiagnosticSeverity.Error
        assert 'normalised' in d.message
        for unit in ('seconds', 'absolute', 'normalized'):
            assert unit in d.message
        assert d.range.start.line == _line_of(text, 'loop_unit')

    @pytest.mark.parametrize('unit', ['seconds', 'absolute', 'normalized',
                                      '"normalized"', "'seconds'",
                                      'normalized  # frazione'])
    def test_valid_units_not_flagged(self, bridge, unit):
        text = _pointer(f"      loop_unit: {unit}\n")
        assert self._loop_unit_diags(bridge, text) == []

    @pytest.mark.parametrize('unit', ['Normalized', 'null', '~', '1', 'true'])
    def test_valori_che_il_motore_rifiuta(self, bridge, unit):
        text = _pointer(f"      loop_unit: {unit}\n")
        diags = self._loop_unit_diags(bridge, text)
        assert len(diags) == 1
        assert diags[0].severity == DiagnosticSeverity.Error

    def test_chiave_vuota_e_un_solo_errore(self, bridge):
        """`loop_unit:` vuoto lo segnala gia' `_check_missing_values`: la fase
        del vocabolario non deve dirlo una seconda volta."""
        text = _pointer("      loop_unit:\n")
        diags = self._loop_unit_diags(bridge, text)
        assert len(diags) == 1
        assert diags[0].severity == DiagnosticSeverity.Error

    def test_fuori_dal_pointer_non_e_affar_suo(self, bridge):
        """Il motore legge `loop_unit` solo dal blocco pointer."""
        text = _stream("    grain:\n"
                       "      duration: 0.05\n"
                       "      loop_unit: normalised\n")
        diags = [d for d in self._loop_unit_diags(bridge, text)
                 if 'normalised' in d.message]
        assert diags == []


# =============================================================================
# 5. Diagnostica: l'avviso di migrazione (ponytail, PGE #242)
# =============================================================================

class TestLoopUnitMigrationWarning:
    """Lo stesso avviso `[LOOP_UNIT]` che il motore stampa a render time,
    detto mentre il file si scrive: `time_mode: normalized`, nessun
    `loop_unit`, almeno una posizione che la vecchia conversione muoveva."""

    def _warnings(self, bridge, text):
        provider = DiagnosticProvider(bridge)
        return [d for d in provider.get_diagnostics(text)
                if d.severity == DiagnosticSeverity.Warning
                and 'loop_unit' in d.message]

    def test_avvisa_chi_cambia_lettura(self, bridge):
        text = _pointer("      loop_start: 0.2\n", time_mode='normalized')
        warns = self._warnings(bridge, text)
        assert len(warns) == 1
        w = warns[0]
        assert 'loop_start' in w.message
        assert 'loop_unit: normalized' in w.message
        assert 'time_mode' in w.message
        assert w.range.start.line == _line_of(text, 'pointer:')

    def test_nomina_le_chiavi_nell_ordine_del_motore(self, bridge):
        text = _pointer("      loop_end: 0.8\n"
                        "      start: 0.1\n", time_mode='normalized')
        warns = self._warnings(bridge, text)
        assert len(warns) == 1
        assert 'start, loop_end' in warns[0].message

    def test_uno_zero_non_si_muove(self, bridge):
        text = _pointer("      start: 0\n"
                        "      speed_ratio: 1.0\n", time_mode='normalized')
        assert self._warnings(bridge, text) == []

    def test_envelope_si_muove(self, bridge):
        text = _pointer("      loop_start: [[0, 0.1], [1, 0.5]]\n"
                        "      loop_dur: 0.1\n", time_mode='normalized')
        warns = self._warnings(bridge, text)
        assert len(warns) == 1
        assert 'loop_start, loop_dur' in warns[0].message

    def test_envelope_block_style_si_muove(self, bridge):
        text = _pointer("      loop_start:\n"
                        "        - [0, 0.1]\n"
                        "        - [1, 0.5]\n", time_mode='normalized')
        assert len(self._warnings(bridge, text)) == 1

    @pytest.mark.parametrize('unit', ['normalized', 'seconds', 'absolute',
                                      'normalised'])
    def test_loop_unit_dichiarata_zittisce_l_avviso(self, bridge, unit):
        """Dichiarata, valida o no: se e' sbagliata lo dice la sua riga."""
        text = _pointer(f"      loop_unit: {unit}\n"
                        "      loop_start: 0.2\n", time_mode='normalized')
        assert self._warnings(bridge, text) == []

    def test_time_mode_absolute_non_avvisa(self, bridge):
        text = _pointer("      loop_start: 0.2\n", time_mode='absolute')
        assert self._warnings(bridge, text) == []

    def test_senza_time_mode_non_avvisa(self, bridge):
        text = _pointer("      loop_start: 0.2\n")
        assert self._warnings(bridge, text) == []

    def test_time_mode_fra_virgolette_e_con_commento(self, bridge):
        text = _pointer("      loop_start: 0.2\n",
                        time_mode='"normalized"  # asse X')
        assert len(self._warnings(bridge, text)) == 1

    def test_time_mode_sulla_riga_del_trattino(self, bridge):
        text = (
            "streams:\n"
            "  - time_mode: normalized\n"
            "    stream_id: s1\n"
            "    sample: f.wav\n"
            "    pointer:\n"
            "      loop_start: 0.2\n"
        )
        assert len(self._warnings(bridge, text)) == 1

    def test_espressione_non_si_valuta(self, bridge):
        text = _pointer('      loop_start: "(1/2)"\n', time_mode='normalized')
        assert self._warnings(bridge, text) == []

    def test_un_avviso_per_stream(self, bridge):
        text = (
            _pointer("      loop_start: 0.2\n", time_mode='normalized')
            + "  - stream_id: s2\n"
              "    sample: f.wav\n"
              "    time_mode: normalized\n"
              "    pointer:\n"
              "      start: 0.5\n"
        )
        warns = self._warnings(bridge, text)
        assert len(warns) == 2
        assert {w.range.start.line for w in warns} == {
            n for n, l in enumerate(text.split('\n')) if l == '    pointer:'}


# =============================================================================
# 6. Completion
# =============================================================================

class TestLoopUnitCompletion:

    def _values(self, bridge, prefix=''):
        provider = CompletionProvider(bridge)
        ctx = make_context(context_type='value', current_text=prefix,
                           current_key='loop_unit', parent_path=['pointer'],
                           indent_level=3)
        return provider.get_completions(
            ctx, document_text=f"pointer:\n  loop_unit: {prefix}")

    def test_i_tre_valori_nell_ordine_del_vocabolario(self, bridge):
        assert [i.label for i in self._values(bridge)] == [
            'seconds', 'absolute', 'normalized']

    @pytest.mark.parametrize('prefix, labels', [
        ('n', ['normalized']), ('a', ['absolute']), ('se', ['seconds']),
        ('"nor', ['normalized']), ('x', []),
    ])
    def test_filtro_per_prefisso(self, bridge, prefix, labels):
        assert [i.label for i in self._values(bridge, prefix)] == labels

    def test_doc_dei_valori(self, bridge):
        docs = {i.label: i.documentation.value for i in self._values(bridge)}
        assert 'default' in docs['seconds'].lower()
        assert 'alias' in docs['absolute'].lower()
        assert 'sample' in docs['normalized']
        for doc in docs.values():
            assert 'eredita' not in doc.lower()

    def test_la_chiave_apre_il_menu_dei_valori(self, pointer_bridge):
        """Il commento «Nessun TRIGGER_SUGGEST: loop_unit accetta solo
        stringhe» valeva finche' quelle stringhe non avevano un menu."""
        provider = CompletionProvider(pointer_bridge)
        ctx = make_context(context_type='key', current_text='',
                           parent_path=['pointer'], indent_level=3)
        items = provider.get_completions(ctx, document_text="pointer:\n  ")
        item = next(i for i in items if i.label == 'loop_unit')
        assert item.command == TRIGGER_SUGGEST

    def test_la_doc_della_chiave_non_parla_di_eredita(self, pointer_bridge):
        provider = CompletionProvider(pointer_bridge)
        ctx = make_context(context_type='key', current_text='',
                           parent_path=['pointer'], indent_level=3)
        items = provider.get_completions(ctx, document_text="pointer:\n  ")
        item = next(i for i in items if i.label == 'loop_unit')
        doc = item.documentation.value
        assert 'eredita il comportamento' not in doc
        assert 'non eredita' in doc.lower()
        assert 'seconds' in doc
        assert 'posizion' in item.detail.lower()

    def test_la_doc_di_start_non_cita_time_mode_come_unita(self):
        from granular_ls.providers.completion_provider import (
            _STREAM_CONTEXT_DOCS,
        )
        assert 'time_mode' not in _STREAM_CONTEXT_DOCS['start']


# =============================================================================
# 7. Hover
# =============================================================================

class TestLoopUnitHover:

    def _hover(self, bridge, key, text='', parent=('pointer',), refs_dir=''):
        provider = HoverProvider(bridge, refs_dir=refs_dir)
        line = _line_of(text, key + ':') if text else 0
        ctx = make_context(context_type='key', current_text=key,
                           parent_path=list(parent), indent_level=3,
                           cursor_line=line)
        return provider.get_hover(ctx, text).contents.value

    def test_doc_della_chiave(self, bridge):
        doc = self._hover(bridge, 'loop_unit')
        for unit in ('seconds', 'absolute', 'normalized'):
            assert f'`{unit}`' in doc
        assert 'non eredita' in doc.lower()
        assert 'fallback' not in doc.lower()
        assert 'InvalidFieldValueError' in doc

    def test_doc_di_start(self, bridge):
        doc = self._hover(bridge, 'start', parent=())
        assert 'time_mode' not in doc

    def test_doc_di_time_mode_rimanda_a_loop_unit(self, bridge):
        """L'equivoco che #222 toglie al motore va tolto anche alla doc:
        `time_mode` e' l'asse del tempo, le posizioni sono di `loop_unit`."""
        from granular_ls.providers.completion_provider import (
            _STREAM_CONTEXT_DOCS as COMPLETION_DOCS,
        )
        hover = self._hover(bridge, 'time_mode', parent=())
        for doc in (hover, COMPLETION_DOCS['time_mode']):
            assert 'loop_unit' in doc

    def test_doc_del_blocco_pointer_elenca_seconds(self, bridge):
        doc = self._hover(bridge, 'pointer', parent=())
        # La voce dell'elenco, a capo compreso.
        riga = doc[doc.index('- `loop_unit` —'):].split('\n\n')[0]
        assert '`seconds`' in riga
        # Governa anche `start`, che loop non e': l'unita' e' delle posizioni.
        assert 'posizion' in riga

    def test_nota_sotto_time_mode_normalized_senza_loop_unit(self, bridge):
        text = _pointer("      start: 2.0\n", time_mode='normalized')
        note = self._hover(bridge, 'start', text)
        assert 'Unità effettiva' in note
        assert 'normalized' not in note.split('Unità effettiva')[1].split('\n')[0]
        assert 'seconds' in note
        assert 'default' in note

    def test_nota_con_loop_unit_normalized(self, bridge):
        text = _pointer("      loop_unit: normalized\n"
                        "      start: 0.2\n")
        note = self._hover(bridge, 'start', text)
        assert '`loop_unit: normalized`' in note

    def test_nota_con_unita_sconosciuta(self, bridge):
        text = _pointer("      loop_unit: normalised\n"
                        "      start: 0.2\n")
        note = self._hover(bridge, 'start', text)
        assert 'InvalidFieldValueError' in note
        assert 'secondi assoluti' not in note

    def test_il_limite_e_la_durata_del_sample_non_dello_stream(
            self, bridge, refs_dir):
        """Stream da 10 s, sample da 8: le posizioni vivono nel file.

        La nota misurava il limite sulla `duration` dello stream — il
        riferimento di `time_mode`, cioe' proprio la confusione fra i due
        assi che #222 toglie — mentre la diagnostica usa gia' il file."""
        text = _pointer("      loop_unit: normalized\n"
                        "      start: 0.2\n")
        note = self._hover(bridge, 'start', text, refs_dir=refs_dir)
        assert '8.000 s' in note
        assert '10.0 s' not in note

    def test_limite_in_secondi(self, bridge, refs_dir):
        text = _pointer("      start: 2.0\n")
        note = self._hover(bridge, 'start', text, refs_dir=refs_dir)
        assert '8.000 s' in note
        assert '10.0' not in note

    def test_limite_con_un_commento_sulla_riga_di_sample(
            self, bridge, refs_dir):
        text = _con_commento_su_sample(_pointer("      start: 2.0\n"))
        note = self._hover(bridge, 'start', text, refs_dir=refs_dir)
        assert '8.000 s' in note

    def test_senza_file_nessun_limite_inventato(self, bridge):
        text = _pointer("      start: 2.0\n")
        note = self._hover(bridge, 'start', text)
        assert 'Limite' not in note


# =============================================================================
# 8. server.py: semantic token e cablaggio dei provider
# =============================================================================

class TestServer:

    def _normalized_lines(self, text):
        import server as srv
        data = srv._compute_semantic_tokens(text)
        righe, riga = [], 0
        for i in range(0, len(data), 5):
            riga += data[i]
            if data[i + 3] == srv._TOKEN_NORMALIZED:
                righe.append(riga)
        return righe

    def test_time_mode_normalized_non_colora_piu_le_posizioni(self):
        text = _pointer("      loop_start: 2.0\n", time_mode='normalized')
        assert self._normalized_lines(text) == []

    def test_loop_unit_normalized_colora(self):
        text = _pointer("      loop_unit: normalized\n"
                        "      loop_start: 0.2\n"
                        "      speed_ratio: 1.0\n")
        assert self._normalized_lines(text) == [_line_of(text, 'loop_start')]

    def test_l_hover_riceve_la_stessa_refs_della_diagnostica(
            self, tmp_path, monkeypatch):
        import server as srv
        (tmp_path / 'src').mkdir()
        (tmp_path / 'refs').mkdir()
        monkeypatch.setattr(srv, '_src_path', str(tmp_path / 'src'))
        for name in ('_completion_provider', '_hover_provider',
                     '_diagnostic_provider'):
            monkeypatch.setattr(srv, name, getattr(srv, name))
        srv._init_providers(_bridge())
        assert srv._hover_provider._refs_dir == str(
            (tmp_path / 'refs').resolve())
        assert srv._hover_provider._refs_dir == \
            srv._diagnostic_provider._refs_dir
