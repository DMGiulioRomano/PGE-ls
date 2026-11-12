"""
tests/pge_engine.py

Il motore contro cui gira la parita' (issue #70): dove sta, a che commit, e
che cosa vuol dire uno skip «al motore manca X».

La parita' (`test_pge_parity.py`, la parte di `test_stream_files.py` che chiede
al motore) ha tre modi di dire verde senza aver fatto la domanda, e questo
modulo li rende rumorosi:

1. **Il motore manca.** Senza checkout del motore la parita' salta per intera.
   Fuori dalla CI e' giusto (sviluppo senza il sibling); in CI vuol dire un
   secret mancante, e da fuori non si vede. `PGE_REQUIRE_PARITY=1` (letto
   `== "1"`, quindi `=0` lo spegne) trasforma quello skip in un fallimento:
   la CI lo passa quando lo step di checkout del motore e' riuscito.

2. **Al motore manca X.** Contro un motore piu' vecchio della feature lo skip
   e' giusto; contro un motore che la feature ce l'ha e l'ha solo spostata o
   riscritta e' la stessa risposta, e quella e' una bugia verde. Li separa
   `ENGINE_BASELINE`: il commit del motore contro cui i patti sono scritti.
   Se e' un antenato del commit sotto test, «manca X» e' impossibile per
   costruzione, ed `engine_lacks` fallisce invece di saltare.

3. I ripieghi del bridge (`SchemaBridge.read_fallbacks`) li guarda la parita'
   stessa, non questo modulo.

**Aggiornare `ENGINE_BASELINE`** quando un patto nuovo legge qualcosa che il
motore ha aggiunto dopo il commit registrato: il patto nuovo, contro un motore
che non lo contiene, salta, e la registrazione e' cio' che lo fa fallire il
giorno che il nome si sposta. Il commit va preso sul ramo di default del
motore (la CI clona quello), e dentro la profondita' del checkout della CI
(`fetch-depth` in `.github/workflows/ci.yml`): un commit fuori dalla finestra
non si trova, e in CI e' un rosso, non uno skip — senza via di fuga per i
clone superficiali, perche' e' esattamente la via da cui un SHA inventato
passerebbe.
"""
import os
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import NamedTuple, Optional

import pytest

# Il commit del motore contro cui i patti della parita' sono scritti: il merge
# di DMGiulioRomano/PythonGranularEngine#316, ramo main. Contro questo commit
# nessuno dei patti salta per «al motore manca X» (misurato: gli unici skip
# della parita' sono quelli senza numpy/soundfile).
ENGINE_BASELINE = 'ace4ce8f27e683edf8657a7f205768a8e2470e49'

REQUIRE_ENV = 'PGE_REQUIRE_PARITY'

_SIBLING = (Path(__file__).resolve().parent.parent.parent
            / 'PythonGranularEngine' / 'src')

ABSENT_REASON = ("PGE src non trovato: imposta PGE_SRC o clona "
                 "PythonGranularEngine come sibling di PGE-ls.")


def resolve_pge_src() -> Optional[str]:
    """Path al 'src' di PythonGranularEngine: env PGE_SRC o checkout sibling."""
    env = os.environ.get('PGE_SRC')
    if env and Path(env).exists():
        return str(Path(env).resolve())
    if _SIBLING.exists():
        return str(_SIBLING)
    return None


PGE_SRC = resolve_pge_src()


def require_parity() -> bool:
    """La parita' e' pretesa? Solo `PGE_REQUIRE_PARITY=1`, alla lettera."""
    return os.environ.get(REQUIRE_ENV) == '1'


class BaselineStatus(NamedTuple):
    """Dove sta il motore sotto test rispetto a `ENGINE_BASELINE`.

    `state` e' uno di:
      - 'assente'      — nessun motore;
      - 'non-git'      — il motore non e' un checkout git (o git manca):
                          non si sa;
      - 'sconosciuto'  — il commit registrato non e' nel checkout: motore piu'
                          vecchio e mai aggiornato, clone troppo corto, o SHA
                          sbagliato;
      - 'contenuto'    — il commit registrato e' antenato di quello sotto test;
      - 'non-contenuto'— c'e' ma non e' antenato: motore piu' vecchio dei
                          patti, o su un ramo che non li contiene.
    `head` e' il commit sotto test, `distance` i commit di distanza (oltre il
    riferimento se contenuto, indietro se no), `detail` cosa ha risposto git.
    """
    state: str
    head: Optional[str] = None
    distance: Optional[int] = None
    detail: str = ''


