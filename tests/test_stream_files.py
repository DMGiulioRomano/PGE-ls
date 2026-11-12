"""
tests/test_stream_files.py

Lo stream come file (PGE #290, PGE-ls #65): `- file: <path>` nella lista
`streams:` del master.

  1. Il registry `granular_ls/stream_files.py` (piazzamento, default di
     `stream_id`, path, forme del file importato) e la sua parita' con
     `pge/engine/stream_files.py`, quando il motore c'e'.
  2. La diagnostica: nessun campo obbligatorio su una voce `file:`, solo il
     piazzamento accanto a `file:`, il file importato che esiste e porta uno
     stream, gli id effettivi duplicati.
  3. Completion e hover.
  4. Il server: path del documento alla diagnostica, link e go-to-file.
"""
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from lsprotocol.types import DiagnosticSeverity

from granular_ls.providers.completion_provider import CompletionProvider
from granular_ls.providers.diagnostic_provider import DiagnosticProvider
from granular_ls.providers.hover_provider import HoverProvider
from granular_ls.schema_bridge import SchemaBridge
from granular_ls.stream_files import (
    PLACEMENT_KEYS,
    default_stream_id,
    entry_imports_file,
    extra_keys,
    import_problem,
    resolve_import_path,
    scalar_text,
    value_span,
)
from granular_ls.yaml_analyzer import YamlAnalyzer, YamlContext


# =============================================================================
# FIXTURE
# =============================================================================

@pytest.fixture
def bridge():
    spec = {
        'name': 'density', 'yaml_path': 'density', 'default': None,
        'is_smart': True, 'exclusive_group': None, 'group_priority': 99,
        'range_path': None, 'deviation_probability_key': None,
    }
    bounds = {'density': {
        'min_val': 0.01, 'max_val': None, 'min_range': 0.0, 'max_range': 0.0,
        'default_jitter': 0.0, 'variation_mode': 'additive',
    }}
    return SchemaBridge({'specs': [spec], 'bounds': bounds})


RISACCA = """\
seed: 1441
duration: 30
bpm: 60
streams:
  - stream_id: risacca
    onset: 0
    duration: 30
    sample: mare.wav
    density: 40
"""


@pytest.fixture
def progetto(tmp_path):
    """Un master in tmp_path e la cartella streams/ accanto."""
    (tmp_path / 'streams').mkdir()
    (tmp_path / 'streams' / 'risacca.yml').write_text(RISACCA)

    def scrivi(nome, testo):
        p = tmp_path / nome
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(testo)
        return p

    return SimpleNamespace(root=tmp_path, scrivi=scrivi,
                           master=tmp_path / 'brano.yml')


def _diag(bridge, testo, path=None):
    return DiagnosticProvider(bridge).get_diagnostics(
        testo, str(path) if path else None)


def _errori(diags):
    return [d for d in diags if d.severity == DiagnosticSeverity.Error]


def _messaggi(diags):
    return [d.message for d in diags]


# =============================================================================
# 1. REGISTRY
# =============================================================================

class TestRegistry:

    def test_piazzamento(self):
        assert PLACEMENT_KEYS == ('stream_id', 'onset', 'mute', 'solo')

    def test_default_stream_id_e_il_nome_senza_estensione(self):
        assert default_stream_id('streams/risacca.yml') == 'risacca'
        assert default_stream_id('a/b/c.d.yaml') == 'c.d'
        assert default_stream_id('nudo') == 'nudo'

    def test_path_relativo_alla_cartella_del_master(self, tmp_path):
        master = tmp_path / 'brani' / 'brano.yml'
        assert (resolve_import_path(master, 'streams/r.yml')
                == tmp_path / 'brani' / 'streams' / 'r.yml')

    def test_path_assoluto_vale_com_e(self, tmp_path):
        assoluto = str(tmp_path / 'altrove' / 'r.yml')
        assert str(resolve_import_path(tmp_path / 'm.yml', assoluto)) == assoluto

    def test_chiavi_estranee(self):
        assert extra_keys(['file', 'onset', 'density', 'mute', 'sample']) == [
            'density', 'sample']
        assert extra_keys(['file', *PLACEMENT_KEYS]) == []

    @pytest.mark.parametrize('raw, atteso', [
        (' streams/r.yml', 'streams/r.yml'),
        (' "streams/r.yml"', 'streams/r.yml'),
        (" 'a b.yml'  # commento", 'a b.yml'),
        (' r.yml # nota', 'r.yml'),
        ('', ''), (' ~', ''), (' null', ''),
    ])
    def test_scalar_text(self, raw, atteso):
        assert scalar_text(raw) == atteso

    @pytest.mark.parametrize('riga, atteso', [
        ('  - file: streams/r.yml', (10, 23, 'streams/r.yml')),
        ('    file: "streams/r.yml"', (11, 24, 'streams/r.yml')),
        ('  - file:', None),
        ('    filex: a.yml', None),
        ('    sample: a.yml', None),
    ])
    def test_value_span(self, riga, atteso):
        assert value_span(riga) == atteso
        if atteso:
            assert riga[atteso[0]:atteso[1]] == atteso[2]

    def test_entry_imports_file(self):
        lines = ('streams:\n  - file: r.yml\n    onset: 2\n'
                 '  - stream_id: a\n    onset: 1\n    grain:\n'
                 '      file: x\n').split('\n')
        assert entry_imports_file(lines, 2)
        assert not entry_imports_file(lines, 4)   # `file` annidata non conta
        assert not entry_imports_file(lines, 0)


