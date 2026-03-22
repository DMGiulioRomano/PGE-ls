#!/usr/bin/env python3
"""
server.py - Entry point del PGE Language Server.

Avvio:
    python server.py                          # usa snapshot se disponibile
    python server.py --src /path/to/granular/src  # importa moduli Python reali

Architettura:
    SchemaBridge  ->  tre provider  ->  server pygls  ->  VSCode

Il server e' stateless rispetto ai documenti: ogni richiesta riceve
il testo completo del documento (LSP full sync mode) e lo processa
da zero. Non c'e' stato condiviso tra richieste diverse.
"""

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Optional

from pygls.server import LanguageServer
from lsprotocol.types import (
    TEXT_DOCUMENT_COMPLETION,
    TEXT_DOCUMENT_HOVER,
    TEXT_DOCUMENT_DID_CHANGE,
    TEXT_DOCUMENT_DID_OPEN,
    TEXT_DOCUMENT_DID_SAVE,
    TEXT_DOCUMENT_CODE_ACTION,
    TEXT_DOCUMENT_DEFINITION,
    TEXT_DOCUMENT_DOCUMENT_LINK,
    CodeAction,
    DocumentLink,
    DocumentLinkOptions,
    DocumentLinkParams,
    DefinitionParams,
    Location,
    LocationLink,
    CodeActionKind,
    CodeActionOptions,
    CodeActionParams,
    CompletionList,
    CompletionOptions,
    CompletionParams,
    DidChangeTextDocumentParams,
    DidOpenTextDocumentParams,
    DidSaveTextDocumentParams,
    HoverParams,
    OptionalVersionedTextDocumentIdentifier,
    Position,
    PublishDiagnosticsParams,
    Range,
    TextDocumentEdit,
    TextDocumentSyncKind,
    TextEdit,
    WorkspaceEdit,
)

# Aggiungiamo la directory del server al path per permettere
# import di granular_ls dall'interno del package vscode-client.
_HERE = Path(__file__).parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from granular_ls.schema_bridge import SchemaBridge
from granular_ls.yaml_analyzer import YamlAnalyzer, is_pge_file
from granular_ls.providers.completion_provider import CompletionProvider
from granular_ls.providers.hover_provider import HoverProvider
from granular_ls.providers.diagnostic_provider import DiagnosticProvider

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('pge-ls')

# =============================================================================
# INIZIALIZZAZIONE SERVER
# =============================================================================

server = LanguageServer(
    name='pge-ls',
    version='0.1.0',
)

# I provider vengono inizializzati dopo il parsing degli argomenti.
# Usiamo Optional per permettere l'inizializzazione lazy.
_completion_provider: Optional[CompletionProvider] = None
_hover_provider: Optional[HoverProvider] = None
_diagnostic_provider: Optional[DiagnosticProvider] = None


def _init_providers(bridge: SchemaBridge) -> None:
    """Inizializza i provider con il bridge fornito."""
    global _completion_provider, _hover_provider, _diagnostic_provider
    _completion_provider = CompletionProvider(bridge)
    _hover_provider = HoverProvider(bridge)
    _diagnostic_provider = DiagnosticProvider(bridge)
    logger.info(
        f"Provider inizializzati con "
        f"{len(bridge.get_all_parameters())} parametri."
    )


# =============================================================================
# HELPER: testo documento
# =============================================================================

def _get_document_text(server: LanguageServer, uri: str) -> str:
    """
    Recupera il testo corrente di un documento dalla workspace di pygls.
    Ritorna stringa vuota se il documento non e' nel workspace.
    """
    try:
        doc = server.workspace.get_text_document(uri)
        return doc.source or ''
    except Exception:
        return ''


# =============================================================================
# FEATURE HANDLERS
# =============================================================================

