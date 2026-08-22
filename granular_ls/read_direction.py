# granular_ls/read_direction.py
"""
Registry statico e validatore di `grain.read_direction` per l'intelligenza LSP.

`read_direction` (PGE issue #207, PR #208) è il verso di lettura INTERNO al
grano: `-1` all'indietro, `+1` in avanti, scalare o envelope. Il bridge la
espone già — è un `ParameterSpec` con bounds `[-1, 1]` — ma **i bounds da soli
la descrivono male**: letti come intervallo continuo accettano `0` e `0.5`, che
il motore rifiuta entrambi al parse. Il dominio è l'insieme `{-1, +1}`, non
l'intervallo, e lo schema non ha modo di esprimerlo; lo stesso vale per
l'interpolazione `step`, imposta e senza un campo che la codifichi.

Da qui la ragione di questo modulo, la stessa di `pitch_units.py`: la chiave
non è servibile dal solo bridge. Qui vivono le due regole di semantica —
dominio a due valori e `step` imposto — e i **guard sulle macro-forme** che il
motore ha aggiunto insieme a loro (arità dei gruppi, cicli del formato
compatto, percentuali del pattern, distribuzione temporale).

Mirror di `src/pge/parameters/read_direction.py`. Le differenze sono due, ed
entrambe volute:

1. **Non solleva**: ritorna il primo problema come `ReadDirectionIssue`, così
   il DiagnosticProvider può ancorarlo a una riga. L'ordine dei controlli è
   quello del motore, quindi su uno YAML con più errori si segnala lo stesso
   che segnalerebbe lui.
2. **La distribuzione temporale si valida per tabella**, non costruendola: qui
   non c'è `TimeDistributionFactory` da chiamare. I bound replicati sono
   quelli dei costruttori (`rate > 0`, `base > 1`, `ratio > 0`, `exponent`
   numerico) — poca cosa e stabile, ma è duplicazione: se il registro del
   motore cambia, `tests/test_pge_parity.py` è il posto che se ne accorge.

Cosa resta fuori, come nel motore: `end_time <= time_offset`, che dipende
dall'offset accumulato dagli elementi che precedono il ciclo in una lista
mista. Verificarlo vorrebbe dire rifare la passata di `EnvelopeBuilder.parse`.
Qui se ne verifica il segno, decidibile perché quell'offset non è mai negativo.

E resta fuori ogni corpo dove compare un'espressione matematica: il `Generator`
valuta `(...)` su tutto lo YAML — ricorrendo dentro liste e dict — prima che il
controller veda i valori, quindi `(0-1)` arriva come `-1` e la stringa scritta
non è il valore. Segnalarla sarebbe un falso positivo su YAML che rende.

La conversione in numero invece non pretende le parentesi (`"1"` arriva come
`1`, un verso legittimo) ed è una `try/except ValueError`, cioè riproducibile:
il valore si normalizza con `normalize_engine_values` prima di guardarlo. Le
stringhe che restano stringhe — `"abc"`, `""`, `"1e3"` — sono errori come per
il motore.
"""

from dataclasses import dataclass
from typing import Any, Optional, Tuple

from granular_ls.envelope_shapes import (
    contains_math_expression,
    is_3tuple_breakpoint as _is_3tuple_breakpoint,
    is_bp_group as _is_bp_group,
    is_loop_block as _is_compact_format,
    normalize_engine_values,
)
from granular_ls.time_distributions import (
    TIME_DISTRIBUTION_NAMES as _TIME_DISTRIBUTION_NAMES,
    check_time_distribution,
)

# La chiave nello YAML: identità del campo in ogni diagnostica.
READ_DIRECTION_PATH = 'grain.read_direction'

# La chiave gemella con cui è in mutua esclusione (gruppo 'grain_direction').
REVERSE_PATH = 'grain.reverse'

# I due soli valori dichiarabili.
READ_DIRECTION_VALUES: Tuple[float, float] = (-1.0, 1.0)

# L'unica interpolazione ammessa, e quella imposta.
REQUIRED_INTERP = 'step'

# Nomi validi della distribuzione temporale dei cicli: vivono in
# time_distributions.py, che è il registro condiviso con le altre chiavi che
# accettano un envelope. Ri-esportati qui perché sono parte della superficie
# storica di questo modulo.
TIME_DISTRIBUTION_NAMES: Tuple[str, ...] = tuple(_TIME_DISTRIBUTION_NAMES)