class TestImportProblem:

    def _scrivi(self, tmp_path, testo):
        p = tmp_path / 'f.yml'
        p.write_text(testo)
        return p

    def test_uno_stream_si_importa(self, tmp_path):
        assert import_problem(self._scrivi(tmp_path, RISACCA)) is None

    def test_file_mancante(self, tmp_path):
        assert 'non esiste' in import_problem(tmp_path / 'manca.yml')

    def test_cartella(self, tmp_path):
        assert 'cartella' in import_problem(tmp_path)

    def test_yaml_malformato(self, tmp_path):
        assert 'malformato' in import_problem(
            self._scrivi(tmp_path, 'streams: [\n'))

    @pytest.mark.parametrize('testo, trovato', [
        ('', 'nessuno stream'),
        ('seed: 1\n', 'nessuno stream'),
        ('streams: []\n', 'nessuno stream'),
        ('- a\n', "non e' una mappa (list)"),
        ('streams: 3\n', "'streams' non e' una lista (int)"),
        ('streams:\n  - a\n', "una voce che non e' uno stream (str)"),
        ('streams:\n  - {stream_id: a}\n  - {stream_id: b}\n', '2 stream'),
    ])
    def test_non_uno_stream(self, tmp_path, testo, trovato):
        problema = import_problem(self._scrivi(tmp_path, testo))
        assert problema is not None and problema.endswith(trovato)

    def test_catena(self, tmp_path):
        problema = import_problem(self._scrivi(
            tmp_path, 'streams:\n  - file: altro.yml\n'))
        assert 'catene' in problema


# -----------------------------------------------------------------------------
# Parita' con il motore
# -----------------------------------------------------------------------------

def _pge_src():
    env = os.environ.get('PGE_SRC')
    if env and Path(env).exists():
        return str(Path(env).resolve())
    sibling = (Path(__file__).resolve().parent.parent.parent
               / 'PythonGranularEngine' / 'src')
    return str(sibling) if sibling.exists() else None


@pytest.fixture(scope='module')
def motore():
    src = _pge_src()
    if src is None:
        pytest.skip('PGE src non trovato: imposta PGE_SRC o clona '
                    'PythonGranularEngine come sibling di PGE-ls.')
    aggiunto = src not in sys.path
    if aggiunto:
        sys.path.insert(0, src)
    prima = set(sys.modules)
    try:
        import importlib
        modulo = importlib.import_module('pge.engine.stream_files')
    except ImportError:
        pytest.skip('il motore non ha pge.engine.stream_files (PGE #290)')
    yield modulo
    for nome in set(sys.modules) - prima:
        if nome == 'pge' or nome.startswith('pge.'):
            sys.modules.pop(nome, None)
    if aggiunto:
        sys.path.remove(src)