@server.feature(
    TEXT_DOCUMENT_COMPLETION,
    CompletionOptions(trigger_characters=[' ', '\n', ':', '-']),
)
def handle_completion(params: CompletionParams) -> CompletionList:
    """
    Chiamato quando VSCode chiede completamenti.

    Flusso:
    1. Verifica che il file sia un PGE file
    2. Recupera il testo del documento
    3. Costruisce il YamlContext dalla posizione del cursore
    4. Chiede i completamenti al provider
    5. Ritorna CompletionList a VSCode
    """
    uri = params.text_document.uri

    if not is_pge_file(uri):
        return CompletionList(is_incomplete=False, items=[])

    if _completion_provider is None:
        return CompletionList(is_incomplete=False, items=[])

    text = _get_document_text(server, uri)
    line = params.position.line
    character = params.position.character

    context = YamlAnalyzer.get_context(text, line, character)
    logger.info(
        f"COMPLETION: line={line} char={character} "
        f"type={context.context_type!r} "
        f"key={context.current_key!r} "
        f"parent={context.parent_path} "
        f"in_stream={context.in_stream_element}"
    )
    items = _completion_provider.get_completions(context, text)
    logger.info(f"COMPLETION: n={len(items)}")
    return CompletionList(is_incomplete=False, items=items)


@server.feature(TEXT_DOCUMENT_HOVER)
def handle_hover(params: HoverParams) -> Optional[object]:
    """
    Chiamato quando il cursore si ferma su una parola.

    Per hover, vogliamo la parola COMPLETA sotto il cursore, non solo
    il prefisso fino al carattere. Usiamo get_word_at_cursor per questo.
    """
    uri = params.text_document.uri

    if not is_pge_file(uri):
        return None

    if _hover_provider is None:
        return None

    text = _get_document_text(server, uri)
    line = params.position.line
    character = params.position.character

    # Ottieni la parola completa sotto il cursore
    full_word = YamlAnalyzer.get_word_at_cursor(text, line, character)
    logger.info(f"HOVER: line={line} char={character} full_word={full_word!r}")

    if not full_word:
        return None

    # Ottieni il contesto base per parent_path e in_stream_element
    context = YamlAnalyzer.get_context(text, line, character)
    logger.info(f"HOVER: context_type={context.context_type!r} current_text={context.current_text!r} parent_path={context.parent_path} in_stream={context.in_stream_element}")

    # Per hover forziamo sempre context_type='key' con la parola intera.
    # Non importa dove il cursore e' posizionato sulla riga: se c'e' una
    # parola sotto il cursore, proviamo a trovare la sua documentazione.
    from granular_ls.yaml_analyzer import YamlContext
    hover_context = YamlContext(
        context_type='key',
        current_text=full_word,
        parent_path=context.parent_path,
        indent_level=context.indent_level,
        in_stream_element=context.in_stream_element,
    )

    result = _hover_provider.get_hover(hover_context)
    logger.info(f"HOVER: result={'Hover' if result else 'None'}")
    return result