@dataclass(frozen=True)
class ReadDirectionIssue:
    """Un problema trovato in `grain.read_direction`: il valore e il perché.

    Un solo problema per volta, come il motore, che alla prima violazione
    solleva. Il campo è sempre `grain.read_direction`, quindi non si porta.
    """
    value: Any
    hint: str


# =============================================================================
# HINT — il perché, non solo il cosa
# =============================================================================
# Sono i messaggi del motore riscritti per l'editor: stessa ragione, stessa
# via d'uscita suggerita. Chi legge la diagnostica e poi l'errore di render
# deve riconoscere lo stesso problema.

INTERP_HINT = (
    "grain.read_direction ammette solo l'interpolazione 'step', che è già "
    "implicita: il verso di lettura ha due stati, non una rampa fra i due, e "
    "un valore intermedio fra -1 e +1 non è un verso. Togli il tipo "
    "dichiarato (l'envelope si scrive come una spezzata qualsiasi) oppure "
    "scrivi 'step', che è ridondante ma valido."
)

VALUE_HINT = (
    "grain.read_direction ammette solo -1 (lettura all'indietro) e +1 "
    "(lettura in avanti). Il verso non ha valori intermedi e lo 0 non ha un "
    "segno: non c'è arrotondamento che non sia arbitrario. Per il verso che "
    "segue la testina, ometti la chiave (modalità 'auto')."
)

FORM_HINT = (
    "grain.read_direction accetta uno scalare (-1 o +1) oppure un envelope "
    "nelle forme note: lista di breakpoint [[t, v], ...], dict "
    "{points: [...]}, BP group o formato compatto."
)

GROUP_ARITY_HINT = (
    "un BP group di grain.read_direction richiede almeno 2 punti: con meno "
    "non ha segmenti interni, quindi non c'è nessuna zona a cui applicare "
    "l'interpolazione. Per un verso costante basta lo scalare (-1 o +1)."
)

REPS_ARITY_HINT = (
    "il numero di ripetizioni del formato compatto è un intero >= 1: con "
    "zero o meno cicli non c'è nessun breakpoint da generare."
)

END_TIME_HINT = (
    "il secondo elemento del formato compatto è l'istante assoluto in cui il "
    "ciclo finisce, e deve superare quello in cui comincia: dev'essere un "
    "numero positivo (`true` non è `1`). Che superi davvero l'istante di "
    "partenza lo verifica il motore, l'unico a conoscere l'offset accumulato "
    "dagli elementi che precedono in una lista mista."
)

PATTERN_X_HINT = (
    "la prima coordinata di un punto del pattern è una percentuale del ciclo "
    "e sta in [0, 100]. Fuori da lì il ciclo sfonda i propri confini: sopra "
    "100 il ciclo successivo comincia prima che questo sia finito, sotto 0 "
    "esce un breakpoint a tempo negativo."
)

PATTERN_ORDER_HINT = (
    "le percentuali del pattern non possono tornare indietro: il ciclo si "
    "percorre in avanti una volta sola. Con tempi che si invertono l'envelope "
    "a 'step' legge l'ultimo valore scritto, e il verso dichiarato prima non "
    "comparirebbe in nessun grano. Una percentuale ripetuta invece va bene: "
    "è la discontinuità."
)

DIST_NAME_HINT = (
    "il quinto elemento del formato compatto è la distribuzione temporale "
    "dei cicli, e ne esiste un elenco chiuso: {disponibili}. Si scrive come "
    "nome ('exponential') o come dict con i suoi parametri "
    "({{type: geometric, ratio: 1.5}}); omettendola i cicli durano uguale."
)

DIST_PARAM_HINT = (
    "i parametri della distribuzione '{nome}' non sono validi.{nota} Il verso "
    "di lettura non ha una distribuzione propria: è quella dell'envelope, e i "
    "vincoli sui suoi parametri sono documentati con lei."
)

DIST_TIPO_IMPLICITO = (
    " Senza la chiave `type` la distribuzione è `linear`, che non prende "
    "parametri: se ne volevi un'altra, dichiarane il nome."
)

# =============================================================================
# DOCUMENTAZIONE HOVER
# =============================================================================