class TestParitaMotore:

    def test_piazzamento_e_quello_del_motore(self, motore):
        assert PLACEMENT_KEYS == tuple(motore.CHIAVI_DI_PIAZZAMENTO)

    @pytest.mark.parametrize('testo', [
        RISACCA,
        '',
        'seed: 1\n',
        'streams: []\n',
        '- a\n',
        'streams: 3\n',
        'streams:\n  - a\n',
        'streams:\n  - {stream_id: a}\n  - {stream_id: b}\n',
        'streams:\n  - file: altro.yml\n',
        'streams:\n  - {file: null, sample: x.wav}\n',
    ])
    def test_stesso_verdetto_sul_file_importato(self, motore, tmp_path, testo):
        """Il motore rifiuta il file esattamente quando import_problem parla.

        `resolve_stream_files` con la lettura piu' semplice: lo YAML del
        file. Il motore non distingue le forme dalla nostra frase, ma dal
        tipo dell'errore; qui si confronta il verdetto.
        """
        (tmp_path / 'streams').mkdir()
        importato = tmp_path / 'streams' / 'x.yml'
        importato.write_text(testo)
        master = tmp_path / 'brano.yml'
        dati = {'streams': [{'file': 'streams/x.yml'}]}

        def leggi(path):
            with open(path, encoding='utf-8') as f:
                return yaml.safe_load(f)

        try:
            motore.resolve_stream_files(dati, str(master), leggi)
            rifiutato = False
        except Exception:
            rifiutato = True
        assert rifiutato == (import_problem(importato) is not None)

    def test_stessa_risoluzione_del_path_e_dell_id(self, motore, tmp_path):
        (tmp_path / 'streams').mkdir()
        (tmp_path / 'streams' / 'risacca.yml').write_text(RISACCA)
        master = tmp_path / 'brano.yml'

        def leggi(path):
            with open(path, encoding='utf-8') as f:
                return yaml.safe_load(f)

        dati, importati = motore.resolve_stream_files(
            {'streams': [{'file': 'streams/risacca.yml'}]}, str(master), leggi)
        assert dati['streams'][0]['stream_id'] == default_stream_id(
            'streams/risacca.yml')
        assert Path(importati[0].origin.path) == resolve_import_path(
            master, 'streams/risacca.yml')

    def test_stesse_chiavi_estranee(self, motore, tmp_path):
        voce = {'file': 'streams/x.yml', 'onset': 1, 'density': 4,
                'sample': 'a.wav'}
        with pytest.raises(motore.StreamFileKeyError) as exc:
            motore.resolve_stream_files({'streams': [voce]},
                                        str(tmp_path / 'm.yml'),
                                        lambda p: {})
        for chiave in extra_keys(voce):
            assert chiave in str(exc.value)


# =============================================================================
# 2. DIAGNOSTICA
# =============================================================================