@server.feature(
    TEXT_DOCUMENT_CODE_ACTION,
    CodeActionOptions(code_action_kinds=[CodeActionKind.QuickFix]),
)
def handle_code_action(params: CodeActionParams):
    """
    Propone azioni di refactoring quando duration cambia.

    Rileva se il cursore e' sulla riga 'duration: X' in uno stream
    con time_mode non-normalized e propone il ricalcolo proporzionale
    delle X di tutti i breakpoints envelope in quello stream.
    """
    uri = params.text_document.uri
    if not is_pge_file(uri):
        return []

    text = _get_document_text(server, uri)
    if not text:
        return []

    lines = text.split('\n')
    cursor_line = params.range.start.line

    if cursor_line >= len(lines):
        return []

    stripped = lines[cursor_line].strip()

    # Controlla se siamo sulla riga duration:
    if not (stripped.startswith('duration:') and ':' in stripped):
        return []

    # Estrai il nuovo valore di duration
    try:
        new_duration = float(stripped.split(':')[1].strip())
    except (ValueError, IndexError):
        return []

    # Recupera il contesto dello stream
    stream_ctx = YamlAnalyzer.get_stream_context_at_line(text, cursor_line)

    # Non proporre se normalized
    if stream_ctx['time_mode'] == 'normalized':
        return []

    # Calcola il vecchio end_time (duration precedente)
    # Scansiona i breakpoints dello stream per trovare il max X
    old_end_time = _find_max_x_in_stream(text, cursor_line)
    if old_end_time <= 0:
        return []

    actions = []

    # Costruisci la lista di TextEdit per riscalare le X
    edits = _build_rescale_edits(text, cursor_line, old_end_time, new_duration)

    # Azione 1: riscala X dei breakpoints standard
    if edits:
        actions.append(CodeAction(
            title=f'Riscala X breakpoints: {old_end_time:.4g} -> {new_duration:.4g}',
            kind=CodeActionKind.QuickFix,
            edit=WorkspaceEdit(
                document_changes=[
                    TextDocumentEdit(
                        text_document=OptionalVersionedTextDocumentIdentifier(uri=uri),
                        edits=edits,
                    )
                ]
            ),
        ))

    # Azione 2: aggiorna end_time nei compact loop
    compact_edits = _build_compact_end_time_edits(text, cursor_line, old_end_time, new_duration)
    if compact_edits:
        actions.append(CodeAction(
            title=f'Aggiorna end_time compact loop: {old_end_time:.4g} -> {new_duration:.4g}',
            kind=CodeActionKind.QuickFix,
            edit=WorkspaceEdit(
                document_changes=[
                    TextDocumentEdit(
                        text_document=OptionalVersionedTextDocumentIdentifier(uri=uri),
                        edits=compact_edits,
                    )
                ]
            ),
        ))

    return actions


def _find_max_x_in_stream(text: str, cursor_line: int) -> float:
    """
    Trova il tempo massimo tra tutti gli envelope nello stream corrente.

    Scansiona:
    - Breakpoints standard [x, y]: usa x come tempo
    - Compact loop [[[...]], end_time, n_reps]: usa end_time come tempo
    - Dict envelope (type: .., points: [...]): usa il max x dei points
    """
    import ast
    lines = text.split('\n')
    max_x = 0.0
    stream_text = '\n'.join(lines[slice(*_get_stream_bounds(lines, cursor_line))])

    # Scansione riga per riga
    for i, line in enumerate(lines[slice(*_get_stream_bounds(lines, cursor_line))]):
        stripped = line.strip()

        # Breakpoint standard: - [x, y]
        if stripped.startswith('- [') and not stripped.startswith('- [[['):
            try:
                parsed = ast.literal_eval(stripped[2:].strip())
                if isinstance(parsed, list) and len(parsed) >= 1:
                    max_x = max(max_x, float(parsed[0]))
            except Exception:
                pass

        # Compact loop: - [[[...]], end_time, n_reps, ...]
        elif stripped.startswith('- [[['):
            try:
                parsed = ast.literal_eval(stripped[2:].strip())
                if isinstance(parsed, list) and len(parsed) >= 2:
                    max_x = max(max_x, float(parsed[1]))
            except Exception:
                pass

        # Valore scalare con end_time inline come tipo: [[[...]], 30.0, 4]
        # (riga senza '- ', dentro un valore di parametro con : )
        elif stripped.startswith('[[['):
            try:
                parsed = ast.literal_eval(stripped)
                if isinstance(parsed, list) and len(parsed) >= 2:
                    max_x = max(max_x, float(parsed[1]))
            except Exception:
                pass

    return max_x


