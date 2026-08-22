# granular_ls/deviation_probability.py
"""
Il corpo di `deviation_probability` che non si costruisce come envelope.

Fino a PGE #209 un corpo malformato sotto questa chiave veniva accettato in
silenzio e diventava un `AlwaysGate`: probabilità 100%, la variazione applicata
a **tutti** i grani. Più l'errore era grossolano, meno il sistema lo segnalava
— e `AlwaysGate` non è un ripiego neutro, è il gate più lontano da quanto
scritto. Ora il motore solleva `InvalidFieldValueError` sul campo
`deviation_probability.<chiave>` (o `deviation_probability` per la forma
globale), e quell'errore arriva a render lanciato.

Qui si anticipa: le forme sono decidibili dal solo testo YAML.

Il criterio è **non essere più severi del motore**. Un language server che
segnala YAML valido è peggio di uno che tace, quindi ogni regola di questo
modulo è stata verificata interrogando `GateFactory.create_gate` sul corpo
corrispondente, e `tests/test_pge_parity.py` continua a interrogarlo su un
corpus perché il confronto non scada.

Cosa resta fuori, deliberatamente:

- **i corpi dove compare un'espressione matematica.** Il `Generator` valuta
  le espressioni fra parentesi su tutto lo YAML prima che il gate le veda,
  **ricorrendo dentro liste e dict**: `(50/2)` arriva come `25` tanto da
  scalare quanto sepolta nella Y di un breakpoint, e `(abc)` non si valuta e
  resta stringa. Prevedere quale delle due sopravviva vorrebbe dire rifare
  `_eval_math_expressions`, quindi si tace — sul corpo intero, perché il
  valore vero dipende da quell'espressione.

  L'altra metà di quella funzione invece si riproduce: la conversione in
  numero non pretende le parentesi (`"50"` arriva come `50`) ed è una
  `try/except ValueError`. Quindi il corpo si normalizza con
  `normalize_engine_values` prima di guardarlo, e le stringhe che restano
  stringhe — `"abc"`, `""`, `"1e3"` — sono errori come per il motore, che su
  quelle alza `InvalidParameterError`.
- **i valori Y fuori da 0-100.** Il motore non li rifiuta: `150` è un
  `AlwaysGate` legittimo, non un errore di scrittura.
- **i guard specifici di `grain.read_direction`** (percentuali del pattern in
  `[0, 100]`, `x` monotone, `n_reps` booleano). Verificati: qui il motore li
  accetta. Sono semantica di quella chiave, non del formato envelope — e
  replicarli qui segnalerebbe YAML che rende. Se PGE#211 li farà salire nel
  layer condiviso, questo modulo è il posto dove aggiungerli.
"""

from dataclasses import dataclass
from typing import Any, Iterable, Optional

from granular_ls.envelope_shapes import (
    VALID_INTERP_TYPES,
    contains_math_expression,
    is_bp_group,
    is_breakpoint,
    is_3tuple_breakpoint,
    is_loop_block,
    is_num,
    is_valid_point,
    normalize_engine_values,
)
from granular_ls.time_distributions import (
    TIME_DISTRIBUTION_NAMES,
    check_time_distribution,
)

# La chiave nello YAML: identità del campo in ogni diagnostica.
DEVIATION_PROBABILITY_PATH = 'deviation_probability'

FORM_HINT = (
    "questo corpo non si costruisce come envelope. Le forme note sono: lista "
    "di breakpoint `[[t, v], ...]`, dict `{points: [...]}`, BP group "
    "`[points, interp]`, formato compatto `[pattern, end_time, n_reps]`. "
    "Un valore scalare (0-100) resta la forma più semplice."
)

EMPTY_HINT = (
    "una lista vuota non è un envelope. Fino a PGE #209 passava in silenzio e "
    "diventava probabilità 100% — la variazione applicata a tutti i grani, "
    "cioè l'opposto di quanto una lista vuota suggerisce."
)

DICT_HINT = (
    "un dict envelope porta i suoi breakpoint sotto `points`. Senza quella "
    "chiave il motore non trova l'envelope e si ferma."
)

POINTS_HINT = (
    "i punti di un envelope sono `[t, v]` o `[t, v, type]`, con `t` e `v` "
    "numerici."
)

INTERP_HINT = (
    "interpolazione non riconosciuta: le sole ammesse sono "
    + ", ".join(f'`{t}`' for t in VALID_INTERP_TYPES) + "."
)

GROUP_ARITY_HINT = (
    "un BP group richiede almeno 2 punti: con meno non ha segmenti interni da "
    "interpolare."
)

PATTERN_HINT = (
    "il pattern di un ciclo è una lista non vuota di punti piatti "
    "(`[x%, y]` o `[x%, y, type]`)."
)

END_TIME_HINT = (
    "l'istante di fine del ciclo è un numero positivo: è il tempo entro cui "
    "le ripetizioni si distribuiscono."
)

