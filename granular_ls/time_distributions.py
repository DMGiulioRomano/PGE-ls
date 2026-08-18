# granular_ls/time_distributions.py
"""
Registro delle distribuzioni temporali dei cicli, e i bound dei loro
costruttori.

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

**Le regole stanno qui, la formulazione degli hint no.** Un errore sul quinto
elemento si spiega diversamente a chi sta scrivendo il verso di lettura e a
chi sta scrivendo una probabilità: il chiamante riceve un `TimeDistIssue` con
il fatto, e ci scrive sopra la propria frase.

Questo è il punto che può divergere dal motore senza che il resto se ne
accorga — `tests/test_pge_parity.py` interroga i due lati sullo stesso corpus.
"""

from dataclasses import dataclass
from typing import Any, Optional

# Nomi validi, alias compresi (mirror di TimeDistributionFactory._DISTRIBUTIONS).
TIME_DISTRIBUTION_NAMES = (
    'exp', 'exponential', 'geo', 'geometric', 'linear', 'log',
    'logarithmic', 'power',
)

# Il nome che vale quando la distribuzione non è dichiarata.
DEFAULT_DISTRIBUTION = 'linear'


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


@dataclass(frozen=True)
class TimeDistIssue:
    """Cosa non va nel quinto elemento, senza dire come raccontarlo.

    Attributes:
        kind: `'name'` se il nome non è nel registro, `'params'` se i
            parametri non reggono i bound del costruttore.
        value: lo spec come l'utente l'ha scritto.
        nome: il nome della distribuzione risolto (per `kind='params'`).
        senza_tipo: True se il dict non porta la chiave `type` — allora la
            distribuzione è `linear`, che non prende parametri, e chi scrive
            l'hint di solito vuole dirlo.
    """
    kind: str
    value: Any
    nome: Optional[str] = None
    senza_tipo: bool = False


def check_time_distribution(spec: Any) -> Optional[TimeDistIssue]:
    """
    Dice se il motore accetterebbe questo quinto elemento.

    Due passaggi come nel motore, per due ragioni diverse: il nome si legge dal
    registro (ed è l'errore più frequente), i parametri si controllano contro i
    bound dei costruttori.

    Args:
        spec: nome nudo (`'exponential'`), dict con i parametri
            (`{type: geometric, ratio: 1.5}`), o `None` per la distribuzione
            omessa.

    Returns:
        None se il motore lo accetterebbe, altrimenti il `TimeDistIssue`.
    """
    nome = spec.get('type', DEFAULT_DISTRIBUTION) if isinstance(spec, dict) else spec
    if nome is None:
        nome = DEFAULT_DISTRIBUTION
    if not isinstance(nome, str) or nome.lower() not in TIME_DISTRIBUTION_NAMES:
        return TimeDistIssue(kind='name', value=spec)

    if not isinstance(spec, dict):
        # Nome nudo: nessun parametro da controllare, i default sono validi.
        return None

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
    return None
