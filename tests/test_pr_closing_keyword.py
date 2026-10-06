"""Il corpo di una PR dichiara l'issue che chiude.

GitHub chiude un'issue al merge soltanto se un closing keyword **inglese**
compare nel **corpo** della pull request. Il caso vero: la PR #293 di
PythonGranularEngine diceva «Chiude #219, entrambi i punti», e' stata merged,
e la #219 e' rimasta aperta -- chiusa a mano giorni dopo. Niente era rotto, e
nessuna CI poteva accorgersene: una frase italiana e' prosa, non un errore.

Questa e' la versione compatta della guardia. La suite intera sta nel repo che
tiene la copia canonica dello script (PythonGranularEngine,
`tests/test_pr_closing_keyword.py`); qui si pretendono le tre cose che, se
cedono, cedono **in silenzio**:

- il verdetto sui corpi che non chiudono, «Chiude #N» compreso;
- il template versionato **non** passa il check -- uno che passa e' un
  template inutile, perche' una PR lasciata col template addosso sarebbe
  verde, ed e' il caso che il meccanismo esiste per prendere;
- il nome del job e il contesto che il ruleset pretende coincidono: divergendo,
  il merge resta appeso a un contesto che nessuno pubblichera' mai.
"""
import importlib.util
import io
import json
import os
import re

import pytest


def _radice():
    """La radice del repo, trovata salendo fino allo script del check.

    Calcolata e non scritta perche' questo file e' lo stesso in tutti i repo,
    dove la cartella dei test e' annidata a profondita' diverse (`tests/`,
    `tests/python/`, `tests/unit/`).
    """
    d = os.path.dirname(os.path.abspath(__file__))
    while True:
        if os.path.isfile(os.path.join(d, '.github', 'scripts',
                                       'check_closing_keyword.py')):
            return d
        su = os.path.dirname(d)
        if su == d:
            raise AssertionError(
                'check_closing_keyword.py non trovato salendo da ' + __file__)
        d = su


RADICE = _radice()
SCRIPT = os.path.join(RADICE, '.github', 'scripts', 'check_closing_keyword.py')
TEMPLATE = os.path.join(RADICE, '.github', 'pull_request_template.md')
WORKFLOW = os.path.join(RADICE, '.github', 'workflows', 'pr-closes-issue.yml')
RULESET = os.path.join(RADICE, '.github', 'rulesets', 'main-closes-issue.json')


def _leggi(percorso):
    with io.open(percorso, encoding='utf-8') as f:
        return f.read()


@pytest.fixture(scope='module')
def check():
    """Il modulo del check, importato per percorso.

    `.github/scripts/` non e' un package e non sta su `sys.path`. Se il file
    non c'e' il test **falla** invece di skippare: uno skip qui direbbe «la
    catena non esiste» in verde.
    """
    assert os.path.isfile(SCRIPT), SCRIPT
    spec = importlib.util.spec_from_file_location('check_closing_keyword', SCRIPT)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


@pytest.mark.parametrize('corpo, issues', [
    ('Closes #219', [219]),
    ('fixes #7', [7]),
    ('Resolved #7', [7]),
    ('Closes #1\nFixes #2', [1, 2]),
    # Ogni issue vuole la sua parola chiave: GitHub non collega la seconda.
    ('Closes #1, #2', [1]),
])
def test_le_grafie_che_chiudono(check, corpo, issues):
    assert check.same_repo_issues(corpo) == issues
    assert check.verdict(corpo)[0] is True


