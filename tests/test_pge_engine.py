"""
tests/test_pge_engine.py

I tre silenzi della parita' con PGE, resi rumorosi (#70).

1. Senza motore la parita' salta per intera. `PGE_REQUIRE_PARITY=1` fa di
   quello skip un rosso — qui, una volta sola, invece che per ogni patto.
2. «Al motore manca X» non distingue un motore vecchio da un nome spostato.
   Il commit registrato (`ENGINE_BASELINE`) li distingue: se e' antenato del
   motore sotto test, `engine_lacks` fallisce.
3. Un patto nuovo che salta con `pytest.skip` invece di `engine_lacks`
   rimetterebbe il silenzio: la guardia sotto conta gli skip rimasti.

La meccanica (stati git, gate, `engine_lacks`) si prova su repository git
costruiti in `tmp_path`, quindi senza motore; i due test che chiedono del
motore vero sono gli unici che dipendono dal checkout.
"""
import ast
import re
import subprocess
from pathlib import Path

import pytest

from tests import pge_engine
from tests.pge_engine import (
    ENGINE_BASELINE, PGE_SRC, BaselineStatus, baseline_status,
    engine_lacks, require_parity, summary_line,
)

_TESTS = Path(__file__).resolve().parent
_ROOT = _TESTS.parent


# -----------------------------------------------------------------------------
# 1. Il motore c'e', quando la parita' e' pretesa
# -----------------------------------------------------------------------------

def test_la_parita_pretesa_trova_il_motore():
    """Il gate che PGE-ui chiama PGE_REQUIRE_ENGINE_FIXTURES, qui per PGE-ls.

    Senza motore la suite di parita' salta, e in CI vuol dire che il secret
    del checkout manca: con `PGE_REQUIRE_PARITY=1` quel verde e' un rosso.
    """
    if PGE_SRC is not None:
        return
    if require_parity():
        pytest.fail(f"{pge_engine.REQUIRE_ENV}=1 ma il motore non c'e': "
                    f"{pge_engine.ABSENT_REASON}", pytrace=False)
    pytest.skip(pge_engine.ABSENT_REASON)


@pytest.mark.parametrize('valore, atteso', [
    ('1', True), ('0', False), ('', False), ('true', False), (' 1', False),
])
def test_il_gate_si_legge_alla_lettera(monkeypatch, valore, atteso):
    monkeypatch.setenv(pge_engine.REQUIRE_ENV, valore)
    assert require_parity() is atteso


def test_il_gate_assente_non_pretende(monkeypatch):
    monkeypatch.delenv(pge_engine.REQUIRE_ENV, raising=False)
    assert require_parity() is False


def test_la_ci_passa_il_gate_sull_outcome_del_checkout():
    """`outcome`, non `conclusion`: con `continue-on-error` la seconda vale
    `success` anche quando lo step fallisce, e il gate diventerebbe inerte."""
    ci = (_ROOT / '.github' / 'workflows' / 'ci.yml').read_text(
        encoding='utf-8')
    assert re.search(
        r"PGE_REQUIRE_PARITY:\s*\$\{\{\s*steps\.engine\.outcome\s*==\s*"
        r"'success'", ci)
    assert 'steps.engine.conclusion' not in ci
    # Il checkout ha l'id che il gate legge, e una storia abbastanza lunga
    # da contenere il commit registrato.
    checkout = ci[ci.index('name: checkout PythonGranularEngine'):]
    checkout = checkout[:checkout.index('- uses: actions/setup-python')]
    assert re.search(r'\bid:\s*engine\b', checkout)
    assert re.search(r'fetch-depth:\s*\d{3,}', checkout)
    # Un verde senza parita' si annuncia.
    assert re.search(r"if:\s*\$\{\{\s*steps\.engine\.outcome\s*!=\s*"
                     r"'success'\s*\}\}", ci)
    assert '::warning' in ci


# -----------------------------------------------------------------------------
# 2. Il commit di riferimento
# -----------------------------------------------------------------------------

def test_il_commit_registrato_e_uno_sha_intero():
    """Abbreviato, un SHA puo' diventare ambiguo col crescere del motore."""
    assert re.fullmatch(r'[0-9a-f]{40}', ENGINE_BASELINE)