N_REPS_HINT = (
    "il numero di ripetizioni del formato compatto è un intero >= 1."
)

DIST_NAME_HINT = (
    "il quinto elemento del formato compatto è la distribuzione temporale dei "
    "cicli, e ne esiste un elenco chiuso: {disponibili}. Si scrive come nome "
    "('exponential') o come dict con i suoi parametri ({{type: geometric, "
    "ratio: 1.5}}); omettendola i cicli durano uguale."
)

DIST_PARAM_HINT = (
    "i parametri della distribuzione '{nome}' non sono validi.{nota}"
)

DIST_TIPO_IMPLICITO = (
    " Senza la chiave `type` la distribuzione è `linear`, che non prende "
    "parametri: se ne volevi un'altra, dichiarane il nome."
)


@dataclass(frozen=True)
class DeviationProbabilityIssue:
    """Il primo problema trovato nel corpo, con la riga a cui ancorarlo.

    Attributes:
        value: il valore incriminato, come l'utente l'ha scritto.
        hint: cosa c'è che non va, già formulato.
        param_key: la chiave dentro il dict per-parametro (`volume`, `pan`,
            ...), o None per la forma globale. È il campo che il motore
            nominerebbe nell'errore.
    """
    value: Any
    hint: str
    param_key: Optional[str] = None

    @property
    def field(self) -> str:
        """Il campo come lo nomina il motore."""
        if self.param_key is None:
            return DEVIATION_PROBABILITY_PATH
        return f'{DEVIATION_PROBABILITY_PATH}.{self.param_key}'


def _issue(value: Any, hint: str) -> DeviationProbabilityIssue:
    return DeviationProbabilityIssue(value=value, hint=hint)


def _check_interp(interp: Any) -> Optional[DeviationProbabilityIssue]:
    """Un interp dichiarato vale una delle tre interpolazioni note.

    `None` è l'interp omesso, che è valido: nel formato compatto il quarto
    elemento si può lasciare vuoto.
    """
    if interp is None:
        return None
    if not isinstance(interp, str) or interp not in VALID_INTERP_TYPES:
        return _issue(interp, INTERP_HINT)
    return None


def _check_time_dist(spec: Any) -> Optional[DeviationProbabilityIssue]:
    """Il quinto elemento, con le regole del registro condiviso."""
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


def _check_points(points: Any, hint: str = POINTS_HINT
                  ) -> Optional[DeviationProbabilityIssue]:
    """Una lista di punti piatti, non vuota."""
    if not isinstance(points, list) or not points:
        return _issue(points, hint)
    for p in points:
        if not is_valid_point(p):
            return _issue(p, POINTS_HINT)
        if len(p) == 3:
            issue = _check_interp(p[2])
            if issue is not None:
                return issue
    return None


def _check_bp_group(group: list) -> Optional[DeviationProbabilityIssue]:
    """BP group `[points, interp]`: interp della macrozona, almeno 2 punti."""
    points, interp = group
    issue = _check_interp(interp)
    if issue is not None:
        return issue
    if len(points) < 2:
        return _issue(points, GROUP_ARITY_HINT)
    return _check_points(points)


def _check_compact(compact: list) -> Optional[DeviationProbabilityIssue]:
    """Formato compatto `[pattern, end_time, n_reps, interp?, dist?, wrap?]`.

    Di `end_time` e `n_reps` si verifica quanto `is_loop_block` non garantisce
    già: il segno e l'arità. Che `n_reps` sia un `int` e `end_time` un numero
    lo garantisce la forma, altrimenti il corpo non sarebbe un ciclo e
    cadrebbe fra gli elementi di una lista qualsiasi.

    Resta fuori `end_time <= time_offset`, che dipende dall'offset accumulato
    dagli elementi che precedono il ciclo in una lista mista: verificarlo
    vorrebbe dire rifare la passata di `EnvelopeBuilder.parse`. Il segno
    invece è decidibile, perché quell'offset non è mai negativo.
    """
    pattern, end_time, n_reps = compact[0], compact[1], compact[2]

    issue = _check_points(pattern, PATTERN_HINT)
    if issue is not None:
        return issue

    if is_num(end_time) and end_time <= 0:
        return _issue(end_time, END_TIME_HINT)

    if is_num(n_reps) and n_reps < 1:
        return _issue(n_reps, N_REPS_HINT)

    if len(compact) >= 4:
        issue = _check_interp(compact[3])
        if issue is not None:
            return issue

    if len(compact) >= 5:
        issue = _check_time_dist(compact[4])
        if issue is not None:
            return issue

    return None