@pytest.mark.parametrize('corpo, perche', [
    ('', 'corpo vuoto'),
    ('Chiude #219, entrambi i punti.', 'il caso vero della #219'),
    ('Risolve #219', 'italiano'),
    ('Closes: #12', "i due punti non sono fra le grafie documentate"),
    ('Closes #', 'la riga del template non compilata'),
    ('prefixes #12', "non e' una parola chiave: e' la coda di un'altra"),
    ('Vedi #219', '«vedi» non chiude'),
    ('<!-- Closes #123 -->', 'in un commento HTML GitHub non legge'),
    ('```\nCloses #9\n```', 'in un recinto GitHub non legge'),
    ('No issue:', 'la via d\'uscita senza motivo non vale'),
])
def test_le_grafie_che_non_chiudono(check, corpo, perche):
    assert check.verdict(corpo)[0] is False, perche


def test_un_issue_di_un_altro_repo_non_chiude(check):
    """In un gruppo di repo che si citano a ogni PR, «manca la riga»
    manderebbe a cercare una riga che c'e'."""
    ok, messaggio = check.verdict('Closes DMGiulioRomano/PythonGranularEngine#219')
    assert ok is False
    assert 'altro repo' in messaggio


def test_la_via_duscita_vuole_un_motivo(check):
    assert check.verdict('No issue: refuso nel README')[0] is True


def test_il_template_versionato_non_passa_il_check(check):
    assert re.search(r'^Closes #\s*$', _leggi(TEMPLATE), re.MULTILINE), (
        "il template deve aprire con una riga `Closes #` da completare")
    assert check.verdict(_leggi(TEMPLATE))[0] is False, (
        "un template che passa il check e' un template inutile")


def test_il_contesto_del_ruleset_e_il_nome_del_job():
    """Il workflow si legge a righe, senza PyYAML.

    Con un `importorskip('yaml')` questo test si skippava dove PyYAML non e'
    una dipendenza -- in PGE-ui, per esempio -- cioe' andava muto proprio
    sull'assert che conta: nome del job e contesto richiesto dal ruleset.
    Il file e' generato e ha una forma fissa, quindi leggerlo a righe e'
    deterministico; e se qualcuno la cambia, questo test diventa rosso invece
    di tacere, che e' il verso giusto.
    """
    testo = _leggi(WORKFLOW)

    tipi = re.search(r'^ {4}types: \[(?P<tipi>[^\]]*)\]\s*$', testo, re.M)
    assert tipi, 'tipi di evento non trovati nella forma attesa'
    tipi = [t.strip() for t in tipi.group('tipi').split(',')]
    for tipo in ('opened', 'edited', 'reopened', 'synchronize'):
        # Senza `edited` correggere il corpo non fa tornare verde il check;
        # senza `synchronize` la head nuova di un push resta senza check e il
        # merge aspetta per sempre un contesto che nessuno pubblica.
        assert tipo in tipi, tipo
    assert re.search(r'^ {2}pull_request:\s*$', testo, re.M)
    assert not re.search(r'^ {2}push:\s*$', testo, re.M), (
        "su un push non c'e' nessun corpo di PR da leggere")

    nomi = re.findall(r'^ {4}name: (\S+)\s*$', testo, re.M)
    assert nomi == ['closes-issue'], nomi

    # Il corpo lo scrive chiunque apra una PR: interpolato nel `run`, un
    # backtick o un `$(...)` sarebbe eseguito dal runner. Passa per
    # l'ambiente, che e' la mitigazione documentata.
    esecuzioni = [r for r in testo.splitlines() if r.strip().startswith('run:')]
    assert esecuzioni, 'il job non esegue niente'
    for riga in esecuzioni:
        assert '${{' not in riga, riga
    assert 'PR_BODY: ${{ github.event.pull_request.body }}' in testo
    assert '.github/scripts/check_closing_keyword.py' in testo

    regole = [r for r in json.loads(_leggi(RULESET))['rules']
              if r['type'] == 'required_status_checks']
    assert len(regole) == 1
    contesti = [c['context']
                for c in regole[0]['parameters']['required_status_checks']]
    assert contesti == nomi, (
        "contesto richiesto e nome del job divergono: il merge resterebbe "
        "appeso a un contesto che nessuno pubblica")