# Il testo utile sulla chiave non sono i suoi bounds — che presi da soli
# mentono, suggerendo un intervallo dove c'è un insieme di due valori — ma la
# distinzione fra le tre grandezze che si confondono facilmente.
READ_DIRECTION_DOC = (
    "**read_direction** — il verso di lettura *dentro* il grano\n\n"
    "`-1` all'indietro, `+1` in avanti. Scalare o envelope; il dominio è "
    "l'insieme `{-1, +1}`, non l'intervallo: `0` e `0.5` sono rifiutati al "
    "parse.\n\n"
    "**Tre grandezze da non confondere:**\n\n"
    "| chiave | governa | segno |\n"
    "|---|---|---|\n"
    "| `pointer.speed_ratio` | la velocità e il verso con cui la **testina** "
    "percorre il buffer | negativo = all'indietro |\n"
    "| `grain.read_direction` | il verso con cui il **grano** legge il "
    "materiale | `-1` indietro, `+1` avanti |\n"
    "| blocco `pitch` | l'**altezza percepita** (trasposizione) | sempre "
    "positivo, per costruzione |\n\n"
    "Sono indipendenti: la testina può percorrere il buffer all'indietro "
    "mentre i grani leggono in avanti.\n\n"
    "**L'interpolazione è `step`**, implicita e obbligatoria: l'envelope si "
    "scrive come una spezzata qualsiasi e il gradino lo impone la chiave. "
    "`type: step` esplicito è ridondanza accettata; qualunque altro interp è "
    "un errore.\n\n"
    "**Esclusiva con `grain.reverse`** (gruppo `grain_direction`): le due "
    "chiavi insieme sono un errore, non una priorità. Con entrambe assenti "
    "vale la modalità `auto` — il verso segue il segno di "
    "`pointer.speed_ratio`.\n\n"
    "```yaml\n"
    "grain:\n"
    "  read_direction: 1                    # sempre in avanti\n"
    "  read_direction: [[0, 1], [12, -1]]   # si inverte a t=12\n"
    "```\n\n"
    "Verso stocastico: `deviation_probability.read_direction` è la "
    "probabilità per-grano di **ribaltare** il verso dichiarato."
)

# Le due chiavi del blocco deviation_probability che governano il verso.
# Distinte, e la distinzione va detta: un vecchio
# `deviation_probability: {reverse: N}` non tocca un read_direction appena
# scritto, e chi non lo sa si aspetta il contrario.
DEVIATION_PROBABILITY_DOCS = {
    'read_direction': (
        "Probabilità per-grano di **ribaltare** il verso dichiarato in "
        "`grain.read_direction` (`variation_mode: negate` — un cambio di "
        "segno).\n\n"
        "Governa **solo** `grain.read_direction`: non ha effetto su "
        "`grain.reverse`, che ha la sua chiave."
    ),
    'reverse': (
        "Probabilità per-grano di invertire il verso quando è dichiarato con "
        "`grain.reverse` (`variation_mode: invert`).\n\n"
        "Governa **solo** `grain.reverse`: non tocca `grain.read_direction`, "
        "che ha la sua chiave. Un `deviation_probability: {reverse: N}` "
        "scritto prima di PGE #207 non ribalta in silenzio un "
        "`read_direction` aggiunto dopo."
    ),
}

EXCLUSIVE_HINT = (
    "grain.read_direction e grain.reverse governano la stessa grandezza — il "
    "verso di lettura del grano — con semantiche opposte, e non possono "
    "coesistere: il motore le rifiuta insieme, non ne sceglie una per "
    "priorità.\n"
    "Tieni grain.read_direction (-1 indietro, +1 avanti, anche come envelope) "
    "oppure grain.reverse (chiave vuota = sempre indietro), non entrambe."
)


# =============================================================================
# RICONOSCIMENTO DELLE FORME
# =============================================================================
# Il riconoscimento viene da `envelope_shapes`, che è il mirror condiviso delle
# forme. Qui c'era una copia locale di `_is_compact_format`, tenuta perché
# `is_loop_block` escludeva i bool da `end_time` e `n_reps` mentre il motore li
# accetta per sottoclasse di `int`: quella divergenza è stata corretta alla
# fonte, e la copia non serve più.