class TestDiagnostica:

    def test_voce_file_senza_campi_obbligatori(self, bridge, progetto):
        testo = 'streams:\n  - file: streams/risacca.yml\n    onset: 12.5\n'
        diags = _diag(bridge, testo, progetto.master)
        assert not [m for m in _messaggi(diags) if 'obbligatorio' in m]
        assert _errori(diags) == []

    def test_lo_stream_scritto_resta_obbligato(self, bridge):
        diags = _diag(bridge, 'streams:\n  - onset: 1\n')
        assert any("'sample'" in m for m in _messaggi(diags))

    def test_stream_id_vuoto_e_il_default(self, bridge, progetto):
        testo = ('streams:\n  - file: streams/risacca.yml\n'
                 '    stream_id:\n')
        assert _errori(_diag(bridge, testo, progetto.master)) == []

    def test_stream_id_vuoto_fuori_da_file_resta_un_errore(self, bridge):
        diags = _diag(bridge, 'streams:\n  - stream_id:\n    sample: a.wav\n')
        assert any("'stream_id' richiede un valore" in m
                   for m in _messaggi(diags))

    def test_chiave_estranea_accanto_a_file(self, bridge, progetto):
        testo = ('streams:\n  - file: streams/risacca.yml\n'
                 '    onset: 1\n    density: 40\n    sample: a.wav\n'
                 '    grain:\n      duration: 0.1\n')
        errori = [d for d in _errori(_diag(bridge, testo, progetto.master))
                  if 'StreamFileKeyError' in d.message]
        assert sorted(d.range.start.line for d in errori) == [3, 4, 5]
        assert "'density'" in errori[0].message
        # la duration annidata in grain non e' una chiave della voce
        assert not any("'duration'" in d.message for d in errori)

    def test_chiave_estranea_sulla_riga_del_trattino(self, bridge, progetto):
        testo = 'streams:\n  - sample: a.wav\n    file: streams/risacca.yml\n'
        errori = [d for d in _errori(_diag(bridge, testo, progetto.master))
                  if 'StreamFileKeyError' in d.message]
        assert [d.range.start.line for d in errori] == [1]

    @pytest.mark.parametrize('valore', ['', ' 12', ' [a, b]', ' {a: 1}',
                                        ' null', ' ""'])
    def test_file_senza_path(self, bridge, valore):
        diags = _diag(bridge, f'streams:\n  - file:{valore}\n')
        assert any("'file:' vuole il path" in m for m in _messaggi(diags))

    def test_file_mancante(self, bridge, progetto):
        testo = 'streams:\n  - file: "streams/manca.yml"\n'
        errori = _errori(_diag(bridge, testo, progetto.master))
        assert len(errori) == 1
        assert "'streams/manca.yml'" in errori[0].message
        assert 'non esiste' in errori[0].message
        # range sul path, virgolette escluse
        assert (errori[0].range.start.character,
                errori[0].range.end.character) == (11, 28)

    def test_senza_path_del_master_il_file_non_si_controlla(self, bridge):
        testo = 'streams:\n  - file: streams/manca.yml\n'
        assert _errori(_diag(bridge, testo)) == []

    def test_file_con_due_stream(self, bridge, progetto):
        progetto.scrivi('streams/due.yml',
                        'streams:\n  - {stream_id: a}\n  - {stream_id: b}\n')
        testo = 'streams:\n  - file: streams/due.yml\n'
        errori = _errori(_diag(bridge, testo, progetto.master))
        assert len(errori) == 1 and '2 stream' in errori[0].message

    def test_catena(self, bridge, progetto):
        progetto.scrivi('streams/catena.yml',
                        'streams:\n  - file: risacca.yml\n')
        testo = 'streams:\n  - file: streams/catena.yml\n'
        errori = _errori(_diag(bridge, testo, progetto.master))
        assert len(errori) == 1 and 'catene' in errori[0].message

    def test_id_effettivo_duplicato_con_uno_scritto(self, bridge, progetto):
        testo = ('streams:\n  - file: streams/risacca.yml\n'
                 '  - stream_id: risacca\n    sample: a.wav\n')
        errori = [d for d in _errori(_diag(bridge, testo, progetto.master))
                  if 'StreamFileDuplicateIdError' in d.message]
        assert sorted(d.range.start.line for d in errori) == [1, 2]
        assert 'alla riga 3' in errori[0].message

    def test_stesso_file_due_volte_senza_stream_id(self, bridge, progetto):
        testo = ('streams:\n  - file: streams/risacca.yml\n'
                 '  - file: streams/risacca.yml\n')
        errori = [d for d in _errori(_diag(bridge, testo, progetto.master))
                  if 'StreamFileDuplicateIdError' in d.message]
        assert len(errori) == 2

    def test_stesso_file_due_volte_con_due_id(self, bridge, progetto):
        testo = ('streams:\n  - file: streams/risacca.yml\n    stream_id: a\n'
                 '  - file: streams/risacca.yml\n    stream_id: b\n')
        assert _errori(_diag(bridge, testo, progetto.master)) == []

    def test_duplicati_fra_stream_scritti_restano_fuori(self, bridge):
        testo = ('streams:\n  - stream_id: a\n    sample: x.wav\n'
                 '  - stream_id: a\n    sample: y.wav\n')
        assert not any('StreamFileDuplicateIdError' in m
                       for m in _messaggi(_diag(bridge, testo)))

    def test_mute_accanto_a_file_sbiadisce(self, bridge, progetto):
        testo = 'streams:\n  - file: streams/risacca.yml\n    mute:\n'
        diags = _diag(bridge, testo, progetto.master)
        assert _errori(diags) == []
        assert any('muted' in m for m in _messaggi(diags))


# =============================================================================
# 3. COMPLETION E HOVER
# =============================================================================

def _completion_labels(bridge, testo, riga, colonna):
    ctx = YamlAnalyzer.get_context(testo, riga, colonna)
    return [i.label for i in CompletionProvider(bridge).get_completions(ctx, testo)]


class TestCompletion:

    def test_file_sul_trattino(self, bridge):
        assert 'file' in _completion_labels(bridge, 'streams:\n  - ', 1, 4)

    def test_dentro_una_voce_file_solo_il_piazzamento(self, bridge):
        testo = 'streams:\n  - file: streams/r.yml\n    onset: 2\n    \n'
        labels = _completion_labels(bridge, testo, 3, 4)
        assert labels and set(labels) <= set(PLACEMENT_KEYS)
        assert 'onset' not in labels

    def test_stream_scritto_invariato(self, bridge):
        testo = 'streams:\n  - stream_id: a\n    \n'
        labels = _completion_labels(bridge, testo, 2, 4)
        assert 'sample' in labels and 'file' not in labels


