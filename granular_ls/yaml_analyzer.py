# granular_ls/yaml_analyzer.py
"""
YamlAnalyzer - Parser tollerante per YAML parziale.

Il problema fondamentale:
    Un YAML mentre viene digitato non e' mai valido.
    pyyaml e ruamel.yaml esplodono su testo incompleto.
    Questo modulo legge riga per riga, usa l'indentazione
    come unico segnale affidabile di gerarchia, e non
    lancia mai eccezioni indipendentemente dall'input.

Componenti:
    is_pge_file(uri)         - filtro sul nome file
    YamlContext              - Value Object che descrive il contesto cursore
    YamlAnalyzer.get_context - punto di ingresso principale
"""

from dataclasses import dataclass
from pathlib import Path
from typing import List


# =============================================================================
# COSTANTI
# =============================================================================

INDENT_SIZE = 2


# =============================================================================
# FILTRO FILE
# =============================================================================

def is_pge_file(uri: str) -> bool:
    """
    Ritorna True se l'URI punta a un file PGE_*.yaml o PGE_*.yml.
    Il prefisso PGE_ e' case-sensitive.
    """
    if not uri:
        return False
    try:
        filename = Path(uri).name
    except Exception:
        return False
    if not filename.startswith('PGE_'):
        return False
    return filename.endswith('.yaml') or filename.endswith('.yml')


# =============================================================================
# VALUE OBJECT
# =============================================================================

@dataclass(frozen=True)
class YamlContext:
    """
    Descrive il contesto del cursore in un documento YAML parziale.

    context_type:
        'key'          - il cursore sta scrivendo il nome di una chiave
        'value'        - il cursore sta scrivendo il valore di una chiave
        'stream_start' - il cursore e' su una riga '  - ' (nuovo elemento lista)
        'unknown'      - non e' possibile determinare il contesto

    current_text:
        Il testo parziale gia' scritto prima del cursore sulla riga.

    parent_path:
        Lista di chiavi antenate dalla radice al blocco corrente.
        Es. ['grain'] se il cursore e' dentro 'grain:'.
        Es. [] se il cursore e' al livello diretto dello stream.

    indent_level:
        Livello di indentazione (spazi_iniziali // INDENT_SIZE).

    in_stream_element:
        True se il cursore e' dentro un elemento della lista streams
        (cioe' c'e' un marcatore '- ' in un antenato).
        False se siamo a root level del documento (fuori da ogni lista).
    """
    context_type: str
    current_text: str
    parent_path: List[str]
    indent_level: int
    in_stream_element: bool = False
    current_key: str = ''
    # La chiave completa sulla riga corrente quando context_type == 'value'.
    # Es. riga 'density: 100' con cursore sul valore -> current_key='density'.
    # Vuoto per context_type 'key', 'stream_start', 'unknown'.
    cursor_line: int = 0
    # Numero di riga del cursore (0-indexed).
    leading_spaces: int = 0
    # Numero esatto di spazi iniziali sulla riga corrente.


# =============================================================================
# ANALIZZATORE
# =============================================================================

