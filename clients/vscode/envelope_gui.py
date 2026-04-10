#!/usr/bin/env python3
"""
Envelope GUI Editor per PGE-ls.
Viene lanciato come subprocess da VSCode (extension.js).
Stampa su stdout il testo YAML da inserire, poi termina.

Uso:
    python envelope_gui.py --ymin=0.01 --ymax=4000.0 --end_time=10.0

Dipendenze: matplotlib (pip install matplotlib)
"""

import argparse
import collections
import copy
import sys
from typing import List, Tuple

Point = Tuple[float, float]   # (t, v)  —  t in [0, end_time], v in [y_min, y_max]


# =============================================================================
# FUNZIONI PURE (testabili senza GUI)
# =============================================================================

def _fmt(v: float) -> str:
    """Formatta un float senza zeri inutili, mantenendo sempre il punto."""
    if v == int(v):
        return f'{int(v)}.0'
    return str(round(v, 4))


def sort_points(points: List[Point]) -> List[Point]:
    """Ritorna i punti ordinati per t crescente (non modifica l'originale)."""
    return sorted(points, key=lambda p: p[0])


def to_breakpoints(points: List[Point], end_time: float) -> str:
    """
    Converte in formato breakpoints standard: [[t0, v0], [t1, v1], ...]
    Tempi assoluti, valori assoluti.
    """
    pts = sort_points(points)
    inner = ', '.join(f'[{_fmt(t)}, {_fmt(v)}]' for t, v in pts)
    return f'[{inner}]'


def to_compact_loop(points: List[Point], end_time: float, n_reps: int) -> str:
    """
    Converte in formato compact loop diretto: [[[pct, v], ...], end_time, n_reps]
    Le coordinate X vengono mappate in percentuale [0, 100].
    """
    pts = sort_points(points)
    et = end_time or 1.0
    pattern = []
    for t, v in pts:
        pct = int(round(t / et * 100))
        pattern.append(f'[{pct}, {_fmt(v)}]')
    pattern_str = '[' + ', '.join(pattern) + ']'
    return f'[{pattern_str}, {_fmt(end_time)}, {n_reps}]'


def to_dict_type(points: List[Point], end_time: float, interp_type: str) -> str:
    """
    Converte in formato dict con tipo esplicito: {type: X, points: [[t, v], ...]}
    Tempi assoluti.
    """
    pts = sort_points(points)
    inner = ', '.join(f'[{_fmt(t)}, {_fmt(v)}]' for t, v in pts)
    return f'{{type: {interp_type}, points: [{inner}]}}'


def format_output(
    points: List[Point],
    end_time: float,
    fmt: str,
    n_reps: int = 4,
) -> str:
    """Dispatcher legacy (backward-compat con i test esistenti)."""
    if fmt == 'compact':
        return to_compact_loop(points, end_time, n_reps)
    elif fmt == 'cubic':
        return to_dict_type(points, end_time, 'cubic')
    elif fmt == 'step':
        return to_dict_type(points, end_time, 'step')
    else:
        return to_breakpoints(points, end_time)


def to_misto_format(segments: list) -> str:
    """
    Serializza una lista di segmenti nel formato misto PGE.
    Ogni segmento è un dict con 'type' ('breakpoints'|'loop') e i relativi campi.
    Breakpoints BP → [t, v] individuali nell'array esterno.
    Loop → sezione compatta [[[%,v],...], abs_end, n, ...].
    """
    elements = []
    for seg in segments:
        if seg['type'] == 'breakpoints':
            for t, v in sort_points(seg['points']):
                elements.append(f'[{_fmt(t)}, {_fmt(v)}]')
        else:
            abs_start = seg.get('abs_start', 0.0)
            duration  = seg.get('duration', seg.get('end_time', 1.0))
            abs_end   = abs_start + duration
            n_reps    = seg.get('n_reps', 4)
            ld        = seg.get('loop_dist', 'base')
            ratio     = seg.get('ratio', 1.5)
            exponent  = seg.get('exponent', 2.0)

            pts = sort_points(seg['points'])
            pattern = []
            for t, v in pts:
                pct = int(round(t))   # punti già in % [0, 100]
                pattern.append(f'[{pct}, {_fmt(v)}]')
            pattern_str = '[' + ', '.join(pattern) + ']'
            base = f'[{pattern_str}, {_fmt(abs_end)}, {n_reps}'

            if ld == 'cubic':
                elements.append(base + ', "cubic"]')
            elif ld == 'accelerando':
                elements.append(base + ', "linear", "exponential"]')
            elif ld == 'ritardando':
                elements.append(base + ', "linear", "logarithmic"]')
            elif ld == 'geometrico':
                elements.append(base + f', "linear", {{type: geometric, ratio: {_fmt(ratio)}}}]')
            elif ld == 'power':
                elements.append(base + f', "linear", {{type: power, exponent: {_fmt(exponent)}}}]')
            else:
                elements.append(base + ']')

    return '[' + ', '.join(elements) + ']'


def to_compact_loop_full(
    points: List[Point],
    end_time: float,
    n_reps: int,
    loop_dist: str = 'base',
    ratio: float = 1.5,
    exponent: float = 2.0,
) -> str:
    """
    Compact loop con tutte le varianti di distribuzione temporale.

    loop_dist:
      'base'        → [pattern, et, n]
      'cubic'       → [pattern, et, n, "cubic"]
      'accelerando' → [pattern, et, n, "linear", "exponential"]
      'ritardando'  → [pattern, et, n, "linear", "logarithmic"]
      'geometrico'  → [pattern, et, n, "linear", {type: geometric, ratio: r}]
      'power'       → [pattern, et, n, "linear", {type: power, exponent: e}]
    """
    pts = sort_points(points)
    et = end_time or 1.0
    pattern = []
    for t, v in pts:
        pct = int(round(t / et * 100))
        pattern.append(f'[{pct}, {_fmt(v)}]')
    pattern_str = '[' + ', '.join(pattern) + ']'
    base = f'[{pattern_str}, {_fmt(end_time)}, {n_reps}'

    if loop_dist == 'cubic':
        return base + ', "cubic"]'
    elif loop_dist == 'accelerando':
        return base + ', "linear", "exponential"]'
    elif loop_dist == 'ritardando':
        return base + ', "linear", "logarithmic"]'
    elif loop_dist == 'geometrico':
        return base + f', "linear", {{type: geometric, ratio: {_fmt(ratio)}}}]'
    elif loop_dist == 'power':
        return base + f', "linear", {{type: power, exponent: {_fmt(exponent)}}}]'
    else:  # 'base'
        return base + ']'


# =============================================================================
# GUI (matplotlib)
# =============================================================================

_HIT_RADIUS_PX  = 12   # pixel radius for hit detection
_CURVE_DENSITY  = 300  # punti densi per la curva cubic
_REP_PTS        = 120  # punti densi per rep nella preview loop


def _compute_rep_times(end_time, n_reps, loop_dist, ratio=1.5, exponent=2.0):
    """
    Calcola (t_start, t_end) per ciascuna delle n_reps ripetizioni del loop.

    Distribuzioni supportate:
      'base' / 'cubic' → durate uniformi
      'accelerando'    → durate decrescenti (esponenziale r=0.6)
      'ritardando'     → durate crescenti   (esponenziale r=1/0.6)
      'geometrico'     → durate con ratio geometrico (ratio fornito)
      'power'          → durate proporzionali a (k+1)^exponent
    """
    if n_reps <= 0:
        return []

    if loop_dist in ('base', 'cubic'):
        dur = end_time / n_reps
        return [(k * dur, (k + 1) * dur) for k in range(n_reps)]

    if loop_dist == 'accelerando':
        weights = [0.6 ** k for k in range(n_reps)]
    elif loop_dist == 'ritardando':
        weights = [(1.0 / 0.6) ** k for k in range(n_reps)]
    elif loop_dist == 'geometrico':
        r = ratio if ratio > 0 else 1.0
        weights = [r ** k for k in range(n_reps)]
    elif loop_dist == 'power':
        weights = [(k + 1) ** exponent for k in range(n_reps)]
    else:
        weights = [1.0] * n_reps

    total = sum(weights) or n_reps
    t, times = 0.0, []
    for w in weights:
        dur = w / total * end_time
        times.append((t, t + dur))
        t += dur
    return times


def _pchip(xs, ys, x_new):
    """
    Monotone piecewise cubic Hermite interpolation (PCHIP / Fritsch-Carlson).
    Usata per la visualizzazione della curva cubic nel canvas.
    Richiede numpy (sempre disponibile tramite matplotlib).
    """
    import numpy as np
    xs = np.asarray(xs, float)
    ys = np.asarray(ys, float)
    x_new = np.asarray(x_new, float)
    n = len(xs)
    if n < 2:
        return np.interp(x_new, xs, ys)

    h = np.diff(xs)
    delta = np.diff(ys) / h

    # Tangenti di Fritsch-Carlson (monotone)
    d = np.zeros(n)
    d[0] = delta[0]
    d[-1] = delta[-1]
    for i in range(1, n - 1):
        if delta[i - 1] * delta[i] <= 0:
            d[i] = 0.0
        else:
            w1 = 2 * h[i] + h[i - 1]
            w2 = h[i] + 2 * h[i - 1]
            d[i] = (w1 + w2) / (w1 / delta[i - 1] + w2 / delta[i])

    # Valutazione vettorializzata
    idx = np.clip(np.searchsorted(xs, x_new, side='right') - 1, 0, n - 2)
    t   = (x_new - xs[idx]) / h[idx]
    return (
        (2*t**3 - 3*t**2 + 1) * ys[idx]
        + (t**3 - 2*t**2 + t) * h[idx] * d[idx]
        + (-2*t**3 + 3*t**2)  * ys[idx + 1]
        + (t**3 - t**2)       * h[idx] * d[idx + 1]
    )

# dark theme colors
_BG     = '#1e1e1e'
_BG2    = '#252526'
_GRID   = '#3c3c3c'
_LINE   = '#4ec9b0'
_PT     = '#c586c0'
_SEL    = '#ffd700'
_FG     = '#cccccc'
_FG2    = '#858585'
_AXIS   = '#6a6a6a'
_BTN_OK = '#0e639c'


