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

import re
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

    def __init__(self, bridge: SchemaBridge):
        self._bridge = bridge

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

        Args:
            document_text: testo completo del documento YAML

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

        # Fase 3b: sbiadisce stream muted e stream non-solo.
        diagnostics.extend(self._check_muted_streams(document_text))
        diagnostics.extend(self._check_solo_streams(document_text))

        # Fase 3: controllo exclusive_group.
        diagnostics.extend(self._check_exclusive_groups(parsed))

        # Fase 4: controllo bounds numerici (valori scalari).
        diagnostics.extend(self._check_bounds(parsed))

        # Fase 5: controllo bounds nei valori envelope (breakpoints Y).
        diagnostics.extend(self._check_envelope_bounds(document_text))

        # Fase 6: validazione del blocco voices (strategy, kwargs, enum).
        diagnostics.extend(self._check_voice_strategies(document_text))

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

                # Raccoglie chiave/valore del blocco dimensione (a 8 spazi)
                dim_keys: Dict[str, Tuple[str, int]] = {}  # key -> (value, line_no)
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
