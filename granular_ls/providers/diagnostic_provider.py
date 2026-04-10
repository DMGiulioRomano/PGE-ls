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

from lsprotocol.types import (
    Diagnostic,
    DiagnosticSeverity,
    DiagnosticTag,
    Position,
    Range,
)

from granular_ls.schema_bridge import SchemaBridge, ParameterInfo
from granular_ls.voice_strategies import (
    VOICE_STRATEGY_REGISTRY,
    VOICE_DIMENSIONS,
    VOICE_ENVELOPE_KEYS,
    get_strategy_spec,
)

# Identificatore del nostro Language Server nei diagnostici.
# VSCode lo mostra accanto al messaggio per indicare la fonte.
SOURCE = 'granular-ls'


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
        self._params_by_yaml_path: Dict[str, ParameterInfo] = {
            p.yaml_path: p
            for p in bridge.get_all_parameters()
            if not p.is_internal
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

        # Fase 1: parsing riga per riga.
        # parsed = lista di (yaml_path, valore_str, n_riga, indent_level)
        parsed = self._parse_document(document_text)

        diagnostics = []

        # Fase 2: controllo chiavi duplicate nello stesso stream.
        diagnostics.extend(self._check_duplicate_keys(document_text))

        # Fase 3: controllo campi obbligatori per ogni stream.
        diagnostics.extend(self._check_mandatory_stream_fields(document_text))

        # Fase 3b: parametri numerici senza valore.
        diagnostics.extend(self._check_missing_values(document_text))

        # Fase 3c: sbiadisce stream muted e stream non-solo.
        diagnostics.extend(self._check_muted_streams(document_text))
        diagnostics.extend(self._check_solo_streams(document_text))

        # Fase 3: controllo exclusive_group.
        diagnostics.extend(self._check_exclusive_groups(parsed))

        # Fase 4: controllo bounds numerici (valori scalari).
        diagnostics.extend(self._check_bounds(parsed))

        # Fase 5: controllo bounds nei valori envelope (breakpoints Y).
        diagnostics.extend(self._check_envelope_bounds(document_text))

        # Fase 5b: validazione grain.envelope (finestratura del grano).
        diagnostics.extend(self._check_grain_envelope(document_text))

        # Fase 6: validazione del blocco voices (strategy, kwargs, enum).
        diagnostics.extend(self._check_voice_strategies(document_text))

        # Fase 7: start bypassato da loop_start envelope.
        diagnostics.extend(self._check_start_bypassed_by_loop_start(document_text))

        # Fase 8: loop_dur e loop_end presenti insieme (loop_dur ha priorita').
        diagnostics.extend(self._check_loop_dur_overrides_loop_end(document_text))

        # Fase 9: bounds dinamici per i parametri pointer (normalized vs absolute).
        diagnostics.extend(self._check_pointer_param_bounds(document_text, self._refs_dir))

        return diagnostics



    def _check_duplicate_keys(self, document_text: str) -> List[Diagnostic]:
        """
        Controlla chiavi duplicate nello stesso stream.

        Per ogni stream raccoglie le chiavi di primo livello (indent 2)
        e di blocco (indent 3, es. pitch:, grain:, pointer:).
        Se una chiave compare piu' di una volta produce un Error
        su tutte le occorrenze.

        Chiavi permesse in stream diversi: non sono duplicate.
        """
        diagnostics = []
        lines = document_text.split('\n')

        # Trova i confini di ogni stream
        stream_starts = []
        for n, line in enumerate(lines):
            stripped = line.strip()
            leading = len(line) - len(line.lstrip())
            if (stripped.startswith('- ') or stripped == '-') and leading == 2:
                stream_starts.append(n)

        for idx, start in enumerate(stream_starts):
            end = stream_starts[idx + 1] if idx + 1 < len(stream_starts) else len(lines)

            # Raccoglie (path_completo, n_riga) per questo stream.
            # Il path include il blocco padre per evitare falsi positivi:
            # 'duration' a livello stream != 'dephase.duration' != 'grain.duration'
            key_occurrences: dict = {}  # path -> [n_riga, ...]

            # Traccia il blocco corrente (grain, pitch, pointer, dephase, ...)
            current_block = None
            current_block_indent = -1

            for n in range(start, end):
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
                    # Path = blocco.chiave (es. 'dephase.duration', 'grain.duration')
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

    _MANDATORY_FIELDS = ['stream_id', 'onset', 'duration', 'sample']

    def _check_mandatory_stream_fields(
        self, document_text: str
    ) -> List[Diagnostic]:
        """
        Controlla che ogni elemento della lista streams abbia i quattro
        campi obbligatori: stream_id, onset, duration, sample.

        Produce un Warning per ogni campo mancante, puntando alla riga
        del marcatore '- ' dello stream.
        """
        diagnostics = []
        lines = document_text.split('\n')

        # Trova tutti gli stream: marcatori '- ' a indent 2 (2 spazi)
        stream_starts = []
        for n, line in enumerate(lines):
            stripped = line.strip()
            leading = len(line) - len(line.lstrip())
            if (stripped.startswith('- ') or stripped == '-') and leading == 2:
                stream_starts.append(n)

        for idx, start_line in enumerate(stream_starts):
            # Determina la fine dello stream (prossimo marcatore o EOF)
            end_line = stream_starts[idx + 1] if idx + 1 < len(stream_starts)                        else len(lines)

            # Raccoglie le chiavi presenti in questo stream
            present_keys = set()
            for n in range(start_line, end_line):
                raw = lines[n]
                stripped = raw.strip()

                # Rimuovi marcatore lista sulla riga di inizio
                if stripped.startswith('- '):
                    stripped = stripped[2:].strip()

                # Estrai chiave da 'chiave: valore' o 'chiave:'
                if ':' in stripped:
                    key = stripped[:stripped.index(':')].strip()
                    if key and all(c.isalnum() or c == '_' for c in key):
                        present_keys.add(key)

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
        'distribution_mode',
    })

    # Campi obbligatori dello stream che richiedono sempre un valore.
    _STREAM_VALUE_REQUIRED = frozenset({
        'stream_id', 'onset', 'duration', 'sample',
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
                for kwarg_name in spec.kwargs:
                    paths.add(f'voices.{dim}.{kwarg_name}')
        return frozenset(paths)

    def _check_missing_values(self, document_text: str) -> List[Diagnostic]:
        """
        Segnala chiavi scritte senza valore quando un valore e' obbligatorio.

        Categorie controllate:
          1. Parametri numerici del bridge (min_val != None): richiedono float
             o envelope [[t, v], ...].
          2. voices.num_voices e voices.scatter: bounds via get_raw_bounds.
          3. Campi obbligatori stream (stream_id, onset, duration, sample):
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

        # 1. Parametri numerici del bridge
        numeric_yaml_paths: set = {
            p.yaml_path
            for p in self._bridge.get_all_parameters()
            if p.min_val is not None and not p.is_internal
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

        Un envelope standard e' una lista YAML di breakpoints [t, y].
        Per ogni y controlla che sia nei bounds del parametro padre.

        Produce Error se un valore y e' fuori dai bounds.
        """
        import ast
        diagnostics = []
        lines = document_text.split('\n')

        # Costruisce mappa yaml_path -> (min_val, max_val)
        params_bounds = {}
        for p in self._bridge.get_all_parameters():
            if p.min_val is not None and p.max_val is not None and not p.is_internal:
                params_bounds[p.yaml_path] = (p.min_val, p.max_val)

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
                    # Cerca il yaml_path corrispondente
                    found = None
                    for yp in params_bounds:
                        if yp.split('.')[-1] == key or yp == key:
                            found = yp
                            break
                    current_param_path = found
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
            # a) un vero blocco padre (grain:, pointer:, pitch:, dephase:)
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

    def _check_muted_streams(self, document_text: str) -> List[Diagnostic]:
        """
        Sbiadisce (DiagnosticTag.Unnecessary) ogni stream con muted: true.
        """
        diagnostics = []
        lines = document_text.split('\n')
        for start, end, keys in self._find_stream_blocks(lines):
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

    def _check_solo_streams(self, document_text: str) -> List[Diagnostic]:
        """
        Se almeno uno stream ha solo: true, sbiadisce tutti gli altri
        (che non hanno a loro volta solo: true).
        """
        diagnostics = []
        lines = document_text.split('\n')
        streams = self._find_stream_blocks(lines)

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

    def _check_voice_strategies(self, document_text: str) -> List[Diagnostic]:
        """
        Valida il blocco voices: di ogni stream.

        Controlli:
          1. Per ogni dimensione (pitch, onset_offset, pointer, pan): se present,
             la chiave 'strategy' deve essere valida per quella dimensione.
          2. I kwargs richiesti dalla strategy devono essere presenti.
          3. I kwargs di tipo enum devono avere un valore nel set consentito.
        """
        diagnostics: List[Diagnostic] = []
        if not document_text:
            return diagnostics

        lines = document_text.split('\n')
        n_lines = len(lines)

        # Trova tutti gli stream (marcatore '- ' a 2 spazi)
        stream_starts: List[int] = []
        for n, line in enumerate(lines):
            stripped = line.strip()
            leading = len(line) - len(line.lstrip())
            if (stripped.startswith('- ') or stripped == '-') and leading == 2:
                stream_starts.append(n)

        for idx, stream_start in enumerate(stream_starts):
            stream_end = (stream_starts[idx + 1] if idx + 1 < len(stream_starts)
                          else n_lines)

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

                # 3. Controlla i kwargs richiesti e i valori enum
                for kwarg_name, kwarg_spec in spec.kwargs.items():
                    if kwarg_name not in dim_keys:
                        if kwarg_spec.required:
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

        return diagnostics

    def _check_start_bypassed_by_loop_start(self, document_text: str) -> List[Diagnostic]:
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
        lines = document_text.split('\n')

        # Trova i confini di ogni stream (marcatori '- ' a 2 spazi)
        stream_starts = [
            n for n, line in enumerate(lines)
            if (line.strip().startswith('- ') or line.strip() == '-')
            and (len(line) - len(line.lstrip())) == 2
        ]
        stream_ranges = [
            (s, stream_starts[i + 1] if i + 1 < len(stream_starts) else len(lines))
            for i, s in enumerate(stream_starts)
        ]

        for stream_start, stream_end in stream_ranges:
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

    def _check_loop_dur_overrides_loop_end(self, document_text: str) -> List[Diagnostic]:
        """
        Warning quando loop_dur e loop_end sono entrambi presenti nello stesso
        blocco pointer:.

        Regola motore: se loop_dur e' definito, viene usato per calcolare
        loop_end = loop_start + loop_dur, ignorando completamente loop_end.
        """
        diagnostics = []
        lines = document_text.split('\n')

        stream_starts = [
            n for n, line in enumerate(lines)
            if (line.strip().startswith('- ') or line.strip() == '-')
            and (len(line) - len(line.lstrip())) == 2
        ]
        stream_ranges = [
            (s, stream_starts[i + 1] if i + 1 < len(stream_starts) else len(lines))
            for i, s in enumerate(stream_starts)
        ]

        for stream_start, stream_end in stream_ranges:
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

    def _check_pointer_param_bounds(
        self, document_text: str, refs_dir: str
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

        I valori envelope (che iniziano con '[') vengono ignorati.
        """
        from granular_ls.providers.hover_provider import _get_effective_unit_mode

        diagnostics = []
        lines = document_text.split('\n')

        stream_starts = [
            n for n, line in enumerate(lines)
            if (line.strip().startswith('- ') or line.strip() == '-')
            and (len(line) - len(line.lstrip())) == 2
        ]
        stream_ranges = [
            (s, stream_starts[i + 1] if i + 1 < len(stream_starts) else len(lines))
            for i, s in enumerate(stream_starts)
        ]

        for stream_start, stream_end in stream_ranges:
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
