# granular_ls/envelope_snippets.py
"""
EnvelopeSnippetProvider - Snippet per tutti i formati envelope.

I valori Y nei tab stops vengono derivati dai bounds del parametro.
L'end_time viene calcolato dal contesto dello stream (duration o 1.0 se normalized).
Il punto di mezzo negli snippet a 3 punti e' sempre end_time / 2.
"""

import re
from typing import Dict, List, NamedTuple, Optional, Tuple
from lsprotocol.types import (
    CompletionItem,
    CompletionItemKind,
    InsertTextFormat,
    MarkupContent,
    MarkupKind,
)

from granular_ls.schema_bridge import (
    SchemaBridge, ParameterInfo, unit_scaled_paths,
)


def _fmt(v: float) -> str:
    """Formatta un float rimuovendo zeri inutili ma mantenendo sempre il punto."""
    if v == int(v):
        return f'{int(v)}.0'
    # Arrotonda a 4 cifre significative per evitare 0.009999999
    return str(round(v, 4))


def _build_snippets(
    y_min: float,
    y_max: float,
    end_time: float,
    range_doc: Optional[str] = None,
) -> List[dict]:
    """
    Costruisce la lista di definizioni snippet con valori dinamici.

    y_min, y_max : bounds del parametro corrente
    end_time     : durata dello stream (o 1.0 se normalized)
    range_doc    : cosa dice «Range parametro» nella doc; default
                   `[y_min, y_max]`. Per un parametro senza tetto `y_max` e'
                   un tetto di disegno, e la doc lo deve dire (`_range_doc`).

    Il punto di mezzo negli snippet a 3 punti e' end_time / 2.
    """
    et = _fmt(end_time)
    mid = _fmt(end_time / 2)
    ymin = _fmt(y_min)
    ymax = _fmt(y_max)
    rng = range_doc if range_doc is not None else f'[{ymin}, {ymax}]'

    return [

        # 1. Standard lineare 2 punti
        {
            'label': 'envelope lineare (2 punti)',
            'detail': '[[t, v], [t, v]]',
            'doc': (
                '**Envelope standard lineare - 2 punti**\n\n'
                'Due breakpoints `[tempo, valore]`. Crea una rampa lineare.\n\n'
                f'Range parametro: {rng}'
            ),
            'insert_text': (
                f' [[${"{1:0.0}"}, ${"{2:" + ymin + "}"}],'
                f' [${"{3:" + et + "}"}, ${"{4:" + ymax + "}"}]]'
            ),
        },

        # 2. Standard lineare 3 punti
        {
            'label': 'envelope lineare (3 punti)',
            'detail': '[[t,v],[t,v],[t,v]]',
            'doc': (
                '**Envelope standard lineare - 3 punti**\n\n'
                f'Attacco, picco, rilascio. Punto di mezzo a t={mid}.\n\n'
                f'Range parametro: {rng}'
            ),
            'insert_text': (
                f' [[${"{1:0.0}"}, ${"{2:" + ymin + "}"}],'
                f' [${"{3:" + mid + "}"}, ${"{4:" + ymax + "}"}],'
                f' [${"{5:" + et + "}"}, ${"{6:" + ymin + "}"}]]'
            ),
        },

        # 3. Dict cubic
        {
            'label': 'envelope cubic (dict)',
            'detail': '{type: cubic, points: [...]}',
            'doc': (
                '**Envelope con interpolazione cubica**\n\n'
                'Usa Fritsch-Carlson per tangenti monotone.\n\n'
                f'Range parametro: {rng}'
            ),
            'insert_text': (
                f' {{type: cubic, points: [[${"{1:0.0}"}, ${"{2:" + ymin + "}"}],'
                f' [${"{3:" + mid + "}"}, ${"{4:" + ymax + "}"}],'
                f' [${"{5:" + et + "}"}, ${"{6:" + ymin + "}"}]]}}'
            ),
        },

        # 4. Dict step
        {
            'label': 'envelope step (dict)',
            'detail': '{type: step, points: [...]}',
            'doc': (
                '**Envelope a gradini**\n\n'
                'Il valore salta istantaneamente senza interpolazione.\n\n'
                f'Range parametro: {rng}'
            ),
            'insert_text': (
                f' {{type: step, points: [[${"{1:0.0}"}, ${"{2:" + ymin + "}"}],'
                f' [${"{3:" + mid + "}"}, ${"{4:" + ymax + "}"}],'
                f' [${"{5:" + et + "}"}, ${"{6:" + ymin + "}"}]]}}'
            ),
        },

        # 5. Compact loop base (formato diretto)
        {
            'label': 'envelope loop (compact base)',
            'detail': '[[[0,v],[100,v]], end_time, n_reps]',
            'doc': (
                '**Envelope compact - loop base**\n\n'
                'Pattern ripetuto in percentuale `[x%, valore]`.\n\n'
                f'Range parametro: {rng}\n\n'
                f'- end_time: {et} (duration dello stream)\n'
                '- n_reps: numero di ripetizioni'
            ),
            'insert_text': (
                f' [[[0, ${"{1:" + ymin + "}"}], [100, ${"{2:" + ymax + "}"}]],'
                f' ${"{3:" + et + "}"}, ${"{4:4}"}]'
            ),
        },

        # 6. Compact cubic (formato diretto)
        {
            'label': 'envelope loop cubic (compact)',
            'detail': '[pattern, end_time, n_reps, "cubic"]',
            'doc': (
                '**Envelope compact - loop con interpolazione cubic**\n\n'
                f'Range parametro: {rng}'
            ),
            'insert_text': (
                f' [[[0, ${"{1:" + ymin + "}"}], [50, ${"{2:" + ymax + "}"}],'
                f' [100, ${"{3:" + ymin + "}"}]],'
                f' ${"{4:" + et + "}"}, ${"{5:4}"}, "${{6:cubic}}"]'
            ),
        },

        # 7. Compact exponential (accelerando, formato diretto)
        {
            'label': 'envelope loop accelerando (exponential)',
            'detail': '[pattern, end_time, n_reps, interp, "exponential"]',
            'doc': (
                '**Envelope compact - cicli che accelerano**\n\n'
                f'Range parametro: {rng}'
            ),
            'insert_text': (
                f' [[[0, ${"{1:" + ymin + "}"}], [100, ${"{2:" + ymax + "}"}]],'
                f' ${"{3:" + et + "}"}, ${"{4:6}"}, "${{5:linear}}", "exponential"]'
            ),
        },

        # 8. Compact logarithmic (ritardando, formato diretto)
        {
            'label': 'envelope loop ritardando (logarithmic)',
            'detail': '[pattern, end_time, n_reps, interp, "logarithmic"]',
            'doc': (
                '**Envelope compact - cicli che rallentano**\n\n'
                f'Range parametro: {rng}'
            ),
            'insert_text': (
                f' [[[0, ${"{1:" + ymin + "}"}], [100, ${"{2:" + ymax + "}"}]],'
                f' ${"{3:" + et + "}"}, ${"{4:6}"}, "${{5:linear}}", "logarithmic"]'
            ),
        },

        # 9. Compact geometric (formato diretto)
        {
            'label': 'envelope loop geometric (ratio)',
            'detail': '[pattern, end_time, n_reps, interp, {geometric,ratio}]',
            'doc': (
                '**Envelope compact - distribuzione geometrica**\n\n'
                f'Range parametro: {rng}'
            ),
            'insert_text': (
                f' [[[0, ${"{1:" + ymin + "}"}], [100, ${"{2:" + ymax + "}"}]],'
                f' ${"{3:" + et + "}"}, ${"{4:5}"}, "${{5:linear}}",'
                f' {{type: geometric, ratio: ${"{6:1.5}"}}}]'
            ),
        },

        # 10. Compact power (formato diretto)
        {
            'label': 'envelope loop power law (exponent)',
            'detail': '[pattern, end_time, n_reps, interp, {power,exponent}]',
            'doc': (
                '**Envelope compact - distribuzione power law**\n\n'
                f'Range parametro: {rng}'
            ),
            'insert_text': (
                f' [[[0, ${"{1:" + ymin + "}"}], [100, ${"{2:" + ymax + "}"}]],'
                f' ${"{3:" + et + "}"}, ${"{4:5}"}, "${{5:linear}}",'
                f' {{type: power, exponent: ${"{6:2.0}"}}}]'
            ),
        },

        # 11. Misto (standard + compact)
        {
            'label': 'envelope misto (standard + loop)',
            'detail': '[[t,v],..., [compact]]',
            'doc': (
                '**Envelope misto: breakpoints standard + sezione loop**\n\n'
                f'Range parametro: {rng}\n\n'
                f'I breakpoints standard arrivano a t={mid},\n'
                f'poi il loop fino a t={et}.'
            ),
            'insert_text': (
                f' [[${"{1:0.0}"}, ${"{2:" + ymin + "}"}],'
                f' [${"{3:" + mid + "}"}, ${"{4:" + ymax + "}"}],'
                f' [[[0, ${"{5:" + ymin + "}"}], [100, ${"{6:" + ymax + "}"}]],'
                f' ${"{7:" + et + "}"}, ${"{8:4}"}]]'
            ),
        },

        # 12. Loop → breakpoints standard
        {
            'label': 'envelope loop → breakpoints',
            'detail': '[[compact], [t,v], [t,v]]',
            'doc': (
                '**Envelope loop poi breakpoints standard**\n\n'
                f'Loop fino a t={mid}, poi rampa lineare fino a t={et}.\n\n'
                f'Range parametro: {rng}'
            ),
            'insert_text': (
                f' [[[[0, ${"{1:" + ymin + "}"}], [100, ${"{2:" + ymax + "}"}]],'
                f' ${"{3:" + mid + "}"}, ${"{4:3}"}],'
                f' [${"{3:" + mid + "}"}, ${"{5:" + ymax + "}"}],'
                f' [${"{6:" + et + "}"}, ${"{7:" + ymin + "}"}]]'
            ),
        },

        # 13. Loop multipli in sequenza
        {
            'label': 'envelope loop multipli',
            'detail': '[[compact1], [compact2]]',
            'doc': (
                '**Envelope con due loop in sequenza**\n\n'
                f'Primo loop fino a t={mid}, secondo fino a t={et}.\n\n'
                f'Range parametro: {rng}\n\n'
                'Il secondo loop parte automaticamente dal punto finale del primo\n'
                '(offset automatico calcolato dal motore).'
            ),
            'insert_text': (
                f' [[[[0, ${"{1:" + ymin + "}"}], [100, ${"{2:" + ymax + "}"}]],'
                f' ${"{3:" + mid + "}"}, ${"{4:3}"}],'
                f' [[[0, ${"{5:" + ymax + "}"}], [100, ${"{6:" + ymin + "}"}]],'
                f' ${"{7:" + et + "}"}, ${"{8:3}"}]]'
            ),
        },

        # 14. BP group diretto (PGE #64)
        {
            'label': 'envelope BP group (interp di zona)',
            'detail': '[[[t,v],...], "interp"]',
            'doc': (
                '**BP group: run di breakpoint con interp di macrozona** '
                '(PGE #64)\n\n'
                'L\'interp governa i soli segmenti interni della zona '
                '(n punti → n−1 segmenti); il segmento in uscita '
                'dall\'ultimo punto resta al default globale. Tempi '
                'assoluti (non percentuali).\n\n'
                f'Range parametro: {rng}\n\n'
                'Interp validi: `linear`, `cubic`, `step`.'
            ),
            'insert_text': (
                f' [[[${"{1:0.0}"}, ${"{2:" + ymin + "}"}],'
                f' [${"{3:" + mid + "}"}, ${"{4:" + ymax + "}"}],'
                f' [${"{5:" + et + "}"}, ${"{6:" + ymin + "}"}]],'
                f' "${{7|cubic,linear,step|}}"]'
            ),
        },

        # 15. Misto con due zone BP group (PGE #64)
        {
            'label': 'envelope misto (due zone BP group)',
            'detail': '[[points, "cubic"], [compact], [points, "step"]]',
            'doc': (
                '**Envelope misto: due macrozone BP con interp diverso** '
                '(PGE #64)\n\n'
                'Ogni zona `[points, interp]` interpola per conto suo; '
                'un loop block puo\' stare in mezzo. Collisione al bordo '
                'zona → `DISCONTINUITY_OFFSET`.\n\n'
                f'Range parametro: {rng}'
            ),
            'insert_text': (
                f' [[[[${"{1:0.0}"}, ${"{2:" + ymin + "}"}],'
                f' [${"{3:" + mid + "}"}, ${"{4:" + ymax + "}"}]],'
                f' "${{5|cubic,linear,step|}}"],'
                f' [[[${"{6:" + mid + "}"}, ${"{7:" + ymax + "}"}],'
                f' [${"{8:" + et + "}"}, ${"{9:" + ymin + "}"}]],'
                f' "${{10|step,linear,cubic|}}"]]'
            ),
        },
    ]