def _check_item(item: Any) -> Optional[DeviationProbabilityIssue]:
    """Un elemento della lista: breakpoint, gruppo, ciclo compatto o dict."""
    if is_loop_block(item):
        return _check_compact(item)

    if is_bp_group(item):
        return _check_bp_group(item)

    if is_3tuple_breakpoint(item):
        return _check_interp(item[2])

    if is_breakpoint(item):
        return None

    if isinstance(item, dict) and 't' in item and 'v' in item:
        if not is_num(item['t']) or not is_num(item['v']):
            return _issue(item, POINTS_HINT)
        return _check_interp(item.get('type'))

    return _issue(item, FORM_HINT)


def check_envelope_body(body: Any, param_key: Optional[str] = None
                        ) -> Optional[DeviationProbabilityIssue]:
    """
    Dice se il motore costruirebbe un envelope da questo corpo.

    Va chiamata solo dove il corpo è **davvero** in posizione di envelope: il
    valore di una chiave dentro il dict per-parametro, o il valore globale
    quando è una lista (un dict globale senza `points` è la mappa
    per-parametro, non un envelope malformato — vedi `check_global_value`).

    Args:
        body: il valore letto dallo YAML.
        param_key: la chiave del dict per-parametro, per nominare il campo.

    Returns:
        None se il motore lo accetterebbe o se non c'è niente da decidere
        (numeri, stringhe, `None`); il primo problema altrimenti.
    """
    issue = _check_body(body)
    if issue is None or param_key is None:
        return issue
    return DeviationProbabilityIssue(value=issue.value, hint=issue.hint,
                                     param_key=param_key)


def _check_body(body: Any) -> Optional[DeviationProbabilityIssue]:
    # Un'espressione ovunque nel corpo rende il corpo indecidibile: il gate
    # non vede quel che c'è scritto, vede quel che ne esce. Vale per l'intero
    # corpo e non solo per il valore che la porta, perché una `(50/2)` nella
    # X di un punto sposta anche il punto accanto.
    if contains_math_expression(body):
        return None

    # Le stringhe numeriche invece si convertono come fa il motore, così la
    # forma si giudica sui valori che vedrà lui: `[[0, "50"]]` è una lista di
    # breakpoint, non un corpo malformato.
    body = normalize_engine_values(body)

    if isinstance(body, str):
        # Quel che resta stringa dopo la normalizzazione, il motore lo vede
        # come è scritto e lo rifiuta (`InvalidParameterError`).
        return _issue(body, FORM_HINT)

    if isinstance(body, dict):
        if 'points' not in body:
            return _issue(body, DICT_HINT)
        issue = _check_points(body['points'])
        if issue is not None:
            return issue
        return _check_interp(body.get('type'))

    if isinstance(body, list):
        if not body:
            return _issue(body, EMPTY_HINT)
        if is_loop_block(body):
            return _check_compact(body)
        if is_bp_group(body):
            return _check_bp_group(body)
        for item in body:
            issue = _check_item(item)
            if issue is not None:
                return issue
        return None

    # Numeri, bool, None, stringhe: nulla da costruire, nulla da dire.
    return None


def check_global_value(
    raw: Any, known_keys: Optional[Iterable[str]] = None,
) -> Optional[DeviationProbabilityIssue]:
    """
    Valida il valore globale di `deviation_probability`.

    La forma globale è ambigua per costruzione e va sciolta prima di
    validare: un **dict con `points`** è un envelope globale, un dict
    **senza** è la mappa per-parametro, e le cinque scritture che disattivano
    la deviazione (chiave assente, `null`, `{}`, `false`, chiave a `null`) non
    sono errori di nessun tipo.

    Il caso da non sbagliare è `deviation_probability:` lasciata vuota: è
    l'unica delle cinque che **non** disattiva niente — vale jitter implicito
    all'1% (PGE #210). Non è un errore, quindi qui tace; è l'hover a doverlo
    dire.

    `known_keys` sono le chiavi che il motore legge davvero. Nella mappa
    per-parametro lui consulta solo quelle dello schema
    (`if param_key in deviation_probability`, gate_factory): tutto il resto
    cade nel gate range-only senza mai passare dalla costruzione
    dell'envelope, quindi un corpo malformato sotto una chiave che non
    esiste non è un errore per nessuno. Omettendo l'argomento si valida
    tutto, che è il comportamento di chi non sa quali siano le chiavi vere.

    Non si segnala la chiave sconosciuta in sé: nemmeno il motore lo fa, ed
    è un'altra diagnostica.
    """
    if isinstance(raw, dict) and 'points' not in raw:
        # Mappa per-parametro: ogni valore è un envelope per conto suo.
        allowed = None if known_keys is None else frozenset(known_keys)
        for key, value in raw.items():
            if value is None:
                continue  # chiave a null: range-only, non un errore
            if allowed is not None and key not in allowed:
                continue  # chiave che il motore non consulta mai
            issue = check_envelope_body(value, param_key=key)
            if issue is not None:
                return issue
        return None
    return check_envelope_body(raw)