class EnvelopeEditor:
    """Editor envelope basato su matplotlib."""

    # Etichette per il radio loop distribuzione (display → chiave interna)
    _LOOP_DIST_LABELS = (
        'Base',
        'Cubic',
        'Accelerando',
        'Ritardando',
        'Geometrico',
        'Power law',
    )
    _LOOP_DIST_KEYS = {
        'Base': 'base',
        'Cubic': 'cubic',
        'Accelerando': 'accelerando',
        'Ritardando': 'ritardando',
        'Geometrico': 'geometrico',
        'Power law': 'power',
    }

    def __init__(
        self,
        y_min: float,
        y_max: float,
        end_time: float,
        initial_points: List[Point] = None,
        struttura: str = 'breakpoints',
        interp: str = 'linear',
        loop_dist: str = 'base',
        n_reps: int = 4,
        ratio: float = 1.5,
        exponent: float = 2.0,
        segments: list = None,
    ):
        self.y_min    = y_min
        self.y_max    = y_max
        self.end_time = end_time
        self._result  = ''
        self._sel     = -1

        # Pan state (middle-click drag)
        self._pan_start = None
        self._pan_xlim  = None
        self._pan_ylim  = None

        # Rubber-band Y-zoom state (Z+drag)
        self._zoom_mode = False   # True quando Z è tenuto premuto
        self._zoom_y0   = None    # Y al momento del press
        self._zoom_rect = None    # Rectangle patch di feedback visivo

        # ── Sempre segment-based: normalizza input in self._segments ───────
        if segments:
            self._segments = [dict(s) for s in segments]
        elif struttura == 'loop':
            pts = sort_points(
                initial_points if initial_points
                else [(0.0, y_min), (100.0, y_max)]
            )
            self._segments = [{
                'type':      'loop',
                'loop_dist': loop_dist,
                'n_reps':    n_reps,
                'ratio':     ratio,
                'exponent':  exponent,
                'abs_start': 0.0,
                'duration':  end_time,
                'end_time':  end_time,
                'points':    pts,
            }]
        else:  # breakpoints
            pts = sort_points(
                initial_points if initial_points
                else [(0.0, y_min), (end_time, y_max)]
            )
            self._segments = [{
                'type':     'breakpoints',
                'interp':   interp,
                'points':   pts,
                'end_time': end_time,
            }]

        self._active_seg = 0

        # Carica stato iniziale dal primo segmento
        seg0      = self._segments[0]
        struttura = seg0['type']
        interp    = seg0.get('interp', 'linear')
        loop_dist = seg0.get('loop_dist', 'base')
        n_reps    = seg0.get('n_reps', 4)
        ratio     = seg0.get('ratio', 1.5)
        exponent  = seg0.get('exponent', 2.0)
        end_time  = seg0.get('end_time', end_time)

        self._struttura      = struttura
        self._interp         = interp
        self._loop_dist      = loop_dist
        self._n_reps         = n_reps
        self._ratio          = ratio
        self._exponent       = exponent
        self._preview_mode   = False
        self._total_preview  = False

        self.points: List[Point] = sort_points(seg0['points'])

        # ── Undo / Redo ───────────────────────────────────────────────────────
        self._undo_stack: collections.deque = collections.deque(maxlen=50)
        self._redo_stack: collections.deque = collections.deque()
        self._undoing = False   # True durante _restore_state → sopprime push

        self._build_figure()
        self._sync_initial_state()
        self._update_seg_chips()
        self._redraw()
        self._update_preview()

    # -------------------------------------------------------------------------
    # Costruzione figura
    # -------------------------------------------------------------------------

    def _build_figure(self):
        import matplotlib.pyplot as plt
        from matplotlib.widgets import RadioButtons, Button, TextBox

        self._plt = plt

        # ── Figura ────────────────────────────────────────────────────────
        # 5 colonne: Struttura | Interpolazione | Distribuzione | Params | Preview
        # Rimuove 'f' dal keymap fullscreen di matplotlib (default) per non
        # entrare in conflitto con il nostro F = fit-to-content.
        import matplotlib
        fs_keys = list(matplotlib.rcParams.get('keymap.fullscreen', []))
        if 'f' in fs_keys:
            fs_keys.remove('f')
            matplotlib.rcParams['keymap.fullscreen'] = fs_keys

        fig = plt.figure(figsize=(13.0, 7.0), facecolor=_BG)
        fig.subplots_adjust(left=0.0, right=1.0, top=1.0, bottom=0.0)
        self.fig = fig

        # ── Axes principale ───────────────────────────────────────────────
        # Lascia il 40% inferiore per i controlli + 2% per i bottoni
        # Lascia spazio per due righe di tab sopra (row1=0.965, row2=0.925)
        ax = fig.add_axes([0.05, 0.44, 0.93, 0.47])
        ax.set_facecolor(_BG2)
        ax.set_xlim(0.0, self.end_time)
        yr = self.y_max - self.y_min
        pad = yr * 0.05 or 0.05
        ax.set_ylim(self.y_min - pad, self.y_max + pad)
        ax.tick_params(colors=_FG2, labelsize=8)
        for spine in ax.spines.values():
            spine.set_edgecolor(_GRID)
        ax.set_xlabel('tempo (s)', color=_AXIS, fontsize=8)
        ax.set_ylabel('valore', color=_AXIS, fontsize=8)
        ax.grid(True, color=_GRID, linestyle='--', linewidth=0.5, alpha=0.7)
        ax.set_title(
            'Cmd+click: aggiungi   Drag: sposta   Destra: elimina   '
            'Cmd+click: aggiungi   Drag: sposta   Destra: elimina   Scroll: zoom Y   Ctrl+Scroll: zoom X   Mid drag: pan   Z+drag: zoom area   F: fit   Dbl: reset',
            color=_FG2, fontsize=8, pad=4,
        )
        self.ax = ax
        self.line, = ax.plot([], [], '-', color=_LINE, lw=2, zorder=2)
        self.scatter = ax.scatter([], [], s=70, zorder=3)

        # ──────────────────────────────────────────────────────────────────
        # Nuovo layout (accordion):
        #
        #  Accordion  x: 0.01 → 0.30  (w=0.29)
        #    Header BP  [0.01, 0.357, 0.29, 0.042]
        #    Header Loop[0.01, 0.312, 0.29, 0.042]
        #    Sub-panel  [0.01, 0.10,  0.29, 0.20]  (uno dei due visibile)
        #
        #  Col 4 Params x: 0.32 → 0.44  (w=0.11)
        #    n_reps, extra, end_time (sempre visibile)
        #
        #  Col 5 Preview x: 0.47 → 0.99
        # ──────────────────────────────────────────────────────────────────

        # ── Accordion: header Breakpoints ────────────────────────────────
        ax_str_bp = fig.add_axes([0.01, 0.357, 0.29, 0.042], facecolor='#2d2d2d')
        btn_str_bp = Button(ax_str_bp, '▼  Breakpoints',
                            color='#2d2d2d', hovercolor='#3a3a3a')
        btn_str_bp.label.set_color(_LINE)
        btn_str_bp.label.set_fontsize(10)
        btn_str_bp.label.set_fontweight('bold')
        self._ax_str_bp  = ax_str_bp
        self._btn_str_bp = btn_str_bp

        # ── Accordion: header Loop ────────────────────────────────────────
        ax_str_lp = fig.add_axes([0.01, 0.312, 0.29, 0.042], facecolor='#1e1e1e')
        btn_str_lp = Button(ax_str_lp, '►  Loop',
                            color='#1e1e1e', hovercolor='#2d2d2d')
        btn_str_lp.label.set_color(_FG2)
        btn_str_lp.label.set_fontsize(10)
        btn_str_lp.label.set_fontweight('normal')
        self._ax_str_lp  = ax_str_lp
        self._btn_str_lp = btn_str_lp

        # ── Accordion: sub-panel Interpolazione (Breakpoints) ─────────────
        self._ax_interp = fig.add_axes([0.01, 0.10, 0.29, 0.20], facecolor='#2d2d2d')
        self._ax_interp.set_title('Interpolazione', color=_FG, fontsize=9, pad=3)
        self.radio_interp = RadioButtons(
            self._ax_interp, ('Linear', 'Step', 'Cubic'), active=0, activecolor=_LINE,
        )
        for lbl in self.radio_interp.labels:
            lbl.set_color(_FG)
            lbl.set_fontsize(10)
        self.radio_interp.on_clicked(self._on_interp_change)
        # visibile di default (struttura iniziale = breakpoints)

        # ── Accordion: sub-panel Distribuzione loop ───────────────────────
        self._ax_loop = fig.add_axes([0.01, 0.10, 0.29, 0.20], facecolor='#2d2d2d')
        self._ax_loop.set_title('Distribuzione loop', color=_FG, fontsize=9, pad=3)
        self.radio_loop = RadioButtons(
            self._ax_loop, self._LOOP_DIST_LABELS, active=0, activecolor=_LINE,
        )
        for lbl in self.radio_loop.labels:
            lbl.set_color(_FG)
            lbl.set_fontsize(9)
        self.radio_loop.on_clicked(self._on_loop_dist_change)
        self._ax_loop.set_visible(False)   # nascosto finché struttura = 'loop'

        # ── Col 4: Parametri (n_reps, extra, end_time) ───────────────────
        # n_reps (visibile solo se loop)
        self._lbl_nreps = fig.text(0.32, 0.40, 'n_reps:', color=_FG, fontsize=8, va='top')
        ax_nreps = fig.add_axes([0.32, 0.32, 0.11, 0.06], facecolor=_BG2)
        self.txt_nreps = TextBox(ax_nreps, '', initial='4',
                                 color=_BG2, hovercolor='#3c3c3c')
        self.txt_nreps.text_disp.set_color(_FG)
        self.txt_nreps.text_disp.set_fontsize(11)
        self.txt_nreps.on_text_change(self._on_nreps_change)
        self._ax_nreps = ax_nreps
        # parametro extra (ratio / esponente, visibile solo se loop+dist specifiche)
        self._lbl_extra = fig.text(0.32, 0.28, '', color=_FG2, fontsize=8, va='top')
        ax_extra = fig.add_axes([0.32, 0.20, 0.11, 0.06], facecolor=_BG2)
        self.txt_extra = TextBox(ax_extra, '', initial='1.5',
                                 color=_BG2, hovercolor='#3c3c3c')
        self.txt_extra.text_disp.set_color(_FG)
        self.txt_extra.text_disp.set_fontsize(11)
        self.txt_extra.on_text_change(self._on_extra_change)
        self._ax_extra = ax_extra
        # end_time (visibile solo in modalità loop, gestito da _set_loop_params_visible)
        self._lbl_endtime = fig.text(
            0.32, 0.16, 'end time (s):', color=_FG2, fontsize=8, va='top',
        )
        ax_et = fig.add_axes([0.32, 0.10, 0.11, 0.06], facecolor=_BG2)
        self.txt_endtime = TextBox(ax_et, '', initial='1.0',
                                   color=_BG2, hovercolor='#3c3c3c')
        self.txt_endtime.text_disp.set_color(_FG)
        self.txt_endtime.text_disp.set_fontsize(11)
        self.txt_endtime.on_text_change(self._on_endtime_change)
        self._ax_endtime = ax_et
        # nascosti finché non c'è un segmento attivo
        for w in (self._lbl_nreps, ax_nreps, self._lbl_extra, ax_extra):
            w.set_visible(False)

        # ── Schede Pattern/Anteprima — set A: loop puro (row 1, y=0.965) ────
        # set B: loop in misto (row 2, y=0.925)
        # Non usiamo on_clicked: intercettiamo button_press_event direttamente.
        def _make_tab(x, y, w, h, label, active):
            fc = '#2d2d2d' if active else '#1e1e1e'
            ax = fig.add_axes([x, y, w, h], facecolor=fc)
            btn = Button(ax, label, color=fc, hovercolor='#383838' if active else '#2d2d2d')
            btn.label.set_fontsize(9)
            btn.label.set_color(_LINE if active else _FG2)
            btn.label.set_fontweight('bold' if active else 'normal')
            ax.set_visible(False)
            return ax, btn

        self._ax_tab_pat,  self.btn_tab_pattern  = _make_tab(0.05, 0.965, 0.13, 0.03, 'Pattern', True)
        self._ax_tab_prev, self.btn_tab_preview   = _make_tab(0.19, 0.965, 0.18, 0.03, 'Anteprima loop', False)
        # Set B (misto row 2)
        self._ax_tab_pat_m,  self.btn_tab_pattern_m  = _make_tab(0.05, 0.925, 0.13, 0.03, 'Pattern', True)
        self._ax_tab_prev_m, self.btn_tab_preview_m  = _make_tab(0.19, 0.925, 0.18, 0.03, 'Anteprima loop', False)

        # ── Chips segmenti (Misto mode, row 1 y=0.965) ────────────────────
        # Chip più stretti (0.09) per fare posto a "Anteprima totale" a destra.
        _MAX_SEGS  = 8
        _CHIP_W    = 0.09
        _CHIP_H    = 0.03
        _CHIP_Y    = 0.965
        _CHIP_GAP  = 0.005
        _CHIP_X0   = 0.05
        self._seg_chip_axes = []
        self._seg_chip_btns = []
        for i in range(_MAX_SEGS):
            x = _CHIP_X0 + i * (_CHIP_W + _CHIP_GAP)
            ax_c = fig.add_axes([x, _CHIP_Y, _CHIP_W, _CHIP_H], facecolor='#1e1e1e')
            btn_c = Button(ax_c, '', color='#1e1e1e', hovercolor='#2d2d2d')
            btn_c.label.set_fontsize(8)
            btn_c.label.set_color(_FG2)
            ax_c.set_visible(False)
            self._seg_chip_axes.append(ax_c)
            self._seg_chip_btns.append(btn_c)
        # Bottone "+"
        x_add = _CHIP_X0 + _MAX_SEGS * (_CHIP_W + _CHIP_GAP)
        ax_add = fig.add_axes([x_add, _CHIP_Y, 0.03, _CHIP_H], facecolor='#2d2d2d')
        btn_add = Button(ax_add, '+', color='#2d2d2d', hovercolor='#3a3a3a')
        btn_add.label.set_color(_FG)
        btn_add.label.set_fontsize(11)
        ax_add.set_visible(False)
        self._ax_seg_add  = ax_add
        self._btn_seg_add = btn_add
        # Chip "Anteprima totale" (destra, separato)
        x_tot = x_add + 0.04
        ax_tot = fig.add_axes([x_tot, _CHIP_Y, 0.135, _CHIP_H], facecolor='#1e1e1e')
        btn_tot = Button(ax_tot, '▶▶ Anteprima totale', color='#1e1e1e', hovercolor='#2d2d2d')
        btn_tot.label.set_color(_FG2)
        btn_tot.label.set_fontsize(8)
        ax_tot.set_visible(False)
        self._ax_total_preview  = ax_tot
        self._btn_total_preview = btn_tot

        # ── (end_time TextBox è ora in Col 4, sotto i parametri loop) ──────

        # ── Overlay: scelta tipo segmento / conferma cancellazione ────────
        # Due bottoni condivisi (riutilizzati per "add" e "delete" modes).
        ax_ov1 = fig.add_axes([x_add, _CHIP_Y, 0.065, _CHIP_H], facecolor='#2d2d2d')
        btn_ov1 = Button(ax_ov1, '+ BP', color='#2d2d2d', hovercolor='#3a3a3a')
        btn_ov1.label.set_color(_LINE)
        btn_ov1.label.set_fontsize(8)
        ax_ov1.set_visible(False)
        ax_ov2 = fig.add_axes([x_add + 0.07, _CHIP_Y, 0.07, _CHIP_H], facecolor='#2d2d2d')
        btn_ov2 = Button(ax_ov2, '+ Loop', color='#2d2d2d', hovercolor='#3a3a3a')
        btn_ov2.label.set_color(_LINE)
        btn_ov2.label.set_fontsize(8)
        ax_ov2.set_visible(False)
        self._ax_overlay_1    = ax_ov1
        self._btn_overlay_1   = btn_ov1
        self._ax_overlay_2    = ax_ov2
        self._btn_overlay_2   = btn_ov2
        self._overlay_mode    = None      # None | 'add' | 'delete'
        self._del_pending_seg = None

        # ── Col 5: Preview ────────────────────────────────────────────────
        fig.text(0.47, 0.42, 'Preview:', color=_FG2, fontsize=9, va='top')
        self.preview_txt = fig.text(
            0.47, 0.38, '',
            color=_LINE, fontsize=8, fontfamily='monospace', va='top',
        )

        # ── Bottoni ───────────────────────────────────────────────────────
        ax_cancel = fig.add_axes([0.75, 0.01, 0.11, 0.08], facecolor=_BG2)
        self.btn_cancel = Button(ax_cancel, 'Annulla',
                                 color=_BG2, hovercolor='#3c3c3c')
        self.btn_cancel.label.set_color(_FG)
        self.btn_cancel.on_clicked(self._on_cancel)

        ax_ok = fig.add_axes([0.88, 0.01, 0.11, 0.08], facecolor=_BTN_OK)
        self.btn_ok = Button(ax_ok, 'Inserisci',
                             color=_BTN_OK, hovercolor='#1177bb')
        self.btn_ok.label.set_color('#ffffff')
        self.btn_ok.label.set_fontweight('bold')
        self.btn_ok.on_clicked(self._on_ok)

        # ── Titolo finestra ───────────────────────────────────────────────
        try:
            fig.canvas.manager.set_window_title('PGE — Envelope Editor')
        except Exception:
            pass

        # ── Connetti eventi mouse ──────────────────────────────────────────
        # Le schede usano button_press_event diretto (non Button.on_clicked)
        # per funzionare al primo click sul backend MacOSX.
        fig.canvas.mpl_connect('button_press_event',   self._on_figure_press)
        fig.canvas.mpl_connect('motion_notify_event',  self._on_motion)
        fig.canvas.mpl_connect('button_release_event', self._on_release)
        fig.canvas.mpl_connect('scroll_event',         self._on_scroll)
        fig.canvas.mpl_connect('key_press_event',      self._on_key_press)
        fig.canvas.mpl_connect('key_release_event',    self._on_key_release)

    # -------------------------------------------------------------------------
    # Sincronizzazione stato iniziale → widget
    # -------------------------------------------------------------------------

    def _sync_initial_state(self):
        """
        Allinea i RadioButtons e la visibilità dei pannelli allo stato
        iniziale (usato quando la GUI viene aperta con un envelope esistente).
        """
        is_loop = self._struttura == 'loop'

        # Accordion headers
        self._update_struttura_header_style(self._struttura)

        # Interpolazione
        interp_labels = [l.get_text() for l in self.radio_interp.labels]
        interp_target = self._interp.capitalize()
        if interp_target in interp_labels:
            self.radio_interp.set_active(interp_labels.index(interp_target))

        # Distribuzione loop
        loop_rev = {v: k for k, v in self._LOOP_DIST_KEYS.items()}
        loop_target = loop_rev.get(self._loop_dist, 'Base')
        loop_labels = [l.get_text() for l in self.radio_loop.labels]
        if loop_target in loop_labels:
            self.radio_loop.set_active(loop_labels.index(loop_target))

        # Visibilità condizionale pannelli Col 2 / Col 3
        self._set_radio_visible(self._ax_interp, not is_loop)
        self._set_radio_visible(self._ax_loop, is_loop)
        if not is_loop:
            self._ax_interp.set_title('Interpolazione', color=_FG, fontsize=9, pad=3)
            for lbl in self.radio_interp.labels:
                lbl.set_color(_FG)
        if is_loop:
            self._ax_loop.set_title('Distribuzione loop', color=_FG, fontsize=9, pad=3)
            for lbl in self.radio_loop.labels:
                lbl.set_color(_FG)

        # Parametri loop
        self._set_loop_params_visible(is_loop)
        if is_loop and self._loop_dist in ('geometrico', 'power'):
            self._lbl_extra.set_text('ratio:' if self._loop_dist == 'geometrico' else 'esponente:')
            val = self._ratio if self._loop_dist == 'geometrico' else self._exponent
            self.txt_extra.set_val(str(val))

        # n_reps
        self.txt_nreps.set_val(str(self._n_reps))

        # Schede: stile iniziale
        if is_loop:
            self._update_tab_style()

    # -------------------------------------------------------------------------
    # Accordion helpers
    # -------------------------------------------------------------------------

    def _update_struttura_header_style(self, struttura: str):
        """Aggiorna l'aspetto dei due header accordion (▼ = aperto, ► = chiuso)."""
        is_bp = struttura == 'breakpoints'
        # BP header
        fc_bp = '#2d2d2d' if is_bp else '#1e1e1e'
        self._ax_str_bp.set_facecolor(fc_bp)
        self._btn_str_bp.ax.set_facecolor(fc_bp)
        self._btn_str_bp.label.set_text('▼  Breakpoints' if is_bp else '►  Breakpoints')
        self._btn_str_bp.label.set_color(_LINE if is_bp else _FG2)
        self._btn_str_bp.label.set_fontweight('bold' if is_bp else 'normal')
        # Loop header
        fc_lp = '#2d2d2d' if not is_bp else '#1e1e1e'
        self._ax_str_lp.set_facecolor(fc_lp)
        self._btn_str_lp.ax.set_facecolor(fc_lp)
        self._btn_str_lp.label.set_text('►  Loop' if is_bp else '▼  Loop')
        self._btn_str_lp.label.set_color(_FG2 if is_bp else _LINE)
        self._btn_str_lp.label.set_fontweight('normal' if is_bp else 'bold')

    # -------------------------------------------------------------------------
    # Visibilità pannelli condizionali
    # -------------------------------------------------------------------------

    def _set_loop_params_visible(self, show: bool):
        """Mostra/nasconde Col 4 e le schede Pattern/Anteprima (sempre Set B)."""
        self._lbl_nreps.set_visible(show)
        self._ax_nreps.set_visible(show)
        # Set A (y=0.965): sempre nascosto — ora si usa sempre Set B
        self._ax_tab_pat.set_visible(False)
        self._ax_tab_prev.set_visible(False)
        # Set B (y=0.925): attivo quando il segmento corrente è loop
        self._ax_tab_pat_m.set_visible(show)
        self._ax_tab_prev_m.set_visible(show)
        needs_extra = show and self._loop_dist in ('geometrico', 'power')
        self._lbl_extra.set_visible(needs_extra)
        self._ax_extra.set_visible(needs_extra)
        
        # FIX: end_time visibile SOLO in modalità loop
        self._lbl_endtime.set_visible(show)
        self._ax_endtime.set_visible(show)

    # -------------------------------------------------------------------------
    # Breakpoints end_time helper
    # -------------------------------------------------------------------------

    def _update_bp_end_time(self):
        """In BP mode: aggiorna self.end_time e xlim in base all'X dell'ultimo punto."""
        if not self.points:
            return
        max_t = max(t for t, v in self.points)
        pad   = max_t * 0.08 or 0.5   # 8% di padding a destra
        self.end_time = max_t
        if self._active_seg < len(self._segments):
            self._segments[self._active_seg]['end_time'] = max_t
        self.ax.set_xlim(0.0, max_t + pad)

    # -------------------------------------------------------------------------
    # Zoom / pan helpers
    # -------------------------------------------------------------------------

    def _reset_view(self):
        """Ripristina X e Y al range originale completo."""
        self.ax.set_xlim(0.0, self.end_time)
        yr  = self.y_max - self.y_min
        pad = yr * 0.05 or 0.05
        self.ax.set_ylim(self.y_min - pad, self.y_max + pad)
        self.fig.canvas.draw_idle()

    def _fit_to_content(self):
        """Zoom automatico sui punti visibili (F key).  Aggiunge 10% di padding."""
        pts = self.points
        if not pts:
            return
        ts = [p[0] for p in pts]
        vs = [p[1] for p in pts]
        t_min, t_max = min(ts), max(ts)
        v_min, v_max = min(vs), max(vs)
        v_pad = (v_max - v_min) * 0.10 or (self.y_max - self.y_min) * 0.05 or 0.05
        # X: clamp a [0, end_time] — niente spazio fuori dai bounds temporali
        self.ax.set_xlim(0.0, self.end_time)
        self.ax.set_ylim(v_min - v_pad, v_max + v_pad)
        self.fig.canvas.draw_idle()

    _HINT_NORMAL = (
        'Cmd+click: aggiungi   Drag: sposta   Destra: elimina   '
        'Cmd+click: aggiungi   Drag: sposta   Destra: elimina   Scroll: zoom Y   Ctrl+Scroll: zoom X   Mid drag: pan   Z+drag: zoom area   F: fit   Dbl: reset'
    )
    _HINT_ZOOM = '[ZOOM Y]  Trascina verticalmente per selezionare l\'area → rilascia per zoomare   Esc: esci'

    def _on_key_press(self, event):
        key     = (event.key or '').lower()
        key_raw = event.key or ''
        # Undo: Cmd+Z / Ctrl+Z
        if key in ('super+z', 'ctrl+z'):
            self._undo()
            return
        # Redo: Cmd+Shift+Z / Ctrl+Shift+Z / Ctrl+Y — anche super+Z (alcuni backend)
        if key in ('super+shift+z', 'ctrl+shift+z', 'ctrl+y') or key_raw in ('super+Z', 'ctrl+Z'):
            self._redo()
            return
        if key == 'f':
            self._fit_to_content()
        elif key == 'z' and not self._zoom_mode:
            self._zoom_mode = True
            self.ax.set_title(self._HINT_ZOOM, color='#00aaff', fontsize=8, pad=4)
            self.fig.canvas.draw_idle()
        elif key == 'escape':
            self._cancel_zoom()
        elif key in ('alt+up', 'alt+down'):
            self._arrow_zoom_y(zoom_in=(key == 'alt+up'))

    def _on_key_release(self, event):
        if (event.key or '').lower() == 'z':
            self._cancel_zoom()

    def _cancel_zoom(self):
        """Esce dalla modalità Z-zoom senza applicare nulla."""
        self._zoom_mode = False
        self._zoom_y0   = None
        if self._zoom_rect is not None:
            try:
                self._zoom_rect.remove()
            except ValueError:
                pass
            self._zoom_rect = None
        self.ax.set_title(self._HINT_NORMAL, color=_FG2, fontsize=8, pad=4)
        self.fig.canvas.draw_idle()

    def _arrow_zoom_y(self, zoom_in: bool):
        """Option+↑/↓ — zoom Y ancorato al breakpoint con Y minima.

        Il limite inferiore della vista è fisso al valore del breakpoint più
        basso; solo il limite superiore si muove (zoom in = si avvicina,
        zoom out = si allontana).
        """
        if not self.points:
            return
        y_anchor = min(v for _, v in self.points)
        _, y_hi = self.ax.get_ylim()
        factor = 0.88 if zoom_in else (1.0 / 0.88)
        new_hi = y_anchor + (y_hi - y_anchor) * factor
        new_hi = min(self.y_max, new_hi)           # non uscire dai bounds
        if new_hi > y_anchor:
            self.ax.set_ylim(y_anchor, new_hi)
            self.fig.canvas.draw_idle()

    # -------------------------------------------------------------------------
    # Undo / Redo
    # -------------------------------------------------------------------------

    def _snapshot(self) -> dict:
        """Ritorna una copia profonda dello stato undoable corrente."""
        self._save_current_segment()
        return {
            'segments':   copy.deepcopy(self._segments),
            'active_seg': self._active_seg,
            'struttura':  self._struttura,
            'interp':     self._interp,
            'loop_dist':  self._loop_dist,
            'n_reps':     self._n_reps,
            'ratio':      self._ratio,
            'exponent':   self._exponent,
            'end_time':   self.end_time,
        }

    def _push_undo(self):
        """Salva lo stato attuale nello stack undo e svuota il redo."""
        self._undo_stack.append(self._snapshot())
        self._redo_stack.clear()

    def _restore_state(self, snap: dict):
        """Ripristina lo stato da uno snapshot e ri-sincronizza i widget."""
        self._undoing = True
        try:
            self._segments   = copy.deepcopy(snap['segments'])
            self._active_seg = snap['active_seg']
            self._struttura  = snap['struttura']
            self._interp     = snap['interp']
            self._loop_dist  = snap['loop_dist']
            self._n_reps     = snap['n_reps']
            self._ratio      = snap['ratio']
            self._exponent   = snap['exponent']
            self.end_time    = snap['end_time']
            # _load_segment sincronizza punti, radio buttons, pannelli, xlim
            self._load_segment(self._active_seg)
            self._update_seg_chips()
            self.fig.canvas.draw()
        finally:
            self._undoing = False

    def _undo(self):
        if not self._undo_stack:
            return
        self._redo_stack.append(self._snapshot())
        self._restore_state(self._undo_stack.pop())

    def _redo(self):
        if not self._redo_stack:
            return
        self._undo_stack.append(self._snapshot())
        self._restore_state(self._redo_stack.pop())

    # -------------------------------------------------------------------------
    # Hit detection (coordinate display, pixel)
    # -------------------------------------------------------------------------

    def _find_nearest(self, event) -> int:
        if event.inaxes is not self.ax or event.xdata is None:
            return -1
        best_idx, best_dist = -1, float('inf')
        for i, (t, v) in enumerate(self.points):
            px, py = self.ax.transData.transform((t, v))
            dist = ((px - event.x) ** 2 + (py - event.y) ** 2) ** 0.5
            if dist < best_dist:
                best_dist = dist
                best_idx = i
        return best_idx if best_dist <= _HIT_RADIUS_PX else -1

    # -------------------------------------------------------------------------
    # Event handlers
    # -------------------------------------------------------------------------

    def _on_figure_press(self, event):
        """Dispatcher di button_press: intercetta schede e chip con hit-test pixel,
        delega il resto a _on_press. Non usa event.inaxes perché sul backend
        MacOSX il primo click (focus) lascia inaxes=None."""

        # ── Tasto destro: cancellazione chip segmento ────────────────────
        if event.button == 3:
            if self._overlay_mode is None:
                for i, ax_c in enumerate(self._seg_chip_axes):
                    if ax_c.get_visible():
                        bb = ax_c.get_window_extent()
                        if bb.x0 <= event.x <= bb.x1 and bb.y0 <= event.y <= bb.y1:
                            if len(self._segments) > 1:
                                self._on_seg_delete_request(i)
                            return
            self._on_press(event)
            return

        # ── Middle-click: avvia pan ───────────────────────────────────────
        if event.button == 2:
            if event.inaxes is self.ax:
                self._pan_start = (event.x, event.y)
                self._pan_xlim  = self.ax.get_xlim()
                self._pan_ylim  = self.ax.get_ylim()
            return

        if event.button != 1:
            self._on_press(event)
            return

        # ── Overlay (scelta tipo segmento / conferma cancellazione) ───────
        if self._overlay_mode is not None:
            bb1 = self._ax_overlay_1.get_window_extent()
            bb2 = self._ax_overlay_2.get_window_extent()
            if bb1.x0 <= event.x <= bb1.x1 and bb1.y0 <= event.y <= bb1.y1:
                self._on_overlay_click(1)
                return
            if bb2.x0 <= event.x <= bb2.x1 and bb2.y0 <= event.y <= bb2.y1:
                self._on_overlay_click(2)
                return
            return   # click fuori dall'overlay: ignora senza propagare

        # ── Accordion: header Struttura ───────────────────────────────────
        for ax_h, label in ((self._ax_str_bp, 'breakpoints'), (self._ax_str_lp, 'loop')):
            bb = ax_h.get_window_extent()
            if bb.x0 <= event.x <= bb.x1 and bb.y0 <= event.y <= bb.y1:
                if self._struttura != label:
                    self._on_struttura_change(label.capitalize())
                return

        # ── Chip segmenti ─────────────────────────────────────────────────
        for i, ax_c in enumerate(self._seg_chip_axes):
            if ax_c.get_visible():
                bb = ax_c.get_window_extent()
                if bb.x0 <= event.x <= bb.x1 and bb.y0 <= event.y <= bb.y1:
                    self._on_seg_click(i)
                    return
        if self._ax_seg_add.get_visible():
            bb = self._ax_seg_add.get_window_extent()
            if bb.x0 <= event.x <= bb.x1 and bb.y0 <= event.y <= bb.y1:
                self._on_add_segment()
                return

        # ── Chip "Anteprima totale" ────────────────────────────────────────
        if self._ax_total_preview.get_visible():
            bb = self._ax_total_preview.get_window_extent()
            if bb.x0 <= event.x <= bb.x1 and bb.y0 <= event.y <= bb.y1:
                self._on_total_preview_click()
                return

        # ── Schede loop set A (loop puro) e set B (loop in misto) ────────
        for ax, is_preview in (
            (self._ax_tab_pat,   False),
            (self._ax_tab_prev,  True),
            (self._ax_tab_pat_m, False),
            (self._ax_tab_prev_m, True),
        ):
            if ax.get_visible():
                bb = ax.get_window_extent()
                if bb.x0 <= event.x <= bb.x1 and bb.y0 <= event.y <= bb.y1:
                    self._on_tab_click(is_preview)
                    return

        self._on_press(event)

    def _on_scroll(self, event):
        """Scroll: zoom Y centrato sul cursore.  Ctrl+Scroll: zoom X.
        Sensibilità: 0.88 per step (più morbido del precedente 0.80).
        Multi-step (trackpad fast scroll) gestito con potenza del fattore.
        """
        if event.inaxes is not self.ax:
            return
        # factor < 1 = zoom in (range si restringe), > 1 = zoom out
        base   = 0.88
        factor = base ** event.step   # event.step > 0: scroll su = zoom in
        key  = (event.key or '').lower()
        ctrl = 'ctrl' in key or 'control' in key or 'cmd' in key
        if ctrl:
            # Zoom X centrato sul cursore
            xmin, xmax = self.ax.get_xlim()
            xc = event.xdata if event.xdata is not None else (xmin + xmax) / 2
            self.ax.set_xlim(xc - (xc - xmin) * factor, xc + (xmax - xc) * factor)
        else:
            # Zoom Y centrato sul cursore, clamped ai bounds del parametro
            ymin, ymax = self.ax.get_ylim()
            yc = event.ydata if event.ydata is not None else (ymin + ymax) / 2
            new_lo = max(self.y_min, yc - (yc - ymin) * factor)
            new_hi = min(self.y_max, yc + (ymax - yc) * factor)
            self.ax.set_ylim(new_lo, new_hi)
        self.fig.canvas.draw_idle()

    def _on_press(self, event):
        if event.inaxes is not self.ax or event.xdata is None:
            return

        # ── Rubber-band Y-zoom (Z tenuto premuto) ─────────────────────────
        if self._zoom_mode and event.button == 1 and event.ydata is not None:
            self._zoom_y0 = event.ydata
            return   # non aggiungere/selezionare punti

        # In total preview o loop preview: solo zoom/reset
        if self._total_preview or self._preview_mode:
            if getattr(event, 'dblclick', False) and event.button == 1:
                self._reset_view()
            return

        # Doppio click sinistro → reset view completo (X + Y)
        if getattr(event, 'dblclick', False) and event.button == 1:
            self._reset_view()
            return

        if event.button == 3:
            idx = self._find_nearest(event)
            if idx >= 0 and len(self.points) > 2:
                if not self._undoing:
                    self._push_undo()
                self.points.pop(idx)
                self._redraw()
                self._update_preview()
            return

        key  = (event.key or '').lower()
        cmd  = 'super' in key or 'cmd' in key or 'meta' in key
        idx  = self._find_nearest(event)
        if idx >= 0:
            # Salva snapshot prima del drag: un unico undo per tutta la trascinata
            if not self._undoing:
                self._push_undo()
            self._sel = idx
        elif cmd:
            # Cmd+click → inserisce un nuovo breakpoint
            if not self._undoing:
                self._push_undo()
            is_bp = self._struttura == 'breakpoints'
            t = max(0.0, event.xdata if is_bp else min(self.end_time, event.xdata))
            v = max(self.y_min, min(self.y_max, event.ydata))
            self.points.append((t, v))
            self.points = sort_points(self.points)
            for i, (pt, pv) in enumerate(self.points):
                if abs(pt - t) < 1e-9 and abs(pv - v) < 1e-9:
                    self._sel = i
                    break
            if is_bp:
                self._update_bp_end_time()
            self._redraw()
            self._update_preview()

    def _on_motion(self, event):
        # ── Rubber-band Y-zoom (Z+drag) ───────────────────────────────────
        if self._zoom_mode and self._zoom_y0 is not None:
            if event.inaxes is not self.ax or event.ydata is None:
                return
            from matplotlib.patches import Rectangle as _Rect
            if self._zoom_rect is not None:
                try:
                    self._zoom_rect.remove()
                except ValueError:
                    pass
            xl = self.ax.get_xlim()
            y0, y1 = sorted([self._zoom_y0, event.ydata])
            self._zoom_rect = self.ax.add_patch(_Rect(
                (xl[0], y0), xl[1] - xl[0], y1 - y0,
                facecolor='#00aaff', alpha=0.15,
                edgecolor='#00aaff', linewidth=1, linestyle='--',
                transform=self.ax.transData, zorder=5,
            ))
            self.fig.canvas.draw_idle()
            return

        # ── Pan (middle-click drag) ────────────────────────────────────────
        if self._pan_start is not None:
            if event.inaxes is not self.ax:
                return
            ax_bb = self.ax.get_window_extent()
            xl = self._pan_xlim
            yl = self._pan_ylim
            x_range = xl[1] - xl[0]
            y_range = yl[1] - yl[0]
            if ax_bb.width > 0 and ax_bb.height > 0 and x_range and y_range:
                # delta in pixel (matplotlib: y=0 bottom)
                dx_pix = event.x - self._pan_start[0]
                dy_pix = event.y - self._pan_start[1]
                # converti in unità dati; drag right/up sposta viewport left/down
                dx = -dx_pix * x_range / ax_bb.width
                dy = -dy_pix * y_range / ax_bb.height
                self.ax.set_xlim(xl[0] + dx, xl[1] + dx)
                self.ax.set_ylim(yl[0] + dy, yl[1] + dy)
                self.fig.canvas.draw_idle()
            return

        if self._sel < 0 or event.inaxes is not self.ax or event.xdata is None:
            return
        is_bp = self._struttura == 'breakpoints'
        t = max(0.0, event.xdata if is_bp else min(self.end_time, event.xdata))
        v = max(self.y_min, min(self.y_max, event.ydata))
        self.points[self._sel] = (t, v)
        self.points = sort_points(self.points)
        for i, (pt, pv) in enumerate(self.points):
            if abs(pt - t) < 1e-9 and abs(pv - v) < 1e-9:
                self._sel = i
                break
        if is_bp:
            self._update_bp_end_time()
        self._redraw()
        self._update_preview()

    def _on_release(self, event):
        # ── Applica rubber-band Y-zoom ────────────────────────────────────
        if self._zoom_mode and self._zoom_y0 is not None:
            if self._zoom_rect is not None:
                try:
                    self._zoom_rect.remove()
                except ValueError:
                    pass
                self._zoom_rect = None
            y1 = event.ydata if (event.inaxes is self.ax and event.ydata is not None) else self._zoom_y0
            y_lo, y_hi = sorted([self._zoom_y0, y1])
            self._zoom_y0 = None
            if y_hi - y_lo > 1e-6:   # selezione non degenerata
                self.ax.set_ylim(max(self.y_min, y_lo), min(self.y_max, y_hi))
            self.fig.canvas.draw_idle()
            return
        self._sel = -1
        self._pan_start = None

    def _on_tab_click(self, preview: bool):
        """Seleziona la scheda Pattern (preview=False) o Anteprima loop (preview=True)."""
        self._preview_mode = preview
        self._update_tab_style()
        # ── Loop: aggiorna xlim in base alla tab attiva ───────────────────
        if self._struttura == 'loop':
            seg = self._segments[self._active_seg]
            if preview:
                abs_start = seg.get('abs_start', 0.0)
                duration  = seg.get('duration', 1.0)
                rep_times = _compute_rep_times(
                    duration, self._n_reps, self._loop_dist,
                    self._ratio, self._exponent,
                )
                total_end = rep_times[-1][1] if rep_times else duration
                self.ax.set_xlim(abs_start, abs_start + total_end)
                self.ax.set_xlabel('tempo (s)', color=_AXIS, fontsize=8)
            else:
                self.ax.set_xlim(0.0, 100.0)
                self.ax.set_xlabel('pattern (%)', color=_AXIS, fontsize=8)
        self._redraw()
        self.fig.canvas.draw()   # ridisegno immediato, non lazy

    def _update_tab_style(self):
        """Aggiorna l'aspetto di entrambi i set di schede Pattern/Anteprima."""
        tab_pairs = [
            (self._ax_tab_pat,   self.btn_tab_pattern,   self._ax_tab_prev,  self.btn_tab_preview),
            (self._ax_tab_pat_m, self.btn_tab_pattern_m, self._ax_tab_prev_m, self.btn_tab_preview_m),
        ]
        if self._preview_mode:
            for ax_pat, btn_pat, ax_prv, btn_prv in tab_pairs:
                ax_pat.set_facecolor('#1e1e1e');  btn_pat.ax.set_facecolor('#1e1e1e')
                btn_pat.label.set_color(_FG2);    btn_pat.label.set_fontweight('normal')
                ax_prv.set_facecolor('#2d2d2d');  btn_prv.ax.set_facecolor('#2d2d2d')
                btn_prv.label.set_color(_LINE);   btn_prv.label.set_fontweight('bold')
            self.ax.set_title(
                'Anteprima loop — sola lettura   '
                'Cmd+click: aggiungi   Drag: sposta   Destra: elimina   Scroll: zoom Y   Ctrl+Scroll: zoom X   Mid drag: pan   Z+drag: zoom area   F: fit   Dbl: reset',
                color='#ffd700', fontsize=8, pad=4,
            )
        else:
            for ax_pat, btn_pat, ax_prv, btn_prv in tab_pairs:
                ax_pat.set_facecolor('#2d2d2d');  btn_pat.ax.set_facecolor('#2d2d2d')
                btn_pat.label.set_color(_LINE);   btn_pat.label.set_fontweight('bold')
                ax_prv.set_facecolor('#1e1e1e');  btn_prv.ax.set_facecolor('#1e1e1e')
                btn_prv.label.set_color(_FG2);    btn_prv.label.set_fontweight('normal')
            self.ax.set_title(
                'Cmd+click: aggiungi   Drag: sposta   Destra: elimina   '
                'Cmd+click: aggiungi   Drag: sposta   Destra: elimina   Scroll: zoom Y   Ctrl+Scroll: zoom X   Mid drag: pan   Z+drag: zoom area   F: fit   Dbl: reset',
                color=_FG2, fontsize=8, pad=4,
            )

    # -------------------------------------------------------------------------
    # Misto mode: gestione segmenti
    # -------------------------------------------------------------------------

    def _update_seg_chips(self):
        """Aggiorna chip segmento, pulsanti + e Anteprima totale."""
        n = len(self._segments)
        bp_count = loop_count = 0
        tot_active = self._total_preview
        for i, (ax_c, btn_c) in enumerate(zip(self._seg_chip_axes, self._seg_chip_btns)):
            if i < n:
                seg = self._segments[i]
                if seg['type'] == 'breakpoints':
                    bp_count += 1
                    label = f'BP {bp_count}'
                else:
                    loop_count += 1
                    label = f'Loop {loop_count}'
                btn_c.label.set_text(label)
                active = (i == self._active_seg) and not tot_active
                fc = '#2d2d2d' if active else '#1e1e1e'
                ax_c.set_facecolor(fc);  btn_c.ax.set_facecolor(fc)
                btn_c.label.set_color(_LINE if active else _FG2)
                btn_c.label.set_fontweight('bold' if active else 'normal')
                ax_c.set_visible(True)
            else:
                ax_c.set_visible(False)
        self._ax_seg_add.set_visible(True)
        # Chip "Anteprima totale"
        self._ax_total_preview.set_visible(True)
        fc_tot = '#2d2d2d' if tot_active else '#1e1e1e'
        self._ax_total_preview.set_facecolor(fc_tot)
        self._btn_total_preview.ax.set_facecolor(fc_tot)
        self._btn_total_preview.label.set_color(_LINE if tot_active else _FG2)
        self._btn_total_preview.label.set_fontweight('bold' if tot_active else 'normal')
        # End-time widget: aggiorna valore, nasconde durante total preview
        self._lbl_endtime.set_visible(not tot_active)
        self._ax_endtime.set_visible(not tot_active)
        if not tot_active and self._segments:
            seg = self._segments[self._active_seg]
            et_val = seg.get('end_time', self.end_time) if seg['type'] == 'breakpoints' \
                     else seg.get('end_time', seg.get('abs_start', 0.0) + seg.get('duration', 1.0))
            self.txt_endtime.set_val(f'{et_val:.4g}')

    def _save_current_segment(self):
        """Salva lo stato del canvas nel segmento attivo."""
        if self._active_seg >= len(self._segments):
            return
        seg = self._segments[self._active_seg]
        seg['points'] = list(self.points)
        if seg['type'] == 'breakpoints':
            seg['interp'] = self._interp
            # end_time = X dell'ultimo punto (auto-derivato)
            if self.points:
                seg['end_time'] = max(t for t, v in self.points)
        else:
            seg['loop_dist'] = self._loop_dist
            seg['n_reps']    = self._n_reps
            seg['ratio']     = self._ratio
            seg['exponent']  = self._exponent
            # Per loop: legge end_time dal TextBox come prima
            try:
                et_val = float(self.txt_endtime.text)
                if et_val > 0:
                    seg['end_time'] = et_val
                    seg['duration'] = et_val - seg.get('abs_start', 0.0)
            except ValueError:
                pass

    def _on_endtime_change(self, text: str):
        """Aggiorna end_time / duration del segmento attivo in tempo reale."""
        if self._active_seg >= len(self._segments):
            return
        try:
            et_val = float(text)
        except ValueError:
            return
        if et_val <= 0:
            return
        seg = self._segments[self._active_seg]
        seg['end_time'] = et_val
        if seg['type'] == 'loop':
            seg['duration'] = et_val - seg.get('abs_start', 0.0)
            # In preview mode: aggiorna xlim con i nuovi tempi
            if self._preview_mode:
                abs_start = seg.get('abs_start', 0.0)
                duration  = seg['duration']
                rep_times = _compute_rep_times(
                    duration, self._n_reps, self._loop_dist,
                    self._ratio, self._exponent,
                )
                total_end = rep_times[-1][1] if rep_times else duration
                self.ax.set_xlim(abs_start, abs_start + total_end)
        else:
            # BP: aggiorna self.end_time per il clamping dei punti
            self.end_time = et_val
            self.ax.set_xlim(0.0, et_val)
        self._update_preview()
        self.fig.canvas.draw_idle()

    def _load_segment(self, i: int):
        """Carica il segmento i nel canvas (aggiorna punti, controlli, xlim)."""
        seg = self._segments[i]
        self.points = sort_points(list(seg['points']))
        if seg['type'] == 'loop':
            # Pattern loop: asse X in % (0-100), i punti sono già in [0,100]
            self.end_time = 100.0
            self.ax.set_xlim(0.0, 100.0)
            self.ax.set_xlabel('pattern (%)', color=_AXIS, fontsize=8)
        else:
            # BP: end_time = X dell'ultimo punto (non un valore fisso)
            pts = self.points
            seg_end = max(t for t, v in pts) if pts else seg.get('end_time', self.end_time)
            pad     = seg_end * 0.08 or 0.5
            self.end_time = seg_end
            seg['end_time'] = seg_end
            self.ax.set_xlim(0.0, seg_end + pad)
            self.ax.set_xlabel('tempo (s)', color=_AXIS, fontsize=8)
        self._preview_mode = False

        if seg['type'] == 'breakpoints':
            self._struttura = 'breakpoints'
            self._interp    = seg.get('interp', 'linear')
        else:
            self._struttura = 'loop'
            self._loop_dist = seg.get('loop_dist', 'base')
            self._n_reps    = seg.get('n_reps', 4)
            self._ratio     = seg.get('ratio', 1.5)
            self._exponent  = seg.get('exponent', 2.0)

        # Aggiorna radio buttons e pannelli
        self._sync_controls_for_current_struttura()
        self._redraw()
        self._update_preview()

    def _on_seg_click(self, i: int):
        """Click su un chip segmento: salva il corrente, carica il nuovo."""
        if i == self._active_seg and not self._total_preview:
            return
        self._total_preview = False
        self._save_current_segment()
        self._active_seg = i
        self._load_segment(i)
        self._update_seg_chips()
        self.fig.canvas.draw()

    def _on_add_segment(self):
        """Mostra scelta tipo segmento (BP / Loop) in-figure."""
        self._show_overlay('add')

    # ── Overlay helpers ───────────────────────────────────────────────────

    def _show_overlay(self, mode: str):
        """Mostra i due bottoni overlay. mode: 'add' o 'delete'."""
        self._overlay_mode = mode
        self._ax_seg_add.set_visible(False)
        self._ax_total_preview.set_visible(False)
        if mode == 'add':
            self._btn_overlay_1.label.set_text('+ BP')
            self._btn_overlay_2.label.set_text('+ Loop')
            for ax in (self._ax_overlay_1, self._ax_overlay_2):
                ax.set_facecolor(_BTN_OK)
            for btn in (self._btn_overlay_1, self._btn_overlay_2):
                btn.ax.set_facecolor(_BTN_OK)
                btn.label.set_color('#ffffff')
        else:  # delete
            self._btn_overlay_1.label.set_text('✓ Conferma')
            self._btn_overlay_2.label.set_text('✗ Annulla')
            self._ax_overlay_1.set_facecolor('#8b0000')
            self._btn_overlay_1.ax.set_facecolor('#8b0000')
            self._btn_overlay_1.label.set_color('#ffffff')
            self._ax_overlay_2.set_facecolor('#2d2d2d')
            self._btn_overlay_2.ax.set_facecolor('#2d2d2d')
            self._btn_overlay_2.label.set_color(_FG)
        self._ax_overlay_1.set_visible(True)
        self._ax_overlay_2.set_visible(True)
        self.fig.canvas.draw_idle()

    def _dismiss_overlay(self):
        """Nasconde i bottoni overlay e ripristina + e Anteprima totale."""
        self._overlay_mode    = None
        self._del_pending_seg = None
        self._ax_overlay_1.set_visible(False)
        self._ax_overlay_2.set_visible(False)
        self._ax_seg_add.set_visible(True)
        self._ax_total_preview.set_visible(True)
        self._update_seg_chips()
        self.fig.canvas.draw_idle()

    def _on_overlay_click(self, btn_idx: int):
        if self._overlay_mode == 'add':
            seg_type = 'breakpoints' if btn_idx == 1 else 'loop'
            self._dismiss_overlay()
            self._do_add_segment(seg_type)
        elif self._overlay_mode == 'delete':
            pending = self._del_pending_seg
            self._dismiss_overlay()
            if btn_idx == 1 and pending is not None:
                self._do_delete_segment(pending)

    def _on_seg_delete_request(self, i: int):
        """Right-click su chip i: avvia la conferma di cancellazione."""
        self._del_pending_seg = i
        # Evidenzia il chip target
        ax_c = self._seg_chip_axes[i]
        ax_c.set_facecolor('#5a0000')
        self._seg_chip_btns[i].ax.set_facecolor('#5a0000')
        self._show_overlay('delete')

    def _do_add_segment(self, seg_type: str):
        """Aggiunge effettivamente un segmento del tipo dato."""
        if not self._undoing:
            self._push_undo()
        self._save_current_segment()
        if self._segments:
            last = self._segments[-1]
            if last['type'] == 'loop':
                t_start = last.get('abs_start', 0.0) + last.get('duration', 1.0)
            else:
                pts = last['points']
                t_start = max(t for t, v in pts) if pts else 0.0
        else:
            t_start = 0.0
        t_end = t_start + 1.0
        if seg_type == 'breakpoints':
            new_seg = {
                'type':     'breakpoints',
                'interp':   'linear',
                'points':   [[t_start, self.y_min], [t_end, self.y_max]],
                'end_time': t_end,
            }
        else:
            new_seg = {
                'type':      'loop',
                'loop_dist': 'base',
                'n_reps':    4,
                'ratio':     1.5,
                'exponent':  2.0,
                'abs_start': t_start,
                'duration':  1.0,
                'end_time':  t_end,
                # punti in % [0, 100] — convenzione GUI per loop in misto
                'points':    [[0.0, self.y_min], [100.0, self.y_max]],
            }
        self._segments.append(new_seg)
        self._active_seg = len(self._segments) - 1
        self._load_segment(self._active_seg)
        self._update_seg_chips()
        self.fig.canvas.draw()

    def _do_delete_segment(self, i: int):
        """Rimuove il segmento i (almeno 1 deve rimanere)."""
        if len(self._segments) <= 1:
            return
        if not self._undoing:
            self._push_undo()
        self._segments.pop(i)
        self._active_seg = min(self._active_seg, len(self._segments) - 1)
        self._load_segment(self._active_seg)
        self._update_seg_chips()
        self.fig.canvas.draw()

    def _on_total_preview_click(self):
        """Attiva/disattiva l'anteprima totale di tutti i segmenti."""
        if self._total_preview:
            # Torna al segmento attivo
            self._total_preview = False
            self._load_segment(self._active_seg)
            self._update_seg_chips()
            self.fig.canvas.draw()
            return
        self._save_current_segment()
        self._total_preview = True
        self._preview_mode  = False
        # Calcola xlim totale
        total_end = self._total_end_time()
        self.ax.set_xlim(0.0, total_end)
        self.ax.set_title(
            'Anteprima totale — sola lettura   '
            'Cmd+click: aggiungi   Drag: sposta   Destra: elimina   Scroll: zoom Y   Ctrl+Scroll: zoom X   Mid drag: pan   Z+drag: zoom area   F: fit   Dbl: reset',
            color='#ffd700', fontsize=8, pad=4,
        )
        self._update_seg_chips()
        self._redraw()
        self.fig.canvas.draw()

    def _total_end_time(self) -> float:
        """Calcola la fine assoluta dell'ultimo segmento."""
        end = 0.0
        for seg in self._segments:
            if seg['type'] == 'loop':
                end = max(end, seg.get('abs_start', 0.0) + seg.get('duration', 0.0))
            else:
                pts = seg.get('points', [])
                if pts:
                    end = max(end, max(t for t, v in pts))
        return end or self.end_time

    def _get_total_preview_data(self):
        """Genera la curva completa espandendo tutti i segmenti in coordinate assolute."""
        import numpy as np
        all_ts, all_vs = [], []

        for seg in self._segments:
            pts = sort_points(seg['points'])
            if not pts:
                continue
            ts = [p[0] for p in pts]
            vs = [p[1] for p in pts]

            if seg['type'] == 'breakpoints':
                interp = seg.get('interp', 'linear')
                if interp == 'cubic' and len(ts) >= 2:
                    t_dense = np.linspace(ts[0], ts[-1], _CURVE_DENSITY)
                    v_dense = _pchip(np.array(ts, float), np.array(vs, float), t_dense)
                    all_ts.extend(t_dense.tolist())
                    all_vs.extend(v_dense.tolist())
                else:
                    all_ts.extend(ts)
                    all_vs.extend(vs)

            else:  # loop
                abs_start = seg.get('abs_start', 0.0)
                duration  = seg.get('duration', seg.get('end_time', 1.0))
                n_reps    = seg.get('n_reps', 4)
                loop_dist = seg.get('loop_dist', 'base')
                ratio     = seg.get('ratio', 1.5)
                exponent  = seg.get('exponent', 2.0)
                use_cubic = loop_dist == 'cubic'
                if duration <= 0:
                    continue
                fracs = [t / 100.0 for t in ts]   # punti loop in % [0, 100]
                rep_times = _compute_rep_times(
                    duration, n_reps, loop_dist, ratio, exponent
                )
                for t_s, t_e in rep_times:
                    rep_dur = t_e - t_s
                    rep_ts  = [abs_start + t_s + f * rep_dur for f in fracs]
                    if use_cubic and len(rep_ts) >= 2:
                        t_dense = np.linspace(rep_ts[0], rep_ts[-1], _REP_PTS)
                        v_dense = _pchip(np.array(rep_ts, float), np.array(vs, float), t_dense)
                        all_ts.extend(t_dense.tolist())
                        all_vs.extend(v_dense.tolist())
                    else:
                        all_ts.extend(rep_ts)
                        all_vs.extend(vs)

        return all_ts, all_vs

    @staticmethod
    def _set_radio_visible(ax, visible: bool):
        """Nasconde/mostra un pannello RadioButtons in modo affidabile.

        ax.set_visible() non propaga sempre la visibilità ai figli in
        matplotlib 3.9 su backend MacOSX — nascondiamo ogni artist figlio
        esplicitamente per garantire che cerchi e label scompaiano davvero.
        """
        ax.set_visible(visible)
        for artist in ax.get_children():
            try:
                artist.set_visible(visible)
            except AttributeError:
                pass

    def _sync_controls_for_current_struttura(self):
        """Allinea radio buttons e visibilità pannelli allo stato corrente."""
        is_loop = self._struttura == 'loop'

        # Accordion headers
        self._update_struttura_header_style(self._struttura)

        # Interpolazione
        interp_labels = [l.get_text() for l in self.radio_interp.labels]
        interp_target = self._interp.capitalize()
        if interp_target in interp_labels:
            self.radio_interp.set_active(interp_labels.index(interp_target))

        # Distribuzione loop
        loop_rev = {v: k for k, v in self._LOOP_DIST_KEYS.items()}
        loop_target = loop_rev.get(self._loop_dist, 'Base')
        loop_labels = [l.get_text() for l in self.radio_loop.labels]
        if loop_target in loop_labels:
            self.radio_loop.set_active(loop_labels.index(loop_target))

        # Visibilità mutuamente esclusiva — nascondi tutti gli artist figli
        self._set_radio_visible(self._ax_interp, not is_loop)
        self._set_radio_visible(self._ax_loop, is_loop)

        if not is_loop:
            self._ax_interp.set_title('Interpolazione', color=_FG, fontsize=9, pad=3)
            for lbl in self.radio_interp.labels:
                lbl.set_color(_FG)
        else:
            self._ax_loop.set_title('Distribuzione loop', color=_FG, fontsize=9, pad=3)
            for lbl in self.radio_loop.labels:
                lbl.set_color(_FG)

        # Parametri loop (incluso end_time)
        self._set_loop_params_visible(is_loop)
        if is_loop and self._loop_dist in ('geometrico', 'power'):
            self._lbl_extra.set_text('ratio:' if self._loop_dist == 'geometrico' else 'esponente:')
            val = self._ratio if self._loop_dist == 'geometrico' else self._exponent
            self.txt_extra.set_val(str(val))

        # n_reps
        self.txt_nreps.set_val(str(self._n_reps))

        self.fig.canvas.draw_idle()

    def _on_struttura_change(self, label: str):
        """Converte il tipo del segmento attivo (BP ↔ Loop)."""
        if not self._undoing:
            self._push_undo()
        self._convert_segment_type(label.lower().rstrip())

    def _convert_segment_type(self, new_type: str):
        """
        Cambia il tipo del segmento attivo adattando i punti:
        - BP → Loop: X normalizzato in [0, 100]% (t_min → 0, t_max → 100)
        - Loop → BP: X da % a tempo assoluto [abs_start, abs_start+duration]
        """
        seg = self._segments[self._active_seg]
        if seg['type'] == new_type:
            return

        pts = sort_points(seg['points'])

        if new_type == 'loop':          # Breakpoints → Loop
            ts = [p[0] for p in pts]
            t_min, t_max = min(ts), max(ts)
            t_span = (t_max - t_min) or 1.0
            pts_new = [((t - t_min) / t_span * 100.0, v) for t, v in pts]
            abs_start = t_min
            duration  = seg.get('end_time', self.end_time) - abs_start
            if duration <= 0:
                duration = 1.0
            seg.clear()
            seg.update({
                'type':      'loop',
                'loop_dist': self._loop_dist,
                'n_reps':    self._n_reps,
                'ratio':     self._ratio,
                'exponent':  self._exponent,
                'abs_start': abs_start,
                'duration':  duration,
                'end_time':  abs_start + duration,
                'points':    pts_new,
            })
        else:                           # Loop → Breakpoints
            abs_start = seg.get('abs_start', 0.0)
            duration  = seg.get('duration', 1.0)
            end_time  = seg.get('end_time', abs_start + duration)
            pts_new = [(abs_start + t / 100.0 * duration, v) for t, v in pts]
            seg.clear()
            seg.update({
                'type':     'breakpoints',
                'interp':   self._interp,
                'points':   pts_new,
                'end_time': end_time,
            })

        self._load_segment(self._active_seg)
        self._update_seg_chips()
        self.fig.canvas.draw()

    def _on_interp_change(self, label: str):
        if not self._undoing:
            self._push_undo()
        self._interp = label.lower()
        self._redraw()
        self._update_preview()

    def _on_loop_dist_change(self, label: str):
        if not self._undoing:
            self._push_undo()
        self._loop_dist = self._LOOP_DIST_KEYS[label]
        # aggiorna etichetta parametro extra
        if self._loop_dist == 'geometrico':
            self._lbl_extra.set_text('ratio:')
            self.txt_extra.set_val(str(self._ratio))
        elif self._loop_dist == 'power':
            self._lbl_extra.set_text('esponente:')
            self.txt_extra.set_val(str(self._exponent))
        self._set_loop_params_visible(True)
        self._redraw()
        self._update_preview()
        self.fig.canvas.draw_idle()

    def _on_nreps_change(self, text: str):
        try:
            self._n_reps = max(1, int(text))
        except ValueError:
            pass
        if self._preview_mode:
            if self._struttura == 'loop':
                seg = self._segments[self._active_seg]
                abs_start = seg.get('abs_start', 0.0)
                duration  = seg.get('duration', 1.0)
                rep_times = _compute_rep_times(
                    duration, self._n_reps, self._loop_dist,
                    self._ratio, self._exponent,
                )
                total_end = rep_times[-1][1] if rep_times else duration
                self.ax.set_xlim(abs_start, abs_start + total_end)
            self._redraw()
        self._update_preview()

    def _on_extra_change(self, text: str):
        try:
            val = float(text)
            if self._loop_dist == 'geometrico':
                self._ratio = val
            elif self._loop_dist == 'power':
                self._exponent = val
        except ValueError:
            pass
        if self._preview_mode:
            self._redraw()
        self._update_preview()

    def _on_ok(self, _event):
        self._result = self._compute_output()
        self._plt.close(self.fig)

    def _on_cancel(self, _event):
        self._result = ''
        self._plt.close(self.fig)

    # -------------------------------------------------------------------------
    # Output
    # -------------------------------------------------------------------------

    def _compute_output(self) -> str:
        """
        Auto-detect sintassi di output:
          1 segmento BP   → breakpoints / dict (step/cubic)
          1 segmento Loop → compact loop
          tutto il resto  → formato misto
        """
        self._save_current_segment()
        segs = self._segments
        if len(segs) == 1:
            seg = segs[0]
            pts = sort_points(seg['points'])
            if seg['type'] == 'breakpoints':
                interp   = seg.get('interp', 'linear')
                end_time = seg.get('end_time', self.end_time)
                if interp == 'step':
                    return to_dict_type(pts, end_time, 'step')
                elif interp == 'cubic':
                    return to_dict_type(pts, end_time, 'cubic')
                else:
                    return to_breakpoints(pts, end_time)
            else:  # loop
                duration  = seg.get('duration', 1.0)
                n_reps    = seg.get('n_reps', self._n_reps)
                loop_dist = seg.get('loop_dist', self._loop_dist)
                ratio     = seg.get('ratio', self._ratio)
                exponent  = seg.get('exponent', self._exponent)
                # Punti in [0, 100] % → converti in [0, duration] per il formatter
                pts_abs = [(t / 100.0 * duration, v) for t, v in pts]
                return to_compact_loop_full(
                    pts_abs, duration, n_reps, loop_dist, ratio, exponent,
                )
        # Più segmenti (o mix di tipi) → formato misto
        return to_misto_format(segs)

    # -------------------------------------------------------------------------
    # Rendering
    # -------------------------------------------------------------------------

    def _get_curve_data(self, ts, vs):
        """
        Restituisce (curve_ts, curve_vs, drawstyle) per la linea del canvas.
        In preview mode ritorna il loop completo; altrimenti il pattern singolo.
        """
        import numpy as np

        if len(ts) < 2:
            return ts, vs, 'default'

        # ── Preview loop: espande il pattern in n_reps cicli ──────────────
        if self._struttura == 'loop' and self._preview_mode:
            return self._get_full_loop_data(ts, vs)

        # ── Pattern singolo ───────────────────────────────────────────────
        if self._struttura == 'loop':
            use_cubic = self._loop_dist == 'cubic'
            use_step  = False
        else:
            use_cubic = self._interp == 'cubic'
            use_step  = self._interp == 'step'

        if use_step:
            return ts, vs, 'steps-post'
        if use_cubic:
            t_dense = np.linspace(ts[0], ts[-1], _CURVE_DENSITY)
            v_dense = _pchip(np.array(ts, float), np.array(vs, float), t_dense)
            return t_dense.tolist(), v_dense.tolist(), 'default'
        return ts, vs, 'default'

    def _get_full_loop_data(self, ts, vs):
        """
        Genera la curva del loop completo espandendo il pattern per n_reps cicli.
        Misto mode: punti in % [0,100], si usa la duration reale del segmento e
        si aggiunge l'offset abs_start per il tempo assoluto.
        Puro: punti in [0, end_time], nessun offset.
        """
        import numpy as np

        seg       = self._segments[self._active_seg]
        duration  = seg.get('duration', 1.0)
        abs_start = seg.get('abs_start', 0.0)
        fracs     = [t / 100.0 for t in ts]   # punti in % [0, 100]

        rep_times = _compute_rep_times(
            duration, self._n_reps, self._loop_dist,
            self._ratio, self._exponent,
        )
        use_cubic = self._loop_dist == 'cubic'

        all_ts, all_vs = [], []
        for t_start, t_end in rep_times:
            rep_dur = t_end - t_start
            rep_ts  = [abs_start + t_start + f * rep_dur for f in fracs]
            rep_vs  = list(vs)

            if use_cubic and len(rep_ts) >= 2:
                t_dense = np.linspace(rep_ts[0], rep_ts[-1], _REP_PTS)
                v_dense = _pchip(np.array(rep_ts, float), np.array(rep_vs, float), t_dense)
                all_ts.extend(t_dense.tolist())
                all_vs.extend(v_dense.tolist())
            else:
                all_ts.extend(rep_ts)
                all_vs.extend(rep_vs)

        return all_ts, all_vs, 'default'

    def _redraw(self):
        # Rimuovi linee verticali cicli precedenti
        for vl in getattr(self, '_rep_vlines', []):
            try:
                vl.remove()
            except ValueError:
                pass
        self._rep_vlines = []

        # ── Anteprima totale (tutti i segmenti concatenati) ───────────────
        if self._total_preview:
            all_ts, all_vs = self._get_total_preview_data()
            # Vlines ai confini dei segmenti loop
            for seg in self._segments:
                if seg['type'] == 'loop':
                    abs_end = seg.get('abs_start', 0.0) + seg.get('duration', 0.0)
                    vl = self.ax.axvline(abs_end, color='#5a5a5a',
                                         linestyle='--', linewidth=1, alpha=0.8)
                    self._rep_vlines.append(vl)
            self.line.set_data(all_ts, all_vs)
            self.line.set_drawstyle('default')
            self.scatter.set_visible(False)
            self.fig.canvas.draw_idle()
            return

        pts = sort_points(self.points)
        ts  = [p[0] for p in pts]
        vs  = [p[1] for p in pts]

        if self._struttura == 'loop' and self._preview_mode:
            seg       = self._segments[self._active_seg]
            duration  = seg.get('duration', 1.0)
            abs_start = seg.get('abs_start', 0.0)
            rep_times = _compute_rep_times(
                duration, self._n_reps, self._loop_dist,
                self._ratio, self._exponent,
            )
            for _, t_end in rep_times[:-1]:
                vl = self.ax.axvline(abs_start + t_end, color='#5a5a5a',
                                     linestyle='--', linewidth=1, alpha=0.8)
                self._rep_vlines.append(vl)

        # Curva
        c_ts, c_vs, drawstyle = self._get_curve_data(ts, vs)
        self.line.set_data(c_ts, c_vs)
        self.line.set_drawstyle(drawstyle)

        # Scatter: visibile in edit mode, nascosto in preview mode
        if self._struttura == 'loop' and self._preview_mode:
            self.scatter.set_visible(False)
        else:
            self.scatter.set_visible(True)
            colors = [_SEL if i == self._sel else _PT for i in range(len(pts))]
            self.scatter.set_offsets(list(zip(ts, vs)))
            self.scatter.set_color(colors)

        self.fig.canvas.draw_idle()

    def _update_preview(self):
        text = self._compute_output()
        # Wrap a 38 char per riga, max 5 righe — evita overflow fuori dai bounds
        chars_per_line, max_lines = 38, 5
        lines, remaining = [], text
        while remaining and len(lines) < max_lines:
            lines.append(remaining[:chars_per_line])
            remaining = remaining[chars_per_line:]
        if remaining:
            lines[-1] = lines[-1][:-3] + '...'
        self.preview_txt.set_text('\n'.join(lines))
        self.fig.canvas.draw_idle()

    # -------------------------------------------------------------------------
    # Entry point
    # -------------------------------------------------------------------------

    def run(self) -> str:
        self._plt.show()
        return self._result