def build_read_direction_snippets(end_time: float) -> List[dict]:
    """
    Snippet per `grain.read_direction` (PGE #207): solo `+1` / `-1`, solo step.

    I template generici non vanno: derivano i valori Y dai bounds — che qui
    sono `[-1, 1]`, quindi gli estremi uscirebbero anche giusti — ma **otto
    dei quindici dichiarano un interp** (`cubic`, `linear`, o la scelta fra i
    tre) che su questa chiave è un errore duro. Un completamento che inserisce
    YAML non renderizzabile è peggio di nessun completamento.

    Il gradino non si scrive: è implicito e obbligatorio, e dichiarare
    `type: step` è ridondanza valida ma rumorosa nel diff. Perciò i template
    qui sotto lo omettono, tranne il BP group — dove l'interp è posizionale e
    non c'è modo di ometterlo.
    """
    et = _fmt(end_time)
    mid = _fmt(end_time / 2)

    return [

        # 1. Il cambio di verso, che è il caso d'uso della chiave
        {
            'label': 'verso: cambio a metà (2 punti)',
            'detail': '[[t, +1], [t, -1]]',
            'doc': (
                '**Verso di lettura del grano — un cambio**\n\n'
                'In avanti fino a `t`, poi all\'indietro.\n\n'
                'L\'interpolazione è `step` e non va scritta: il verso ha due '
                'stati, non una rampa fra i due.'
            ),
            'insert_text': (
                f' [[${"{1:0.0}"}, ${"{2|1,-1|}"}],'
                f' [${"{3:" + mid + "}"}, ${"{4|-1,1|}"}]]'
            ),
        },

        # 2. Andata e ritorno
        {
            'label': 'verso: avanti → indietro → avanti (3 punti)',
            'detail': '[[t, +1], [t, -1], [t, +1]]',
            'doc': (
                '**Verso di lettura del grano — due cambi**\n\n'
                f'Il verso si inverte a t={mid} e torna a t={et}.'
            ),
            'insert_text': (
                f' [[${"{1:0.0}"}, ${"{2|1,-1|}"}],'
                f' [${"{3:" + mid + "}"}, ${"{4|-1,1|}"}],'
                f' [${"{5:" + et + "}"}, ${"{6|1,-1|}"}]]'
            ),
        },

        # 3. Forma dict, per chi ci deve mettere altre chiavi (time_unit)
        {
            'label': 'verso: forma dict (points)',
            'detail': '{points: [[t, v], ...]}',
            'doc': (
                '**Verso di lettura — forma dict**\n\n'
                'Utile quando servono le altre chiavi del dict '
                '(es. `time_unit`). Senza `type`: lo `step` è imposto.\n\n'
                '`type: step` esplicito sarebbe accettato; qualunque altro '
                'interp è un errore.'
            ),
            'insert_text': (
                f' {{points: [[${"{1:0.0}"}, ${"{2|1,-1|}"}],'
                f' [${"{3:" + mid + "}"}, ${"{4|-1,1|}"}]]}}'
            ),
        },

        # 4. Alternanza periodica: il ciclo compatto
        {
            'label': 'verso: alternanza periodica (loop compact)',
            'detail': '[[[0, +1], [50, -1]], end_time, n_reps]',
            'doc': (
                '**Verso di lettura — alternanza ripetuta**\n\n'
                'Il pattern è in percentuale del ciclo `[x%, verso]`, con '
                '`x` in `[0, 100]` e non decrescente.\n\n'
                f'- `end_time`: {et} (istante assoluto di fine, non durata)\n'
                '- `n_reps`: numero di cicli (intero >= 1)'
            ),
            'insert_text': (
                f' [[[0, ${"{1|1,-1|}"}], [50, ${"{2|-1,1|}"}]],'
                f' ${"{3:" + et + "}"}, ${"{4:4}"}]'
            ),
        },

        # 5. BP group: l'unica forma in cui lo step va scritto
        {
            'label': 'verso: BP group (step esplicito)',
            'detail': '[[[t, v], ...], "step"]',
            'doc': (
                '**Verso di lettura — BP group** (PGE #64)\n\n'
                'Qui l\'interp è posizionale e non si può omettere: `step` è '
                'l\'unico valore ammesso.\n\n'
                'Il gruppo richiede almeno 2 punti.'
            ),
            'insert_text': (
                f' [[[${"{1:0.0}"}, ${"{2|1,-1|}"}],'
                f' [${"{3:" + mid + "}"}, ${"{4|-1,1|}"}]], "step"]'
            ),
        },
    ]


