# granular_ls/time_distributions.py
"""
Registro delle distribuzioni temporali dei cicli, i bound dei loro
costruttori, e la coppia `(parametro, n_reps)` che trabocca.

È il quinto elemento del formato compatto — `[pattern, end_time, n_reps,
interp?, time_dist?, wrap?]` — e governa quanto dura ogni ripetizione del
ciclo rispetto alle altre. Il motore lo costruisce con
`TimeDistributionFactory`; qui non c'è niente da costruire, quindi nome e
bound sono replicati per tabella.

Il modulo nasce da `read_direction.py`, dove queste regole erano scritte per
la prima volta: sono le stesse per ogni chiave che accetta un envelope, non
una semantica del verso di lettura, e una seconda copia dentro
`deviation_probability.py` avrebbe significato due tabelle da tenere allineate
allo stesso registro.

**Le regole stanno qui, e con loro la frase che vale per ogni chiave.** Il
chiamante riceve un `TimeDistIssue` con il fatto; `describe_issue` lo racconta
come lo racconterebbe chiunque, e chi ha qualcosa di proprio da dire — il
verso di lettura, che non ha una distribuzione sua e rimanda all'envelope — ci
scrive sopra la propria frase. Per l'overflow una frase propria non c'è:
l'errore nasce nella distribuzione, che non sa sotto quale chiave sta, e il
motore dice la stessa cosa per tutte (`overflow_hint`).

Questo è il punto che può divergere dal motore senza che il resto se ne
accorga — `tests/test_pge_parity.py` interroga i due lati sullo stesso corpus.

L'overflow (PGE #212)
---------------------

Le tre potenze del registro — `ratio ** n_reps`, `rate ** -i`,
`(i + 1) ** exponent` — con abbastanza cicli escono dai float, e il motore lo
dice con un `ParameterBoundError` che nomina la coppia: nessuno dei due valori
è fuori posto da solo. Il suo docstring spiega perché la soglia non si replica
a monte — sarebbe «aritmetica destinata a divergere dal comportamento reale di
CPython» — e per un language server scritto in un altro linguaggio è vero. Qui
no: il language server **è** CPython, e legge lo YAML con lo stesso PyYAML del
motore, quindi `ratio: 10` arriva `int` e `ratio: 10.0` arriva `float` come
arrivano a lui. La regola non stima la soglia: rifà le stesse operazioni sugli
stessi tipi, e trabocca dove trabocca il motore.

Con una sola eccezione, che è di costo e non di verdetto: con `ratio` intero
il motore calcola `ratio ** n_reps` su interi illimitati, e a `n_reps` grande
sono megabyte. Il language server gira a ogni tasto premuto, quindi lì decide
coi logaritmi dove il verdetto è certo — con due bit di margine — e rifà il
conto esatto solo vicino al bordo, dove la potenza ha al più un paio di
migliaia di bit.

Cosa la regola **non** chiama errore, perché il motore non lo fa:

- il tratto, prima della soglia, dove i pesi di `exponential` e `power` stanno
  ciascuno in un float e la loro somma no. Il motore divide per infinito, ogni
  ciclo dura zero, e l'envelope resta fermo sul primo valore del pattern — un
  collasso muto, non un rifiuto. Chiamarlo errore sarebbe essere più severi
  del motore;
- `end_time` e l'offset: la soglia non dipende dalla durata del ciclo;
- un parametro intero che da solo non sta in un float (più di 308 cifre):
  lì il motore non arriva alla coppia, e quel che alza non è questa regola.

E cosa invece chiama errore anche se il motore non lo dice con un
`ParameterBoundError`: con `ratio` float fra 1 e 2 c'è un tratto dove
`ratio ** n_reps` sta in un float ma la somma geometrica, divisa per
`1 - ratio`, no. Lì il motore alza un `ZeroDivisionError` nudo (la somma
infinita fa durare zero ogni ciclo, e poi divide per la loro somma): il render
fallisce comunque, e la causa è la stessa coppia.
"""

import math
from dataclasses import dataclass
from typing import Any, Optional

