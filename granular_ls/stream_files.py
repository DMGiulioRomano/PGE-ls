"""
granular_ls/stream_files.py

Lo stream come file (PGE #290, PGE-ls #65): una voce della lista `streams:`
del master puo' essere `- file: <path>`, e lo stream sta in un altro
documento YAML -- un documento del laboratorio, che si apre e si rende anche
da solo. Il master ne tiene soltanto il piazzamento.

Questo modulo e' il mirror delle regole del motore (`pge/engine/
stream_files.py`, `resolve_stream_files`): le chiavi ammesse accanto a
`file:`, il default di `stream_id`, come si risolve il path, e quando il file
importato non e' importabile. Lo leggono diagnostica, hover, completion e il
go-to-file del server; nessuno dei quattro ricopia una regola.

Le regole, con il numero che hanno nel reference del motore
(`docs/reference/yaml.md`, «Stream come file»):

  2. Il path e' relativo alla cartella del master; un path assoluto vale
     com'e' (`os.path.join(dirname(master), file)`).
  3. Il file importato contiene un solo stream, e niente `file:` dentro.
  4. Accanto a `file:` solo il piazzamento (`PLACEMENT_KEYS`); qualunque
     altra chiave e' `StreamFileKeyError`. Il default di `stream_id` e' il
     nome del file senza estensione, e `stream_id: null` vale come assente.
  5. Del file importato il motore ignora i top-level (`seed`, `duration`,
     `bpm`, ...) e le chiavi di piazzamento dentro lo stream.
  7. Due stream con lo stesso `stream_id` effettivo, se almeno uno viene da
     `file:`, sono `StreamFileDuplicateIdError`.

Limite dichiarato: il motore confronta gli id *dopo* le espressioni
matematiche (un file `01.yml` importato senza `stream_id` e' lo stream `1`).
Qui l'id si legge com'e' scritto, senza math eval: `01` e `1` non collidono.
"""
import os
import re
from pathlib import Path
from typing import List, Optional, Tuple

import yaml


#: La chiave della voce che importa. Mirror di `'file' in voce` nel motore.
FILE_KEY = 'file'

_FILE_KEY_RE = re.compile(rf'^{FILE_KEY}\s*:')

#: Le chiavi che il master tiene accanto a `file:`, nell'ordine del motore.
#: Mirror di `CHIAVI_DI_PIAZZAMENTO` (`pge/engine/stream_files.py`); la
#: parita' la verifica `tests/test_stream_files.py`.
PLACEMENT_KEYS: Tuple[str, ...] = ('stream_id', 'onset', 'mute', 'solo')

#: Top-level che un file importato puo' scrivere per rendersi da solo, e che
#: il motore ignora quando il file e' importato (regola 5). `seed` serve
#: solo all'avviso `[SEED]` della regola 6.
IGNORED_TOP_LEVEL: Tuple[str, ...] = ('seed', 'duration', 'bpm')


def default_stream_id(file_value: str) -> str:
    """Lo `stream_id` di una voce `file:` che non ne scrive uno.

    Mirror di `os.path.splitext(os.path.basename(voce['file']))[0]`.
    """
    return os.path.splitext(os.path.basename(file_value))[0]


def resolve_import_path(master_path, file_value: str) -> Path:
    """Il path del file importato, risolto sulla cartella del master.

    Mirror di `os.path.join(os.path.dirname(master_path), voce['file'])`:
    un `file_value` assoluto vale com'e'.
    """
    return Path(os.path.join(os.path.dirname(str(master_path)), file_value))


def extra_keys(entry_keys) -> List[str]:
    """Le chiavi di una voce `file:` che non sono piazzamento (regola 4).

    `entry_keys` e' l'iterabile delle chiavi della voce, nell'ordine scritto.
    """
    return [k for k in entry_keys
            if k != FILE_KEY and k not in PLACEMENT_KEYS]