def build_envelope_n_points(y_min: float, y_max: float, end_time: float, n_points: int) -> str:
    """
    Genera N breakpoints equidistanziati nel tempo da y_min a y_max.
    Formato inline: [[t0, v0], [t1, v1], ..., [tN-1, vN-1]]

    n_points >= 2. Se passato un valore minore viene silenziosamente portato a 2.
    I tempi sono distribuiti uniformemente in [0, end_time].
    I valori seguono una rampa lineare da y_min a y_max.
    """
    if n_points < 2:
        n_points = 2
    points = []
    for i in range(n_points):
        t = end_time * i / (n_points - 1)
        v = y_min + (y_max - y_min) * i / (n_points - 1)
        points.append(f'[{_fmt(t)}, {_fmt(v)}]')
    return '[' + ', '.join(points) + ']'


# Bounds default usati quando il parametro non ha bounds definiti
_DEFAULT_Y_MIN = 0.0
_DEFAULT_Y_MAX = 1.0
_DEFAULT_END_TIME = 10.0


# =============================================================================
# FINESTRA DI DISEGNO (PGE-ls #51)
# =============================================================================

# Sopra questa density il motore scrive una riga sul clip log
# (`DENSITY_NOTICE_THRESHOLD` in `controllers/density_controller.py`, PGE
# #272). E' il vecchio `density.max_val`: tolto il tetto, lo stesso numero
# smette di tagliare e diventa il punto in cui il motore parla. Mirror
# statico, pinnato da tests/test_pge_parity.py.
DENSITY_NOTICE_THRESHOLD = 4000.0

