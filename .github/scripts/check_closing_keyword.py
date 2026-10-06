#!/usr/bin/env python3
# Copia. L'originale, con la suite intera che lo esercita, sta in
# DMGiulioRomano/PythonGranularEngine, .github/scripts/check_closing_keyword.py:
# se questo file va cambiato, si cambia la' e si ricopia. I sette repo tengono
# lo stesso check perche' la regola che codifica e' una proprieta' di GitHub --
# le nove parole chiave, la grammatica del riferimento -- e non di un repo.
"""Il corpo di una PR dichiara l'issue che chiude, oppure dichiara di non chiuderne.

GitHub chiude un'issue al merge soltanto se un **closing keyword** compare nel
**corpo** della pull request -- non nel titolo, non in un commento, non in un
messaggio di commit della branch -- e la PR entra nel **branch di default**.
Le parole chiave sono inglesi e sono nove: `close`/`closes`/`closed`,
`fix`/`fixes`/`fixed`, `resolve`/`resolves`/`resolved`.

Questo modulo e' il guardiano di quella riga. Tre scelte lo governano, e
puntano tutte nella stessa direzione.

**Piu' stretto di GitHub, mai piu' largo.** Un check che rifiuta una grafia
che GitHub collegherebbe costa una riga da riscrivere, e lo si vede subito;
uno che accetta una grafia che GitHub *non* collega costa l'issue che resta
aperta dopo il merge senza che nessuno lo veda -- cioe' esattamente il guasto
per cui esiste. Percio' fra parola chiave e riferimento si pretende spazio
(`Closes #12`) e non i due punti (`Closes: #12`), che la documentazione di
GitHub non dichiara fra le grafie supportate.

**Si legge solo cio' che GitHub legge.** I commenti HTML del template, i
blocchi di codice recintati e il codice in linea escono dal corpo prima del
confronto. Non e' pedanteria: il template stesso porta un esempio con un
numero dentro un commento, e senza quella potatura il check sarebbe verde su
ogni PR mai compilata -- verde per via del proprio esempio, che e' il modo
silenzioso di non controllare niente.

**Un'issue di un altro repo non si chiude.** `Closes DMGiulioRomano/PGE-ui#162`
crea un rimando, non una chiusura: il closing keyword chiude solo nel repo
della PR. Qui dentro quella grafia non vale come dichiarazione, e il messaggio
lo dice invece di tacere -- e' la confusione piu' probabile in un gruppo di
repo che si citano a vicenda a ogni PR.

Esce 0 se il corpo dichiara, 1 altrimenti, con la diagnosi su stdout.
"""

from __future__ import annotations

import os
import re
import sys
from typing import List, Tuple

#: Le nove parole chiave che GitHub riconosce, in ordine di lunghezza
#: decrescente: l'alternanza della regex e' golosa da sinistra, e con
#: `close|closes` davanti, `closes` verrebbe letto come `close` + una `s`
#: che poi non e' uno spazio.
KEYWORDS: Tuple[str, ...] = (
    "closes", "closed", "close",
    "fixes", "fixed", "fix",
    "resolves", "resolved", "resolve",
)

_KW = "|".join(KEYWORDS)

#: `Closes #12` e `Closes GH-12`, le due grafie che chiudono un'issue di
#: QUESTO repo. Il lookbehind evita che `prefixes #12` o `un-fix #12`
#: contino come parola chiave.
SAME_REPO_RE = re.compile(
    r"(?<![\w-])(?:" + _KW + r")[ \t]+(?:#|GH-)(\d+)(?![\w-])",
    re.IGNORECASE,
)

#: `Closes owner/repo#12` e `Closes https://github.com/owner/repo/issues/12`:
#: riconosciute per poterle *rifiutare* con il motivo giusto.
CROSS_REPO_RE = re.compile(
    r"(?<![\w-])(?:" + _KW + r")[ \t]+"
    r"(?:https?://github\.com/(?P<url_repo>[\w.-]+/[\w.-]+)/issues/(?P<url_num>\d+)"
    r"|(?P<slug_repo>[\w.-]+/[\w.-]+)#(?P<slug_num>\d+))(?![\w-])",
    re.IGNORECASE,
)

#: La via d'uscita dichiarata: una riga `No issue: <motivo>`. Non e'
#: un'etichetta perche' il motivo e' il punto, e il motivo va dove lo legge
#: chi rivede, cioe' nel corpo.
NO_ISSUE_RE = re.compile(r"^[ \t]*No issue:[ \t]*(?P<reason>\S.*?)[ \t]*$", re.MULTILINE)