def _build_rescale_edits(text: str, cursor_line: int,
                          old_end: float, new_end: float):
    """Costruisce TextEdit per riscalare proporzionalmente le X dei breakpoints."""
    import ast
    import re
    lines = text.split('\n')
    edits = []
    ratio = new_end / old_end

    start, end = _get_stream_bounds(lines, cursor_line)
    for i in range(start, end):
        raw = lines[i]
        stripped = raw.strip()
        # Solo breakpoints standard [x, y], non compact [[[...]]]
        if not (stripped.startswith('- [') and not stripped.startswith('- [[[')):
            continue
        try:
            inner = stripped[2:].strip()
            parsed = ast.literal_eval(inner)
            if not (isinstance(parsed, list) and len(parsed) >= 2):
                continue
            old_x = float(parsed[0])
            new_x = round(old_x * ratio, 6)
            # Ricostruisce la riga con nuovo X
            new_inner = str([new_x] + list(parsed[1:]))
            leading = raw[:len(raw) - len(raw.lstrip())]
            new_line = leading + '- ' + new_inner
            edits.append(TextEdit(
                range=Range(
                    start=Position(line=i, character=0),
                    end=Position(line=i, character=len(raw)),
                ),
                new_text=new_line,
            ))
        except Exception:
            pass
    return edits


def _build_compact_end_time_edits(text: str, cursor_line: int,
                                  old_end: float, new_end: float):
    """
    Costruisce TextEdit per aggiornare l'end_time nei compact loop.

    Gestisce tutte le varianti:
    - Riga '- [[[...]], old_end, n_reps]' (compact con marcatore lista)
    - Riga '[[[...]], old_end, n_reps]' (compact inline senza marcatore)
    """
    import ast
    lines = text.split('\n')
    edits = []
    start, end_line = _get_stream_bounds(lines, cursor_line)

    # Tutti i valori da sostituire (gestisce int e float)
    old_strs = set()
    old_strs.add(str(old_end))
    if old_end == int(old_end):
        old_strs.add(str(int(old_end)) + '.0')
        old_strs.add(str(int(old_end)))
    new_str = str(new_end) if new_end != int(new_end) else str(int(new_end)) + '.0'

    for i in range(start, end_line):
        raw = lines[i]
        stripped = raw.strip()

        # Rimuovi prefisso '- ' per analisi
        has_dash = stripped.startswith('- ')
        inner = stripped[2:].strip() if has_dash else stripped

        if not inner.startswith('[[['):
            continue

        try:
            parsed = ast.literal_eval(inner)
            if not (isinstance(parsed, list) and len(parsed) >= 2):
                continue
            current_end = float(parsed[1])
        except Exception:
            continue

        # Controlla se e' il valore da aggiornare
        matched_old = None
        for os in old_strs:
            try:
                if float(os) == current_end:
                    matched_old = os
                    break
            except Exception:
                pass

        if matched_old is None:
            continue

        # Ricostruisce la riga sostituendo end_time
        parsed[1] = new_end
        new_inner = repr(parsed).replace('(', '[').replace(')', ']')
        # Usa ast per una rappresentazione pulita
        try:
            import json
            new_inner = json.dumps(parsed, separators=(', ', ': '))
            new_inner = new_inner.replace('"', '')
        except Exception:
            new_inner = str(parsed)

        leading = raw[:len(raw) - len(raw.lstrip())]
        prefix = '- ' if has_dash else ''
        new_line = leading + prefix + new_inner

        edits.append(TextEdit(
            range=Range(
                start=Position(line=i, character=0),
                end=Position(line=i, character=len(raw)),
            ),
            new_text=new_line,
        ))

    return edits


def _get_stream_bounds(lines, cursor_line):
    """Ritorna (start, end) dello stream che contiene cursor_line."""
    stream_start = 0
    for i in range(cursor_line, -1, -1):
        raw = lines[i]
        stripped = raw.strip()
        leading = len(raw) - len(raw.lstrip())
        if (stripped.startswith('- ') or stripped == '-') and leading == 2:
            stream_start = i
            break

    stream_end = len(lines)
    for i in range(stream_start + 1, len(lines)):
        raw = lines[i]
        stripped = raw.strip()
        leading = len(raw) - len(raw.lstrip())
        if (stripped.startswith('- ') or stripped == '-') and leading == 2:
            stream_end = i
            break

    return stream_start, stream_end