# Tetto di DISEGNO dei parametri senza tetto (`max_val=None`), per yaml_path:
# (tetto, perche' quello). Non e' un bound — nessuna diagnostica lo legge — ma
# un asse Y (snippet, GUI envelope, `pge.buildEnvelope`) un tetto deve pur
# averlo, e sceglierlo e' una decisione per parametro, non un default che
# capita: `_DEFAULT_Y_MAX` avrebbe disegnato density fino a 1 grano al
# secondo. La parita' pretende una voce per ogni parametro senza tetto del
# motore. Per i `loop_*` il `None` del registro non e' un'assenza ma un tetto
# dinamico (la durata del sample): la finestra resta dichiarata qui, e la doc
# degli snippet non la spaccia per un dominio (`DrawBounds.unit_scaled`).
_LOOP_DRAW_REASON = ("l'intero file con `loop_unit: normalized`; in secondi "
                     "il tetto vero e' la durata del sample")
OPEN_CEILING_DRAW_MAX: Dict[str, Tuple[float, str]] = {
    'density': (DENSITY_NOTICE_THRESHOLD,
                'la soglia oltre cui il motore avvisa sul clip log'),
    'pointer.loop_start': (1.0, _LOOP_DRAW_REASON),
    'pointer.loop_end': (1.0, _LOOP_DRAW_REASON),
    'pointer.loop_dur': (1.0, _LOOP_DRAW_REASON),
}