# Nomi validi, alias compresi (mirror di TimeDistributionFactory._DISTRIBUTIONS).
TIME_DISTRIBUTION_NAMES = (
    'exp', 'exponential', 'geo', 'geometric', 'linear', 'log',
    'logarithmic', 'power',
)

# Il nome che vale quando la distribuzione non è dichiarata.
DEFAULT_DISTRIBUTION = 'linear'

# Il nome della classe dietro ciascun alias: è quello che il motore stampa.
_CANONICAL_NAMES = {'exp': 'exponential', 'geo': 'geometric',
                    'log': 'logarithmic'}


def _is_num(value: Any) -> bool:
    """True se è un numero. Il `bool` passa, come in Python: dove i bound lo
    escludono cade da solo (`rate: false` vale 0 e non è > 0)."""
    return isinstance(value, (int, float))


# Bound dei costruttori del registro, replicati per tabella.
#
# `power.exponent` chiede solo che sia un numero, e il `bool` glielo passa:
# `true ** n` fa 1, non alza niente, e rifiutarlo qui romperebbe YAML che oggi
# rendono. Le sorelle invece hanno bound veri, su cui i bool cadono da soli.
DIST_PARAM_SPECS = {
    'linear': {},
    'exponential': {'rate': lambda v: _is_num(v) and v > 0},
    'exp': {'rate': lambda v: _is_num(v) and v > 0},
    'logarithmic': {'base': lambda v: _is_num(v) and v > 1},
    'log': {'base': lambda v: _is_num(v) and v > 1},
    'geometric': {'ratio': lambda v: _is_num(v) and v > 0},
    'geo': {'ratio': lambda v: _is_num(v) and v > 0},
    'power': {'exponent': lambda v: isinstance(v, (int, float))},
}

# Il parametro che entra nella potenza e il default del suo costruttore: con il
# nome nudo (`'geometric'`) è il default a traboccare, e trabocca anche lui.
_POWER_PARAMS = {
    'geometric': ('ratio', 1.5),
    'exponential': ('rate', 2.0),
    'power': ('exponent', 2.0),
}

# Come uscire da un overflow, per parametro (mirror di `_RIMEDI_OVERFLOW`, PGE
# #216). Non è lo stesso consiglio per tutti: `ratio` e `rate` sono fattori di
# una progressione, e verso 1 la progressione si appiattisce; `exponent` è una
# scala, dove 1 è un valore ordinario e a traboccare è l'ordine di grandezza.
OVERFLOW_REMEDIES = {
    'ratio': "avvicina ratio a 1",
    'rate': "avvicina rate a 1",
    'exponent': "riduci exponent in valore assoluto",
}

# Oltre questo il quoziente di `geometric` con `ratio` intero non sta in un
# float (il massimo è appena sotto 2 ** 1024). La regola lo usa solo per non
# calcolare potenze intere lontane dal bordo; vicino, rifà il conto esatto.
_FLOAT_MAX_EXPONENT = 1024

# La soglia sotto cui il motore tratta `ratio` come 1 e devia su `linear`.
_GEOMETRIC_LINEAR_TOLERANCE = 1e-6


@dataclass(frozen=True)
class TimeDistIssue:
    """Cosa non va nel quinto elemento, senza dire come raccontarlo.

    Attributes:
        kind: `'name'` se il nome non è nel registro, `'params'` se i
            parametri non reggono i bound del costruttore, `'overflow'` se
            reggono ma la potenza che la distribuzione calcola con quei
            parametri e `n_reps` non sta in un float.
        value: lo spec come l'utente l'ha scritto.
        nome: il nome della distribuzione risolto (per `kind='params'` e
            `kind='overflow'`).
        senza_tipo: True se il dict non porta la chiave `type` — allora la
            distribuzione è `linear`, che non prende parametri, e chi scrive
            l'hint di solito vuole dirlo.
        param: per l'overflow, il parametro della coppia (`ratio`, `rate`,
            `exponent`).
        param_value: il suo valore, default del costruttore compreso quando
            lo spec è un nome nudo.
        n_reps: l'altra metà della coppia.
        formula: l'espressione che trabocca, scritta come la scrive il motore.
    """
    kind: str
    value: Any
    nome: Optional[str] = None
    senza_tipo: bool = False
    param: Optional[str] = None
    param_value: Any = None
    n_reps: Optional[int] = None
    formula: Optional[str] = None