def import_problem(path: Path) -> Optional[str]:
    """Perche' il file importato non si importa, o None se si importa.

    Le stesse forme del motore, nello stesso ordine: il file che non c'e' o
    non si legge, lo YAML malformato, un documento che non porta esattamente
    uno stream (`_stream_unico` -> `StreamFileCountError`), e un `file:`
    dentro lo stream importato (`StreamFileChainError`).

    Il messaggio e' una frase sola, senza il path: chi lo mostra sa gia'
    quale file ha letto.
    """
    try:
        testo = path.read_text(encoding='utf-8')
    except FileNotFoundError:
        return "il file non esiste"
    except IsADirectoryError:
        return "e' una cartella, non un documento YAML"
    except (OSError, UnicodeDecodeError) as exc:
        return f"il file non si legge ({exc.__class__.__name__})"
    try:
        documento = yaml.safe_load(testo)
    except yaml.YAMLError as exc:
        dove = getattr(exc, 'problem_mark', None)
        riga = f" (riga {dove.line + 1})" if dove is not None else ''
        return f"YAML malformato{riga}"

    trovati = _count_problem(documento)
    if trovati is not None:
        return ("il file importato deve contenere esattamente uno stream "
                f"(una lista 'streams:' con un mapping dentro); trovato: "
                f"{trovati}")
    (stream,) = documento['streams']
    if FILE_KEY in stream:
        return ("lo stream importato ha a sua volta una chiave 'file:': "
                "niente catene di file importati")
    return None


def _count_problem(documento) -> Optional[str]:
    """La riga `Trovati:` di `StreamFileCountError`, o None con uno stream.

    Mirror di `_stream_unico` del motore, forma per forma.
    """
    if not isinstance(documento, dict):
        if documento is None:
            return 'nessuno stream'
        return f"il documento non e' una mappa ({type(documento).__name__})"
    streams = documento.get('streams')
    if streams is None:
        return 'nessuno stream'
    if not isinstance(streams, list):
        return f"'streams' non e' una lista ({type(streams).__name__})"
    if len(streams) != 1:
        return 'nessuno stream' if not streams else f'{len(streams)} stream'
    if not isinstance(streams[0], dict):
        return f"una voce che non e' uno stream ({type(streams[0]).__name__})"
    return None


def scalar_text(raw: str) -> str:
    """Il valore scalare di una riga `chiave: valore`, senza virgolette.

    `raw` e' il testo dopo i due punti. Un commento inline (`# ...` dopo uno
    spazio) cade; le virgolette che racchiudono il valore pure. Uno scalare
    vuoto, `~` o `null` torna `''`: per il motore e' assente.
    """
    valore = raw.strip()
    if valore[:1] in ('"', "'"):
        fine = valore.find(valore[0], 1)
        return valore[1:fine] if fine > 0 else valore[1:]
    for i, c in enumerate(valore):
        if c == '#' and (i == 0 or valore[i - 1] in ' \t'):
            valore = valore[:i]
            break
    valore = valore.strip()
    return '' if valore in ('~', 'null', 'Null', 'NULL') else valore


def value_span(line: str) -> Optional[Tuple[int, int, str]]:
    """Dove sta, sulla riga, il valore di una chiave `file:`.

    Ritorna `(inizio, fine, valore)` in caratteri della riga, virgolette
    escluse, o None se la riga non e' una chiave `file:` con un valore.
    Accetta sia `    file: x.yml` sia `  - file: x.yml`.
    """
    testo = line.lstrip()
    inizio = len(line) - len(testo)
    if testo.startswith('- '):
        resto = testo[2:].lstrip()
        inizio += len(testo) - len(resto)
        testo = resto
    if not testo.startswith(FILE_KEY):
        return None
    dopo = testo[len(FILE_KEY):]
    if not dopo.lstrip().startswith(':'):
        return None
    # dopo i due punti (anche `file :` e' la chiave `file` per YAML)
    pos = inizio + len(FILE_KEY) + (len(dopo) - len(dopo.lstrip())) + 1
    raw = line[pos:]
    valore = scalar_text(raw)
    if not valore:
        return None
    start = line.find(valore, pos)
    if start < 0:
        return None
    return start, start + len(valore), valore


# -----------------------------------------------------------------------------
# Documentazione condivisa da hover e completion
# -----------------------------------------------------------------------------