class DrawBounds(NamedTuple):
    """La finestra Y in cui si disegna l'envelope di un parametro.

    `ceiling_note` e' None quando `y_max` e' il tetto vero del parametro (o il
    default di chi non ha bounds); altrimenti il parametro non ha tetto, e la
    nota dice perche' si disegna fin li'.

    `unit_scaled` e' True per le posizioni che `loop_unit` riscala
    (`schema_bridge.unit_scaled_paths`): i bounds del registro valgono dopo la
    riscalatura, quindi la finestra non descrive il numero nello YAML e la doc
    non la puo' dichiarare come suo dominio — lo stesso motivo per cui
    `get_value_domain` non ne da' uno all'hover.
    """
    y_min: float
    y_max: float
    ceiling_note: Optional[str] = None
    unit_scaled: bool = False


def draw_bounds(yaml_path: str, min_val: Optional[float],
                max_val: Optional[float]) -> DrawBounds:
    """Fin dove si disegna l'envelope di un parametro.

    Una sola risposta per snippet e GUI (`server._resolve_envelope_context`):
    prima ciascuno aveva la sua, e con `max_val=None` la GUI ricadeva su
    `(0, 1)` — density disegnata da 0, sotto il pavimento — mentre gli
    snippet tenevano il pavimento e prendevano `_DEFAULT_Y_MAX`.
    """
    bounds = _draw_window(yaml_path, min_val, max_val)
    if yaml_path in unit_scaled_paths():
        return bounds._replace(unit_scaled=True)
    return bounds