# =============================================================================
# ENTRY POINT
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description='PGE Envelope GUI Editor')
    parser.add_argument('--ymin',      type=float,  default=0.0,  help='Valore minimo Y')
    parser.add_argument('--ymax',      type=float,  default=1.0,  help='Valore massimo Y')
    parser.add_argument('--end_time',  type=float,  default=10.0, help='Tempo finale (s)')
    # Stato iniziale opzionale (usato quando si apre un envelope esistente)
    parser.add_argument('--points',    type=str,    default='',   help='JSON [[t,v],...]')
    parser.add_argument('--struttura', type=str,    default='breakpoints')
    parser.add_argument('--interp',    type=str,    default='linear')
    parser.add_argument('--loop-dist', type=str,    default='base', dest='loop_dist')
    parser.add_argument('--nreps',     type=int,    default=4)
    parser.add_argument('--ratio',     type=float,  default=1.5)
    parser.add_argument('--exponent',  type=float,  default=2.0)
    parser.add_argument('--segments',  type=str,    default='',
                        help='JSON lista segmenti per formato misto')
    args = parser.parse_args()

    try:
        import matplotlib.pyplot  # noqa: F401
    except ImportError:
        print('ERROR: matplotlib non disponibile. Esegui: pip install matplotlib',
              file=sys.stderr)
        sys.exit(1)

    import json

    # Modalità misto: --segments ha la precedenza su --points
    segments = None
    if args.segments:
        try:
            segments = json.loads(args.segments)
        except Exception:
            pass

    # Punti iniziali (usati solo in modalità non-misto)
    initial_points = None
    if not segments and args.points:
        try:
            raw = json.loads(args.points)
            initial_points = [(float(t), float(v)) for t, v in raw]
        except Exception:
            pass

    editor = EnvelopeEditor(
        args.ymin, args.ymax, args.end_time,
        initial_points=initial_points,
        struttura=args.struttura,
        interp=args.interp,
        loop_dist=args.loop_dist,
        n_reps=args.nreps,
        ratio=args.ratio,
        exponent=args.exponent,
        segments=segments,
    )
    result = editor.run()
    if result:
        print(result)


if __name__ == '__main__':
    main()
