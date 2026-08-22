# granular_ls/providers/diagnostic_provider.py
"""
DiagnosticProvider - Analizza l'intero documento YAML e segnala problemi.

Differenza rispetto agli altri provider:
    Lavora sull'intero documento (non su un punto del cursore).
    Viene chiamato ogni volta che il testo cambia.
    Produce una lista completa di tutti i problemi trovati.

Due tipi di controllo:
    1. EXCLUSIVE_GROUP: due parametri mutuamente esclusivi presenti insieme.
       Severita' Warning: l'utente potrebbe volerlo intenzionalmente,
       ma quasi sempre e' un errore di configurazione.

    2. VALUE OUT OF BOUNDS: valore numerico fuori da [min_val, max_val].
       Severita' Error: il motore granulare rifiutera' o clampera' il valore.

Algoritmo in tre fasi:
    1. _parse_document(): estrae coppie (yaml_path, valore, n_riga)
    2. _check_exclusive_groups(): cerca violazioni di mutua esclusivita'
    3. _check_bounds(): cerca valori numerici fuori range
"""

import os
import re
import wave
from typing import Dict, List, Optional, Tuple

import yaml

from lsprotocol.types import (
    Diagnostic,
    DiagnosticSeverity,
    DiagnosticTag,
    Position,
    Range,
)

from granular_ls.envelope_shapes import (
    VALID_INTERP_TYPES,
    is_bp_group,
    is_bp_group_candidate,
    is_loop_block,
    is_valid_point,
)
from granular_ls.schema_bridge import SchemaBridge, ParameterInfo
from granular_ls.voice_strategies import (
    VOICE_STRATEGY_REGISTRY,
    VOICE_DIMENSIONS,
    VOICE_ENVELOPE_KEYS,
    CHORD_INTERVALS,
    get_strategy_spec,
)
from granular_ls.read_direction import (
    EXCLUSIVE_HINT,
    READ_DIRECTION_PATH,
    check_read_direction,
)
from granular_ls.deviation_probability import (
    DEVIATION_PROBABILITY_PATH,
    check_global_value,
)
from granular_ls.pitch_units import (
    PITCH_UNIT_KEYS,
    PITCH_UNIT_PRESETS,
    PITCH_BLOCK_KEYS,
    SEMITONE_LOCKED_STRATEGIES,
    VOICE_PITCH_UNIT_VALUES,
    get_unit_info,
    parse_edo_divisions,
    parse_voice_unit_value,
    split_inline_mapping,
)

# Identificatore del nostro Language Server nei diagnostici.
# VSCode lo mostra accanto al messaggio per indicare la fonte.
SOURCE = 'granular-ls'

# Il blocco pitch e' unit-driven (PGE PR #84): la sua superficie non vive
# piu' nello schema del bridge ma nel registry pitch_units, e la validazione
# in _check_pitch_block. Le spec 'pitch.*' di snapshot legacy vengono escluse
# dai controlli generici per evitare doppie segnalazioni con bounds obsoleti.
_PITCH_OWNED_PREFIX = 'pitch.'

# grain.read_direction (PGE #207): il bridge la espone con bounds [-1, 1], ma
# il dominio e' l'insieme {-1, +1} e lo schema non ha modo di dirlo — un check
# generico accetterebbe 0 e 0.5, che il motore rifiuta. Stessa cosa per lo
# 'step' imposto e per il gruppo esclusivo 'grain_direction', che qui e' un
# errore e non una priorita'. Il check dedicato e' _check_read_direction; il
# path e' escluso dai generici per non dire due volte cose diverse sullo
# stesso problema, come gia' per il blocco pitch.
_READ_DIRECTION_OWNED = frozenset({READ_DIRECTION_PATH})


def _is_generically_checkable(yaml_path: str) -> bool:
    """False per i parametri che hanno un check dedicato e lo escludono.

    La superficie di questi parametri non e' descrivibile dal solo schema del
    bridge: i controlli generici (bounds, valore mancante, exclusive group)
    ne direbbero qualcosa di vero ma insufficiente, e la diagnostica giusta
    arriverebbe accanto a una sbagliata.
    """
    return (not yaml_path.startswith(_PITCH_OWNED_PREFIX)
            and yaml_path not in _READ_DIRECTION_OWNED)

# grain.duration_unit (PGE #158, terza unità in v5.2.0): unità di misura di
# grain.duration e grain.duration_range. Meta-parametro, mirror di loop_unit
# del pointer. output_sr è una config globale del motore (48000 Hz), non
# impostabile per-stream: qui è una costante statica come lato PGE
# (DEFAULT_OUTPUT_SR).
_GRAIN_DURATION_UNITS = ('seconds', 'samples', 'milliseconds')
_OUTPUT_SR = 48000

# Quanti secondi vale una unità, per le unità che vanno convertite. Il
# fattore, non due rami paralleli: 'samples' dipende da output_sr,
# 'milliseconds' no (lato PGE è SECONDS_PER_MILLISECOND, costante), e
# modellarli come lo stesso conto con un fattore diverso è ciò che tiene
# insieme bounds, soppressione dei falsi positivi e messaggi.
_GRAIN_DURATION_UNIT_SECONDS = {
    'samples': 1.0 / _OUTPUT_SR,
    'milliseconds': 1e-3,
}

# Come si chiamano i valori di quell'unità, nei messaggi.
_GRAIN_DURATION_UNIT_LABELS = {
    'samples': 'campioni',
    'milliseconds': 'millisecondi',
}


