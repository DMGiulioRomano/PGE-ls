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

La forma dell'envelope invece non è di questa chiave. Fino a PGE #211 i guard
sul formato compatto (percentuali del pattern, `n_reps` e `end_time` booleani)
erano semantica di `grain.read_direction` e qui il motore li accettava; da lì
vivono in `EnvelopeBuilder` e valgono per ogni chiave, e PGE #288 ha aggiunto
quelli che vengono prima del builder (la forma dict, lo slot interp del
compatto, il tipo per punto). Il gate passa il corpo a `create_scaled_envelope`
come il parser di qualunque altro parametro, quindi qui la forma la giudica lo
specchio unico, `envelope_guards.check_envelope_shape`, con il `time_mode` dello
stream. Questo modulo tiene solo cio' che viene prima: l'espressione, la
stringa, la mappa per-parametro, il corpo vuoto che rifiuta il classificatore
(PGE #209). Una copia della forma qui dentro e' gia' divergita una volta: era
alla lettera dove la factory non lo e' (`'Step'`, uno slot interp vuoto,
`type: Cubic`), e validava lo slot di un compatto che l'envelope non legge.
"""

from dataclasses import dataclass
from typing import Any, Iterable, Optional

from granular_ls.envelope_guards import check_envelope_shape
from granular_ls.envelope_shapes import (
    contains_math_expression,
    normalize_engine_values,
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


def check_envelope_body(body: Any, param_key: Optional[str] = None,
                        time_mode: str = 'absolute',
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
        time_mode: il `time_mode` dello stream, con cui il gate scala il
            corpo (`create_scaled_envelope`): vedi `envelope_guards`.

    Returns:
        None se il motore lo accetterebbe o se non c'è niente da decidere
        (numeri, stringhe, `None`); il primo problema altrimenti.
    """
    issue = _check_body(body, time_mode)
    if issue is None or param_key is None:
        return issue
    return DeviationProbabilityIssue(value=issue.value, hint=issue.hint,
                                     param_key=param_key)


def _check_body(body: Any,
                time_mode: str = 'absolute',
                ) -> Optional[DeviationProbabilityIssue]:
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

    if isinstance(body, dict) and 'points' not in body:
        return _issue(body, DICT_HINT)

    if isinstance(body, list) and not body:
        # La rifiuta il classificatore (PGE #209), prima del gate.
        return _issue(body, EMPTY_HINT)

    if isinstance(body, (list, dict)):
        # Il resto e' la forma dell'envelope, la stessa sotto ogni chiave
        # (PGE #211, #288): il gate passa il corpo a `create_scaled_envelope`
        # come il parser di qualunque altro parametro.
        issue = check_envelope_shape(body, time_mode)
        if issue is None:
            return None
        return _issue(issue.value, issue.hint)

    # Numeri, bool, None: nulla da costruire, nulla da dire.
    return None


def check_global_value(
    raw: Any, known_keys: Optional[Iterable[str]] = None,
    time_mode: str = 'absolute',
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
            issue = check_envelope_body(value, param_key=key,
                                        time_mode=time_mode)
            if issue is not None:
                return issue
        return None
    return check_envelope_body(raw, time_mode=time_mode)