def _git(src: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(['git', '-C', src, *args], capture_output=True,
                          text=True, timeout=30)


def baseline_status(src: Optional[str] = None,
                    baseline: str = ENGINE_BASELINE) -> BaselineStatus:
    """Il motore in `src` (default: quello della suite) contiene `baseline`?"""
    if src is None:
        return _cached_status(PGE_SRC, baseline)
    return _status(src, baseline)


@lru_cache(maxsize=None)
def _cached_status(src: Optional[str], baseline: str) -> BaselineStatus:
    return _status(src, baseline)


def _status(src: Optional[str], baseline: str) -> BaselineStatus:
    if src is None:
        return BaselineStatus('assente')
    try:
        head = _git(src, 'rev-parse', 'HEAD')
    except (OSError, subprocess.SubprocessError) as exc:
        return BaselineStatus('non-git', detail=f'{type(exc).__name__}: {exc}')
    if head.returncode != 0:
        return BaselineStatus('non-git', detail=head.stderr.strip())
    head_sha = head.stdout.strip()
    esiste = _git(src, 'cat-file', '-e', f'{baseline}^{{commit}}')
    if esiste.returncode != 0:
        shallow = _git(src, 'rev-parse', '--is-shallow-repository')
        nota = ('clone superficiale' if shallow.stdout.strip() == 'true'
                else 'clone completo')
        return BaselineStatus('sconosciuto', head=head_sha,
                              detail=f'{baseline} non e\' nel checkout ({nota})')
    antenato = _git(src, 'merge-base', '--is-ancestor', baseline, head_sha)
    if antenato.returncode == 0:
        conta = _git(src, 'rev-list', '--count', f'{baseline}..{head_sha}')
        return BaselineStatus('contenuto', head=head_sha,
                              distance=_int(conta.stdout))
    if antenato.returncode == 1:
        conta = _git(src, 'rev-list', '--count', f'{head_sha}..{baseline}')
        return BaselineStatus('non-contenuto', head=head_sha,
                              distance=_int(conta.stdout))
    return BaselineStatus('non-git', head=head_sha,
                          detail=antenato.stderr.strip())


def _int(text: str) -> Optional[int]:
    try:
        return int(text.strip())
    except ValueError:
        return None


def engine_lacks(reason: str, status: Optional[BaselineStatus] = None):
    """Il ripiego dei patti quando al motore manca cio' che leggono.

    Salta se il motore puo' essere piu' vecchio della feature; fallisce se
    contiene il commit di riferimento, perche' li' «manca X» vuol dire che X
    e' stato spostato o riscritto — e un patto che salta in quel caso e' un
    verde che non ha fatto la domanda.
    """
    status = status or baseline_status()
    if status.state == 'contenuto':
        pytest.fail(
            f"{reason} — ma il motore sotto test ({status.head[:7]}) contiene "
            f"il commit contro cui i patti sono scritti "
            f"({ENGINE_BASELINE[:7]}, tests/pge_engine.py): non e' un motore "
            f"piu' vecchio della feature, e' un nome spostato o riscritto. "
            f"Aggiorna la lettura del patto (e il commit registrato, se il "
            f"motore ha cambiato forma di proposito).",
            pytrace=False)
    pytest.skip(reason)


def summary_line(status: Optional[BaselineStatus] = None) -> str:
    """La riga che chiude la run: un verde con parita' non e' uno senza."""
    status = status or baseline_status()
    base = ENGINE_BASELINE[:7]
    if status.state == 'assente':
        return ("parita' con PGE: motore assente — i patti non sono girati"
                + (" (e PGE_REQUIRE_PARITY=1 li pretendeva)"
                   if require_parity() else ""))
    head = status.head[:7] if status.head else '?'
    if status.state == 'contenuto':
        return (f"parita' con PGE: motore {head}, {status.distance} commit "
                f"oltre il riferimento {base}")
    if status.state == 'non-contenuto':
        return (f"parita' con PGE: motore {head}, {status.distance} commit "
                f"indietro rispetto al riferimento {base} (o su un altro "
                f"ramo): gli skip «al motore manca X» restano skip")
    if status.state == 'sconosciuto':
        return (f"parita' con PGE: motore {head}, riferimento {base} non "
                f"trovato ({status.detail}): gli skip «al motore manca X» "
                f"restano skip")
    return (f"parita' con PGE: motore non in un checkout git ({status.detail})"
            f": gli skip «al motore manca X» restano skip")