@server.feature(
    TEXT_DOCUMENT_DOCUMENT_LINK,
    DocumentLinkOptions(resolve_provider=False),
)
def handle_document_link(params: DocumentLinkParams):
    """
    Sottolinea il nome del file in 'sample: "file.wav"' quando esiste in refs/.
    Cmd+Click apre il file audio.
    """
    uri = params.text_document.uri
    if not is_pge_file(uri):
        return []
    if not _src_path:
        return []

    text = _get_document_text(server, uri)
    if not text:
        return []

    import re
    from pathlib import Path
    links = []
    refs_dir = (Path(_src_path) / '..' / 'refs').resolve()

    for line_n, line in enumerate(text.split('\n')):
        stripped = line.strip()
        if not re.match(r'sample\s*:', stripped):
            continue

        colon_idx = line.find(':')
        if colon_idx < 0:
            continue

        after_colon = line[colon_idx + 1:].lstrip()
        if not after_colon:
            continue

        value_start_in_line = len(line) - len(line[colon_idx + 1:].lstrip()) + colon_idx + 1

        if after_colon[0] in ('"', "'"):
            quote_char = after_colon[0]
            end_quote = after_colon.find(quote_char, 1)
            if end_quote < 0:
                continue
            filename = after_colon[1:end_quote]
            char_start = value_start_in_line + 1
            char_end   = value_start_in_line + end_quote
        else:
            m = re.match(r'([^\s]+)', after_colon)
            if not m:
                continue
            filename = m.group(1)
            char_start = value_start_in_line
            char_end   = value_start_in_line + len(filename)

        if not filename:
            continue

        target = refs_dir / filename
        if not target.exists():
            continue

        links.append(DocumentLink(
            range=Range(
                start=Position(line=line_n, character=char_start),
                end=Position(line=line_n, character=char_end),
            ),
            target=target.as_uri(),
        ))

    return links


@server.feature(TEXT_DOCUMENT_DEFINITION)
def handle_definition(params: DefinitionParams):
    """
    Cmd+Click su 'sample: "file.wav"' apre il file audio.

    Percorso ricostruito: {src_path}/../refs/{filename}

    Funziona solo se --src e' stato passato al server.
    """
    uri = params.text_document.uri
    if not is_pge_file(uri):
        return None
    if not _src_path:
        return None

    text = _get_document_text(server, uri)
    if not text:
        return None

    lines = text.split('\n')
    line_n = params.position.line
    if line_n >= len(lines):
        return None

    line = lines[line_n]
    stripped = line.strip()

    # Controlla che la riga sia 'sample: ...' ed estrae il nome file.
    # Gestisce: file.wav, "file with spaces.wav", 'file.wav'
    import re
    if not re.match(r'sample\s*:', stripped):
        return None
    colon_idx = stripped.find(':')
    raw_value = stripped[colon_idx + 1:].strip()
    if ((raw_value.startswith('"') and raw_value.endswith('"')) or
            (raw_value.startswith("'") and raw_value.endswith("'"))):
        filename = raw_value[1:-1]
    else:
        filename = raw_value.split()[0] if raw_value else ''
    if not filename:
        return None

    # Ricostruisce il percorso: src/../refs/filename
    from pathlib import Path
    refs_dir = (Path(_src_path) / '..' / 'refs').resolve()
    target = refs_dir / filename

    if not target.exists():
        logger.warning(f"File audio non trovato: {target}")
        return None

    target_uri = target.as_uri()
    logger.info(f"Definition -> {target_uri}")

    return Location(
        uri=target_uri,
        range=Range(
            start=Position(line=0, character=0),
            end=Position(line=0, character=0),
        ),
    )