FILE_KEY_DOC = (
    "**file** — lo stream come file (PGE #290)\n\n"
    "La voce importa uno stream scritto in un altro documento YAML: un "
    "documento del laboratorio, che si apre e si rende anche da solo. Il "
    "master ne decide soltanto il piazzamento.\n\n"
    "- Il path e' **relativo alla cartella del master**, non alla directory "
    "da cui si lancia; un path assoluto vale com'e'.\n"
    "- Il file importato contiene **un solo stream** (una lista `streams:` "
    "con un mapping dentro), e nessun `file:` dentro: niente catene.\n"
    "- Accanto a `file:` stanno solo `stream_id`, `onset`, `mute`, `solo`. "
    "Ogni altra chiave e' un errore (`StreamFileKeyError`): si scrive nel "
    "file importato.\n"
    "- Senza `stream_id` (o con `stream_id: null`) l'id e' il nome del file "
    "senza estensione: `streams/risacca.yml` -> `risacca`.\n"
    "- Del file importato si legge lo stream: i top-level (`seed`, "
    "`duration`, `bpm`) e il piazzamento dentro lo stream sono ignorati.\n\n"
    "Lo stream suona come nel laboratorio solo con lo stesso `seed` nel "
    "master e nel file e lo stesso `stream_id` effettivo."
)

FILE_KEY_DETAIL = 'stream da file (PGE #290)'

PLACEMENT_NOTE = (
    "Accanto a `file:` (PGE #290) questa chiave e' piazzamento: la decide "
    "il master, e quella scritta dentro il file importato e' ignorata."
)

_IGNORED_TOP_LEVEL_DOCS = {
    'seed': (
        "**seed** (top-level)\n\n"
        "Seed del documento: lo stesso YAML produce lo stesso render "
        "(renderer NumPy). Assente, il motore genera un seed di sessione e "
        "lo logga."
    ),
    'duration': (
        "**duration** (top-level)\n\n"
        "Durata del documento quando si rende da solo. Non e' la `duration` "
        "di uno stream: quella si scrive dentro lo stream."
    ),
    'bpm': (
        "**bpm** (top-level)\n\n"
        "Tempo del documento quando si rende da solo (lo scrive il "
        "laboratorio)."
    ),
}

IMPORTED_NOTE = (
    "**Se questo file e' importato** da un master con `- file:` "
    "(PGE #290) la chiave e' ignorata: il brano ha un seed solo, quello del "
    "master, e la durata e il tempo del brano li decide il master. Un `seed` "
    "diverso da quello del master fa scrivere al motore un avviso "
    "(`[SEED] Il file importato ...`): lo stream non suona come quando il "
    "file si rende da solo."
)


def top_level_doc(key: str) -> Optional[str]:
    """La doc di un top-level che un file importato scrive e il motore ignora."""
    doc = _IGNORED_TOP_LEVEL_DOCS.get(key)
    if doc is None:
        return None
    return f"{doc}\n\n{IMPORTED_NOTE}"


def entry_imports_file(lines: List[str], line_n: int) -> bool:
    """True se la riga `line_n` sta in una voce `streams:` con `file:`.

    La voce e' quella del marcatore `- ` piu' vicino sopra la riga, a indent
    2 come in tutto il language server; le chiavi guardate sono le sue
    dirette (sulla riga del trattino e all'indentazione del contenuto).
    """
    start = None
    for i in range(min(line_n, len(lines) - 1), -1, -1):
        raw = lines[i]
        stripped = raw.strip()
        leading = len(raw) - len(raw.lstrip())
        if (stripped.startswith('- ') or stripped == '-') and leading == 2:
            start = i
            break
        if stripped and not stripped.startswith('#') and leading == 0:
            return False
    if start is None:
        return False
    content_indent = 4
    for n in range(start, len(lines)):
        raw = lines[n]
        stripped = raw.strip()
        if not stripped or stripped.startswith('#'):
            continue
        indent = len(raw) - len(raw.lstrip())
        if n == start:
            stripped = stripped[2:].lstrip() if stripped.startswith('- ') else ''
        elif indent < content_indent:
            break
        elif indent != content_indent:
            continue
        if _FILE_KEY_RE.match(stripped):
            return True
    return False