class DiagnosticProvider:
    """
    Analizza un documento YAML completo e produce diagnostici LSP.

    Costruzione:
        provider = DiagnosticProvider(bridge)

    Uso:
        diagnostics = provider.get_diagnostics(document_text)
    """

    def __init__(self, bridge: SchemaBridge, refs_dir: str = ''):
        self._bridge = bridge
        self._refs_dir = refs_dir  # path assoluto a refs/ del progetto PGE

        # Indice yaml_path -> ParameterInfo per lookup O(1).
        # Costruito una volta in __init__, non a ogni chiamata.
        # I parametri 'pitch.*' sono esclusi: il blocco e' unit-driven.
        self._params_by_yaml_path: Dict[str, ParameterInfo] = {
            p.yaml_path: p
            for p in bridge.get_all_parameters()
            if not p.is_internal
            and _is_generically_checkable(p.yaml_path)
        }

    def get_diagnostics(self, document_text: str) -> List[Diagnostic]:
        """
        Analizza il documento e ritorna tutti i diagnostici trovati.

        Returns:
            Lista di Diagnostic LSP. Mai None. Mai eccezioni.
        """
        try:
            return self._analyze(document_text)
        except Exception:
            return []

    def _analyze(self, document_text: str) -> List[Diagnostic]:
        if not document_text:
            return []

        # Scansione UNICA dei confini stream: tutte le fasi per-stream
        # ricevono questa lista invece di rifare in proprio la ricerca
        # dei marcatori '- ' a indent 2.
        lines = document_text.split('\n')
        streams = self._find_stream_blocks(lines)

        # Fase 1: parsing riga per riga.
        # parsed = lista di (yaml_path, valore_str, n_riga, indent_level)
        parsed = self._parse_document(document_text)

        diagnostics = []

        # Fase 2: controllo chiavi duplicate nello stesso stream.
        diagnostics.extend(self._check_duplicate_keys(lines, streams))

        # Fase 3: controllo campi obbligatori per ogni stream.
        diagnostics.extend(self._check_mandatory_stream_fields(lines, streams))

        # Fase 3b: parametri numerici senza valore.
        diagnostics.extend(self._check_missing_values(document_text))

        # Fase 3c: sbiadisce stream muted e stream non-solo.
        diagnostics.extend(self._check_muted_streams(lines, streams))
        diagnostics.extend(self._check_solo_streams(lines, streams))

        # Fase 3: controllo exclusive_group.
        diagnostics.extend(self._check_exclusive_groups(parsed))

        # Blocchi grain (PGE #158): scansione unica riusata da fase 4, 5 e 11.
        grain_blocks = self._scan_grain_blocks(lines, streams)
        # Righe di grain.duration/duration_range in un'unità non-secondi: i
        # loro valori sono campioni o millisecondi → esclusi dai bound generici.
        scaled_lines = self._scaled_unit_suppressed_lines(grain_blocks)

        # Fase 4: controllo bounds numerici (valori scalari).
        diagnostics.extend(
            d for d in self._check_bounds(parsed)
            if d.range.start.line not in scaled_lines
        )

        # Fase 5: controllo bounds nei valori envelope (breakpoints Y).
        diagnostics.extend(
            d for d in self._check_envelope_bounds(document_text)
            if d.range.start.line not in scaled_lines
        )

        # Fase 5d: validazione BP group [points, interp] (PGE #64).
        diagnostics.extend(self._check_bp_groups(lines))

        # Fase 5b: validazione grain.envelope (finestratura del grano).
        diagnostics.extend(self._check_grain_envelope(document_text))

        # Fase 5c: validazione del blocco pitch (superficie unit-driven).
        diagnostics.extend(self._check_pitch_block(lines, streams))

        # Fase 6: validazione del blocco voices (strategy, kwargs, enum).
        diagnostics.extend(self._check_voice_strategies(lines, streams))

        # Fase 7: start bypassato da loop_start envelope.
        diagnostics.extend(self._check_start_bypassed_by_loop_start(lines, streams))

        # Fase 8: loop_dur e loop_end presenti insieme (loop_dur ha priorita').
        diagnostics.extend(self._check_loop_dur_overrides_loop_end(lines, streams))

        # Fase 9: bounds dinamici per i parametri pointer (normalized vs absolute).
        diagnostics.extend(self._check_pointer_param_bounds(
            document_text, lines, streams, self._refs_dir,
        ))

        # Fase 10: loop_end <= loop_start (finestra di loop degenere).
        diagnostics.extend(self._check_loop_end_le_loop_start(lines, streams))

        # Fase 11: grain.duration_unit (PGE #158).
        diagnostics.extend(self._check_grain_duration_unit(grain_blocks))

        # Fase 12: rng_group non-scalare (PGE #169).
        diagnostics.extend(self._check_rng_group_type(lines))

        # Fase 13: range_anchor fuori enum (PGE range-anchor-mode).
        diagnostics.extend(self._check_range_anchor(lines))

        # Fase 14: grain.read_direction (PGE #207) — dominio a due valori,
        # 'step' imposto, gruppo esclusivo con grain.reverse.
        diagnostics.extend(self._check_read_direction(lines, grain_blocks))

        # Fase 15: corpo di deviation_probability che non si costruisce come
        # envelope (PGE #209). Prima diventava un AlwaysGate in silenzio.
        diagnostics.extend(
            self._check_deviation_probability(lines, streams)
        )

        # Fase 16: tetto della banda sotto range_anchor: min.
        diagnostics.extend(
            self._check_band_ceiling(lines, streams, grain_blocks)
        )

        return diagnostics



    def _check_duplicate_keys(
        self, lines: List[str],
        streams: List[Tuple[int, int, dict]],
    ) -> List[Diagnostic]:
        """
        Controlla chiavi duplicate nello stesso stream.

        Per ogni stream raccoglie le chiavi di primo livello (indent 2)
        e di blocco (indent 3, es. pitch:, grain:, pointer:).
        Se una chiave compare piu' di una volta produce un Error
        su tutte le occorrenze.

        Chiavi permesse in stream diversi: non sono duplicate.
        """
        diagnostics = []

        for start, end, _keys in streams:
            # Raccoglie (path_completo, n_riga) per questo stream.
            # Il path include il blocco padre per evitare falsi positivi:
            # 'duration' a livello stream != 'deviation_probability.duration' != 'grain.duration'
            key_occurrences: dict = {}  # path -> [n_riga, ...]

            # Traccia il blocco corrente (grain, pitch, pointer, deviation_probability, ...)
            current_block = None
            current_block_indent = -1

            for n in range(start, end + 1):
                raw = lines[n]
                stripped = raw.strip()
                leading = len(raw) - len(raw.lstrip())

                # Salta righe vuote, commenti
                if not stripped or stripped.startswith('#'):
                    continue

                # Salta breakpoints e liste envelope
                if stripped.startswith('- [') or stripped.startswith('- {'):
                    continue

                # Riga con marcatore stream: estrai chiave se presente
                inner = stripped
                if stripped.startswith('- ') and leading == 2:
                    inner = stripped[2:].strip()

                # Aggiorna il blocco corrente:
                # se torniamo a indent <= indent del blocco, usciamo dal blocco
                if current_block and leading <= current_block_indent:
                    current_block = None
                    current_block_indent = -1

                # Estrai chiave dalla riga
                if ':' not in inner:
                    continue
                key = inner[:inner.index(':')].strip()
                if not key or not all(c.isalnum() or c == '_' for c in key):
                    continue

                # Solo livelli significativi:
                # indent 4 = parametri diretti dello stream (o dopo '- ')
                # indent 6 = parametri dentro un blocco (grain, pitch, ...)
                if leading == 4 or (leading == 2 and stripped.startswith('- ')):
                    # Controlla se e' un blocco (nessun valore sulla riga)
                    after_colon = inner[inner.index(':') + 1:].strip()
                    is_block = not after_colon or after_colon.startswith('#')
                    if is_block:
                        current_block = key
                        current_block_indent = leading
                    # Path = chiave semplice a livello stream
                    path = key

                elif leading == 6 and current_block:
                    # Path = blocco.chiave (es. 'deviation_probability.duration', 'grain.duration')
                    path = current_block + '.' + key

                else:
                    # Livelli piu' profondi (envelope dict, points, ecc.): ignora
                    continue

                if path not in key_occurrences:
                    key_occurrences[path] = []
                key_occurrences[path].append(n)

            # Identifica i blocchi duplicati (chiavi senza punto, es. 'grain', 'pitch')
            # Le loro chiavi interne (es. 'grain.duration') non vengono segnalate
            # separatamente: basta segnalare il blocco padre.
            duplicate_blocks = {
                key for key, occ in key_occurrences.items()
                if len(occ) >= 2 and '.' not in key
            }

            # Segnala duplicati
            for key, occurrences in key_occurrences.items():
                if len(occurrences) < 2:
                    continue
                # Salta chiavi interne a blocchi gia' segnalati come duplicati
                if '.' in key:
                    parent_block = key.split('.')[0]
                    if parent_block in duplicate_blocks:
                        continue
                for n in occurrences:
                    diagnostics.append(Diagnostic(
                        range=Range(
                            start=Position(line=n, character=0),
                            end=Position(line=n, character=len(lines[n])),
                        ),
                        message=(
                            f"Chiave duplicata '{key}' nello stesso stream. "
                            f"Ogni chiave puo' apparire una sola volta."
                        ),
                        severity=DiagnosticSeverity.Error,
                        source='pge-ls',
                    ))

        return diagnostics

    # -------------------------------------------------------------------------
    # CONTROLLO CAMPI OBBLIGATORI
    # -------------------------------------------------------------------------

    # Le condizioni di esistenza di uno stream sono due (PGE #220).
    # Fuori da queste due ci sono i campi che il motore sa risolvere da solo:
    # `duration` assente vale la durata del file audio dichiarato in `sample`
    # (PGE #205), `onset` assente vale l'origine della timeline — 0 non e'
    # "nulla", e' l'origine (PGE #220). Uno stream a riposo e' il sample:
    # stessa origine, stessa durata, contenuto risintetizzato.
    _MANDATORY_FIELDS = ['stream_id', 'sample']

    def _check_mandatory_stream_fields(
        self, lines: List[str],
        streams: List[Tuple[int, int, dict]],
    ) -> List[Diagnostic]:
        """
        Controlla che ogni elemento della lista streams abbia i due
        campi obbligatori: stream_id, sample.

        Produce un Warning per ogni campo mancante, puntando alla riga
        del marcatore '- ' dello stream.

        Le chiavi presenti vengono lette dal dict gia' raccolto da
        _find_stream_blocks (stessa logica di estrazione).
        """
        diagnostics = []

        for start_line, _end, present_keys in streams:
            # Produce Warning per ogni campo mancante
            for field in self._MANDATORY_FIELDS:
                if field not in present_keys:
                    diagnostics.append(Diagnostic(
                        range=Range(
                            start=Position(line=start_line, character=0),
                            end=Position(line=start_line,
                                         character=len(lines[start_line])),
                        ),
                        message=(
                            f"Campo obbligatorio mancante nello stream: '{field}'. "
                            f"Ogni stream deve avere: "
                            f"{', '.join(self._MANDATORY_FIELDS)}."
                        ),
                        severity=DiagnosticSeverity.Warning,
                        source='pge-ls',
                    ))

        return diagnostics

    # -------------------------------------------------------------------------
    # CONTROLLO VALORI MANCANTI
    # -------------------------------------------------------------------------

    # Chiavi stringa che richiedono un valore se presenti (non hanno bounds numerici).
    _STRING_REQUIRED_KEYS = frozenset({
        'time_mode',
        'loop_unit',
        'duration_unit',
        'distribution_mode',
        # rng_group (PGE #169): vuoto e' un silent no-op — l'engine ricade
        # sull'identita' stream_id e la sequenza non viene condivisa.
        'rng_group',
    })

    # Campi obbligatori dello stream che richiedono sempre un valore.
    # `duration` (PGE #205) e `onset` (PGE #220) sono fuori, per lo stesso
    # motivo: la chiave scritta e lasciata vuota e' `null`, che per il motore
    # vale come chiave assente, non come dichiarazione lasciata a meta'.
    _STREAM_VALUE_REQUIRED = frozenset({
        'stream_id', 'sample',
    })

    def _build_voice_required_paths(self) -> 'frozenset[str]':
        """
        Costruisce l'insieme di yaml_path che richiedono un valore
        nel blocco voices: strategy + tutti i kwargs di tutte le strategy.

        Esempio: 'voices.pitch.strategy', 'voices.pitch.step',
                 'voices.pan.spread', ecc.
        """
        paths: set = set()
        for dim, strategies in VOICE_STRATEGY_REGISTRY.items():
            paths.add(f'voices.{dim}.strategy')
            for spec in strategies.values():
                for kwarg_name, kwarg_spec in spec.kwargs.items():
                    # I kwarg di tipo lista (es. progression) hanno il valore su
                    # righe successive (block list): non sono "senza valore".
                    # La lista vuota è validata da _check_chord_progression.
                    if kwarg_spec.type == 'list':
                        continue
                    paths.add(f'voices.{dim}.{kwarg_name}')
        return frozenset(paths)

    def _check_missing_values(self, document_text: str) -> List[Diagnostic]:
        """
        Segnala chiavi scritte senza valore quando un valore e' obbligatorio.

        Categorie controllate:
          1. Parametri numerici del bridge (min_val != None): richiedono float
             o envelope [[t, v], ...].
          2. voices.num_voices e voices.scatter: bounds via get_raw_bounds.
          3. Campi obbligatori stream (stream_id, sample):
             richiedono qualsiasi valore.
          4. Chiavi stringa obbligatorie (time_mode, loop_unit, ...):
             richiedono un valore stringa.
          5. Voice kwargs e strategy: richiedono un valore.

        Usa uno stack di blocchi per il contesto gerarchico, evitando falsi
        positivi su chiavi omonime a livelli diversi (es. 'pan' dentro
        'voices' != 'pan' a livello stream).

        Chiavi non in nessuna categoria sono trattte come blocchi-contenitore.
        Esclusioni naturali: solo, mute (min_val=None, non nelle liste sopra).
        """
        diagnostics = []
        lines = document_text.split('\n')

        # Chiavi per cui il valore null (riga 'key:' senza niente) è semanticamente
        # valido secondo il motore PGE. Non vengono segnalate come "manca il valore".
        # Esempio: 'grain.reverse:' senza valore → forzato sempre reverse.
        NULL_VALID_PATHS: frozenset = frozenset({'grain.reverse'})

        # 1. Parametri numerici del bridge (pitch.* escluso: unit-driven)
        numeric_yaml_paths: set = {
            p.yaml_path
            for p in self._bridge.get_all_parameters()
            if p.min_val is not None and not p.is_internal
            and _is_generically_checkable(p.yaml_path)
        }

        # 2. voices.num_voices e voices.scatter (bounds via get_raw_bounds)
        for param_name in VOICE_ENVELOPE_KEYS:
            b = self._bridge.get_raw_bounds(param_name)
            if b and b.get('min_val') is not None:
                numeric_yaml_paths.add('voices.' + param_name)

        # 5. Voice strategy + kwargs
        voice_required_paths = self._build_voice_required_paths()

        # Stack di (yaml_path_prefix, indent_del_blocco)
        block_stack: List[Tuple[str, int]] = []
        in_stream: bool = False

        i = 0
        while i < len(lines):
            raw = lines[i]
            stripped = raw.strip()
            leading = len(raw) - len(raw.lstrip())

            if not stripped or stripped.startswith('#'):
                i += 1
                continue

            # Marcatore stream (indent 2): reset completo del contesto
            if leading == 2 and (stripped.startswith('- ') or stripped == '-'):
                block_stack.clear()
                in_stream = True
                if stripped.startswith('- '):
                    stripped = stripped[2:].strip()
                    leading = 4  # contenuto inline trattato come indent 4
                else:
                    i += 1
                    continue

            if not in_stream:
                i += 1
                continue

            # Pop blocchi con indentazione >= quella corrente
            while block_stack and leading <= block_stack[-1][1]:
                block_stack.pop()

            current_prefix = block_stack[-1][0] if block_stack else None

            # Rimuovi commenti inline
            if '#' in stripped:
                stripped = stripped[:stripped.find('#')].rstrip()
            if not stripped:
                i += 1
                continue

            # Cerca chiave SENZA valore inline: 'key:'
            m = re.match(r'^([a-zA-Z_][a-zA-Z0-9_]*)\s*:\s*$', stripped)
            if not m:
                i += 1
                continue

            key = m.group(1)
            yaml_path = (current_prefix + '.' + key) if current_prefix else key

            if yaml_path in numeric_yaml_paths:
                # Chiavi per cui null è sintassi valida: non controllare il valore.
                if yaml_path in NULL_VALID_PATHS:
                    pass  # 'key:' senza valore è intenzionale (es. grain.reverse)

                else:
                    # Parametro numerico: accetta envelope lista (- [...]) o
                    # dict (type:/points:) — qualsiasi contenuto più indentato.
                    has_value = False
                    j = i + 1
                    while j < len(lines):
                        nxt = lines[j]
                        nxt_s = nxt.strip()
                        nxt_l = len(nxt) - len(nxt.lstrip())
                        if not nxt_s or nxt_s.startswith('#'):
                            j += 1
                            continue
                        if nxt_l <= leading:
                            break
                        # Qualsiasi riga non-vuota più indentata = valore presente
                        has_value = True
                        break

                    if not has_value:
                        diagnostics.append(Diagnostic(
                            range=self._line_range(i),
                            message=(
                                f"'{yaml_path}' richiede un valore "
                                f"(float o envelope [[t, v], ...]) ma non ne ha uno."
                            ),
                            severity=DiagnosticSeverity.Error,
                            source=SOURCE,
                        ))

            elif current_prefix is None and key in self._STREAM_VALUE_REQUIRED:
                # Campo obbligatorio stream senza valore
                diagnostics.append(Diagnostic(
                    range=self._line_range(i),
                    message=f"'{key}' richiede un valore ma non ne ha uno.",
                    severity=DiagnosticSeverity.Error,
                    source=SOURCE,
                ))

            elif key in self._STRING_REQUIRED_KEYS:
                # Chiave stringa obbligatoria
                diagnostics.append(Diagnostic(
                    range=self._line_range(i),
                    message=f"'{key}' richiede un valore stringa ma non ne ha uno.",
                    severity=DiagnosticSeverity.Error,
                    source=SOURCE,
                ))

            elif yaml_path in voice_required_paths:
                # Strategy o kwarg voices senza valore
                diagnostics.append(Diagnostic(
                    range=self._line_range(i),
                    message=f"'{yaml_path}' richiede un valore ma non ne ha uno.",
                    severity=DiagnosticSeverity.Error,
                    source=SOURCE,
                ))

            else:
                # Contenitore / chiave non categorizzata: push allo stack
                block_stack.append((yaml_path, leading))

            i += 1

        return diagnostics


    def _check_envelope_bounds(
        self, document_text: str
    ) -> List[Diagnostic]:
        """
        Controlla i valori Y dei breakpoints negli envelope standard.

        Due forme riconosciute:
          - block-style: chiave nuda seguita da righe '- [t, y]'
          - inline: 'key: [[t, v], ...]' sulla riga della chiave
            (anche breakpoint singolo [t, y] e formato compact loop)

        Produce Error se un valore y e' fuori dai bounds del parametro.
        """
        import ast
        diagnostics = []
        lines = document_text.split('\n')

        # Costruisce mappa yaml_path -> (min_val, max_val).
        # pitch.* escluso: bounds per-unità validati da _check_pitch_block.
        params_bounds = {}
        for p in self._bridge.get_all_parameters():
            if p.min_val is not None and p.max_val is not None and not p.is_internal \
                    and _is_generically_checkable(p.yaml_path):
                params_bounds[p.yaml_path] = (p.min_val, p.max_val)

        # Indice di risoluzione chiave -> yaml_path (chiave locale e path
        # completo, primo match in ordine di registrazione): usato dal ramo
        # block-style e da quello inline senza riscandire params_bounds.
        key_to_path: Dict[str, str] = {}
        for yp in params_bounds:
            key_to_path.setdefault(yp.split('.')[-1], yp)
            key_to_path.setdefault(yp, yp)

        # Scansione: tiene traccia del parametro corrente e del suo path
        current_param_path = None
        current_indent = 0

        for n, line in enumerate(lines):
            stripped = line.strip()
            if not stripped or stripped.startswith('#'):
                continue

            leading = len(line) - len(line.lstrip())

            # Rilevamento riga con parametro e valore lista (chiave senza valore)
            if ': ' not in stripped and stripped.endswith(':'):
                key = stripped[:-1].strip()
                if key and all(c.isalnum() or c == '_' for c in key):
                    current_param_path = key_to_path.get(key)
                    current_indent = leading
                continue

            # Reset se siamo risaliti di livello
            if current_param_path and leading <= current_indent:
                current_param_path = None

            # Analisi riga breakpoint
            if current_param_path and stripped.startswith('- ['):
                bounds = params_bounds.get(current_param_path)
                if bounds is None:
                    continue
                min_val, max_val = bounds

                try:
                    inner = stripped[2:].strip()
                    parsed_list = ast.literal_eval(inner)
                    if not isinstance(parsed_list, list):
                        continue

                    # Determina il formato e raccoglie i valori Y da controllare
                    y_values_to_check: list = []

                    # Formato compact: [[[p1, p2, ...], end_time, n_reps, ...]]
                    # Il primo elemento e' una lista di liste (i punti del pattern).
                    # Ogni punto ha forma [x_pct, y] dove x_pct e' percentuale [0,100].
                    if (len(parsed_list) >= 2
                            and isinstance(parsed_list[0], list)
                            and all(isinstance(pt, list) for pt in parsed_list[0])):
                        # pattern points: ciascuno e' [x_pct, y]
                        for pt in parsed_list[0]:
                            if isinstance(pt, list) and len(pt) >= 2:
                                y_values_to_check.append((n, pt[1]))

                    # Formato dict con 'points': gestito separatamente
                    # (le righe points sono righe distinte con '- [')
                    # qui arrivano solo i breakpoints standard [t, y]
                    elif (len(parsed_list) >= 2
                              and isinstance(parsed_list[0], (int, float))
                              and isinstance(parsed_list[1], (int, float))):
                        # Breakpoint standard [t, y]
                        y_values_to_check.append((n, parsed_list[1]))

                    for line_n, y_val in y_values_to_check:
                        if isinstance(y_val, (int, float)):
                            if y_val < min_val or y_val > max_val:
                                diagnostics.append(Diagnostic(
                                    range=Range(
                                        start=Position(line=line_n, character=0),
                                        end=Position(line=line_n,
                                                     character=len(lines[line_n])),
                                    ),
                                    message=(
                                        f"Valore envelope {y_val} fuori dai bounds "
                                        f"del parametro '{current_param_path}': "
                                        f"[{min_val}, {max_val}]."
                                    ),
                                    severity=DiagnosticSeverity.Error,
                                    source='pge-ls',
                                ))
                except Exception:
                    pass
                continue

            # Envelope inline sulla riga della chiave: 'key: [[t, v], ...]',
            # breakpoint singolo [t, y] o compact loop. Stessa risoluzione e
            # stesso messaggio del ramo block-style; valori non parseabili
            # vengono ignorati (tolleranza, come literal_eval sui breakpoint).
            m_inline = re.match(r'^([a-zA-Z_][a-zA-Z0-9_]*)\s*:\s*(\[.*)$',
                                stripped)
            if m_inline:
                inline_path = key_to_path.get(m_inline.group(1))
                if inline_path is None:
                    continue
                min_val, max_val = params_bounds[inline_path]
                for y_val in self._extract_envelope_y_values(m_inline.group(2)):
                    if y_val < min_val or y_val > max_val:
                        diagnostics.append(Diagnostic(
                            range=Range(
                                start=Position(line=n, character=0),
                                end=Position(line=n, character=len(line)),
                            ),
                            message=(
                                f"Valore envelope {y_val} fuori dai bounds "
                                f"del parametro '{inline_path}': "
                                f"[{min_val}, {max_val}]."
                            ),
                            severity=DiagnosticSeverity.Error,
                            source='pge-ls',
                        ))

        return diagnostics

    # -------------------------------------------------------------------------
    # FASE 1: PARSING
    # -------------------------------------------------------------------------

    def _parse_document(
        self, text: str
    ) -> List[Tuple[str, str, int, int, int]]:
        """
        Estrae coppie chiave-valore dal documento riga per riga.

        Ritorna una lista di tuple:
            (yaml_path, valore_str, n_riga, indent_level, stream_idx)

        stream_idx: indice incrementale dello stream corrente (0-based).
        Usato da _check_exclusive_groups per scoping per-stream.

        yaml_path e' costruito tenendo traccia del blocco corrente:
        se siamo dentro 'grain:' e troviamo 'duration: 0.05',
        yaml_path diventa 'grain.duration'.

        Approccio tollerante: righe malformate vengono saltate.
        """
        results = []
        lines = text.split('\n')

        # Stack dei blocchi aperti: lista di (nome_blocco, indent_level)
        block_stack: List[Tuple[str, int]] = []
        stream_idx = -1  # incrementa ogni volta che troviamo un marcatore '- '

        for n, line in enumerate(lines):
            # Salta righe vuote e commenti
            stripped = line.strip()
            if not stripped or stripped.startswith('#'):
                continue

            # Calcola indentazione
            leading = len(line) - len(line.lstrip())
            indent = leading // 2

            # Gestione marcatore lista '- ':
            # Ogni '- ' indica un nuovo elemento stream indipendente.
            # Azzeriamo lo stack dei blocchi perche' ogni elemento lista
            # e' una radice separata (non eredita il contesto degli elementi
            # precedenti). Poi analizziamo il contenuto dopo il '- '.
            if stripped.startswith('- '):
                block_stack.clear()
                # Nuovo stream: incrementa indice solo per marcatori a indent 1
                raw_line_check = lines[n]
                leading_check = len(raw_line_check) - len(raw_line_check.lstrip())
                if leading_check == 2:
                    stream_idx += 1
                stripped = stripped[2:].strip()
                if not stripped:
                    continue  # trattino da solo, nessun parametro su questa riga
            elif stripped == '-':
                block_stack.clear()
                stream_idx += 1
                continue

            # Aggiorna lo stack dei blocchi:
            # rimuoviamo i blocchi con indent >= quello corrente
            while block_stack and block_stack[-1][1] >= indent:
                block_stack.pop()

            # Rimuovi commenti inline
            if '#' in stripped:
                stripped = stripped[:stripped.find('#')].rstrip()

            # Pattern chiave: valore
            match = re.match(r'^([a-zA-Z_][a-zA-Z0-9_]*)\s*:\s*(.*)', stripped)
            if not match:
                continue

            key = match.group(1)
            value_str = match.group(2).strip()

            # Salta il blocco 'streams:' stesso: e' il contenitore della lista,
            # non un parametro da validare.
            if key == 'streams' and not value_str:
                continue

            # Costruisci yaml_path completo usando lo stack
            if block_stack:
                parent = '.'.join(b[0] for b in block_stack)
                yaml_path = parent + '.' + key
            else:
                yaml_path = key

            # Se il valore e' vuoto, potrebbe essere:
            # a) un vero blocco padre (grain:, pointer:, pitch:, deviation_probability:)
            # b) un parametro con valore envelope sulle righe successive (density:)
            # Distinguiamo i due casi: se yaml_path e' un parametro noto del bridge
            # con valore lista, lo registriamo comunque per il check exclusive_group.
            if not value_str:
                if yaml_path in self._params_by_yaml_path:
                    # Parametro noto con valore envelope: registra per exclusive_group
                    results.append((yaml_path, '<envelope>', n, indent, stream_idx))
                    # Non aggiungiamo allo stack: i suoi sotto-elementi sono breakpoints,
                    # non chiavi YAML di parametri ulteriori.
                else:
                    block_stack.append((key, indent))
            else:
                results.append((yaml_path, value_str, n, indent, stream_idx))

        return results

    # -------------------------------------------------------------------------
    # FASE 2: EXCLUSIVE GROUP
    # -------------------------------------------------------------------------

    def _check_exclusive_groups(
        self, parsed: List[Tuple[str, str, int, int]]
    ) -> List[Diagnostic]:
        """
        Cerca violazioni di mutua esclusivita'.

        Per ogni exclusive_group del bridge, controlla se piu' di un
        membro e' presente nel documento. Se si', produce un Warning
        per ogni membro in eccesso trovato.
        """
        diagnostics = []

        # Raggruppa i parametri trovati per stream_idx.
        # Il check di mutua esclusivita' e' PER-STREAM:
        # stream diversi possono usare parametri diversi dello stesso gruppo.
        from collections import defaultdict
        # stream_found[stream_idx] = {yaml_path: n_riga}
        stream_found: dict = defaultdict(dict)
        for entry in parsed:
            if len(entry) == 5:
                yaml_path, _, n_riga, _, sidx = entry
            else:
                yaml_path, _, n_riga, _ = entry
                sidx = 0
            if yaml_path in self._params_by_yaml_path:
                stream_found[sidx][yaml_path] = n_riga

        # Per ogni gruppo esclusivo, controlliamo per ogni stream.
        groups = self._bridge.get_exclusive_groups()

        for group_name, members in groups.items():
          for found in stream_found.values():
            # Membri del gruppo presenti IN QUESTO STREAM
            present_members = [
                m for m in members
                if m.yaml_path in found
            ]

            if len(present_members) <= 1:
                continue

            # Il membro con group_priority piu' basso ha priorita' maggiore.
            priority_winner = min(present_members, key=lambda m: m.group_priority)
            names = ', '.join(m.yaml_path for m in present_members)

            for member in present_members:
                if member == priority_winner:
                    # Sul parametro vincente: segnala che l'altro e' in conflitto
                    msg = (
                        f"Exclusive group '{group_name}': "
                        f"'{member.yaml_path}' ha priorita' e sara' usato. "
                        f"Rimuovere gli altri: "
                        f"{', '.join(m.yaml_path for m in present_members if m != member)}."
                    )
                else:
                    # Sul parametro perdente: segnala che verra' ignorato
                    msg = (
                        f"Exclusive group '{group_name}': "
                        f"'{member.yaml_path}' verra' ignorato perche' "
                        f"'{priority_winner.yaml_path}' ha priorita' piu' alta "
                        f"(group_priority={priority_winner.group_priority} "
                        f"< {member.group_priority})."
                    )
                n_riga = found[member.yaml_path]
                diagnostics.append(Diagnostic(
                    range=self._line_range(n_riga),
                    message=msg,
                    severity=DiagnosticSeverity.Warning,
                    source=SOURCE,
                ))

        return diagnostics

    # -------------------------------------------------------------------------
    # FASE 3: BOUNDS
    # -------------------------------------------------------------------------

    def _check_bounds(
        self, parsed: List[Tuple[str, str, int, int]]
    ) -> List[Diagnostic]:
        """
        Verifica che i valori numerici siano dentro i bounds del parametro.

        Valori non numerici (stringhe, liste, envelope) vengono ignorati:
        non possiamo confrontarli con min/max e non e' un errore scrivere
        un envelope o una lista breakpoints per un parametro.
        """
        diagnostics = []

        for yaml_path, value_str, n_riga, *_ in parsed:
            param = self._params_by_yaml_path.get(yaml_path)
            if param is None:
                continue

            # Nessun bounds definito: non possiamo fare controlli.
            if param.min_val is None or param.max_val is None:
                continue

            # Proviamo a interpretare il valore come numero.
            # Se non e' un numero, saltiamo silenziosamente.
            numeric_value = self._try_parse_number(value_str)
            if numeric_value is None:
                continue

            # Controllo range.
            if numeric_value < param.min_val or numeric_value > param.max_val:
                message = (
                    f"'{yaml_path}': valore {numeric_value} fuori range "
                    f"[{param.min_val}, {param.max_val}]."
                )
                diagnostics.append(Diagnostic(
                    range=self._line_range(n_riga),
                    message=message,
                    severity=DiagnosticSeverity.Error,
                    source=SOURCE,
                ))

        return diagnostics

    # -------------------------------------------------------------------------
    # MUTED / SOLO
    # -------------------------------------------------------------------------

    def _find_stream_blocks(
        self, lines: List[str]
    ) -> List[Tuple[int, int, dict]]:
        """
        Ritorna lista di (start_line, end_line, keys) per ogni stream.
        keys e' un dict {chiave: valore} delle chiavi dirette dello stream.
        """
        stream_starts = []
        for n, line in enumerate(lines):
            stripped = line.strip()
            leading = len(line) - len(line.lstrip())
            if (stripped.startswith('- ') or stripped == '-') and leading == 2:
                stream_starts.append(n)

        streams = []
        for idx, start in enumerate(stream_starts):
            end = (stream_starts[idx + 1] - 1
                   if idx + 1 < len(stream_starts)
                   else len(lines) - 1)
            keys: dict = {}
            for n in range(start, end + 1):
                raw = lines[n]
                stripped = raw.strip()
                if stripped.startswith('- '):
                    stripped = stripped[2:].strip()
                if ':' in stripped:
                    key = stripped[:stripped.index(':')].strip()
                    value = stripped[stripped.index(':') + 1:].strip()
                    if key and all(c.isalnum() or c == '_' for c in key):
                        keys[key] = value
            streams.append((start, end, keys))
        return streams

    def _check_muted_streams(
        self, lines: List[str],
        streams: List[Tuple[int, int, dict]],
    ) -> List[Diagnostic]:
        """
        Sbiadisce (DiagnosticTag.Unnecessary) ogni stream con muted: true.
        """
        diagnostics = []
        for start, end, keys in streams:
            if 'mute' in keys:
                end_char = len(lines[end]) if end < len(lines) else 0
                diagnostics.append(Diagnostic(
                    range=Range(
                        start=Position(line=start, character=0),
                        end=Position(line=end, character=end_char),
                    ),
                    message="Stream muted: questo stream non verra' riprodotto.",
                    severity=DiagnosticSeverity.Hint,
                    source=SOURCE,
                    tags=[DiagnosticTag.Unnecessary],
                ))
        return diagnostics

    def _check_solo_streams(
        self, lines: List[str],
        streams: List[Tuple[int, int, dict]],
    ) -> List[Diagnostic]:
        """
        Se almeno uno stream ha solo: true, sbiadisce tutti gli altri
        (che non hanno a loro volta solo: true).
        """
        diagnostics = []
        solo_set = {
            i for i, (_, _, keys) in enumerate(streams)
            if 'solo' in keys
        }
        if not solo_set:
            return []

        for i, (start, end, _) in enumerate(streams):
            if i not in solo_set:
                end_char = len(lines[end]) if end < len(lines) else 0
                diagnostics.append(Diagnostic(
                    range=Range(
                        start=Position(line=start, character=0),
                        end=Position(line=end, character=end_char),
                    ),
                    message="Stream non attivo: un altro stream ha 'solo: true'.",
                    severity=DiagnosticSeverity.Hint,
                    source=SOURCE,
                    tags=[DiagnosticTag.Unnecessary],
                ))
        return diagnostics

    # -------------------------------------------------------------------------
    # HELPERS
    # -------------------------------------------------------------------------

    def _try_parse_number(self, value_str: str) -> Optional[float]:
        """
        Prova a convertire una stringa in float.
        Ritorna None se non e' un numero valido.

        Gestiamo: interi, float, notazione scientifica.
        Non gestiamo: liste, dict, stringhe, valori YAML speciali.
        """
        if not value_str:
            return None
        # Rifiutiamo subito stringhe che iniziano con caratteri non numerici
        # (ad eccezione del segno meno).
        if value_str[0] not in '-0123456789.':
            return None
        try:
            return float(value_str)
        except ValueError:
            return None

    def _line_range(self, n_riga: int) -> Range:
        """
        Costruisce un Range LSP che copre l'intera riga n_riga.

        character=0 a character=999 e' un'approssimazione comune
        per "tutta la riga" quando non conosciamo la lunghezza esatta.
        VSCode clampera' automaticamente alla fine della riga.
        """
        return Range(
            start=Position(line=n_riga, character=0),
            end=Position(line=n_riga, character=999),
        )

    # -------------------------------------------------------------------------
    # CONTROLLO GRAIN ENVELOPE
    # -------------------------------------------------------------------------

    def _check_grain_envelope(self, document_text: str) -> List[Diagnostic]:
        """
        Valida grain.envelope in ogni stream.

        Valori accettati:
          - Un nome di finestratura valido (stringa)
          - Una lista di nomi: [hanning, hamming]
          - Il valore speciale 'all' o true
        """
        diagnostics: List[Diagnostic] = []
        if not document_text:
            return diagnostics

        valid_names = set(self._bridge.get_grain_envelope_names())
        lines = document_text.split('\n')
        in_grain = False

        for n, raw in enumerate(lines):
            stripped = raw.strip()
            if not stripped or stripped.startswith('#'):
                continue
            leading = len(raw) - len(raw.lstrip())

            # Traccia il blocco grain: (indent 4)
            if leading == 4 and stripped == 'grain:':
                in_grain = True
                continue
            if in_grain and leading <= 4 and stripped:
                in_grain = False

            if not in_grain:
                continue

            # Cerca 'envelope:' a indent 6
            if leading != 6:
                continue
            m = re.match(r'^envelope\s*:\s*(.*)', stripped)
            if not m:
                continue

            val_str = m.group(1).strip()
            if not val_str:
                continue  # valore mancante — gestito da _check_missing_values

            # Accetta: true, all, lista [...], o nome singolo
            if val_str in ('true', 'all'):
                continue
            if val_str.startswith('['):
                # Lista: estrai i token
                inner = val_str.strip('[]')
                tokens = [t.strip().strip('"\'') for t in inner.split(',') if t.strip()]
                for tok in tokens:
                    if tok and tok not in valid_names:
                        diagnostics.append(Diagnostic(
                            range=self._line_range(n),
                            message=(
                                f"Finestratura `{tok}` non valida per `grain.envelope`. "
                                f"Valori disponibili: {', '.join(sorted(valid_names))}."
                            ),
                            severity=DiagnosticSeverity.Error,
                            source=SOURCE,
                        ))
                continue

            # Stringa singola
            name = val_str.strip('"\'')
            if name not in valid_names:
                diagnostics.append(Diagnostic(
                    range=self._line_range(n),
                    message=(
                        f"Finestratura `{name}` non valida per `grain.envelope`. "
                        f"Valori disponibili: {', '.join(sorted(valid_names))}."
                    ),
                    severity=DiagnosticSeverity.Error,
                    source=SOURCE,
                ))

        return diagnostics

    # -------------------------------------------------------------------------
    # CONTROLLO VOICES
    # -------------------------------------------------------------------------

    # -------------------------------------------------------------------------
    # FASE 5c: BLOCCO PITCH (superficie unit-driven)
    # -------------------------------------------------------------------------

    def _check_pitch_block(
        self, lines: List[str],
        streams: List[Tuple[int, int, dict]],
    ) -> List[Diagnostic]:
        """
        Valida il blocco pitch: di ogni stream, speculare alla validazione
        strict di PitchController._select_unit del motore:

          1. `pitch:` vuoto (null) o non-mapping (lista/scalare) -> Error.
          2. Chiavi fuori da PITCH_BLOCK_KEYS (refusi) -> Error.
          3. Piu' di una chiave-unita' nello stesso blocco -> Error.
          4. `value` senza `edo` -> Error.
          5. `edo`: intero > 0; forma annidata {divisions, value} -> Error
             (hard break); `value` mancante -> Error.
          6. Bounds per-unita' su scalari ed envelope Y:
             preset da PitchUnitInfo, `value` in ±3·N, `range` in
             [0, max_range dell'unita' attiva].

        `pitch: {}` e blocco assente restano validi (default semitoni neutro).
        """
        diagnostics: List[Diagnostic] = []

        for stream_start, stream_end, _keys in streams:
            pitch_line = None
            for n in range(stream_start, stream_end + 1):
                raw = lines[n]
                leading = len(raw) - len(raw.lstrip())
                if leading == 4 and raw.strip().startswith('pitch:'):
                    pitch_line = n
                    break
            if pitch_line is None:
                continue
            diagnostics.extend(
                self._check_single_pitch_block(lines, pitch_line, stream_end + 1)
            )

        return diagnostics

    _PITCH_EMPTY_HINT = (
        "il blocco pitch deve specificare un'unità (es. semitones: 7, "
        "ratio: 1.5, oppure edo: 31 + value: 18). Per nessuna trasposizione, "
        "ometti del tutto il blocco pitch."
    )

    def _check_single_pitch_block(self, lines: List[str], pitch_line: int,
                                   stream_end: int) -> List[Diagnostic]:
        """Applica le regole strict a un singolo blocco pitch:."""
        diagnostics: List[Diagnostic] = []

        inline_val = lines[pitch_line].strip()[len('pitch:'):].strip()
        if '#' in inline_val:
            inline_val = inline_val[:inline_val.find('#')].rstrip()

        # entries: chiave -> (valore_str, n_riga)
        entries: Dict[str, Tuple[str, int]] = {}

        if inline_val:
            if inline_val.startswith('{'):
                inner = inline_val.strip().strip('{}').strip()
                if not inner:
                    return diagnostics  # pitch: {} -> default neutro valido
                for key, value in split_inline_mapping(inner):
                    entries[key] = (value, pitch_line)
            else:
                # non-mapping: scalare o lista inline (pitch: 3.0 / pitch: [[...]])
                diagnostics.append(Diagnostic(
                    range=self._line_range(pitch_line),
                    message=(
                        "`pitch` deve essere un mapping di chiavi-unità, "
                        f"non un valore diretto: {self._PITCH_EMPTY_HINT}"
                    ),
                    severity=DiagnosticSeverity.Error,
                    source=SOURCE,
                ))
                return diagnostics
        else:
            # Forma block: chiavi a 6 spazi fino a indent <= 4
            block_end = stream_end
            for n in range(pitch_line + 1, stream_end):
                raw = lines[n]
                if not raw.strip():
                    continue
                if (len(raw) - len(raw.lstrip())) <= 4:
                    block_end = n
                    break
            has_children = False
            for n in range(pitch_line + 1, block_end):
                raw = lines[n]
                stripped = raw.strip()
                if not stripped or stripped.startswith('#'):
                    continue
                has_children = True
                if (len(raw) - len(raw.lstrip())) != 6:
                    continue  # righe più profonde: envelope/figli di una chiave
                if '#' in stripped:
                    stripped = stripped[:stripped.find('#')].rstrip()
                m = re.match(r'^([a-zA-Z_]\w*)\s*:\s*(.*)$', stripped)
                if m:
                    entries[m.group(1)] = (m.group(2).strip(), n)

            if not has_children:
                # pitch: presente ma vuoto (null)
                diagnostics.append(Diagnostic(
                    range=self._line_range(pitch_line),
                    message=f"`pitch:` è vuoto: {self._PITCH_EMPTY_HINT}",
                    severity=DiagnosticSeverity.Error,
                    source=SOURCE,
                ))
                return diagnostics
            if not entries:
                # figli presenti ma nessuna coppia chiave: valore
                # (es. lista di breakpoints direttamente sotto pitch:)
                diagnostics.append(Diagnostic(
                    range=self._line_range(pitch_line),
                    message=(
                        "`pitch` deve essere un mapping di chiavi-unità: "
                        + self._PITCH_EMPTY_HINT
                    ),
                    severity=DiagnosticSeverity.Error,
                    source=SOURCE,
                ))
                return diagnostics

        # --- Regola 2: chiavi sconosciute (validazione strict) ---
        for key, (_val, n) in entries.items():
            if key not in PITCH_BLOCK_KEYS:
                diagnostics.append(Diagnostic(
                    range=self._line_range(n),
                    message=(
                        f"Chiave sconosciuta nel blocco pitch: `{key}`. "
                        f"Chiavi valide: {sorted(PITCH_BLOCK_KEYS)}."
                    ),
                    severity=DiagnosticSeverity.Error,
                    source=SOURCE,
                ))

        # --- Regola 3: una sola chiave-unità per blocco ---
        present_units = [k for k in PITCH_UNIT_KEYS if k in entries]
        if len(present_units) > 1:
            for k in present_units:
                diagnostics.append(Diagnostic(
                    range=self._line_range(entries[k][1]),
                    message=(
                        f"Una sola unità per blocco pitch; trovate: "
                        f"{present_units}. Unità disponibili: "
                        f"{sorted(PITCH_UNIT_KEYS)}."
                    ),
                    severity=DiagnosticSeverity.Error,
                    source=SOURCE,
                ))
        unit_key = present_units[0] if present_units else None

        # --- Regola 4: value solo con edo ---
        if 'value' in entries and unit_key != 'edo':
            diagnostics.append(Diagnostic(
                range=self._line_range(entries['value'][1]),
                message=(
                    "`pitch.value` è ammesso solo con `edo: N`; per i preset "
                    "il valore sta nella chiave (es. semitones: 7)."
                ),
                severity=DiagnosticSeverity.Error,
                source=SOURCE,
            ))

        # --- Regola 5: grammatica edo ---
        divisions = None
        if 'edo' in entries:
            edo_val, edo_line = entries['edo']
            nested = edo_val.startswith('{')
            if not nested and not edo_val:
                # edo: senza valore inline: figli più profondi?
                # La vecchia forma annidata (divisions:/value: sotto edo:)
                # è un hard break con hint di migrazione.
                nested = any(
                    (len(lines[j]) - len(lines[j].lstrip())) > 6
                    and lines[j].strip()
                    for j in range(edo_line + 1, min(edo_line + 4, len(lines)))
                )
            if nested:
                diagnostics.append(Diagnostic(
                    range=self._line_range(edo_line),
                    message=(
                        "`pitch.edo`: forma cambiata — ora `edo: N` con "
                        "`value: X` a fianco (es. edo: 31, value: 18). La "
                        "forma annidata {divisions, value} non è più valida."
                    ),
                    severity=DiagnosticSeverity.Error,
                    source=SOURCE,
                ))
            else:
                divisions = parse_edo_divisions(edo_val)
                if divisions is None:
                    diagnostics.append(Diagnostic(
                        range=self._line_range(edo_line),
                        message=(
                            "`pitch.edo` richiede le divisioni per ottava: "
                            "un intero > 0 (es. 12, 24, 31)."
                        ),
                        severity=DiagnosticSeverity.Error,
                        source=SOURCE,
                    ))
            if 'value' not in entries:
                diagnostics.append(Diagnostic(
                    range=self._line_range(edo_line),
                    message=(
                        "`pitch.edo`: con `edo: N` serve `value: X` a "
                        "fianco (es. edo: 31, value: 18)."
                    ),
                    severity=DiagnosticSeverity.Error,
                    source=SOURCE,
                ))

        # --- Regola 6: bounds per-unità (scalari ed envelope Y) ---
        if unit_key and unit_key != 'edo':
            info = PITCH_UNIT_PRESETS[unit_key]
            diagnostics.extend(self._check_pitch_value_bounds(
                lines, entries[unit_key], f'pitch.{unit_key}',
                info.min_val, info.max_val,
            ))
        if 'value' in entries and unit_key == 'edo' and divisions:
            diagnostics.extend(self._check_pitch_value_bounds(
                lines, entries['value'], 'pitch.value',
                -3.0 * divisions, 3.0 * divisions,
            ))
        if 'range' in entries:
            info = get_unit_info(unit_key or 'semitones', divisions)
            if info is not None:
                diagnostics.extend(self._check_pitch_value_bounds(
                    lines, entries['range'], 'pitch.range',
                    0.0, info.max_range,
                ))

        return diagnostics

    def _check_pitch_value_bounds(self, lines: List[str],
                                   entry: Tuple[str, int], label: str,
                                   min_val: float,
                                   max_val: float) -> List[Diagnostic]:
        """
        Bounds di una chiave del blocco pitch: scalare, envelope inline
        ([[t, v], ...] o compact sulla stessa riga) o envelope block-style
        (righe '- [...]' più indentate sotto la chiave).
        """
        diagnostics: List[Diagnostic] = []
        value_str, line_no = entry

        def _check_y(y_val, n_riga):
            if isinstance(y_val, (int, float)) and not isinstance(y_val, bool):
                if y_val < min_val or y_val > max_val:
                    diagnostics.append(Diagnostic(
                        range=self._line_range(n_riga),
                        message=(
                            f"'{label}': valore {y_val} fuori range "
                            f"[{min_val:g}, {max_val:g}]."
                        ),
                        severity=DiagnosticSeverity.Error,
                        source=SOURCE,
                    ))

        if value_str:
            numeric = self._try_parse_number(value_str)
            if numeric is not None:
                _check_y(numeric, line_no)
                return diagnostics
            if value_str.startswith('['):
                for y in self._extract_envelope_y_values(value_str):
                    _check_y(y, line_no)
            return diagnostics

        # Chiave senza valore inline: cerca envelope block-style nei figli
        key_leading = len(lines[line_no]) - len(lines[line_no].lstrip())
        has_value = False
        for n in range(line_no + 1, len(lines)):
            raw = lines[n]
            stripped = raw.strip()
            if not stripped or stripped.startswith('#'):
                continue
            leading = len(raw) - len(raw.lstrip())
            if leading <= key_leading:
                break
            has_value = True
            if stripped.startswith('- ['):
                for y in self._extract_envelope_y_values(stripped[2:].strip()):
                    _check_y(y, n)

        if not has_value:
            diagnostics.append(Diagnostic(
                range=self._line_range(line_no),
                message=(
                    f"'{label}' richiede un valore "
                    f"(float o envelope [[t, v], ...]) ma non ne ha uno."
                ),
                severity=DiagnosticSeverity.Error,
                source=SOURCE,
            ))
        return diagnostics

    def _check_bp_groups(self, lines: List[str]) -> List[Diagnostic]:
        """
        Valida i BP group [points, interp] (PGE issue #64).

        Un BP group avvolge un run di breakpoint dichiarando l'interpolazione
        dell'intera macrozona: [[[t, v], ...], 'interp']. Regole (identiche
        a EnvelopeBuilder._expand_bp_group in PGE):

          - interp deve essere in VALID_INTERP_TYPES (linear/cubic/step),
            altrimenti il motore solleva InvalidFieldValueError;
          - la zona deve avere almeno 2 punti (n punti -> n-1 segmenti
            interni), altrimenti ValueError;
          - i punti devono essere [t, v] o [t, v, type].

        Cerca i gruppi sia nella forma diretta 'key: [points, interp]' sia
        come item di envelope misti (inline o block-style '- [...]').
        Parsing tollerante via yaml.safe_load: righe non parseabili
        (es. flow spezzato su piu' righe) vengono ignorate.
        """
        diagnostics: List[Diagnostic] = []

        for n, line in enumerate(lines):
            stripped = line.strip()
            if not stripped or stripped.startswith('#'):
                continue
            if stripped.startswith('- '):
                stripped = stripped[2:].strip()

            # Estrae il valore lista: 'key: [...]' oppure item '- [...]'.
            if stripped.startswith('['):
                value_str = stripped
            else:
                m = re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*\s*:\s*(\[.*)$',
                             stripped)
                if not m:
                    continue
                value_str = m.group(1)

            try:
                parsed = yaml.safe_load(value_str)
            except Exception:
                continue
            if not isinstance(parsed, list):
                continue

            # Candidati: forma diretta o item di una lista mista.
            if is_bp_group_candidate(parsed):
                candidates = [parsed]
            else:
                candidates = [item for item in parsed
                              if is_bp_group_candidate(item)]

            for group in candidates:
                points, interp = group
                if not all(is_valid_point(p) for p in points):
                    message = (
                        "BP group con punti malformati: ogni punto deve "
                        "essere [t, v] o [t, v, type] con t, v numerici."
                    )
                elif interp not in VALID_INTERP_TYPES:
                    message = (
                        f"BP group: interp '{interp}' non valido. "
                        f"Tipi validi: {', '.join(VALID_INTERP_TYPES)}."
                    )
                elif len(points) < 2:
                    message = (
                        f"BP group richiede almeno 2 punti, trovati: "
                        f"{len(points)}. Una zona con meno di 2 punti "
                        "non ha segmenti interni."
                    )
                else:
                    continue
                diagnostics.append(Diagnostic(
                    range=self._line_range(n),
                    message=message,
                    severity=DiagnosticSeverity.Error,
                    source=SOURCE,
                ))

        return diagnostics

    @staticmethod
    def _extract_envelope_y_values(value_str: str) -> List[float]:
        """
        Estrae i valori Y da un envelope serializzato.

        Formati riconosciuti (stessi di _check_envelope_bounds):
          - breakpoints standard: [[t, y], ...] oppure il singolo [t, y]
          - compact loop: [[[x_pct, y], ...], end_time, ...]
          - BP group diretto: [[[t, y], ...], 'interp'] (PGE #64)
          - misto: BP group e loop block come item della lista esterna
        Formati non riconosciuti: lista vuota (tolleranza).
        """
        import ast
        ys: List[float] = []
        try:
            parsed = ast.literal_eval(value_str)
        except Exception:
            return ys
        if not isinstance(parsed, list) or not parsed:
            return ys

        def _is_num(v):
            return isinstance(v, (int, float)) and not isinstance(v, bool)

        # Formato compact: [pattern, end_time, ...] con pattern = [[x, y], ...]
        if (len(parsed) >= 2
                and isinstance(parsed[0], list) and parsed[0]
                and all(isinstance(pt, list) for pt in parsed[0])):
            return [pt[1] for pt in parsed[0]
                    if len(pt) >= 2 and _is_num(pt[1])]

        # Singolo breakpoint [t, y]
        if len(parsed) >= 2 and _is_num(parsed[0]) and _is_num(parsed[1]):
            return [parsed[1]]

        # Breakpoints standard [[t, y], ...] + item misti:
        # BP group [points, interp] e loop block come elementi della lista.
        for item in parsed:
            if (isinstance(item, list) and len(item) >= 2
                    and _is_num(item[0]) and _is_num(item[1])):
                ys.append(item[1])
            elif is_bp_group(item) or is_loop_block(item):
                ys.extend(pt[1] for pt in item[0]
                          if isinstance(pt, list) and len(pt) >= 2
                          and _is_num(pt[1]))
        return ys

    def _check_voice_strategies(
        self, lines: List[str],
        streams: List[Tuple[int, int, dict]],
    ) -> List[Diagnostic]:
        """
        Valida il blocco voices: di ogni stream.

        Controlli:
          1. Per ogni dimensione (pitch, onset_offset, pointer, pan): se present,
             la chiave 'strategy' deve essere valida per quella dimensione.
          2. I kwargs richiesti dalla strategy devono essere presenti.
          3. I kwargs di tipo enum devono avere un valore nel set consentito.
        """
        diagnostics: List[Diagnostic] = []

        for stream_start, stream_end_incl, _keys in streams:
            # I range interni usano il confine esclusivo
            stream_end = stream_end_incl + 1

            # Cerca il blocco voices: nello stream (a 4 spazi)
            voices_start = None
            for n in range(stream_start, stream_end):
                raw = lines[n]
                stripped = raw.strip()
                leading = len(raw) - len(raw.lstrip())
                if leading == 4 and stripped == 'voices:':
                    voices_start = n
                    break

            if voices_start is None:
                continue

            # Determina la fine del blocco voices
            voices_end = stream_end
            for n in range(voices_start + 1, stream_end):
                raw = lines[n]
                if not raw.strip():
                    continue
                leading = len(raw) - len(raw.lstrip())
                if leading <= 4:
                    voices_end = n
                    break

            # Raccoglie le chiavi di primo livello dentro voices (a 6 spazi)
            voices_keys: Dict[str, int] = {}  # key_name -> line_number
            for n in range(voices_start + 1, voices_end):
                raw = lines[n]
                stripped = raw.strip()
                leading = len(raw) - len(raw.lstrip())
                if leading != 6 or not stripped or stripped.startswith('#'):
                    continue
                m = re.match(r'^([a-zA-Z_][a-zA-Z0-9_]*)\s*:', stripped)
                if m:
                    voices_keys[m.group(1)] = n

            # Valida num_voices e scatter (scalari con bounds dal bridge)
            for param_name in VOICE_ENVELOPE_KEYS:
                if param_name not in voices_keys:
                    continue
                raw_bounds = self._bridge.get_raw_bounds(param_name)
                if not raw_bounds:
                    continue
                param_line = voices_keys[param_name]
                raw = lines[param_line]
                stripped = raw.strip()
                m = re.match(r'^[a-zA-Z_]\w*\s*:\s*(.+)', stripped)
                if not m:
                    continue
                val_str = m.group(1).strip()
                # Salta envelope (iniziano con '[') — non validiamo i breakpoints qui
                if val_str.startswith('['):
                    continue
                try:
                    val = float(val_str)
                except ValueError:
                    continue
                min_v = raw_bounds['min_val']
                max_v = raw_bounds['max_val']
                if val < min_v or val > max_v:
                    diagnostics.append(Diagnostic(
                        range=self._line_range(param_line),
                        message=(
                            f"`voices.{param_name}` = {val} fuori range "
                            f"[{min_v}, {max_v}]."
                        ),
                        severity=DiagnosticSeverity.Error,
                        source=SOURCE,
                    ))

            # Valida ogni dimensione presente
            for dim in VOICE_DIMENSIONS:
                if dim not in voices_keys:
                    continue
                dim_line = voices_keys[dim]
                dim_raw = lines[dim_line]
                dim_stripped = dim_raw.strip()

                # Raccoglie chiave/valore del blocco dimensione (a 8 spazi)
                dim_keys: Dict[str, Tuple[str, int]] = {}  # key -> (value, line_no)

                # Controlla se la dimensione usa sintassi inline dict: pan: {strategy: ..., k: v}
                inline_m = re.match(
                    r'^[a-zA-Z_]\w*\s*:\s*\{(.+)\}', dim_stripped
                )
                if inline_m:
                    # Parsea le coppie key: value dall'inline dict
                    for pair in inline_m.group(1).split(','):
                        pair = pair.strip()
                        pm = re.match(r'^([a-zA-Z_]\w*)\s*:\s*(.*)', pair)
                        if pm:
                            dim_keys[pm.group(1)] = (pm.group(2).strip().strip('"\''), dim_line)
                else:
                    # Trova la fine del blocco dimensione
                    dim_end = voices_end
                    for n in range(dim_line + 1, voices_end):
                        raw = lines[n]
                        if not raw.strip():
                            continue
                        leading = len(raw) - len(raw.lstrip())
                        if leading <= 6:
                            dim_end = n
                            break

                    for n in range(dim_line + 1, dim_end):
                        raw = lines[n]
                        stripped = raw.strip()
                        leading = len(raw) - len(raw.lstrip())
                        if leading != 8 or not stripped or stripped.startswith('#'):
                            continue
                        m = re.match(r'^([a-zA-Z_][a-zA-Z0-9_]*)\s*:\s*(.*)', stripped)
                        if m:
                            dim_keys[m.group(1)] = (m.group(2).strip().strip('"\''), n)

                # Hard break PGE: voices.pitch.semitone_range -> pitch_range.
                # Segnalato prima dei check di strategy: la chiave e' un
                # errore indipendentemente dalla strategy attiva.
                if dim == 'pitch' and 'semitone_range' in dim_keys:
                    _sr_val, sr_line = dim_keys['semitone_range']
                    diagnostics.append(Diagnostic(
                        range=self._line_range(sr_line),
                        message=(
                            "`semitone_range` non esiste più: rinominato in "
                            "`pitch_range` (stesso valore, letto nell'unità "
                            "attiva: semitones/cents/edo/ratio…)."
                        ),
                        severity=DiagnosticSeverity.Error,
                        source=SOURCE,
                    ))

                # 1. Controlla che 'strategy' sia presente
                if 'strategy' not in dim_keys:
                    diagnostics.append(Diagnostic(
                        range=self._line_range(dim_line),
                        message=(
                            f"Il blocco `{dim}` in `voices` richiede la chiave `strategy`. "
                            f"Strategy disponibili: "
                            f"{', '.join(VOICE_STRATEGY_REGISTRY.get(dim, {}).keys())}."
                        ),
                        severity=DiagnosticSeverity.Warning,
                        source=SOURCE,
                    ))
                    continue

                strategy_val, strategy_line = dim_keys['strategy']

                # 2. Controlla che il nome strategy sia valido
                # Salta se il valore è vuoto: _check_missing_values produce già un Error.
                if not strategy_val:
                    continue

                valid_strategies = list(VOICE_STRATEGY_REGISTRY.get(dim, {}).keys())
                if strategy_val not in valid_strategies:
                    diagnostics.append(Diagnostic(
                        range=self._line_range(strategy_line),
                        message=(
                            f"Strategy `{strategy_val}` non valida per `voices.{dim}`. "
                            f"Valori consentiti: {', '.join(f'`{s}`' for s in valid_strategies)}."
                        ),
                        severity=DiagnosticSeverity.Error,
                        source=SOURCE,
                    ))
                    continue

                spec = get_strategy_spec(dim, strategy_val)
                if spec is None:
                    continue

                # 2b. Validazioni pitch unit-aware (unit, lock semitoni,
                # ampiezza con ratio, inversion degli accordi).
                if dim == 'pitch':
                    diagnostics.extend(
                        self._check_voice_pitch_unit(dim_keys, strategy_val)
                    )
                    if strategy_val == 'chord_progression':
                        diagnostics.extend(
                            self._check_chord_progression(
                                lines, dim_line, voices_end
                            )
                        )

                # 3. Controlla i kwargs richiesti e i valori enum
                for kwarg_name, kwarg_spec in spec.kwargs.items():
                    if kwarg_name not in dim_keys:
                        if kwarg_spec.required:
                            # pitch_range mancante ma semitone_range presente:
                            # l'Error di rename sopra e' gia' la diagnosi giusta.
                            if (kwarg_name == 'pitch_range'
                                    and 'semitone_range' in dim_keys):
                                continue
                            diagnostics.append(Diagnostic(
                                range=self._line_range(dim_line),
                                message=(
                                    f"La strategy `{strategy_val}` in `voices.{dim}` "
                                    f"richiede il kwarg `{kwarg_name}`."
                                ),
                                severity=DiagnosticSeverity.Warning,
                                source=SOURCE,
                            ))
                        continue

                    kwarg_val_str, kwarg_line = dim_keys[kwarg_name]

                    # Valore vuoto: _check_missing_values produce già un Error.
                    if not kwarg_val_str:
                        continue

                    # Controlla valori enum
                    if (kwarg_spec.type == 'enum'
                            and kwarg_spec.enum_values is not None
                            and kwarg_val_str not in kwarg_spec.enum_values):
                        valid_vals = ', '.join(
                            f'`{v}`' for v in kwarg_spec.enum_values
                        )
                        diagnostics.append(Diagnostic(
                            range=self._line_range(kwarg_line),
                            message=(
                                f"Valore `{kwarg_val_str}` non valido per "
                                f"`voices.{dim}.{strategy_val}.{kwarg_name}`. "
                                f"Valori consentiti: {valid_vals}."
                            ),
                            severity=DiagnosticSeverity.Error,
                            source=SOURCE,
                        ))

                    # Controlla valori bool
                    if (kwarg_spec.type == 'bool'
                            and kwarg_val_str not in ('true', 'false')):
                        diagnostics.append(Diagnostic(
                            range=self._line_range(kwarg_line),
                            message=(
                                f"Valore `{kwarg_val_str}` non valido per "
                                f"`voices.{dim}.{kwarg_name}`. "
                                f"Valori consentiti: `true`, `false`."
                            ),
                            severity=DiagnosticSeverity.Error,
                            source=SOURCE,
                        ))

        return diagnostics

    def _check_voice_pitch_unit(self, dim_keys: Dict[str, Tuple[str, int]],
                                 strategy_val: str) -> List[Diagnostic]:
        """
        Validazioni unit-aware del blocco voices.pitch (issue #9/#10):

          1. `unit` deve essere un preset valido o `{edo: N}` (N intero > 0).
          2. chord/spectral sono semitone-locked: `unit` diverso da
             `semitones` -> Error (speculare a InvalidStrategyConfigError).
          3. Con `unit: ratio` l'ampiezza (`step`/`pitch_range`) deve essere
             > 0: con valore <= 0 il motore produce identità -> Warning.
          4. `inversion` di chord: intero in [0, n_note-1] dell'accordo.
        """
        diagnostics: List[Diagnostic] = []

        unit_kind = None
        unit_payload = None
        if 'unit' in dim_keys:
            unit_val, unit_line = dim_keys['unit']
            if unit_val:
                kind, payload = parse_voice_unit_value(unit_val)
                if kind == 'invalid':
                    diagnostics.append(Diagnostic(
                        range=self._line_range(unit_line),
                        message=(
                            f"Valore `{payload}` non valido per "
                            "`voices.pitch.unit`. Unità disponibili: "
                            + ', '.join(f'`{u}`' for u in VOICE_PITCH_UNIT_VALUES)
                            + " oppure `{edo: N}`."
                        ),
                        severity=DiagnosticSeverity.Error,
                        source=SOURCE,
                    ))
                else:
                    unit_kind, unit_payload = kind, payload
                    if (strategy_val in SEMITONE_LOCKED_STRATEGIES
                            and not (kind == 'preset'
                                     and payload == 'semitones')):
                        diagnostics.append(Diagnostic(
                            range=self._line_range(unit_line),
                            message=(
                                f"La strategia `{strategy_val}` è definita "
                                "in semitoni: ometti `unit` oppure usa "
                                "`semitones`."
                            ),
                            severity=DiagnosticSeverity.Error,
                            source=SOURCE,
                        ))

        # Ampiezza > 0 con unit: ratio (amount <= 0 -> identità nel motore)
        if unit_kind == 'preset' and unit_payload == 'ratio':
            amp_key = ('step' if strategy_val == 'step'
                       else 'pitch_range'
                       if strategy_val in ('range', 'stochastic') else None)
            if amp_key and amp_key in dim_keys:
                amp_val, amp_line = dim_keys[amp_key]
                numeric = self._try_parse_number(amp_val)
                if numeric is not None and numeric <= 0:
                    diagnostics.append(Diagnostic(
                        range=self._line_range(amp_line),
                        message=(
                            f"Con `unit: ratio` l'ampiezza `{amp_key}` deve "
                            f"essere > 0: con {numeric} il motore produce "
                            "identità (nessuna distribuzione)."
                        ),
                        severity=DiagnosticSeverity.Warning,
                        source=SOURCE,
                    ))

        # inversion di chord: intero in [0, n_note-1]
        if strategy_val == 'chord' and 'inversion' in dim_keys:
            inv_val, inv_line = dim_keys['inversion']
            if inv_val:
                if not re.fullmatch(r'[+-]?\d+', inv_val):
                    diagnostics.append(Diagnostic(
                        range=self._line_range(inv_line),
                        message=(
                            f"`inversion` deve essere un intero ≥ 0, "
                            f"trovato `{inv_val}`."
                        ),
                        severity=DiagnosticSeverity.Error,
                        source=SOURCE,
                    ))
                else:
                    chord_name = dim_keys.get('chord', ('', 0))[0]
                    if chord_name in CHORD_INTERVALS:
                        n_note = len(CHORD_INTERVALS[chord_name])
                        inversion = int(inv_val)
                        if not (0 <= inversion < n_note):
                            diagnostics.append(Diagnostic(
                                range=self._line_range(inv_line),
                                message=(
                                    f"L'accordo `{chord_name}` ha {n_note} "
                                    f"note: `inversion` deve essere in "
                                    f"[0, {n_note - 1}]."
                                ),
                                severity=DiagnosticSeverity.Error,
                                source=SOURCE,
                            ))

        return diagnostics

    @staticmethod
    def _safe_yaml(text: str):
        """Parsa un frammento YAML in modo tollerante; None su errore."""
        try:
            return yaml.safe_load(text)
        except Exception:
            return None

    def _check_chord_progression(self, lines: List[str], dim_line: int,
                                  voices_end: int) -> List[Diagnostic]:
        """
        Valida il kwarg `progression` della strategy pitch `chord_progression`.

        Controlli (speculari a ChordProgressionPitchStrategy del motore):
          1. `progression` è una lista non vuota.
          2. Ogni step è `[tempo, accordo]` (o `[t, chord, inversion]` /
             `[t, {chord, inversion}]`); il tempo è numerico.
          3. I tempi sono non decrescenti.
          4. Il nome accordo è in CHORD_INTERVALS.
          5. `inversion` è un intero in `[0, n_note − 1]` dell'accordo.

        Parsing tollerante: ogni step viene letto singolarmente con
        yaml.safe_load, così una riga malformata non azzera gli altri check e
        le diagnostiche puntano alla riga esatta. Se `progression` è assente il
        metodo non emette nulla (il warning di kwarg richiesto è già prodotto
        dal loop principale).
        """
        diagnostics: List[Diagnostic] = []

        # Confine del blocco pitch (indent <= 6 chiude la dimensione).
        block_end = voices_end
        for n in range(dim_line + 1, voices_end):
            raw = lines[n]
            if not raw.strip():
                continue
            leading = len(raw) - len(raw.lstrip())
            if leading <= 6:
                block_end = n
                break

        # Trova la chiave progression: (indent 8) dentro il blocco pitch.
        prog_line = None
        prog_inline = ''
        for n in range(dim_line + 1, block_end):
            raw = lines[n]
            leading = len(raw) - len(raw.lstrip())
            if leading != 8:
                continue
            m = re.match(r'^progression\s*:\s*(.*)', raw.strip())
            if m:
                prog_line = n
                prog_inline = m.group(1).strip()
                break

        if prog_line is None:
            return diagnostics

        # Raccoglie gli step come (valore_parsato, riga).
        entries: List[Tuple[object, int]] = []
        if prog_inline and not prog_inline.startswith('#'):
            # Forma inline flow: progression: [[0, maj7], ...]
            parsed = self._safe_yaml(prog_inline)
            if isinstance(parsed, list):
                for item in parsed:
                    entries.append((item, prog_line))
            elif parsed is not None:
                diagnostics.append(Diagnostic(
                    range=self._line_range(prog_line),
                    message=(
                        "`progression` deve essere una lista non vuota di "
                        "step `[tempo, accordo]`."
                    ),
                    severity=DiagnosticSeverity.Error,
                    source=SOURCE,
                ))
                return diagnostics
        else:
            # Forma a blocco: item '- [...]' a indent > 8.
            for n in range(prog_line + 1, block_end):
                raw = lines[n]
                stripped = raw.strip()
                if not stripped or stripped.startswith('#'):
                    continue
                leading = len(raw) - len(raw.lstrip())
                if leading <= 8:
                    break
                if stripped == '-':
                    entries.append((None, n))
                elif stripped.startswith('- '):
                    entries.append((self._safe_yaml(stripped[2:].strip()), n))

        if not entries:
            diagnostics.append(Diagnostic(
                range=self._line_range(prog_line),
                message=(
                    "`progression` è vuota: serve almeno uno step "
                    "`[tempo, accordo]`."
                ),
                severity=DiagnosticSeverity.Error,
                source=SOURCE,
            ))
            return diagnostics

        prev_t = None
        for item, ln in entries:
            if not isinstance(item, list) or len(item) < 2:
                diagnostics.append(Diagnostic(
                    range=self._line_range(ln),
                    message=(
                        "Ogni step della progressione deve essere "
                        "`[tempo, accordo]` (opzionale `[t, chord, inversion]` "
                        "o `[t, {chord, inversion}]`)."
                    ),
                    severity=DiagnosticSeverity.Error,
                    source=SOURCE,
                ))
                continue

            t = item[0]
            spec = item[1]
            inversion: object = 0
            if isinstance(spec, dict):
                chord = spec.get('chord')
                inversion = spec.get('inversion', 0)
            else:
                chord = spec
                if len(item) >= 3:
                    inversion = item[2]

            # Tempo numerico e non decrescente.
            if isinstance(t, (int, float)) and not isinstance(t, bool):
                if prev_t is not None and t < prev_t:
                    diagnostics.append(Diagnostic(
                        range=self._line_range(ln),
                        message=(
                            f"I tempi della progressione devono essere non "
                            f"decrescenti: {t} viene dopo {prev_t}."
                        ),
                        severity=DiagnosticSeverity.Error,
                        source=SOURCE,
                    ))
                prev_t = t
            else:
                diagnostics.append(Diagnostic(
                    range=self._line_range(ln),
                    message=(
                        f"Il tempo dello step deve essere numerico, trovato "
                        f"`{t}`."
                    ),
                    severity=DiagnosticSeverity.Error,
                    source=SOURCE,
                ))

            # Nome accordo.
            if chord not in CHORD_INTERVALS:
                diagnostics.append(Diagnostic(
                    range=self._line_range(ln),
                    message=(
                        f"Accordo `{chord}` non valido. Valori consentiti: "
                        + ', '.join(f'`{c}`' for c in CHORD_INTERVALS)
                        + "."
                    ),
                    severity=DiagnosticSeverity.Error,
                    source=SOURCE,
                ))
                continue

            # inversion intero in [0, n_note-1].
            n_note = len(CHORD_INTERVALS[chord])
            if not isinstance(inversion, int) or isinstance(inversion, bool):
                diagnostics.append(Diagnostic(
                    range=self._line_range(ln),
                    message=(
                        f"`inversion` deve essere un intero, trovato "
                        f"`{inversion}`."
                    ),
                    severity=DiagnosticSeverity.Error,
                    source=SOURCE,
                ))
            elif not (0 <= inversion < n_note):
                diagnostics.append(Diagnostic(
                    range=self._line_range(ln),
                    message=(
                        f"L'accordo `{chord}` ha {n_note} note: `inversion` "
                        f"deve essere in [0, {n_note - 1}]."
                    ),
                    severity=DiagnosticSeverity.Error,
                    source=SOURCE,
                ))

        return diagnostics

    def _check_start_bypassed_by_loop_start(
        self, lines: List[str],
        streams: List[Tuple[int, int, dict]],
    ) -> List[Diagnostic]:
        """
        Warning su 'start' quando 'loop_start' e' definito come envelope
        nello stesso blocco pointer:.

        Regola: se loop_start ha un valore non scalare (lista o dict),
        il motore granulare usa loop_start come posizione iniziale e
        ignora completamente il valore di start.

        Rilevamento envelope: loop_start e' un envelope se
        - il valore inline inizia con '[' (es. loop_start: [[0,0.1],...])
        - oppure il valore e' assente (es. loop_start:\n    - [...])
          e le righe successive sono piu' indentate (blocco lista o dict)
        """
        diagnostics = []

        for stream_start, stream_end_incl, _keys in streams:
            stream_end = stream_end_incl + 1
            # Trova il blocco pointer: (header a 4 spazi)
            pointer_start = None
            for n in range(stream_start, stream_end):
                raw = lines[n]
                stripped = raw.strip()
                leading = len(raw) - len(raw.lstrip())
                if leading == 4 and (stripped == 'pointer:' or stripped.startswith('pointer:')):
                    pointer_start = n
                    break
            if pointer_start is None:
                continue

            # Trova la fine del blocco pointer (prima riga a indent <= 4 dopo l'header)
            pointer_end = stream_end
            for n in range(pointer_start + 1, stream_end):
                raw = lines[n]
                if not raw.strip():
                    continue
                if (len(raw) - len(raw.lstrip())) <= 4:
                    pointer_end = n
                    break

            # Dentro il blocco pointer: trova 'start' e 'loop_start' (a 6 spazi)
            start_line: Optional[int] = None
            loop_start_is_envelope = False

            for n in range(pointer_start + 1, pointer_end):
                raw = lines[n]
                stripped = raw.strip()
                leading = len(raw) - len(raw.lstrip())
                if not stripped or stripped.startswith('#') or leading != 6:
                    continue

                m = re.match(r'^([a-zA-Z_][a-zA-Z0-9_]*)\s*:(.*)', stripped)
                if not m:
                    continue
                key, val = m.group(1), m.group(2).strip()

                if key == 'start':
                    start_line = n
                elif key == 'loop_start':
                    if val.startswith('['):
                        # Envelope inline
                        loop_start_is_envelope = True
                    elif not val or val.startswith('#'):
                        # Valore su righe successive: controlla se piu' indentato
                        for nn in range(n + 1, pointer_end):
                            next_raw = lines[nn]
                            if not next_raw.strip():
                                continue
                            loop_start_is_envelope = (
                                len(next_raw) - len(next_raw.lstrip())
                            ) > 6
                            break

            if start_line is not None and loop_start_is_envelope:
                diagnostics.append(Diagnostic(
                    range=self._line_range(start_line),
                    message=(
                        '`start` ridondante: quando `loop_start` e\' un envelope, '
                        'il motore usa `loop_start(0)` come posizione iniziale. '
                        'Rimuovi `start` per lasciare che il motore lo calcoli automaticamente.'
                    ),
                    severity=DiagnosticSeverity.Warning,
                    source=SOURCE,
                ))

        return diagnostics

    def _check_loop_dur_overrides_loop_end(
        self, lines: List[str],
        streams: List[Tuple[int, int, dict]],
    ) -> List[Diagnostic]:
        """
        Warning quando loop_dur e loop_end sono entrambi presenti nello stesso
        blocco pointer:.

        Regola motore: se loop_dur e' definito, viene usato per calcolare
        loop_end = loop_start + loop_dur, ignorando completamente loop_end.
        """
        diagnostics = []

        for stream_start, stream_end_incl, _keys in streams:
            stream_end = stream_end_incl + 1
            # Trova il blocco pointer: (header a 4 spazi)
            pointer_start = None
            for n in range(stream_start, stream_end):
                raw = lines[n]
                stripped = raw.strip()
                leading = len(raw) - len(raw.lstrip())
                if leading == 4 and (stripped == 'pointer:' or stripped.startswith('pointer:')):
                    pointer_start = n
                    break
            if pointer_start is None:
                continue

            # Trova la fine del blocco pointer
            pointer_end = stream_end
            for n in range(pointer_start + 1, stream_end):
                raw = lines[n]
                if not raw.strip():
                    continue
                if (len(raw) - len(raw.lstrip())) <= 4:
                    pointer_end = n
                    break

            # Dentro il blocco pointer: trova loop_dur e loop_end (a 6 spazi)
            loop_dur_line: Optional[int] = None
            loop_end_line: Optional[int] = None

            for n in range(pointer_start + 1, pointer_end):
                raw = lines[n]
                stripped = raw.strip()
                leading = len(raw) - len(raw.lstrip())
                if not stripped or stripped.startswith('#') or leading != 6:
                    continue
                m = re.match(r'^([a-zA-Z_][a-zA-Z0-9_]*)\s*:', stripped)
                if not m:
                    continue
                key = m.group(1)
                if key == 'loop_dur':
                    loop_dur_line = n
                elif key == 'loop_end':
                    loop_end_line = n

            if loop_dur_line is not None and loop_end_line is not None:
                diagnostics.append(Diagnostic(
                    range=self._line_range(loop_end_line),
                    message=(
                        '`loop_end` ignorato: quando `loop_dur` e\' definito, '
                        'il motore calcola `loop_end = loop_start + loop_dur` '
                        'e ignora il valore di `loop_end`. '
                        'Rimuovi `loop_end` oppure usa solo `loop_end` senza `loop_dur`.'
                    ),
                    severity=DiagnosticSeverity.Warning,
                    source=SOURCE,
                ))
                diagnostics.append(Diagnostic(
                    range=self._line_range(loop_dur_line),
                    message=(
                        '`loop_dur` ha priorita\' su `loop_end`: '
                        'il motore usa `loop_end = loop_start + loop_dur`.'
                    ),
                    severity=DiagnosticSeverity.Warning,
                    source=SOURCE,
                ))

        return diagnostics

    def _check_loop_end_le_loop_start(
        self, lines: List[str],
        streams: List[Tuple[int, int, dict]],
    ) -> List[Diagnostic]:
        """
        Error quando loop_end <= loop_start (entrambi valori scalari) nello
        stesso blocco pointer:.

        Speculare all'InvalidFieldValueError che il motore solleva per una
        finestra di loop statica degenere (PGE commit ec61242). Vale solo per
        i bound statici: se loop_start o loop_end sono envelope (lista/dict)
        gli endpoint sono dinamici e l'engine esenta il controllo.

        loop_dur ha priorita' su loop_end: se loop_dur e' presente, loop_end
        viene ignorato dal motore e non ha senso segnalare la degenerazione
        (gia' coperto dal warning di _check_loop_dur_overrides_loop_end).
        """
        diagnostics = []

        for stream_start, stream_end_incl, _keys in streams:
            stream_end = stream_end_incl + 1
            # Trova il blocco pointer: (header a 4 spazi)
            pointer_start = None
            for n in range(stream_start, stream_end):
                raw = lines[n]
                stripped = raw.strip()
                leading = len(raw) - len(raw.lstrip())
                if leading == 4 and (stripped == 'pointer:' or stripped.startswith('pointer:')):
                    pointer_start = n
                    break
            if pointer_start is None:
                continue

            # Trova la fine del blocco pointer
            pointer_end = stream_end
            for n in range(pointer_start + 1, stream_end):
                raw = lines[n]
                if not raw.strip():
                    continue
                if (len(raw) - len(raw.lstrip())) <= 4:
                    pointer_end = n
                    break

            # Raccoglie i valori scalari di loop_start/loop_end (a 6 spazi) e
            # rileva la presenza di loop_dur.
            loop_start_val: Optional[float] = None
            loop_end_val: Optional[float] = None
            loop_end_line: Optional[int] = None
            has_loop_dur = False

            for n in range(pointer_start + 1, pointer_end):
                raw = lines[n]
                stripped = raw.strip()
                leading = len(raw) - len(raw.lstrip())
                if not stripped or stripped.startswith('#') or leading != 6:
                    continue
                m = re.match(r'^([a-zA-Z_][a-zA-Z0-9_]*)\s*:\s*(.*)', stripped)
                if not m:
                    continue
                key, val_str = m.group(1), m.group(2).strip()
                if key == 'loop_dur':
                    has_loop_dur = True
                    continue
                if key not in ('loop_start', 'loop_end'):
                    continue
                # Envelope o valore vuoto: endpoint dinamici, esenti dal check.
                if not val_str or val_str.startswith('[') or val_str.startswith('{') \
                        or val_str.startswith('#'):
                    continue
                try:
                    val = float(val_str)
                except ValueError:
                    continue
                if key == 'loop_start':
                    loop_start_val = val
                else:
                    loop_end_val = val
                    loop_end_line = n

            # loop_dur vince su loop_end: nessuna segnalazione di degenerazione.
            if has_loop_dur:
                continue

            if (loop_start_val is not None and loop_end_val is not None
                    and loop_end_val <= loop_start_val):
                diagnostics.append(Diagnostic(
                    range=self._line_range(loop_end_line),
                    message=(
                        f'`loop_end` ({loop_end_val}) deve essere maggiore di '
                        f'`loop_start` ({loop_start_val}): finestra di loop '
                        'degenere. Per un loop a cavallo della fine del file usa '
                        '`loop_dur` (`loop_end` resta confinato a '
                        '[0, sample_dur]).'
                    ),
                    severity=DiagnosticSeverity.Error,
                    source=SOURCE,
                ))

        return diagnostics

    # -------------------------------------------------------------------------
    # FASE 9: BOUNDS DINAMICI PARAMETRI POINTER
    # -------------------------------------------------------------------------

    @staticmethod
    def _read_wav_duration(path: str) -> Optional[float]:
        """
        Legge la durata in secondi di un file WAV.

        Supporta PCM intero (format 1) e IEEE float (format 3),
        leggendo l'header RIFF manualmente per evitare il limite
        del modulo wave (solo format 1).

        Ritorna None se il file non esiste o non e' leggibile.
        """
        import struct
        try:
            with open(path, 'rb') as f:
                # RIFF header: "RIFF" size "WAVE"
                riff = f.read(12)
                if len(riff) < 12 or riff[:4] != b'RIFF' or riff[8:12] != b'WAVE':
                    return None
                # Scansiona i chunk fino a trovare 'fmt ' e 'data'
                sample_rate = None
                block_align = None
                data_size = None
                while True:
                    chunk_hdr = f.read(8)
                    if len(chunk_hdr) < 8:
                        break
                    chunk_id = chunk_hdr[:4]
                    chunk_size = struct.unpack_from('<I', chunk_hdr, 4)[0]
                    if chunk_id == b'fmt ':
                        fmt_data = f.read(chunk_size)
                        if len(fmt_data) < 16:
                            break
                        sample_rate = struct.unpack_from('<I', fmt_data, 4)[0]
                        block_align = struct.unpack_from('<H', fmt_data, 12)[0]
                    elif chunk_id == b'data':
                        data_size = chunk_size
                        break
                    else:
                        f.seek(chunk_size, 1)  # salta chunk sconosciuto
                if sample_rate and block_align and data_size is not None:
                    n_frames = data_size // block_align
                    return n_frames / sample_rate
                return None
        except Exception:
            return None

    _POINTER_SCALAR_PARAMS = {'start', 'loop_start', 'loop_end', 'loop_dur'}

    # L'hint del motore, parola per parola: le due strade vere per far
    # variare nel tempo la posizione di lettura.
    _POINTER_START_HINT = (
        "e' un valore scalare — la posizione di partenza nel sample — e non "
        "accetta envelope. Se non ti serve, ometti la chiave: il default e' "
        "0.0 (o loop_start, con un loop attivo). Per far variare nel tempo la "
        "posizione di lettura usa `pointer.speed_ratio`, oppure un loop mobile "
        "con `loop_start` come envelope."
    )

    def _check_pointer_start_envelope(
        self, lines: List[str], key_line: int, block_end: int,
    ) -> Optional[Diagnostic]:
        """
        Segnala `pointer.start` scritto come envelope (PGE #199, PR #200).

        La spec dichiara `pointer_start` con `is_smart=False`: il valore non
        diventa mai un `Parameter`, resta grezzo, e `calculate` lo somma alla
        posizione. Il motore lo ferma all'inizializzazione dello stream con
        un `InvalidFieldValueError`; prima cadeva piu' a valle come
        `TypeError` dentro la generazione dei grani, dove non c'era piu' modo
        di dire all'utente cosa avesse scritto.

        Si segnalano le **strutture** — lista di breakpoint, formato compatto,
        BP group, dict con `points` — e non ogni valore non numerico. Una
        stringa puo' essere legittima: il Generator valuta le espressioni fra
        parentesi (`(10/2)` → 5.0) su tutto lo YAML prima che il
        PointerController veda il valore.

        Ritorna None per la chiave vuota (materia di _check_missing_values) e
        per il frammento non interpretabile, che e' il documento a meta'
        scrittura.
        """
        found, raw = self._read_key_value(lines, key_line, block_end)
        if not found or not isinstance(raw, (list, dict)):
            return None
        return Diagnostic(
            range=self._line_range(key_line),
            message=f"'pointer.start' {self._POINTER_START_HINT}",
            severity=DiagnosticSeverity.Error,
            source=SOURCE,
        )

    def _check_pointer_param_bounds(
        self, document_text: str, lines: List[str],
        streams: List[Tuple[int, int, dict]], refs_dir: str,
    ) -> List[Diagnostic]:
        """
        Valida i valori scalari di start, loop_start, loop_end, loop_dur
        nel blocco pointer: di ogni stream.

        Bounds applicati:
          - normalized (loop_unit=normalized o time_mode=normalized):
              [0.0, 1.0]
          - absolute (default):
              [0.0, durata_sample] se il file WAV e' leggibile,
              altrimenti solo [0.0, +inf] (controlla solo limite inferiore)

        I valori envelope vengono ignorati per loop_start, loop_end e
        loop_dur, che gli envelope li accettano davvero. Per `start` no:
        li' una curva e' un errore, e la segnala
        _check_pointer_start_envelope.

        document_text resta necessario per _get_effective_unit_mode
        (helper condiviso con l'hover, lavora sul testo completo).
        """
        from granular_ls.providers.hover_provider import _get_effective_unit_mode

        diagnostics = []

        for stream_start, stream_end_incl, _keys in streams:
            stream_end = stream_end_incl + 1
            # Estrai il path del sample da questo stream (chiave a indent 4)
            sample_path_raw = ''
            for n in range(stream_start, stream_end):
                raw = lines[n]
                stripped = raw.strip()
                if stripped.startswith('- '):
                    stripped = stripped[2:].strip()
                leading = len(raw) - len(raw.lstrip())
                if leading > 4:
                    continue
                m = re.match(r'^sample\s*:\s*(.+)', stripped)
                if m:
                    sample_path_raw = m.group(1).strip().strip('"\'')
                    break

            # Trova il blocco pointer: (a 4 spazi)
            pointer_start = None
            for n in range(stream_start, stream_end):
                raw = lines[n]
                stripped = raw.strip()
                leading = len(raw) - len(raw.lstrip())
                if leading == 4 and (stripped == 'pointer:' or stripped.startswith('pointer:')):
                    pointer_start = n
                    break
            if pointer_start is None:
                continue

            pointer_end = stream_end
            for n in range(pointer_start + 1, stream_end):
                raw = lines[n]
                if not raw.strip():
                    continue
                if (len(raw) - len(raw.lstrip())) <= 4:
                    pointer_end = n
                    break

            # Determina modalita' (normalized vs absolute)
            mode, _ = _get_effective_unit_mode(document_text, pointer_start + 1)

            # Calcola i bounds
            if mode == 'normalized':
                min_val, max_val = 0.0, 1.0
                unit_label = 'normalized [0.0, 1.0]'
            else:
                min_val = 0.0
                max_val = None
                unit_label = 'secondi assoluti'
                if sample_path_raw and refs_dir:
                    # refs_dir e' il path assoluto a refs/ del progetto PGE.
                    # Il sample e' sempre relativo a refs/.
                    dur = self._read_wav_duration(
                        os.path.join(refs_dir, sample_path_raw)
                    )
                    if dur is not None:
                        max_val = dur
                        unit_label = f'secondi assoluti [0.0, {dur:.3f}s]'

            # Valida le chiavi scalari a 6 spazi
            for n in range(pointer_start + 1, pointer_end):
                raw = lines[n]
                stripped = raw.strip()
                leading = len(raw) - len(raw.lstrip())
                if not stripped or stripped.startswith('#') or leading != 6:
                    continue
                m = re.match(r'^([a-zA-Z_][a-zA-Z0-9_]*)\s*:\s*(.*)', stripped)
                if not m:
                    continue
                key, val_str = m.group(1), m.group(2).strip()
                if key not in self._POINTER_SCALAR_PARAMS:
                    continue
                # `start` e' il solo dei quattro a non accettare envelope:
                # va deciso prima dello skip, che altrimenti lo rende muto.
                if key == 'start':
                    diag = self._check_pointer_start_envelope(
                        lines, n, pointer_end
                    )
                    if diag is not None:
                        diagnostics.append(diag)
                        continue
                # Salta envelope e valori vuoti
                if not val_str or val_str.startswith('[') or val_str.startswith('#'):
                    continue
                # Prova a parsare come float
                try:
                    val = float(val_str)
                except ValueError:
                    continue

                if val < min_val:
                    diagnostics.append(Diagnostic(
                        range=self._line_range(n),
                        message=(
                            f'`{key}` = {val_str}: valore negativo non valido. '
                            f'Il minimo e\' {min_val} ({unit_label}).'
                        ),
                        severity=DiagnosticSeverity.Error,
                        source=SOURCE,
                    ))
                elif max_val is not None and val > max_val:
                    if mode == 'normalized':
                        msg = (
                            f'`{key}` = {val_str} fuori bounds normalized: '
                            f'il valore deve essere in [0.0, 1.0].'
                        )
                    else:
                        msg = (
                            f'`{key}` = {val_str} supera la durata del sample '
                            f'({max_val:.3f}s). Valore fuori bounds ({unit_label}).'
                        )
                    diagnostics.append(Diagnostic(
                        range=self._line_range(n),
                        message=msg,
                        severity=DiagnosticSeverity.Error,
                        source=SOURCE,
                    ))

        return diagnostics

    # -------------------------------------------------------------------------
    # FASE 11: grain.duration_unit (PGE #158)
    # -------------------------------------------------------------------------

    def _scan_grain_blocks(
        self, lines: List[str],
        streams: List[Tuple[int, int, dict]],
    ) -> List[dict]:
        """
        Scansiona il blocco grain: (indent 4) di ogni stream e ne estrae
        la superficie di duration_unit e quella del verso di lettura.

        Ritorna una lista di dict, uno per blocco grain trovato:
          - start, end:      confini del blocco grain (end escluso)
          - unit:            valore di duration_unit (str) o None se assente
          - unit_present:    True se la chiave duration_unit è scritta
          - unit_line:       riga della chiave duration_unit (o None)
          - has_duration:    True se grain.duration è presente con un valore
          - duration_scalar: valore scalare di duration (str) se numerico inline
          - duration_line:   riga della chiave duration (o None)
          - value_lines:     set di righe che portano valori di duration /
                             duration_range (scalare + breakpoint envelope),
                             usato per sopprimere i falsi positivi dei bound
                             generici quando l'unità è samples.
          - read_direction_line: riga della chiave read_direction (o None)
          - reverse_line:    riga della chiave reverse (o None)

        Le due righe del verso servono a _check_read_direction: il gruppo
        esclusivo 'grain_direction' è per-blocco-grain, quindi va deciso qui
        dove i confini del blocco sono già noti.
        """
        blocks: List[dict] = []
        for stream_start, stream_end_incl, _keys in streams:
            stream_end = stream_end_incl + 1
            grain_start = None
            for n in range(stream_start, stream_end):
                raw = lines[n]
                stripped = raw.strip()
                leading = len(raw) - len(raw.lstrip())
                if leading == 4 and stripped == 'grain:':
                    grain_start = n
                    break
            if grain_start is None:
                continue

            grain_end = stream_end
            for n in range(grain_start + 1, stream_end):
                raw = lines[n]
                if not raw.strip():
                    continue
                if (len(raw) - len(raw.lstrip())) <= 4:
                    grain_end = n
                    break

            info = {
                'start': grain_start, 'end': grain_end,
                'unit': None, 'unit_present': False, 'unit_line': None,
                'has_duration': False, 'duration_scalar': None,
                'duration_line': None, 'value_lines': set(),
                'read_direction_line': None, 'reverse_line': None,
            }

            n = grain_start + 1
            while n < grain_end:
                raw = lines[n]
                stripped = raw.strip()
                leading = len(raw) - len(raw.lstrip())
                if not stripped or stripped.startswith('#') or leading != 6:
                    n += 1
                    continue
                m = re.match(r'^([a-zA-Z_][a-zA-Z0-9_]*)\s*:\s*(.*)', stripped)
                if not m:
                    n += 1
                    continue
                key, val = m.group(1), m.group(2).strip()

                if key == 'read_direction':
                    info['read_direction_line'] = n
                elif key == 'reverse':
                    info['reverse_line'] = n
                elif key == 'duration_unit':
                    info['unit_present'] = True
                    info['unit_line'] = n
                    info['unit'] = val.strip('"\'') if val else None
                elif key in ('duration', 'duration_range'):
                    info['value_lines'].add(n)
                    if key == 'duration':
                        info['duration_line'] = n
                        # Valore presente: inline (scalare/lista) o envelope
                        # block-style sulle righe seguenti (indent > 6).
                        if val:
                            info['has_duration'] = True
                            if not val.startswith('['):
                                info['duration_scalar'] = val
                    # Raccoglie le righe breakpoint dell'envelope block-style.
                    if not val:
                        m2 = n + 1
                        while m2 < grain_end:
                            r2 = lines[m2]
                            if not r2.strip():
                                m2 += 1
                                continue
                            if (len(r2) - len(r2.lstrip())) <= 6:
                                break
                            info['value_lines'].add(m2)
                            if key == 'duration':
                                info['has_duration'] = True
                            m2 += 1
                n += 1

            blocks.append(info)
        return blocks

    def _check_read_direction(self, lines: List[str],
                              grain_blocks: List[dict]) -> List[Diagnostic]:
        """
        Valida `grain.read_direction` (PGE #207) e il gruppo 'grain_direction'.

        Due controlli, nell'ordine in cui li fa il motore:

        1. **`reverse` + `read_direction` insieme**: errore esplicito, non una
           priorità. Il motore lo solleva prima di ogni altra validazione, e
           qui si fa lo stesso — con entrambe le chiavi scritte non ha senso
           dire anche cosa c'è che non va nel valore di una delle due.
        2. **Il valore**: dominio `{-1, +1}`, `step` imposto, e i guard sulle
           macro-forme. La regola sta in `read_direction.py`.

        Ancoraggio: la riga della chiave. Il valore incriminato è nel
        messaggio, così un envelope block-style resta leggibile anche quando
        il punto sbagliato è qualche riga più giù.
        """
        diagnostics: List[Diagnostic] = []

        for block in grain_blocks:
            rd_line = block['read_direction_line']
            rev_line = block['reverse_line']

            if rd_line is None:
                continue

            if rev_line is not None:
                # Le due chiavi governano la stessa grandezza con semantiche
                # opposte: il render fallisce, non sceglie.
                message = f"Gruppo esclusivo 'grain_direction': {EXCLUSIVE_HINT}"
                for n in (rd_line, rev_line):
                    diagnostics.append(Diagnostic(
                        range=self._line_range(n),
                        message=message,
                        severity=DiagnosticSeverity.Error,
                        source=SOURCE,
                    ))
                continue

            found, raw = self._read_key_value(lines, rd_line, block['end'])
            if not found:
                # Frammento non interpretabile come YAML: l'utente sta ancora
                # scrivendo. Tolleranza, come altrove nel provider.
                continue

            issue = check_read_direction(raw)
            if issue is None:
                continue

            diagnostics.append(Diagnostic(
                range=self._line_range(rd_line),
                message=(
                    f"'{READ_DIRECTION_PATH}': {issue.value!r} non è ammesso. "
                    f"{issue.hint}"
                ),
                severity=DiagnosticSeverity.Error,
                source=SOURCE,
            ))

        return diagnostics

    def _check_deviation_probability(
        self, lines: List[str],
        streams: List[Tuple[int, int, dict]],
    ) -> List[Diagnostic]:
        """
        Segnala il corpo di `deviation_probability` che non si costruisce come
        envelope (PGE #209).

        Prima quel corpo veniva accettato in silenzio e diventava un
        `AlwaysGate` — probabilità 100%, la variazione su tutti i grani, cioè
        il gate più lontano da quanto scritto. Ora il motore lo rifiuta, e la
        regola sta in `deviation_probability.py`.

        L'ancoraggio segue il campo che il motore nominerebbe: la riga della
        chiave per-parametro quando l'errore è suo, quella di
        `deviation_probability` per la forma globale. Così un dict lungo non
        manda l'utente a cercare quale delle sue chiavi non va.
        """
        diagnostics: List[Diagnostic] = []

        for stream_start, stream_end_incl, _keys in streams:
            stream_end = stream_end_incl + 1
            key_line = None
            for n in range(stream_start, stream_end):
                raw = lines[n]
                stripped = raw.strip()
                if stripped.startswith('- '):
                    stripped = stripped[2:].strip()
                    leading = 4
                else:
                    leading = len(raw) - len(raw.lstrip())
                if leading == 4 and re.match(
                        r'^deviation_probability\s*:', stripped):
                    key_line = n
                    break
            if key_line is None:
                continue

            found, raw_value = self._read_key_value(lines, key_line, stream_end)
            if not found:
                # Frammento non interpretabile: documento a metà scrittura.
                continue

            issue = check_global_value(raw_value)
            if issue is None:
                continue

            anchor = key_line
            if issue.param_key is not None:
                # La ricerca si ferma al blocco: in flow style la chiave
                # interna non ha una riga propria, e scorrere fino a fine
                # stream la farebbe combaciare con la chiave omonima di
                # livello stream — una riga corretta, che non c'entra niente
                # con l'errore. Senza una riga sua l'ancora resta qui.
                param_line = self._find_key_line(
                    lines,
                    key_line + 1,
                    self._block_end(lines, key_line, stream_end, 4),
                    issue.param_key,
                )
                if param_line is not None:
                    anchor = param_line

            diagnostics.append(Diagnostic(
                range=self._line_range(anchor),
                message=(
                    f"'{issue.field}': {issue.value!r} non è ammesso. "
                    f"{issue.hint}"
                ),
                severity=DiagnosticSeverity.Error,
                source=SOURCE,
            ))

        return diagnostics

    # Le coppie base/_range che l'ancora governa, con il blocco in cui vivono.
    # `pointer.offset_range` resta fuori: la sua base è `_dummy_fixed_zero_`,
    # fissa a 0 e non scrivibile, quindi la banda non può sforare il tetto per
    # colpa di quanto l'utente ha scritto come base.
    _ANCHOR_BAND_PAIRS = (
        (None, 'volume', 'volume_range', 'volume'),
        (None, 'pan', 'pan_range', 'pan'),
        ('grain', 'duration', 'duration_range', 'grain.duration'),
    )

    def _check_band_ceiling(
        self, lines: List[str],
        streams: List[Tuple[int, int, dict]],
        grain_blocks: List[dict],
    ) -> List[Diagnostic]:
        """
        Con `range_anchor: min`, verifica che `base + range` stia sotto il
        tetto del parametro.

        Sotto l'ancora `center` la banda arriva a `base + range/2` e resta
        gestita dal safety clamp: è il comportamento storico. Sotto `min` no —
        la modalità promette una banda esatta, e il motore preferisce
        rifiutarla al parse (`ParameterBoundError`) invece di schiacciarla
        contro il tetto e lasciare l'utente convinto di avere la banda che ha
        scritto. Da qui la severità Error, non un warning.

        Si controlla solo dove il massimo della somma è calcolabile da un solo
        lato (scalare+scalare, envelope+scalare, scalare+envelope). Con
        entrambi envelope il massimo della somma non è la somma dei massimi —
        i due picchi possono cadere in istanti diversi — e un falso positivo
        bloccherebbe un render valido: lì tace anche il motore.

        Il picco di un envelope è stimato dai suoi breakpoint, come lato
        motore: con interpolazione cubica la curva può superarli, quindi la
        stima è per difetto. L'errore cade dalla parte giusta — si lascia
        passare una banda che sfora di poco, non se ne blocca una valida.
        """
        diagnostics: List[Diagnostic] = []
        grain_by_start = {b['start']: b for b in grain_blocks}

        for stream_start, stream_end_incl, _keys in streams:
            stream_end = stream_end_incl + 1
            if not self._anchor_is_min(lines, stream_start, stream_end):
                continue

            for block, base_key, range_key, yaml_path in self._ANCHOR_BAND_PAIRS:
                param = self._params_by_yaml_path.get(yaml_path)
                if param is None or param.max_val is None:
                    continue

                grain = None
                if block is None:
                    scope_start, scope_end, indent = stream_start, stream_end, 4
                else:
                    grain = self._grain_block_of(
                        grain_by_start, stream_start, stream_end
                    )
                    if grain is None:
                        continue
                    scope_start, scope_end, indent = (
                        grain['start'] + 1, grain['end'], 6
                    )

                # I valori di grain.duration sono nell'unità dichiarata, e il
                # motore li scala prima che il parser li veda: il confronto va
                # fatto in secondi come fa lui. Il messaggio no — rispondere
                # in secondi a chi ha scritto millisecondi è rispondere con
                # numeri che non ha mai visto. Quindi si tiene tutto
                # nell'unità dichiarata e si converte solo per il confronto.
                factor, label = 1.0, ''
                if grain is not None:
                    unit_factor = _GRAIN_DURATION_UNIT_SECONDS.get(grain['unit'])
                    if unit_factor is not None:
                        factor = unit_factor
                        label = ' ' + _GRAIN_DURATION_UNIT_LABELS[grain['unit']]

                range_line = self._find_key_line_at_indent(
                    lines, scope_start, scope_end, range_key, indent)
                if range_line is None:
                    # Senza range non c'è banda: nemmeno il motore controlla.
                    continue

                base_line = self._find_key_line_at_indent(
                    lines, scope_start, scope_end, base_key, indent)
                if base_line is None:
                    # Chiave assente: vale il default della spec, che è quanto
                    # l'orchestrator passa al parser. `volume_range: 24` da
                    # solo sfora il tetto, e tacere qui lo perderebbe.
                    if not isinstance(param.default, (int, float)) \
                            or isinstance(param.default, bool):
                        continue
                    # Il default sta in secondi qualunque unità sia dichiarata
                    # — il motore non lo converte — quindi qui va portato
                    # nell'unità, non moltiplicato per il fattore come il resto.
                    base_peak, base_is_env = float(param.default) / factor, False
                else:
                    base_peak, base_is_env = self._peak_of(
                        lines, base_line, scope_end)
                range_peak, range_is_env = self._peak_of(
                    lines, range_line, scope_end)
                if base_peak is None or range_peak is None:
                    continue
                if base_is_env and range_is_env:
                    continue

                ceiling = base_peak + range_peak
                if ceiling * factor <= param.max_val:
                    continue

                # `center` fa arrivare la banda a `base + range/2`, e la via
                # d'uscita "cambia ancora" esiste solo se lì sta sotto il
                # tetto. Prometterla sempre manda a fare la modifica sbagliata
                # chi ha una coppia che sfora anche da centrata: lì l'ancora
                # non decide se la banda sta dentro, decide solo se il motore
                # la rifiuta o la lascia schiacciare al clamp.
                centrata = base_peak + range_peak / 2
                if centrata * factor <= param.max_val:
                    coda = (
                        "Da centrata la stessa coppia starebbe dentro, ma "
                        "`min` promette una banda esatta e il motore la "
                        "rifiuta al parse invece di schiacciarla col clamp."
                    )
                else:
                    coda = (
                        f"Da centrata sforerebbe anche lei "
                        f"({self._fmt_unit_value(centrata)}{label}), ma "
                        f"finendo sotto il safety clamp invece che rifiutata "
                        f"al parse: cambiare ancora non la fa stare dentro, "
                        f"va stretto il range."
                    )

                diagnostics.append(Diagnostic(
                    range=self._line_range(range_line),
                    message=(
                        f"`range_anchor: min`: la banda di `{yaml_path}` "
                        f"arriva a {self._fmt_unit_value(ceiling)}{label} "
                        f"(base + range) e sfora il tetto "
                        f"{self._fmt_unit_value(param.max_val / factor)}"
                        f"{label}. {coda}"
                    ),
                    severity=DiagnosticSeverity.Error,
                    source=SOURCE,
                ))

        return diagnostics

    @staticmethod
    def _grain_block_of(grain_by_start: dict, stream_start: int,
                        stream_end: int) -> Optional[dict]:
        """Il blocco grain di questo stream, se ne ha uno."""
        for start, block in grain_by_start.items():
            if stream_start <= start < stream_end:
                return block
        return None

    def _anchor_is_min(self, lines: List[str], start: int, end: int) -> bool:
        """True se lo stream dichiara `range_anchor: min`."""
        for n in range(start, min(end, len(lines))):
            m = self._RANGE_ANCHOR_VALUE.match(lines[n])
            if m:
                return m.group(1) == 'min'
        return False

    @staticmethod
    def _find_key_line_at_indent(lines: List[str], start: int, end: int,
                                 key: str, indent: int) -> Optional[int]:
        """La riga di `key:` a un indent preciso, o None."""
        pattern = re.compile(r'^' + re.escape(key) + r'\s*:')
        for n in range(start, min(end, len(lines))):
            raw = lines[n]
            stripped = raw.strip()
            if stripped.startswith('- '):
                stripped = stripped[2:].strip()
                leading = 4
            else:
                leading = len(raw) - len(raw.lstrip())
            if leading == indent and pattern.match(stripped):
                return n
        return None

    def _peak_of(self, lines: List[str], key_line: int,
                 block_end: int) -> Tuple[Optional[float], bool]:
        """
        Il massimo del valore di una chiave, e se quel valore è un envelope.

        Per uno scalare il massimo è il valore; per un envelope è il più alto
        dei suoi breakpoint, che è la stessa stima che fa il motore.

        Returns:
            `(picco, is_envelope)`, con picco None se il valore non è
            interpretabile — documento a metà scrittura, o una forma da cui
            non si ricava un numero.
        """
        found, raw = self._read_key_value(lines, key_line, block_end)
        if not found or raw is None:
            return None, False
        if isinstance(raw, bool):
            return None, False
        if isinstance(raw, (int, float)):
            return float(raw), False
        ys = self._envelope_peak(raw)
        return ys, True

    @staticmethod
    def _envelope_peak(raw) -> Optional[float]:
        """Il breakpoint più alto di un envelope già letto come struttura.

        Il motore prende `max(y)` sui breakpoint **espansi**, quindi le
        macro-forme vanno aperte invece che lette di piatto: in un ciclo
        compatto `[pattern, end_time, n_reps]` e in un BP group
        `[points, interp]` le Y stanno dentro l'elemento 0. Leggere
        l'elemento 1 come Y darebbe l'`end_time` del ciclo — un numero che
        non è una Y e che sotto `range_anchor: min` fa scattare un Error su
        uno YAML che rende, cioè il modo peggiore di sbagliarsi.

        Stessa apertura che `_extract_envelope_y_values` fa sul testo; qui la
        struttura è già parsata, quindi si riusano le forme di
        `envelope_shapes` invece di riconoscerle di nuovo.
        """
        if isinstance(raw, dict):
            raw = raw.get('points')
        if not isinstance(raw, list):
            return None

        def _is_num(v) -> bool:
            return isinstance(v, (int, float)) and not isinstance(v, bool)

        def _ys_of_pattern(points) -> List[float]:
            """Le Y dei punti piatti dentro una macro-forma."""
            return [float(pt[1]) for pt in points
                    if isinstance(pt, list) and len(pt) >= 2 and _is_num(pt[1])]

        # Macro-forma come corpo intero: le Y stanno nel suo elemento 0.
        if is_loop_block(raw) or is_bp_group(raw):
            ys = _ys_of_pattern(raw[0])
            return max(ys) if ys else None

        ys: List[float] = []
        for item in raw:
            # Le macro-forme si riconoscono per prime: un ciclo compatto ha
            # `item[1]` numerico e cadrebbe fra i breakpoint piatti.
            if is_loop_block(item) or is_bp_group(item):
                ys.extend(_ys_of_pattern(item[0]))
            elif (isinstance(item, list) and len(item) >= 2
                    and _is_num(item[0]) and _is_num(item[1])):
                ys.append(float(item[1]))
        return max(ys) if ys else None

    @staticmethod
    def _block_end(lines: List[str], key_line: int, limit: int,
                   key_indent: int) -> int:
        """La prima riga dopo `key_line` che esce dal blocco della chiave.

        Il blocco finisce dove l'indentazione torna al livello della chiave o
        sopra; le righe vuote non lo chiudono. `key_indent` si passa perche'
        non sempre coincide con l'indentazione grezza: sulla riga del trattino
        la chiave sta a colonna 2 ma il suo livello e' 4.
        """
        for n in range(key_line + 1, min(limit, len(lines))):
            riga = lines[n]
            if not riga.strip():
                continue
            if (len(riga) - len(riga.lstrip())) <= key_indent:
                return n
        return min(limit, len(lines))

    @staticmethod
    def _find_key_line(lines: List[str], start: int, end: int,
                       key: str) -> Optional[int]:
        """La riga di `key:` dentro un intervallo, o None se non c'è."""
        pattern = re.compile(r'^' + re.escape(key) + r'\s*:')
        for n in range(start, min(end, len(lines))):
            if pattern.match(lines[n].strip()):
                return n
        return None

    def _read_key_value(self, lines: List[str], key_line: int,
                        block_end: int) -> Tuple[bool, object]:
        """
        Rilegge il valore di una chiave via YAML, inline o block-style.

        Serve dove il valore è una struttura e non uno scalare: gli envelope
        si scrivono in cinque forme diverse e riconoscerle a regex vorrebbe
        dire riscrivere un parser che PyYAML ha già.

        Il frammento va dalla riga della chiave alla prima riga con indent
        minore o uguale al suo, dedentato a colonna 0 perché `safe_load` non
        accetta un documento che comincia rientrato.

        Returns:
            `(True, valore)` se il frammento è YAML valido e contiene la
            chiave — `valore` è `None` per la chiave scritta e lasciata vuota.
            `(False, None)` se non è interpretabile: documento a metà
            scrittura, da non segnalare.
        """
        raw_line = lines[key_line]
        key_indent = len(raw_line) - len(raw_line.lstrip())
        m = re.match(r'^([a-zA-Z_][a-zA-Z0-9_]*)\s*:', raw_line.strip())
        if not m:
            return False, None
        key = m.group(1)

        frammento = [raw_line[key_indent:]]
        n = key_line + 1
        while n < block_end:
            riga = lines[n]
            if not riga.strip():
                frammento.append('')
                n += 1
                continue
            if (len(riga) - len(riga.lstrip())) <= key_indent:
                break
            frammento.append(riga[key_indent:])
            n += 1

        data = self._safe_yaml('\n'.join(frammento))
        if not isinstance(data, dict) or key not in data:
            return False, None
        return True, data[key]

    def _scaled_unit_suppressed_lines(self, grain_blocks: List[dict]) -> 'frozenset[int]':
        """Righe di duration/duration_range dentro un blocco grain in un'unità
        non-secondi: i loro valori sono campioni o millisecondi, non secondi —
        vanno esclusi dai bound generici (in secondi) per non produrre falsi
        positivi. `50` in millisecondi è la grana breve, non cinquanta volte
        il tetto del parametro."""
        suppressed: set = set()
        for b in grain_blocks:
            if b['unit'] in _GRAIN_DURATION_UNIT_SECONDS:
                suppressed |= b['value_lines']
        return frozenset(suppressed)

    # Valore inline di rng_group che apre una lista o una mappa.
    _RNG_GROUP_NON_SCALAR = re.compile(r'^\s*rng_group\s*:\s*([\[{])')

    # range_anchor con valore inline: cattura il valore (eventualmente quotato).
    _RANGE_ANCHOR_VALUE = re.compile(
        r'^\s*range_anchor\s*:\s*["\']?([A-Za-z_][A-Za-z0-9_]*)["\']?\s*(?:#.*)?$'
    )

    def _check_range_anchor(self, lines: List[str]) -> List[Diagnostic]:
        """
        Valida il valore di range_anchor contro l'enum del bridge (center | min).

        L'engine solleva InvalidFieldValueError per un valore non ammesso, quindi
        la severità è Error, non un semplice warning. Un valore mancante è coperto
        dalla fase _check_missing_values; qui si guarda solo il valore inline.
        """
        anchors = self._bridge.get_range_anchors()
        diagnostics = []
        for i, line in enumerate(lines):
            m = self._RANGE_ANCHOR_VALUE.match(line)
            if not m:
                continue
            value = m.group(1)
            if value not in anchors:
                diagnostics.append(Diagnostic(
                    range=self._line_range(i),
                    message=(
                        f"`range_anchor`: valore `{value}` non valido. "
                        f"Valori ammessi: {', '.join(anchors)}."
                    ),
                    severity=DiagnosticSeverity.Error,
                    source=SOURCE,
                ))
        return diagnostics

    def _check_rng_group_type(self, lines: List[str]) -> List[Diagnostic]:
        """
        rng_group (PGE #169) e' un'identita' testuale, non un parametro
        sintetizzabile: l'engine la interpola in una f-string per derivare
        gli RNG, quindi una lista o una mappa diventerebbero l'identita'
        "['a', 'b']" senza che nulla protesti a runtime.

        Copre la forma inline (`rng_group: [a, b]` / `{...}`), che e' quella
        in cui si finisce provando a trattarlo come un envelope o un
        generatore. Parita' con la diagnostica rng-group-type di gl-ls.
        """
        diagnostics = []
        for i, line in enumerate(lines):
            if self._RNG_GROUP_NON_SCALAR.match(line):
                diagnostics.append(Diagnostic(
                    range=self._line_range(i),
                    message=(
                        "'rng_group' e' un'identita' testuale, non un valore "
                        "sintetizzabile: liste e mappe non sono ammesse "
                        "(l'engine ne userebbe la resa testuale come nome "
                        "del gruppo). Usa una stringa."
                    ),
                    severity=DiagnosticSeverity.Error,
                    source=SOURCE,
                ))
        return diagnostics

    @staticmethod
    def _fmt_unit_value(value: float) -> str:
        """Un numero come si scrive in un messaggio, qualunque grandezza sia.

        Durate, dB, gradi: la regola e' la stessa — 480000 e non 480000.0, ma
        0.0208 quando il minimo di un campione in millisecondi lo richiede.
        L'unita' la mette il chiamante, che e' l'unico a sapere quale sia.
        """
        if abs(value - round(value)) < 1e-9:
            return str(int(round(value)))
        return f'{value:.4g}'

    def _check_grain_duration_unit(self, grain_blocks: List[dict]) -> List[Diagnostic]:
        """
        Valida grain.duration_unit (PGE #158, terza unità in v5.2.0):
          - unità non in {seconds, samples, milliseconds} → Error;
          - con un'unità non-secondi, grain.duration deve essere esplicita
            (il default 0.05 è in secondi e non viene convertito) → Error;
          - con un'unità non-secondi, valida il valore scalare di duration
            contro i bound del parametro convertiti in quell'unità.

        La regola della durata esplicita vale per ogni unità non-secondi, non
        per `samples` soltanto: senza `grain.duration` la base resterebbe in
        secondi mentre `duration_range` sarebbe nell'unità dichiarata — due
        domini nello stesso blocco.

        I bound arrivano dal parametro (secondi) e si convertono dividendo per
        il fattore dell'unità. Il minimo non è quello del registro ma **un
        campione**, come lato motore, che in campioni fa 1 e in millisecondi
        1/48: dipende da output_sr anche quando l'unità non ne dipende.
        """
        diagnostics: List[Diagnostic] = []
        grain_dur = self._params_by_yaml_path.get('grain.duration')
        max_seconds = grain_dur.max_val if grain_dur is not None else None

        for b in grain_blocks:
            unit = b['unit']
            # Unità presente con valore ma non valida (vuota → _check_missing_values)
            if unit is not None and unit not in _GRAIN_DURATION_UNITS:
                diagnostics.append(Diagnostic(
                    range=self._line_range(b['unit_line']),
                    message=(
                        f"`grain.duration_unit`: valore `{unit}` non valido. "
                        f"Unità disponibili: {', '.join(_GRAIN_DURATION_UNITS)}."
                    ),
                    severity=DiagnosticSeverity.Error,
                    source=SOURCE,
                ))
                continue

            factor = _GRAIN_DURATION_UNIT_SECONDS.get(unit)
            if factor is None:
                continue  # 'seconds' o chiave assente: nulla da convertire
            label = _GRAIN_DURATION_UNIT_LABELS[unit]

            # Un'unità non-secondi richiede grain.duration esplicita.
            if not b['has_duration']:
                diagnostics.append(Diagnostic(
                    range=self._line_range(b['unit_line']),
                    message=(
                        f"`grain.duration_unit: {unit}` richiede una "
                        f"`grain.duration` esplicita in {label}: il default "
                        f"0.05 è in secondi e non verrebbe convertito."
                    ),
                    severity=DiagnosticSeverity.Error,
                    source=SOURCE,
                ))

            # Bound del parametro, espressi nell'unità dichiarata.
            min_in_unit = (1.0 / _OUTPUT_SR) / factor
            max_in_unit = max_seconds / factor if max_seconds is not None else None

            scalar = self._try_parse_number(b['duration_scalar']) \
                if b['duration_scalar'] is not None else None
            if scalar is None:
                continue

            if scalar < min_in_unit:
                diagnostics.append(Diagnostic(
                    range=self._line_range(b['duration_line']),
                    message=(
                        f"`grain.duration` = {b['duration_scalar']} {label}: "
                        f"la durata minima è un campione "
                        f"({self._fmt_unit_value(min_in_unit)} {label})."
                    ),
                    severity=DiagnosticSeverity.Error,
                    source=SOURCE,
                ))
            elif max_in_unit is not None and scalar > max_in_unit:
                diagnostics.append(Diagnostic(
                    range=self._line_range(b['duration_line']),
                    message=(
                        f"`grain.duration` = {b['duration_scalar']} {label} "
                        f"fuori range [{self._fmt_unit_value(min_in_unit)}, "
                        f"{self._fmt_unit_value(max_in_unit)}] "
                        f"(max = {max_seconds}s)."
                    ),
                    severity=DiagnosticSeverity.Error,
                    source=SOURCE,
                ))

        return diagnostics