def check_time_distribution(spec: Any,
                            n_reps: Any = None) -> Optional[TimeDistIssue]:
    """
    Dice se il motore accetterebbe questo quinto elemento.

    Tre passaggi come nel motore, per tre ragioni diverse: il nome si legge dal
    registro (ed è l'errore più frequente), i parametri si controllano contro i
    bound dei costruttori, e solo con parametri validi si guarda la coppia con
    `n_reps` — prima non c'è una distribuzione da cui calcolare niente.

    Args:
        spec: nome nudo (`'exponential'`), dict con i parametri
            (`{type: geometric, ratio: 1.5}`), o `None` per la distribuzione
            omessa.
        n_reps: il terzo elemento del ciclo. Omesso, la coppia non si guarda:
            è la firma di chi valida lo spec da solo. Un `n_reps` minore di 1
            è un altro errore, di chi valida il ciclo, e qui non si guarda.

    Returns:
        None se il motore lo accetterebbe, altrimenti il `TimeDistIssue`.
    """
    nome = spec.get('type', DEFAULT_DISTRIBUTION) if isinstance(spec, dict) else spec
    if nome is None:
        nome = DEFAULT_DISTRIBUTION
    if not isinstance(nome, str) or nome.lower() not in TIME_DISTRIBUTION_NAMES:
        return TimeDistIssue(kind='name', value=spec)

    if isinstance(spec, dict):
        senza_tipo = 'type' not in spec
        ammessi = DIST_PARAM_SPECS[nome.lower()]
        for chiave, valore in spec.items():
            if chiave == 'type':
                continue
            # Parametro estraneo al tipo: il costruttore lo rifiuterebbe come
            # kwarg inatteso (TypeError), che il factory riveste di ValueError.
            if chiave not in ammessi or not ammessi[chiave](valore):
                return TimeDistIssue(kind='params', value=spec, nome=nome,
                                     senza_tipo=senza_tipo)

    # Nome nudo o dict valido: i default dei costruttori sono validi, ma con
    # abbastanza cicli traboccano anche loro.
    if not isinstance(n_reps, int) or n_reps < 1:
        return None
    return _check_overflow(spec, nome.lower(), n_reps)


def _check_overflow(spec: Any, nome: str,
                    n_reps: int) -> Optional[TimeDistIssue]:
    """La coppia `(parametro, n_reps)`, per le tre distribuzioni che elevano."""
    canonico = _CANONICAL_NAMES.get(nome, nome)
    if canonico not in _POWER_PARAMS:
        return None  # linear e logarithmic non elevano niente a potenza

    param, default = _POWER_PARAMS[canonico]
    valore = spec.get(param, default) if isinstance(spec, dict) else default

    formula = _OVERFLOWS[canonico](valore, n_reps)
    if formula is None:
        return None
    return TimeDistIssue(kind='overflow', value=spec, nome=canonico,
                         param=param, param_value=valore, n_reps=n_reps,
                         formula=formula)


def _geometric_overflow(ratio: Any, n_reps: int) -> Optional[str]:
    """`sum_geometric = (1 - ratio ** n_reps) / (1 - ratio)`, dentro il `try`.

    Con `ratio` float trabocca la potenza (`float ** int` alza). Con `ratio`
    intero la potenza è esatta e trabocca il quoziente, un ciclo più in là:
    `ratio: 10` rende a 309, `ratio: 10.0` no.

    Il quoziente può anche uscire infinito senza alzare niente — la divisione
    fra float non controlla l'overflow — e capita con `ratio` fra 1 e 2, dove
    `1 - ratio` è minore di 1 in valore assoluto e ingrandisce. Il motore poi
    fa durare zero ogni ciclo e divide per la loro somma: `ZeroDivisionError`.
    """
    try:
        if abs(ratio - 1.0) < _GEOMETRIC_LINEAR_TOLERANCE:
            return None  # il motore devia su `linear` prima di elevare
    except OverflowError:
        # Un `ratio` intero che da solo non sta in un float: il motore alza
        # qui, fuori dal suo `try`, un `OverflowError` nudo che con la coppia
        # non c'entra. Non è questa la regola che lo racconta.
        return None

    if isinstance(ratio, int):
        # Il quoziente è 1 + ratio + ... + ratio ** (n_reps - 1): sta fra
        # ratio ** (n_reps - 1) e il suo doppio. Lontano dal bordo il verdetto
        # è certo e la potenza intera non si calcola; vicino, la potenza ha al
        # più un paio di migliaia di bit e il conto esatto costa niente.
        bit = (n_reps - 1) * math.log2(ratio)
        if bit >= _FLOAT_MAX_EXPONENT + 1:
            return 'ratio ** n_reps'
        if bit < _FLOAT_MAX_EXPONENT - 2:
            return None

    try:
        somma = (1 - ratio ** n_reps) / (1 - ratio)
    except OverflowError:
        return 'ratio ** n_reps'
    if math.isinf(somma):
        return '(1 - ratio ** n_reps) / (1 - ratio)'
    return None