def _draw_window(yaml_path: str, min_val: Optional[float],
                 max_val: Optional[float]) -> DrawBounds:
    """La finestra Y, senza chiedersi se il registro descrive il numero."""
    y_min = min_val if min_val is not None else _DEFAULT_Y_MIN
    if max_val is not None:
        return DrawBounds(y_min, max_val)
    if min_val is None:
        return DrawBounds(y_min, _DEFAULT_Y_MAX)
    declared = OPEN_CEILING_DRAW_MAX.get(yaml_path)
    if declared is not None:
        return DrawBounds(y_min, *declared)
    # Senza tetto e senza una voce: la parita' lo segnala. Intanto il tetto di
    # comodo non scende sotto il pavimento e non fa uno snippet piatto.
    ceiling = (_DEFAULT_Y_MAX if y_min < _DEFAULT_Y_MAX
               else y_min + _DEFAULT_Y_MAX)
    return DrawBounds(y_min, ceiling,
                      'un tetto di comodo: nessuno ne ha dichiarato uno')


def _range_doc(bounds: DrawBounds) -> Optional[str]:
    """«Range parametro» per uno snippet: None se `y_max` e' un tetto vero.

    Per le posizioni che `loop_unit` riscala non si dichiara un dominio: il
    `≥ 0.005` di `loop_dur` e' in secondi, e un tetto c'e' (la durata del
    sample) anche se il registro non lo porta. Si dice solo cosa si disegna.
    """
    if bounds.unit_scaled:
        drawn = (f'dipende da `loop_unit` — lo snippet disegna '
                 f'[{_fmt(bounds.y_min)}, {_fmt(bounds.y_max)}]')
        if bounds.ceiling_note is None:
            return drawn
        return f'{drawn}, {bounds.ceiling_note}'
    if bounds.ceiling_note is None:
        return None
    return (f'≥ {_fmt(bounds.y_min)} — nessun tetto; lo snippet arriva a '
            f'{_fmt(bounds.y_max)}, {bounds.ceiling_note}')