def _hover(bridge, testo, riga, colonna):
    parola = YamlAnalyzer.get_word_at_cursor(testo, riga, colonna)
    ctx = YamlAnalyzer.get_context(testo, riga, colonna)
    h = HoverProvider(bridge).get_hover(YamlContext(
        'key', parola, ctx.parent_path, ctx.indent_level,
        ctx.in_stream_element, cursor_line=riga), testo)
    return h.contents.value if h else None


class TestHover:

    MASTER = ('streams:\n  - file: streams/r.yml\n    onset: 2\n'
              '  - stream_id: a\n    onset: 1\n    sample: x.wav\n')

    def test_hover_su_file(self, bridge):
        testo = _hover(bridge, self.MASTER, 1, 5)
        assert 'PGE #290' in testo and 'cartella del master' in testo

    def test_piazzamento_accanto_a_file(self, bridge):
        assert 'Accanto a `file:`' in _hover(bridge, self.MASTER, 2, 5)

    def test_piazzamento_di_uno_stream_scritto(self, bridge):
        assert 'Accanto a `file:`' not in _hover(bridge, self.MASTER, 4, 5)

    @pytest.mark.parametrize('chiave', ['seed', 'duration', 'bpm'])
    def test_top_level_ignorati_da_importato(self, bridge, chiave):
        testo = f'{chiave}: 1\n' + RISACCA.split('\n', 3)[3]
        doc = _hover(bridge, testo, 0, 1)
        assert doc.startswith(f'**{chiave}** (top-level)')
        assert 'ignorata' in doc

    def test_duration_dello_stream_non_e_il_top_level(self, bridge):
        doc = _hover(bridge, RISACCA, 6, 6)
        assert doc is None or '(top-level)' not in doc


# =============================================================================
# 4. SERVER
# =============================================================================

class TestServer:

    @pytest.fixture
    def srv(self, monkeypatch):
        import server as srv
        holder = {'text': ''}
        monkeypatch.setattr(srv, '_get_document_text',
                            lambda _s, _u: holder['text'])
        monkeypatch.setattr(srv, '_src_path', None)
        return SimpleNamespace(mod=srv,
                               set_text=lambda t: holder.update(text=t))

    def _uri(self, progetto, nome='PGE_brano.yml'):
        return (progetto.root / nome).as_uri()

    def test_document_path(self, srv, progetto):
        assert srv.mod._document_path(self._uri(progetto)) == str(
            progetto.root / 'PGE_brano.yml')

    def test_document_link_su_file(self, srv, progetto):
        srv.set_text('streams:\n  - file: streams/risacca.yml\n'
                     '  - file: streams/manca.yml\n')
        links = srv.mod.handle_document_link(SimpleNamespace(
            text_document=SimpleNamespace(uri=self._uri(progetto))))
        assert len(links) == 1
        assert links[0].target == (
            progetto.root / 'streams' / 'risacca.yml').resolve().as_uri()
        assert links[0].range.start.line == 1
        assert (links[0].range.start.character,
                links[0].range.end.character) == (10, 29)

    def test_definition_su_file(self, srv, progetto):
        srv.set_text('streams:\n  - file: streams/risacca.yml\n')
        loc = srv.mod.handle_definition(SimpleNamespace(
            text_document=SimpleNamespace(uri=self._uri(progetto)),
            position=SimpleNamespace(line=1, character=14)))
        assert loc.uri == (
            progetto.root / 'streams' / 'risacca.yml').resolve().as_uri()

    def test_definition_su_file_mancante(self, srv, progetto):
        srv.set_text('streams:\n  - file: streams/manca.yml\n')
        assert srv.mod.handle_definition(SimpleNamespace(
            text_document=SimpleNamespace(uri=self._uri(progetto)),
            position=SimpleNamespace(line=1, character=14))) is None

    def test_diagnostica_riceve_il_path(self, srv, progetto, monkeypatch):
        visti = []

        class Provider:
            def get_diagnostics(self, text, document_path=None):
                visti.append(document_path)
                return []

        monkeypatch.setattr(srv.mod, '_diagnostic_provider', Provider())
        monkeypatch.setattr(srv.mod.server, 'publish_diagnostics',
                            lambda *a, **k: None)
        srv.mod._compute_and_publish(self._uri(progetto), 'streams: []\n',
                                     use_cache=False)
        assert visti == [str(progetto.root / 'PGE_brano.yml')]