def _exponential_overflow(rate: Any, n_reps: int) -> Optional[str]:
    """`weights = [rate ** (-i) for i in range(n_reps)]`, dentro il `try`.

    I pesi crescono solo con `rate < 1`, e allora il più grande è l'ultimo:
    guardare lui equivale a guardarli tutti. Con `rate >= 1` nessun peso
    supera 1 — e un `rate` intero, che fra 0 e 1 non può stare, sotto
    esponente negativo diventa comunque float.
    """
    if not rate < 1:
        return None
    try:
        rate ** -(n_reps - 1)
    except OverflowError:
        return 'rate ** -i'
    return None


def _power_overflow(exponent: Any, n_reps: int) -> Optional[str]:
    """`weights = [(i + 1) ** exponent for i in range(n_reps)]`, dentro il `try`.

    Con `exponent` intero la potenza fra interi è esatta e non trabocca mai (e
    la normalizzazione che segue torna fra 0 e 1): lo stesso `exponent: 150`
    che rende a qualunque `n_reps` in grafia `150.0` si ferma a 114. Con un
    float positivo il peso più grande è l'ultimo, `n_reps ** exponent`.
    """
    if isinstance(exponent, int) or not exponent > 0:
        return None
    try:
        n_reps ** exponent
    except OverflowError:
        return '(i + 1) ** exponent'
    return None


_OVERFLOWS = {
    'geometric': _geometric_overflow,
    'exponential': _exponential_overflow,
    'power': _power_overflow,
}


# =============================================================================
# HINT — la frase che vale per ogni chiave
# =============================================================================

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

# La frase del motore (`TimeDistributionStrategy._overflow`), riscritta per
# l'editor: chi legge la diagnostica e poi l'errore di render deve riconoscere
# lo stesso problema. Nomina entrambi i valori perché nessuno dei due è il
# colpevole: dirne uno solo non direbbe all'utente quale ridurre.
OVERFLOW_HINT = (
    "la distribuzione '{nome}' calcola {formula} con n_reps={n_reps}, e il "
    "risultato non sta in un float. Né {param}={valore} né n_reps={n_reps} è "
    "fuori posto da solo: è la coppia a esplodere. Riduci n_reps, oppure "
    "{rimedio}."
)


def overflow_hint(issue: TimeDistIssue) -> str:
    """La frase per un `TimeDistIssue` di tipo `'overflow'`."""
    return OVERFLOW_HINT.format(
        nome=issue.nome, formula=issue.formula, n_reps=issue.n_reps,
        param=issue.param, valore=issue.param_value,
        rimedio=OVERFLOW_REMEDIES.get(issue.param, f'riduci {issue.param}'),
    )


def describe_issue(issue: TimeDistIssue) -> str:
    """La frase per qualunque `TimeDistIssue`, senza niente di una chiave."""
    if issue.kind == 'name':
        return DIST_NAME_HINT.format(
            disponibili=', '.join(TIME_DISTRIBUTION_NAMES))
    if issue.kind == 'overflow':
        return overflow_hint(issue)
    return DIST_PARAM_HINT.format(
        nome=issue.nome,
        nota=DIST_TIPO_IMPLICITO if issue.senza_tipo else '',
    )