class YamlAnalyzer:


    @staticmethod
    def get_word_at_cursor(text: str, line: int, character: int) -> str:
        """
        Ritorna la parola completa sotto il cursore, indipendentemente
        da dove si trova il cursore all'interno della parola.

        Usato dal HoverProvider: se il cursore e' a meta' di 'density',
        ritorna 'density' completo, non solo il prefisso 'den'.

        Ritorna stringa vuota se il cursore non e' su un identificatore.
        """
        try:
            lines = text.split('\n') if text else ['']
            if line < 0 or line >= len(lines):
                return ''
            current_line = lines[line]
            character = min(character, len(current_line))

            # Trova l'inizio della parola (vai a sinistra)
            start = character
            while start > 0 and (current_line[start-1].isalnum()
                                  or current_line[start-1] == '_'):
                start -= 1

            # Trova la fine della parola (vai a destra)
            end = character
            while end < len(current_line) and (current_line[end].isalnum()
                                                or current_line[end] == '_'):
                end += 1

            return current_line[start:end]
        except Exception:
            return ''




    @staticmethod
    def get_stream_context_at_line(text: str, line: int) -> dict:
        """
        Risale dal cursore al blocco stream corrente ed estrae
        onset e time_mode.

        Algoritmo:
        1. Risale le righe dalla posizione corrente cercando il
           marcatore '- ' piu' vicino (inizio dello stream).
        2. Scansiona in avanti dal marcatore raccogliendo le chiavi
           del blocco corrente fino al prossimo marcatore '- '
           o alla fine del documento.
        3. Ritorna i valori trovati, con default se assenti.

        Returns:
            dict con:
                'onset'     : float (default 0.0)
                'time_mode' : str   (default 'absolute')
        """
        DEFAULT = {'duration': 0.0, 'time_mode': 'absolute'}

        try:
            lines = text.split('\n') if text else ['']
            if line < 0 or line >= len(lines):
                return dict(DEFAULT)

            # Passo 1: risali cercando il marcatore '- ' dello stream.
            # Il marcatore di stream e' SEMPRE a indent 1 (2 spazi).
            # Ignorare i '- ' piu' profondi (elementi di lista dentro parametri).
            stream_start_line = None
            for i in range(line, -1, -1):
                raw = lines[i]
                stripped = raw.strip()
                leading = len(raw) - len(raw.lstrip())
                if (stripped.startswith('- ') or stripped == '-') and leading == 2:
                    stream_start_line = i
                    break

            if stream_start_line is None:
                return dict(DEFAULT)

            # Passo 2: scansiona in avanti dal marcatore
            result = dict(DEFAULT)
            for i in range(stream_start_line, len(lines)):
                stripped = lines[i].strip()

                # Fermati solo al prossimo marcatore di STREAM (indent 2).
                # I '- ' piu' profondi sono elementi di lista dentro parametri.
                if i > stream_start_line:
                    raw_i = lines[i]
                    leading_i = len(raw_i) - len(raw_i.lstrip())
                    if (stripped.startswith('- ') or stripped == '-') and leading_i == 2:
                        break

                # Rimuovi prefisso '- ' sulla riga del marcatore
                if stripped.startswith('- '):
                    stripped = stripped[2:].strip()

                # Estrai coppie chiave: valore
                if ': ' in stripped:
                    key = stripped[:stripped.index(': ')].strip()
                    val = stripped[stripped.index(': ') + 2:].strip()
                    # Rimuovi commenti inline
                    if '#' in val:
                        val = val[:val.index('#')].strip()
                    # Rimuovi quotes
                    val = val.strip('"\' ')

                    if key == 'duration':
                        try:
                            result['duration'] = float(val)
                        except (ValueError, TypeError):
                            pass
                    elif key == 'time_mode':
                        result['time_mode'] = val

            return result

        except Exception:
            return dict(DEFAULT)

    @staticmethod
    def _extract_key_from_line(line: str) -> str:
        """
        Estrae la chiave YAML da una riga nel formato 'chiave: valore'.

        Rimuove il marcatore lista se presente ('  - chiave: ...').
        Ritorna stringa vuota se la riga non ha il pattern chiave-valore.
        """
        try:
            # lstrip() non rimuove il trailing space dopo ':'
            # strip() trasforma 'pan: ' in 'pan:' e ': ' non viene trovato
            stripped = line.lstrip()
            # Rimuovi marcatore lista
            if stripped.startswith('- '):
                stripped = stripped[2:]
            # Cerca il separatore ': ' (chiave con valore)
            if ': ' in stripped:
                key = stripped[:stripped.index(': ')].strip()
                if key and all(c.isalnum() or c == '_' for c in key):
                    return key
            # Caso 'chiave:' senza spazio finale (fine riga)
            rstripped = stripped.rstrip()
            if rstripped.endswith(':'):
                key = rstripped[:-1].strip()
                if key and all(c.isalnum() or c == '_' for c in key):
                    return key
        except Exception:
            pass
        return ''

    @staticmethod
    def _parent_is_streams(lines: list, current_line_idx: int) -> bool:
        """
        Ritorna True se la riga padre (a indent 0) e' 'streams:'.
        Usato per rilevare il livello lista dove inserire nuovi stream.
        """
        for i in range(current_line_idx - 1, -1, -1):
            line = lines[i]
            stripped = line.strip()
            if not stripped or stripped.startswith('#'):
                continue
            leading = len(line) - len(line.lstrip())
            if leading == 0:
                return stripped.startswith('streams:') or stripped == 'streams:'
        return False

    @staticmethod
    def get_context(text: str, line: int, character: int) -> YamlContext:
        """
        Analizza il testo YAML e la posizione del cursore.
        Mai None, mai eccezioni.
        """
        try:
            return YamlAnalyzer._analyze(text, line, character)
        except Exception:
            return YamlContext(
                context_type='unknown',
                current_text='',
                parent_path=[],
                indent_level=0,
                in_stream_element=False,
            )

    @staticmethod
    def _analyze(text: str, line: int, character: int) -> YamlContext:
        lines = text.split('\n') if text else ['']

        # Normalizzazione posizione cursore
        if line < 0:
            line = 0
        if line >= len(lines):
            line = max(0, len(lines) - 1)

        current_line = lines[line]
        character = min(character, len(current_line))
        line_up_to_cursor = current_line[:character]

        # Indentazione riga corrente
        stripped = current_line.lstrip()
        leading_spaces = len(current_line) - len(stripped)
        indent_level = leading_spaces // INDENT_SIZE

        # Rilevamento se siamo dentro un elemento lista
        is_list_root = stripped.startswith('- ') or stripped == '-'
        if is_list_root:
            parent_path = []
            in_stream_element = True
        else:
            in_stream_element = YamlAnalyzer._is_in_stream_element(
                lines, line, leading_spaces
            )
            parent_path = YamlAnalyzer._build_parent_path(
                lines, line, indent_level
            )

        # Analisi riga corrente
        context_type, current_text = YamlAnalyzer._analyze_line(
            line_up_to_cursor, stripped
        )

        # Rilevamento contesto 'streams_list_level':
        # se siamo a indent 1, la riga e' vuota (o ha solo spazi),
        # e il blocco padre e' 'streams:', siamo nel livello lista
        # dove l'utente sta per scrivere un nuovo '- '.
        if (context_type == 'key'
                and not current_text
                and not in_stream_element
                and indent_level == 1
                and YamlAnalyzer._parent_is_streams(lines, line)):
            context_type = 'streams_list_level'

        # Estrai la chiave corrente se siamo in contesto 'value'.
        # Serve a EnvelopeSnippetProvider per sapere su quale parametro
        # il cursore sta per scrivere un valore.
        current_key = ''
        if context_type == 'value':
            current_key = YamlAnalyzer._extract_key_from_line(current_line)

        return YamlContext(
            context_type=context_type,
            current_text=current_text,
            parent_path=parent_path,
            indent_level=indent_level,
            in_stream_element=in_stream_element,
            current_key=current_key,
            cursor_line=line,
            leading_spaces=leading_spaces,
        )

    @staticmethod
    def _is_in_stream_element(lines: list, current_line_idx: int,
                               current_leading: int) -> bool:
        """
        Ritorna True se il cursore e' dentro un elemento lista YAML.

        Risale le righe cercando un marcatore '- ' a indentazione
        inferiore a quella corrente. Se trovato prima di raggiungere
        una riga a indentazione 0 non-lista, siamo in un stream element.
        """
        for i in range(current_line_idx - 1, -1, -1):
            line = lines[i]
            line_stripped = line.strip()
            if not line_stripped or line_stripped.startswith('#'):
                continue

            line_leading = len(line) - len(line.lstrip())

            # Marcatore lista a indentazione inferiore: siamo in uno stream.
            # Esclude i breakpoints envelope ('- [' o '- {') che non sono stream markers.
            if (line_stripped.startswith('- ') or line_stripped == '-') \
                    and line_leading < current_leading:
                after_dash = line_stripped[2:].strip() if line_stripped.startswith('- ') else ''
                is_breakpoint = after_dash.startswith('[') or after_dash.startswith('{')
                if not is_breakpoint:
                    return True

            # Root level senza lista: non siamo in uno stream
            if line_leading == 0 and not line_stripped.startswith('-'):
                return False

        return False

    @staticmethod
    def _analyze_line(line_up_to_cursor: str, stripped: str) -> tuple:
        """
        Determina context_type e current_text dalla riga corrente.
        """
        # Commento
        if stripped.startswith('#'):
            return 'unknown', ''

        # Marcatore lista
        if stripped.startswith('- ') or stripped == '-':
            after_dash = stripped[2:].strip() if stripped.startswith('- ') else ''
            if not after_dash:
                return 'stream_start', ''
            dash_prefix_len = line_up_to_cursor.find('-') + 2
            line_after_dash = line_up_to_cursor[dash_prefix_len:]
            return YamlAnalyzer._analyze_line(line_after_dash, after_dash)

        # Rimuovi commenti inline
        line_for_analysis = line_up_to_cursor
        if '#' in line_up_to_cursor:
            hash_pos = line_up_to_cursor.find('#')
            line_for_analysis = line_up_to_cursor[:hash_pos]

        # Contesto valore
        if ': ' in line_for_analysis:
            colon_pos = line_for_analysis.rfind(': ')
            current_text = line_for_analysis[colon_pos + 2:].strip()
            return 'value', current_text

        # Riga finisce con ':'
        if line_for_analysis.rstrip().endswith(':'):
            return 'key', line_for_analysis.rstrip()[:-1].lstrip()

        # Contesto chiave
        current_text = line_for_analysis.lstrip()
        return 'key', current_text

    @staticmethod
    def _build_parent_path(lines: list, current_line_idx: int,
                           current_indent: int) -> List[str]:
        """
        Risale le righe precedenti per costruire il parent_path.
        Si ferma al marcatore lista '- ' senza attraversarlo.
        """
        if current_indent == 0:
            return []

        path = []
        target_indent = current_indent - 1

        for i in range(current_line_idx - 1, -1, -1):
            raw_line = lines[i]
            stripped = raw_line.strip()
            if not stripped or stripped.startswith('#'):
                continue

            line_leading = len(raw_line) - len(raw_line.lstrip())
            line_indent = line_leading // INDENT_SIZE

            # Marcatore lista: fermiamo solo se e' a indent 1 (marcatore stream).
            # I '- ' profondi sono breakpoints envelope, non stream markers.
            # Distinguiamo stream markers ('- chiave: ...') da breakpoints ('- [' o '- {').
            if stripped.startswith('- ') or stripped == '-':
                after_dash = stripped[2:].strip() if stripped.startswith('- ') else ''
                is_breakpoint = after_dash.startswith('[') or after_dash.startswith('{')
                if line_leading == 2 and not is_breakpoint:
                    break
                continue

            if line_indent == target_indent:
                line_clean = stripped.split('#')[0].rstrip()
                if line_clean.endswith(':'):
                    key = line_clean[:-1].strip()
                    if key.startswith('- '):
                        key = key[2:].strip()
                    if key:
                        path.insert(0, key)
                    if target_indent == 0:
                        break
                    target_indent -= 1

        return path
