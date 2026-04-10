# granular_ls/envelope_snippets.py
"""
EnvelopeSnippetProvider - Snippet per tutti i formati envelope.

I valori Y nei tab stops vengono derivati dai bounds del parametro.
L'end_time viene calcolato dal contesto dello stream (duration o 1.0 se normalized).
Il punto di mezzo negli snippet a 3 punti e' sempre end_time / 2.
"""

import re
from typing import List, Optional, Tuple
from lsprotocol.types import (
    CompletionItem,
    CompletionItemKind,
    InsertTextFormat,
    MarkupContent,
    MarkupKind,
)

from granular_ls.schema_bridge import SchemaBridge, ParameterInfo


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
) -> List[dict]:
    """
    Costruisce la lista di definizioni snippet con valori dinamici.

    y_min, y_max : bounds del parametro corrente
    end_time     : durata dello stream (o 1.0 se normalized)

    Il punto di mezzo negli snippet a 3 punti e' end_time / 2.
    """
    et = _fmt(end_time)
    mid = _fmt(end_time / 2)
    ymin = _fmt(y_min)
    ymax = _fmt(y_max)

    return [

        # 1. Standard lineare 2 punti
        {
            'label': 'envelope lineare (2 punti)',
            'detail': '[[t, v], [t, v]]',
            'doc': (
                '**Envelope standard lineare - 2 punti**\n\n'
                'Due breakpoints `[tempo, valore]`. Crea una rampa lineare.\n\n'
                f'Range parametro: [{ymin}, {ymax}]'
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
                f'Range parametro: [{ymin}, {ymax}]'
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
                f'Range parametro: [{ymin}, {ymax}]'
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
                f'Range parametro: [{ymin}, {ymax}]'
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
                f'Range parametro: [{ymin}, {ymax}]\n\n'
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
                f'Range parametro: [{ymin}, {ymax}]'
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
                f'Range parametro: [{ymin}, {ymax}]'
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
                f'Range parametro: [{ymin}, {ymax}]'
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
                f'Range parametro: [{ymin}, {ymax}]'
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
                f'Range parametro: [{ymin}, {ymax}]'
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
                f'Range parametro: [{ymin}, {ymax}]\n\n'
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
                f'Range parametro: [{ymin}, {ymax}]'
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
                f'Range parametro: [{ymin}, {ymax}]\n\n'
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
        self, y_min: float, y_max: float, end_time: float
    ) -> List[CompletionItem]:
        """Snippet con bounds espliciti e end_time dinamico."""
        specs = _build_snippets(y_min, y_max, end_time)
        return [self._build_item(s) for s in specs]

    def get_snippets_with_end_time(self, end_time: float) -> List[CompletionItem]:
        """Snippet con end_time dinamico e bounds default."""
        specs = _build_snippets(_DEFAULT_Y_MIN, _DEFAULT_Y_MAX, end_time)
        return [self._build_item(s) for s in specs]

    def get_snippets_for_parameter(self, yaml_path: str) -> List[CompletionItem]:
        """
        Snippet per un parametro specifico, bounds default, end_time=10.0.
        Ritorna lista vuota se il parametro e' sconosciuto, interno o non-smart.
        """
        param = self._find_param(yaml_path)
        if param is None:
            return []
        y_min, y_max = self._get_bounds(param)
        specs = _build_snippets(y_min, y_max, _DEFAULT_END_TIME)
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
        y_min, y_max = self._get_bounds(param)
        specs = _build_snippets(y_min, y_max, end_time)
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

    def _get_bounds(self, param: ParameterInfo) -> Tuple[float, float]:
        y_min = param.min_val if param.min_val is not None else _DEFAULT_Y_MIN
        y_max = param.max_val if param.max_val is not None else _DEFAULT_Y_MAX
        return y_min, y_max

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