class EnvelopeSnippetProvider:

    def __init__(self, bridge: SchemaBridge):
        self._bridge = bridge
        self._items_cache: Optional[List[CompletionItem]] = None

    def get_snippets(self) -> List[CompletionItem]:
        """Snippet generici con bounds default e end_time=10.0."""
        if self._items_cache is None:
            specs = _build_snippets(_DEFAULT_Y_MIN, _DEFAULT_Y_MAX, _DEFAULT_END_TIME)
            self._items_cache = [self._build_item(s) for s in specs]
        return self._items_cache

    def get_snippets_with_bounds_and_end_time(
        self, y_min: float, y_max: float, end_time: float,
        ceiling_note: Optional[str] = None,
    ) -> List[CompletionItem]:
        """Snippet con bounds espliciti e end_time dinamico.

        `ceiling_note` (da `draw_bounds`) dice che `y_max` e' un tetto di
        disegno e non del parametro.
        """
        rng = _range_doc(DrawBounds(y_min, y_max, ceiling_note))
        specs = _build_snippets(y_min, y_max, end_time, rng)
        return [self._build_item(s) for s in specs]

    def get_snippets_with_end_time(self, end_time: float) -> List[CompletionItem]:
        """Snippet con end_time dinamico e bounds default."""
        specs = _build_snippets(_DEFAULT_Y_MIN, _DEFAULT_Y_MAX, end_time)
        return [self._build_item(s) for s in specs]

    def get_read_direction_snippets(
        self, end_time: float
    ) -> List[CompletionItem]:
        """Snippet di `grain.read_direction`: solo `+1`/`-1`, solo step."""
        return [self._build_item(s)
                for s in build_read_direction_snippets(end_time)]

    def get_snippets_for_parameter(self, yaml_path: str) -> List[CompletionItem]:
        """
        Snippet per un parametro specifico, bounds default, end_time=10.0.
        Ritorna lista vuota se il parametro e' sconosciuto, interno o non-smart.
        """
        param = self._find_param(yaml_path)
        if param is None:
            return []
        bounds = self._get_bounds(param)
        specs = _build_snippets(bounds.y_min, bounds.y_max, _DEFAULT_END_TIME,
                                _range_doc(bounds))
        return [self._build_item(s) for s in specs]

    def get_snippets_for_parameter_with_context(
        self, yaml_path: str, end_time: float
    ) -> List[CompletionItem]:
        """
        Snippet con bounds del parametro specifico E end_time dinamico.
        Questo e' il metodo principale usato dal CompletionProvider.
        """
        param = self._find_param(yaml_path)
        if param is None:
            return []
        bounds = self._get_bounds(param)
        specs = _build_snippets(bounds.y_min, bounds.y_max, end_time,
                                _range_doc(bounds))
        return [self._build_item(s) for s in specs]

    # -------------------------------------------------------------------------
    # HELPERS
    # -------------------------------------------------------------------------

    def _find_param(self, yaml_path: str) -> Optional[ParameterInfo]:
        if not yaml_path:
            return None
        for p in self._bridge.get_all_parameters():
            if p.yaml_path == yaml_path or p.name == yaml_path:
                if p.is_internal or not p.is_smart:
                    return None
                return p
        return None

    def _get_bounds(self, param: ParameterInfo) -> DrawBounds:
        return draw_bounds(param.yaml_path, param.min_val, param.max_val)

    @staticmethod
    def _build_item(spec: dict) -> CompletionItem:
        return CompletionItem(
            label=spec['label'],
            insert_text=spec['insert_text'],
            insert_text_format=InsertTextFormat.Snippet,
            kind=CompletionItemKind.Value,
            detail=spec['detail'],
            documentation=MarkupContent(
                kind=MarkupKind.Markdown,
                value=spec['doc'],
            ),
        )
