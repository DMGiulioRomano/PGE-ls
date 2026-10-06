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

- `end_time` e l'offset: la soglia non dipende dalla durata del ciclo;
- un `ratio` intero che da solo non sta in un float (più di 308 cifre): lì
  il motore non arriva alla coppia, e quel che alza — un `OverflowError`
  nudo, fuori dal suo `try` — non è questa regola. Un `rate` della stessa
  taglia invece ci arriva: trabocca dentro il `try`, sul secondo peso.

E cosa invece chiama errore anche se il motore non lo dice con un
`ParameterBoundError`: con `ratio` float fra 1 e 2 c'è un tratto dove
`ratio ** n_reps` sta in un float ma la somma geometrica, divisa per
`1 - ratio`, no. Lì il motore alza un `ZeroDivisionError` nudo (la somma
infinita fa durare zero ogni ciclo, e poi divide per la loro somma): il render
fallisce comunque, e la causa è la stessa coppia.

La somma dei pesi (PGE #219)
----------------------------

Quattro distribuzioni su cinque normalizzano dividendo ogni peso per la somma
di tutti, e il motore ora rifiuta una somma float che non è un numero finito.
Sono due casi, e prima nessuno dei due era un errore:

- la somma che **esce dai float** mentre i pesi ci stanno ancora: il tratto
  prima della soglia di #212, in `exponential` e `power`. Il motore divideva
  per infinito e faceva durare zero ogni ciclo — un collasso muto, che questo
  modulo dichiarava non-errore. Verso `rate` 1 il tratto è largo: la somma
  vale circa `max / (1 - rate)`, e a `rate: 0.99` sono 458 cicli;
- la somma **`nan`**, da un parametro `.nan` o `.inf` che nessun bound ferma:
  i confronti con `nan` sono tutti falsi, e `inf` li passa per definizione.
  Per questo i bound di `DIST_PARAM_SPECS` sono i confronti del motore scritti
  al contrario (`not v <= 0`, non `v > 0`): su `nan` le due grafie non
  coincidono, e la seconda lo rifiutava come parametro sbagliato a ogni
  `n_reps`, dove il motore lo prende sulla somma — e a un ciclo solo, in
  `exponential` e `power`, lo rende.

Il criterio è la somma, non il parametro: `base: .inf` dà pesi tutti a 1 e
`rate: .inf` un ciclo che prende tutto il tempo, somme finite che rendono.

La somma non si rifà a ogni tasto: con `rate` vicino a 1 i pesi sono milioni.
I logaritmi la stringono — in forma chiusa per `exponential`, fra due
integrali per `power` — con un margine che copre l'errore di arrotondamento
della somma float, e il conto esatto, con la stessa `sum` del motore, si fa
solo dove il margine non basta a decidere. Oltre `_EXACT_SUM_MAX_TERMS` pesi
nemmeno lì: decide la stima centrale — per `power` il punto medio fra i due
integrali, che è la regola dei trapezi — e lì sbaglia, al più, su un `n_reps`
a meno di un millesimo di ciclo dal bordo vero. Un caso da milioni di cicli,
e l'unico in cui questo modulo non rifà il conto del motore.
"""

import math
import sys
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
# Sono i confronti del motore scritti al contrario — `rate <= 0` alza, quindi
# passa `not v <= 0` — e non `v > 0`, che dice la stessa cosa su ogni numero
# tranne `nan`. Lì il motore lascia passare, e prende il valore sulla somma dei
# pesi (PGE #219), che è un altro errore e dipende da `n_reps`.
#
# `power.exponent` chiede solo che sia un numero, e il `bool` glielo passa:
# `true ** n` fa 1, non alza niente, e rifiutarlo qui romperebbe YAML che oggi
# rendono. Le sorelle invece hanno bound veri, su cui i bool cadono da soli.
DIST_PARAM_SPECS = {
    'linear': {},
    'exponential': {'rate': lambda v: _is_num(v) and not v <= 0},
    'exp': {'rate': lambda v: _is_num(v) and not v <= 0},
    'logarithmic': {'base': lambda v: _is_num(v) and not v <= 1},
    'log': {'base': lambda v: _is_num(v) and not v <= 1},
    'geometric': {'ratio': lambda v: _is_num(v) and not v <= 0},
    'geo': {'ratio': lambda v: _is_num(v) and not v <= 0},
    'power': {'exponent': lambda v: isinstance(v, (int, float))},
}

# Il parametro da cui dipendono i pesi e il default del suo costruttore: con il
# nome nudo (`'geometric'`) è il default a traboccare, e trabocca anche lui.
# `linear` non c'è perché non ha pesi: divide `total_time` per `n_reps`.
_WEIGHT_PARAMS = {
    'geometric': ('ratio', 1.5),
    'exponential': ('rate', 2.0),
    'logarithmic': ('base', 2.0),
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

# Il logaritmo naturale del float più grande: la somma dei pesi trabocca dove
# il suo logaritmo lo supera.
_LN_FLOAT_MAX = math.log(sys.float_info.max)

# Oltre questi pesi la somma esatta costa più di un tasto premuto (un paio di
# decimi di secondo), e sul bordo decide la stima.
_EXACT_SUM_MAX_TERMS = 2_000_000


@dataclass(frozen=True)
class TimeDistIssue:
    """Cosa non va nel quinto elemento, senza dire come raccontarlo.

    Attributes:
        kind: `'name'` se il nome non è nel registro, `'params'` se i
            parametri non reggono i bound del costruttore, `'overflow'` se
            reggono ma ciò che la distribuzione calcola con quei parametri e
            `n_reps` — una potenza, o la somma dei pesi — non è un numero
            finito. Nel motore è lo stesso errore in tutti e due i casi.
        value: lo spec come l'utente l'ha scritto.
        nome: il nome della distribuzione risolto (per `kind='params'` e
            `kind='overflow'`).
        senza_tipo: True se il dict non porta la chiave `type` — allora la
            distribuzione è `linear`, che non prende parametri, e chi scrive
            l'hint di solito vuole dirlo.
        param: per l'overflow, il parametro della coppia (`ratio`, `rate`,
            `exponent`, e `base` per la somma `nan`).
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
    """La coppia `(parametro, n_reps)`, per le quattro distribuzioni a pesi."""
    canonico = _CANONICAL_NAMES.get(nome, nome)
    if canonico not in _WEIGHT_PARAMS:
        return None  # linear non ha pesi da elevare né da sommare

    param, default = _WEIGHT_PARAMS[canonico]
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

    Su `ratio: .nan` e `.inf` il quoziente è `nan` a ogni `n_reps`, e con lui
    le durate: le prende la somma che le normalizza (PGE #219). Con un `ratio`
    finito quella somma vale `total_time` e non trabocca.
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
    if math.isnan(somma):
        return 'sum(first_duration * ratio ** i)'
    return None


def _exponential_overflow(rate: Any, n_reps: int) -> Optional[str]:
    """`weights = [rate ** (-i) for i in range(n_reps)]`, dentro il `try`, e
    poi la loro somma.

    Per i pesi basta l'ultimo. Con `rate < 1` i pesi crescono, e il più grande
    è lui; con `rate >= 1` nessuno supera 1, e l'ultimo non trabocca più degli
    altri. Resta un caso, e anche lì decide l'ultimo: sotto esponente negativo
    un `rate` intero passa dai float, e con più di 308 cifre non ci entra —
    dal secondo peso in poi, perché `rate ** 0` resta intero.

    La somma (PGE #219) è una serie geometrica di ragione `1 / rate`, quindi
    ha forma chiusa: `(q ** n - 1) / (q - 1)`. Trabocca solo con `rate < 1`, e
    prima del peso: la somma vale circa `max / (1 - rate)`. Con `rate: .nan`
    ogni peso dopo il primo è `nan`; con `.inf` vale zero, e la somma 1.
    """
    try:
        rate ** -(n_reps - 1)
    except OverflowError:
        return 'rate ** -i'

    if n_reps < 2:
        return None  # un peso solo, `rate ** 0`, che vale 1 anche su `nan`
    if _is_nan(rate):
        return 'sum(rate ** -i)'
    if not rate < 1:
        return None  # pesi fino a 1, `.inf` compreso: somma fino a n_reps

    ln_q = -math.log(rate)
    ln_somma = (n_reps * ln_q + math.log(-math.expm1(-n_reps * ln_q))
                - math.log(math.expm1(ln_q)))
    if _sum_overflows(ln_somma, ln_somma, n_reps,
                      lambda: sum(rate ** -i for i in range(n_reps))):
        return 'sum(rate ** -i)'
    return None


def _logarithmic_overflow(base: Any, n_reps: int) -> Optional[str]:
    """`weights = [log(i + 1, base) + 1 for i in range(n_reps)]`, e la somma.

    Niente potenze: i pesi valgono circa 1 ciascuno, e la somma uscirebbe dai
    float a un `n_reps` dell'ordine di `1e307`. Il motore ha la guardia anche
    qui (PGE #219), perché è della normalizzazione e non della formula, e la
    sola via per farla scattare è `base: .nan`: `log(1, nan)` è già `nan`,
    quindi a ogni `n_reps`. `base: .inf` dà pesi tutti a 1, e rende.
    """
    if _is_nan(base):
        return 'sum(log(i + 1, base) + 1)'
    return None


def _power_overflow(exponent: Any, n_reps: int) -> Optional[str]:
    """`weights = [(i + 1) ** exponent for i in range(n_reps)]`, dentro il
    `try`, e poi la loro somma.

    Con `exponent` intero la potenza fra interi è esatta e non trabocca mai (e
    la normalizzazione che segue torna fra 0 e 1): lo stesso `exponent: 150`
    che rende a qualunque `n_reps` in grafia `150.0` si ferma a 114. Nemmeno la
    somma: è un `int` di centinaia di cifre, e il motore guarda solo le somme
    float. Con un float positivo il peso più grande è l'ultimo,
    `n_reps ** exponent`.

    La somma (PGE #219) trabocca prima del peso, e non ha forma chiusa: la
    stringono i due integrali di `x ** exponent`, da 0 a `n_reps` e da 1 a
    `n_reps + 1`, che distano circa un ciclo. Su `.nan` e `.inf` il primo
    peso, `1 ** exponent`, vale 1, e ogni altro è `nan` o infinito: senza
    `OverflowError`, che Python alza solo su argomenti finiti.
    """
    if isinstance(exponent, int) or n_reps < 2:
        return None  # fra interi nessuna soglia; a un ciclo il peso è 1
    if _is_nan(exponent) or exponent == math.inf:
        return 'sum((i + 1) ** exponent)'
    if not exponent > 0:
        return None  # pesi fino a 1, `-.inf` compreso
    try:
        n_reps ** exponent
    except OverflowError:
        return '(i + 1) ** exponent'

    ln_e1 = math.log(exponent + 1)
    basso = (exponent + 1) * math.log(n_reps) - ln_e1
    alto = (exponent + 1) * math.log(n_reps + 1) - ln_e1
    if _sum_overflows(basso, alto, n_reps,
                      lambda: sum((i + 1) ** exponent for i in range(n_reps))):
        return 'sum((i + 1) ** exponent)'
    return None


def _is_nan(value: Any) -> bool:
    """`math.isnan` senza alzare su un intero che non sta in un float."""
    return isinstance(value, float) and math.isnan(value)


def _sum_overflows(ln_basso: float, ln_alto: float, n_reps: int,
                   somma_esatta) -> bool:
    """Se la somma float dei pesi esce dai float.

    `ln_basso` e `ln_alto` sono i logaritmi di due numeri che stringono la
    somma vera. La somma float se ne discosta per l'arrotondamento, al più di
    circa `n_reps` volte l'epsilon dei float: è il margine, e dentro il
    margine il verdetto lo dà `somma_esatta`, che rifà la `sum` del motore.
    """
    margine = 1e-12 + 4e-16 * n_reps
    if ln_basso > _LN_FLOAT_MAX + margine:
        return True
    if ln_alto < _LN_FLOAT_MAX - margine:
        return False
    if n_reps <= _EXACT_SUM_MAX_TERMS:
        return not math.isfinite(somma_esatta())
    return (ln_basso + ln_alto) / 2 > _LN_FLOAT_MAX


_OVERFLOWS = {
    'geometric': _geometric_overflow,
    'exponential': _exponential_overflow,
    'logarithmic': _logarithmic_overflow,
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
# lo stesso problema. Dice che il risultato non è un numero finito, non che
# «non sta in un float»: da PGE #219 è vero anche di una somma `nan`, che in
# un float ci sta.
OVERFLOW_HINT = (
    "la distribuzione '{nome}' calcola {formula} con n_reps={n_reps}, e il "
    "risultato non è un numero finito. {diagnosi}"
)

# Dove il valore è finito la colpa è della coppia, e la frase nomina entrambi:
# nessuno dei due è fuori posto da solo, quindi dirne uno non direbbe quale
# ridurre.
OVERFLOW_PAIR_HINT = (
    "Né {param}={valore} né n_reps={n_reps} è fuori posto da solo: è la "
    "coppia a esplodere. Riduci n_reps, oppure {rimedio}."
)

# Dove non lo è, la coppia non c'entra: `exponent: .nan` è fuori posto da solo
# a qualunque `n_reps`, e invitare a ridurre i cicli manderebbe a cercare una
# soglia che non esiste.
NON_FINITE_HINT = (
    "{param}={valore} non è un numero finito, quindi non lo è nemmeno ciò che "
    "se ne calcola: qui n_reps={n_reps} non c'entra, e ridurlo non aiuta. "
    "Scrivi {param} come un numero (YAML legge `.nan` e `.inf` come valori, "
    "non come errori di battitura)."
)


def _is_finite(value: Any) -> bool:
    """Se la diagnosi della coppia vale per `value` (mirror di `_is_finite`
    del motore). Un intero è finito per quanto grande — `math.isfinite`
    alzerebbe — e il `bool` conta come numero: qui conta la grandezza."""
    if isinstance(value, int):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    return False


def overflow_hint(issue: TimeDistIssue) -> str:
    """La frase per un `TimeDistIssue` di tipo `'overflow'`."""
    if _is_finite(issue.param_value):
        diagnosi = OVERFLOW_PAIR_HINT.format(
            param=issue.param, valore=issue.param_value, n_reps=issue.n_reps,
            rimedio=OVERFLOW_REMEDIES.get(issue.param,
                                          f'riduci {issue.param}'),
        )
    else:
        diagnosi = NON_FINITE_HINT.format(
            param=issue.param, valore=issue.param_value, n_reps=issue.n_reps)
    return OVERFLOW_HINT.format(
        nome=issue.nome, formula=issue.formula, n_reps=issue.n_reps,
        diagnosi=diagnosi,
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