_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`\n]*`")
_FENCE_RE = re.compile(r"^[ \t]*(?:```|~~~)")


def strip_noise(body: str) -> str:
    """Toglie dal corpo cio' che GitHub non legge come testo.

    Nell'ordine: commenti HTML, blocchi recintati, codice in linea. I
    recinti si tolgono per righe e non con una regex su tutto il testo,
    perche' un recinto non chiuso deve mangiare fino alla fine -- che e'
    anche cio' che fa il renderer di GitHub.
    """
    body = _HTML_COMMENT_RE.sub(" ", body)

    out: List[str] = []
    dentro = False
    for riga in body.splitlines():
        if _FENCE_RE.match(riga):
            dentro = not dentro
            continue
        if not dentro:
            out.append(riga)
    senza_recinti = "\n".join(out)

    return _INLINE_CODE_RE.sub(" ", senza_recinti)


def same_repo_issues(body: str) -> List[int]:
    """I numeri di issue di questo repo che il corpo dichiara di chiudere."""
    testo = strip_noise(body)
    visti: List[int] = []
    for numero in SAME_REPO_RE.findall(testo):
        n = int(numero)
        if n not in visti:
            visti.append(n)
    return visti


def cross_repo_refs(body: str) -> List[str]:
    """Le chiusure scritte verso un altro repo, che GitHub non eseguira'."""
    testo = strip_noise(body)
    fuori: List[str] = []
    for m in CROSS_REPO_RE.finditer(testo):
        repo = m.group("url_repo") or m.group("slug_repo")
        numero = m.group("url_num") or m.group("slug_num")
        ref = "{}#{}".format(repo, numero)
        if ref not in fuori:
            fuori.append(ref)
    return fuori


def no_issue_reason(body: str) -> str:
    """Il motivo dichiarato per una PR che non chiude niente, o stringa vuota."""
    m = NO_ISSUE_RE.search(strip_noise(body))
    return m.group("reason") if m else ""


def verdict(body: str) -> Tuple[bool, str]:
    """(va bene?, messaggio). Il messaggio e' sempre per chi ha scritto il corpo."""
    body = body or ""

    issues = same_repo_issues(body)
    if issues:
        elenco = ", ".join("#{}".format(n) for n in issues)
        fuori = cross_repo_refs(body)
        nota = ""
        if fuori:
            nota = (
                "\nNota: {} e' scritta come chiusura ma sta in un altro repo, "
                "quindi al merge non si chiudera'. Se e' solo un rimando, "
                "scrivila senza parola chiave (`Refs {}`); se va chiusa, la "
                "chiude una PR sul suo repo.".format(", ".join(fuori), fuori[0])
            )
        return True, "Il corpo chiude {} al merge su main.{}".format(elenco, nota)

    motivo = no_issue_reason(body)
    if motivo:
        return True, 'Nessuna issue da chiudere, dichiarato: "{}".'.format(motivo)

    righe = [
        "Il corpo di questa PR non dichiara nessuna issue da chiudere.",
        "",
        "Aggiungi al CORPO (non al titolo) una riga per ogni issue:",
        "",
        "    Closes #123",
        "    Closes #124",
        "",
        "Le parole chiave sono inglesi: close/closes/closed, fix/fixes/fixed,",
        "resolve/resolves/resolved. `Closes #123, #124` collega solo la prima:",
        "ogni issue vuole la sua parola chiave. Fra parola chiave e `#numero`",
        "va uno spazio, non i due punti.",
        "",
        "Se questa PR non chiude nessuna issue, dichiaralo con una riga:",
        "",
        "    No issue: <perche'>",
    ]

    fuori = cross_repo_refs(body)
    if fuori:
        righe += [
            "",
            "Trovata una chiusura verso un altro repo ({}): un closing keyword".format(
                ", ".join(fuori)
            ),
            "chiude solo le issue del repo della PR. Quella issue la chiude una PR",
            "sul suo repo; qui e' un rimando, e va scritta senza parola chiave.",
        ]

    if body.strip() and not strip_noise(body).strip():
        righe += [
            "",
            "Il corpo contiene solo i commenti del template: le righe fra",
            "`<!--` e `-->` non le legge nessuno, GitHub comprese.",
        ]

    return False, "\n".join(righe)


def main(argv: List[str]) -> int:
    body = os.environ.get("PR_BODY", "")
    ok, messaggio = verdict(body)
    print(messaggio)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