def _is_num(value: Any) -> bool:
    """Numero vero: `bool` è sottoclasse di `int`, ma `true` non è `+1`."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _issue(value: Any, hint: str) -> ReadDirectionIssue:
    return ReadDirectionIssue(value=value, hint=hint)


def _check_direction_value(value: Any) -> Optional[ReadDirectionIssue]:
    """Un valore dichiarato — scalare o Y di un breakpoint — vale -1 o +1.

    Il confronto è sull'appartenenza e non converte: `float()` su un intero
    più grande di ogni double alza `OverflowError`. `1 == 1.0` in Python,
    quindi il confronto misto costa niente.
    """
    if not _is_num(value) or value not in READ_DIRECTION_VALUES:
        return _issue(value, VALUE_HINT)
    return None


def _check_interp(interp: Any) -> Optional[ReadDirectionIssue]:
    """Un interp dichiarato — dovunque sia dichiarato — vale 'step'."""
    if interp is None:
        return None
    if interp != REQUIRED_INTERP:
        return _issue(interp, INTERP_HINT)
    return None


def _check_envelope_body(body: Any) -> Optional[ReadDirectionIssue]:
    """Il corpo di un envelope: una macro-forma, oppure una lista di elementi.

    Punto unico della grammatica dei due ingressi — `{points: ...}` e lista
    nuda — perché il motore costruisce entrambe le forme allo stesso modo: un
    ingresso più stretto dell'altro rifiuterebbe uno YAML che renderizza.
    """
    if _is_compact_format(body):
        return _check_compact(body)
    if _is_bp_group(body):
        return _check_bp_group(body)
    return _check_points(body)


def _check_points(points: Any) -> Optional[ReadDirectionIssue]:
    """Percorre una lista di elementi envelope, qualunque forma abbiano."""
    if not isinstance(points, list) or not points:
        return _issue(points, FORM_HINT)
    for item in points:
        issue = _check_item(item)
        if issue is not None:
            return issue
    return None


def _check_item(item: Any) -> Optional[ReadDirectionIssue]:
    """Un elemento della lista: breakpoint, gruppo, ciclo compatto o dict."""
    if _is_compact_format(item):
        return _check_compact(item)

    if _is_bp_group(item):
        return _check_bp_group(item)

    if _is_3tuple_breakpoint(item):
        # Tag per-punto (PGE #54): il type governa il segmento uscente.
        return (_check_interp(item[2])
                or _check_direction_value(item[1]))

    if isinstance(item, dict) and 't' in item and 'v' in item:
        # Il tempo si pretende numerico come nella forma lista: è la stessa
        # grandezza scritta in un altro modo.
        if not _is_num(item['t']):
            return _issue(item, FORM_HINT)
        return (_check_interp(item.get('type'))
                or _check_direction_value(item['v']))

    if isinstance(item, list) and len(item) == 2 and _is_num(item[0]):
        return _check_direction_value(item[1])

    return _issue(item, FORM_HINT)


def _check_bp_group(group: list) -> Optional[ReadDirectionIssue]:
    """BP group (PGE #64): `[points, interp]`, interp della macrozona."""
    points, interp = group
    issue = _check_interp(interp)
    if issue is not None:
        return issue
    # Solo l'arità: che `points` sia una lista di breakpoint piatti lo
    # garantisce già `_is_bp_group`, che qui è sempre passato.
    if len(points) < 2:
        return _issue(points, GROUP_ARITY_HINT)
    return _check_points(points)


def _check_compact(compact: list) -> Optional[ReadDirectionIssue]:
    """Formato compatto: l'interp è il quarto elemento, i valori nel pattern.

    Di `end_time` si verifica il segno, decidibile qui, non il confronto con
    l'istante di partenza (vedi `END_TIME_HINT`).
    """
    pattern, end_time, n_reps = compact[0], compact[1], compact[2]
    interp = compact[3] if len(compact) >= 4 else None

    issue = _check_interp(interp)
    if issue is not None:
        return issue

    if not _is_num(end_time) or end_time <= 0:
        return _issue(end_time, END_TIME_HINT)

    # Solo la natura di `n_reps`: che sia un `int` lo garantisce già
    # `_is_compact_format`. Resta fuori il solo `bool`, che lì passa per
    # sottoclasse e qui no: senza questo `True < 1` è falso, il guard non
    # scatta e il motore renderebbe un ciclo in silenzio.
    if not _is_num(n_reps) or n_reps < 1:
        return _issue(n_reps, REPS_ARITY_HINT)

    if not pattern:
        return _issue(pattern, FORM_HINT)

    precedente = None
    for point in pattern:
        issue = _check_pattern_point(point, precedente)
        if issue is not None:
            return issue
        precedente = point[0]

    # Solo se dichiarata: senza questo ogni ciclo validerebbe una
    # distribuzione implicita per non dire niente.
    if len(compact) >= 5:
        return _check_time_dist(compact[4])
    return None


def _check_pattern_point(point: list,
                         precedente: Any = None) -> Optional[ReadDirectionIssue]:
    """Un punto del pattern di un ciclo: `[x%, y]` o `[x%, y, type]`, piatto.

    Non passa da `_check_item` perché lì le macro-forme sono ammesse e qui no:
    `_is_compact_format` filtra i punti del pattern sulla sola lunghezza (2 o
    3) e un BP group è lungo 2, quindi ci si infila.

    La `x` è una percentuale del ciclo e deve percorrerlo in avanti una volta
    sola. Una `x` ripetuta è ammessa: è la discontinuità.
    """
    if not _is_num(point[0]):
        return _issue(point, FORM_HINT)
    if not 0 <= point[0] <= 100:
        return _issue(point[0], PATTERN_X_HINT)
    if precedente is not None and point[0] < precedente:
        return _issue(point[0], PATTERN_ORDER_HINT)
    if len(point) == 3:
        issue = _check_interp(point[2])
        if issue is not None:
            return issue
    return _check_direction_value(point[1])


def _check_time_dist(spec: Any) -> Optional[ReadDirectionIssue]:
    """La distribuzione temporale del ciclo, raccontata per questa chiave.

    Le regole — nome dal registro, bound dei costruttori — stanno in
    `time_distributions.py`: sono le stesse per ogni chiave che accetta un
    envelope, non una semantica del verso di lettura. Qui resta la
    formulazione, che invece è di questa chiave: chi sbaglia il quinto
    elemento scrivendo un verso va rimandato all'envelope, che è dove quei
    vincoli sono documentati.
    """
    issue = check_time_distribution(spec)
    if issue is None:
        return None
    if issue.kind == 'name':
        return _issue(spec, DIST_NAME_HINT.format(
            disponibili=', '.join(TIME_DISTRIBUTION_NAMES)))
    return _issue(spec, DIST_PARAM_HINT.format(
        nome=issue.nome,
        nota=DIST_TIPO_IMPLICITO if issue.senza_tipo else '',
    ))


def check_read_direction(raw: Any) -> Optional[ReadDirectionIssue]:
    """
    Valida il valore grezzo di `grain.read_direction`.

    Mirror di `normalize_read_direction` del motore, senza la normalizzazione:
    qui non si costruisce niente, si dice solo se il motore accetterebbe.

    Args:
        raw: il valore letto dallo YAML — scalare, envelope in una delle forme
            note, o `None` per la chiave scritta e lasciata vuota.

    Returns:
        `None` se il motore lo accetterebbe; il primo `ReadDirectionIssue`
        altrimenti, nello stesso ordine in cui il motore solleverebbe.
    """
    if contains_math_expression(raw):
        # Il `Generator` valuta le espressioni prima del parse: qui non si
        # legge il valore, si legge la sua scrittura. Il silenzio è sul corpo
        # intero, perché il verso vero dipende da quell'espressione.
        return None

    # Le stringhe numeriche invece si convertono come fa il motore: `"1"` è
    # un verso, e `[[0, "1"], [10, "-1"]]` è una lista di breakpoint.
    raw = normalize_engine_values(raw)

    if raw is None:
        # Chiave vuota: a differenza di `grain.reverse`, qui è un errore.
        return _issue(raw, VALUE_HINT)

    if _is_num(raw):
        return _check_direction_value(raw)

    if isinstance(raw, dict):
        if 'points' not in raw:
            return _issue(raw, FORM_HINT)
        return (_check_interp(raw.get('type'))
                or _check_envelope_body(raw['points']))

    if isinstance(raw, list):
        return _check_envelope_body(raw)

    return _issue(raw, FORM_HINT)