def _publish_diagnostics(uri: str) -> None:
    """
    Analizza il documento e pubblica i diagnostici a VSCode.

    Questo e' il meccanismo push: il server invia i diagnostici
    senza che VSCode li abbia richiesti esplicitamente.
    Viene chiamato ogni volta che il documento cambia.
    """
    if not is_pge_file(uri):
        return

    if _diagnostic_provider is None:
        return

    text = _get_document_text(server, uri)
    diagnostics = _diagnostic_provider.get_diagnostics(text)

    server.publish_diagnostics(uri, diagnostics)
    logger.debug(f"Pubblicati {len(diagnostics)} diagnostici per {uri}")


@server.feature(TEXT_DOCUMENT_DID_OPEN)
def handle_did_open(params: DidOpenTextDocumentParams) -> None:
    """Documento aperto: pubblica subito i diagnostici iniziali."""
    _publish_diagnostics(params.text_document.uri)


@server.feature(TEXT_DOCUMENT_DID_CHANGE)
def handle_did_change(params: DidChangeTextDocumentParams) -> None:
    """Documento modificato: ricalcola e pubblica i diagnostici."""
    _publish_diagnostics(params.text_document.uri)


@server.feature(TEXT_DOCUMENT_DID_SAVE)
def handle_did_save(params: DidSaveTextDocumentParams) -> None:
    """Documento salvato: ricalcola i diagnostici anche al salvataggio."""
    _publish_diagnostics(params.text_document.uri)


# =============================================================================
# ENTRY POINT
# =============================================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        description='PGE Language Server per file PGE_*.yaml'
    )
    parser.add_argument(
        '--src',
        type=str,
        default=None,
        help='Path alla directory src del progetto granulare. '
             'Se non fornito, usa lo snapshot JSON se disponibile.'
    )
    parser.add_argument(
        '--snapshot',
        type=str,
        default=None,
        help='Path a un file snapshot JSON generato da SchemaBridge.'
    )
    args, _ = parser.parse_known_args()

    # Strategia di caricamento del bridge (in ordine di preferenza):
    # 1. --src fornito esplicitamente: importa i moduli Python reali
    # 2. --snapshot fornito esplicitamente: carica da JSON
    # 3. snapshot.json nella stessa directory del server: carica da JSON
    # 4. Nessuna fonte disponibile: avvia con bridge vuoto (degraded mode)

    bridge = None

    global _src_path
    if args.src:
        _src_path = args.src
        try:
            logger.info(f"Caricamento schema da: {args.src}")
            bridge = SchemaBridge.from_python_path(args.src)
        except Exception as e:
            logger.error(f"Errore caricamento da --src: {e}")

    if bridge is None and args.snapshot:
        try:
            logger.info(f"Caricamento snapshot da: {args.snapshot}")
            bridge = SchemaBridge.from_snapshot(args.snapshot)
        except Exception as e:
            logger.error(f"Errore caricamento da --snapshot: {e}")

    if bridge is None:
        default_snapshot = _HERE / 'schema_snapshot.json'
        if default_snapshot.exists():
            try:
                logger.info(f"Caricamento snapshot default: {default_snapshot}")
                bridge = SchemaBridge.from_snapshot(str(default_snapshot))
            except Exception as e:
                logger.error(f"Errore snapshot default: {e}")

    if bridge is None:
        logger.warning(
            "Nessuna fonte schema disponibile. "
            "Il server funzionera' senza autocompletion. "
            "Usa --src o --snapshot per abilitare le funzionalita'."
        )
        bridge = SchemaBridge({'specs': [], 'bounds': {}})

    _init_providers(bridge)

    # Avvia il server in modalita' stdio: legge da stdin, scrive su stdout.
    # E' la modalita' standard per Language Server lanciati da VSCode.
    logger.info("PGE Language Server avviato (stdio mode).")
    server.start_io()


if __name__ == '__main__':
    main()