def test_il_motore_contiene_il_commit_registrato():
    """Il commit dei patti e' antenato del motore sotto test.

    Solo cosi' «al motore manca X» e' impossibile per costruzione. Pretesa
    in CI (`PGE_REQUIRE_PARITY=1`) senza vie di fuga: un commit che il clone
    non contiene — troppo corto, o SHA inventato — e' un rosso. Fuori dalla
    CI un motore piu' vecchio e' legittimo e salta, dicendo quanto.
    """
    if PGE_SRC is None:
        pytest.skip(pge_engine.ABSENT_REASON)
    stato = baseline_status()
    if stato.state == 'contenuto':
        return
    messaggio = summary_line(stato)
    if require_parity():
        pytest.fail(messaggio + ". Aggiorna il checkout del motore, o "
                    "ENGINE_BASELINE in tests/pge_engine.py se il commit "
                    "registrato e' fuori dalla finestra del clone.",
                    pytrace=False)
    pytest.skip(messaggio)


def _git(cwd, *args):
    return subprocess.run(
        ['git', '-c', 'user.name=t', '-c', 'user.email=t@example.invalid',
         '-c', 'commit.gpgsign=false', *args],
        cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def motore_git(tmp_path):
    """Un 'motore' con tre commit su main e uno su un ramo laterale."""
    repo = tmp_path / 'motore'
    (repo / 'src').mkdir(parents=True)
    _git(repo, 'init', '-q', '-b', 'main')
    shas = []
    for i in range(3):
        (repo / 'src' / 'f.py').write_text(f'X = {i}\n')
        _git(repo, 'add', '.')
        _git(repo, 'commit', '-q', '-m', f'c{i}')
        shas.append(_git(repo, 'rev-parse', 'HEAD'))
    _git(repo, 'checkout', '-q', '-b', 'laterale', shas[0])
    (repo / 'src' / 'g.py').write_text('Y = 1\n')
    _git(repo, 'add', '.')
    _git(repo, 'commit', '-q', '-m', 'laterale')
    laterale = _git(repo, 'rev-parse', 'HEAD')
    _git(repo, 'checkout', '-q', 'main')
    return repo, shas, laterale


def test_baseline_antenato_e_contenuto(motore_git):
    repo, shas, _ = motore_git
    stato = baseline_status(str(repo / 'src'), shas[0])
    assert stato.state == 'contenuto'
    assert stato.head == shas[2]
    assert stato.distance == 2


def test_baseline_piu_recente_non_e_contenuto(motore_git):
    repo, shas, _ = motore_git
    _git(repo, 'checkout', '-q', shas[1])
    stato = baseline_status(str(repo / 'src'), shas[2])
    assert stato.state == 'non-contenuto'
    assert stato.distance == 1


def test_baseline_su_un_altro_ramo_non_e_contenuto(motore_git):
    repo, _, laterale = motore_git
    assert baseline_status(str(repo / 'src'), laterale).state == \
        'non-contenuto'


def test_baseline_inventato_e_sconosciuto(motore_git):
    """Il caso `deadbeef…` che in PGE-ui passava dalla via di fuga."""
    repo, _, _ = motore_git
    stato = baseline_status(str(repo / 'src'), 'deadbeef' * 5)
    assert stato.state == 'sconosciuto'
    assert 'clone completo' in stato.detail


def test_fuori_da_git_non_si_sa(tmp_path):
    src = tmp_path / 'src'
    src.mkdir()
    # GIT_CEILING_DIRECTORIES: niente risalita verso un repo che contenga
    # tmp_path per caso.
    import os
    env_prima = os.environ.get('GIT_CEILING_DIRECTORIES')
    os.environ['GIT_CEILING_DIRECTORIES'] = str(tmp_path)
    try:
        assert baseline_status(str(src), 'a' * 40).state == 'non-git'
    finally:
        if env_prima is None:
            os.environ.pop('GIT_CEILING_DIRECTORIES')
        else:
            os.environ['GIT_CEILING_DIRECTORIES'] = env_prima


def test_engine_lacks_fallisce_su_un_motore_che_contiene_i_patti():
    stato = BaselineStatus('contenuto', head='f' * 40, distance=3)
    with pytest.raises(pytest.fail.Exception) as info:
        engine_lacks("engine precede GRAIN_DURATION_UNITS", stato)
    messaggio = str(info.value)
    assert 'GRAIN_DURATION_UNITS' in messaggio
    assert 'spostato o riscritto' in messaggio
    assert ENGINE_BASELINE[:7] in messaggio


@pytest.mark.parametrize('stato', [
    'non-contenuto', 'sconosciuto', 'non-git', 'assente'])
def test_engine_lacks_salta_dove_il_motore_puo_essere_vecchio(stato):
    with pytest.raises(pytest.skip.Exception) as info:
        engine_lacks("engine precede X", BaselineStatus(stato))
    assert str(info.value) == 'engine precede X'


def test_la_riga_di_chiusura_dice_con_quale_motore(monkeypatch):
    monkeypatch.delenv(pge_engine.REQUIRE_ENV, raising=False)
    assert 'motore assente' in summary_line(BaselineStatus('assente'))
    monkeypatch.setenv(pge_engine.REQUIRE_ENV, '1')
    assert 'PGE_REQUIRE_PARITY=1' in summary_line(BaselineStatus('assente'))
    riga = summary_line(BaselineStatus('contenuto', head='a' * 40,
                                       distance=12))
    assert '12 commit oltre' in riga
    riga = summary_line(BaselineStatus('non-contenuto', head='a' * 40,
                                       distance=4))
    assert 'restano skip' in riga


# -----------------------------------------------------------------------------
# 3. Nessun patto salta in silenzio
# -----------------------------------------------------------------------------

# Gli skip che non dipendono dall'eta' del motore, e quindi restano skip
# anche contro uno che contiene i patti: le dipendenze che la CI del language
# server non installa, e un input che il motore rifiuta (il mirror tace lo
# stesso). Un prefisso per voce; tutto il resto passa da `engine_lacks`.
_SKIP_LEGITTIMI = (
    'PointerController non costruibile senza numpy/soundfile',
    'il motore rifiuta ',
)


def _skip_di(path):
    """I messaggi dei `pytest.skip(...)` di un file, come testo (le
    f-string col solo testo letterale)."""
    tree = ast.parse(path.read_text(encoding='utf-8'))
    for nodo in ast.walk(tree):
        if (isinstance(nodo, ast.Call)
                and isinstance(nodo.func, ast.Attribute)
                and nodo.func.attr == 'skip'
                and isinstance(nodo.func.value, ast.Name)
                and nodo.func.value.id == 'pytest'):
            arg = nodo.args[0] if nodo.args else None
            if isinstance(arg, ast.Constant):
                yield nodo.lineno, arg.value
            elif isinstance(arg, ast.Name) and arg.id == 'ABSENT_REASON':
                yield nodo.lineno, pge_engine.ABSENT_REASON
            elif isinstance(arg, ast.JoinedStr):
                yield nodo.lineno, ''.join(
                    v.value for v in arg.values
                    if isinstance(v, ast.Constant))
            else:
                yield nodo.lineno, None


@pytest.mark.parametrize('nome', ['test_pge_parity.py', 'test_stream_files.py'])
def test_i_patti_saltano_solo_dove_l_eta_del_motore_non_c_entra(nome):
    """Un `pytest.skip` per «al motore manca X» e' il silenzio di #70.

    Misurato: `GRAIN_DURATION_UNITS = tuple([...])` nel motore — la stessa
    costante, non piu' letterale — faceva saltare il patto dedicato con
    «engine precede GRAIN_DURATION_UNITS», in verde. Quei casi chiamano
    `engine_lacks`, che li' fallisce; qui si pretende che nessuno torni a
    `pytest.skip`.
    """
    abusivi = [
        (riga, testo) for riga, testo in _skip_di(_TESTS / nome)
        if testo is None or (not testo.startswith(_SKIP_LEGITTIMI)
                             and testo != pge_engine.ABSENT_REASON)
    ]
    assert not abusivi, (
        f"{nome}: skip che rispondono «al motore manca X» senza passare da "
        f"engine_lacks: {abusivi}")


def test_la_guardia_vede_gli_skip():
    """Senza questo, una guardia che non trova niente accusa niente."""
    trovati = list(_skip_di(_TESTS / 'test_pge_parity.py'))
    assert len(trovati) >= 4
    assert sum(1 for _, t in trovati
               if t and t.startswith(_SKIP_LEGITTIMI[0])) >= 2
