# granular_ls/providers/completion_provider.py
"""
CompletionProvider - Suggerisce chiavi YAML valide in base al contesto.

Logica per livello:

ROOT LEVEL (in_stream_element=False, parent_path=[]):
    Solo 'streams:' come snippet con i campi obbligatori.

STREAM START (context_type='stream_start', sulla riga '- '):
    Le stream context keys (stream_id, onset, duration, sample, ...).

STREAM ELEMENT LEVEL (in_stream_element=True, parent_path=[]):
    - Stream context keys (stream_id, onset, duration, ...)
    - Parametri root dello stream (density, volume, fill_factor, ...)
    - Block keys come blocchi (grain, pointer, pitch, dephase)

DENTRO UN BLOCCO (parent_path=['grain']):
    Solo i parametri di quel blocco con label locale (duration, non grain.duration).

DENTRO DEPHASE (parent_path=['dephase']):
    Le dephase keys ricavate dagli ParameterSpec.
"""

import re
from typing import List, Optional, Set

from lsprotocol.types import (
    Command,
    CompletionItem,
    CompletionItemKind,
    InsertTextFormat,
    MarkupContent,
    MarkupKind,
)

# Comando VSCode che apre automaticamente il menu di completion.
# Allegato ai block keys cosi' dopo l'inserimento appare subito il menu.
def _next_stream_id(document_text: str) -> str:
    """
    Calcola il prossimo stream_id di default contando gli stream gia' presenti.

    Conta i marcatori '- ' a indent 2 (2 spazi) nel documento.
    Ritorna 'stream1', 'stream2', ecc.
    """
    if not document_text:
        return 'stream1'
    count = sum(
        1 for line in document_text.split('\n')
        if (len(line) - len(line.lstrip())) == 2
        and line.strip().startswith('- ')
    )
    return f'stream{count + 1}'


TRIGGER_SUGGEST = Command(
    title='Trigger Suggest',
    command='editor.action.triggerSuggest',
)

from granular_ls.schema_bridge import SchemaBridge, ParameterInfo
from granular_ls.envelope_snippets import EnvelopeSnippetProvider
from granular_ls.yaml_analyzer import YamlContext

# Documentazione statica per le stream context keys
_STREAM_CONTEXT_DOCS = {
    'stream_id':           'Identificatore univoco dello stream (stringa).',
    'onset':               'Tempo di inizio dello stream in secondi.',
    'duration':            'Durata totale dello stream in secondi.',
    'sample':              'Percorso relativo al file audio sorgente (.wav).',
    'time_mode':           "Modalita tempo: 'absolute' (default) | 'normalized'.",
    'time_scale':          'Moltiplicatore globale dei tempi (default: 1.0).',
    'range_always_active': 'Se True, il range si applica anche senza dephase.',
    'distribution_mode':   "Distribuzione grani: 'uniform' (default).",
    'dephase':             "Randomizzazione inter-grano. Bool, float, envelope o dict.",
    'solo': (
        "Flag: quando presente, SOLO gli stream con 'solo' vengono renderizzati. "
        "Gli altri vengono ignorati. Utile per isolare un layer durante la composizione."
    ),
    'mute': (
        "Flag: silenzia questo stream senza rimuoverlo dal YAML. "
        "Utile per disabilitare temporaneamente un layer sonoro."
    ),
    # Chiavi speciali del blocco pointer
    'start': (
        "Posizione iniziale di lettura nel sample (in secondi assoluti o normalizzati). "
        "Valore raw: NON accetta envelope. "
        "Default: 0.0 (inizio del sample). "
        "Se loop_unit o time_mode e' 'normalized', il valore e' in [0.0, 1.0] "
        "e viene scalato per la durata del sample."
    ),
    'loop_unit': (
        "Meta-parametro che controlla l'unita' di misura dei parametri loop "
        "(loop_start, loop_end, loop_dur) e di start.\n\n"
        "Valori accettati:\n"
        "- 'normalized': i valori loop sono in [0.0, 1.0] e vengono scalati "
        "moltiplicandoli per la durata del sample sorgente.\n"
        "- 'absolute' (o assente): i valori sono in secondi assoluti.\n\n"
        "Se assente, eredita il comportamento da time_mode dello stream. "
        "Non e' un parametro sintetizzabile: non accetta envelope."
    ),
}


class CompletionProvider:

    def __init__(self, bridge: SchemaBridge):
        self._bridge = bridge
        self._envelope_provider = EnvelopeSnippetProvider(bridge)

    def get_completions(self, context: YamlContext,
                        document_text: str) -> List[CompletionItem]:
        """
        Produce la lista di CompletionItem per il contesto dato.
        """
        # STREAMS LIST LEVEL: cursore a indent 1 dentro streams:, riga vuota
        # L'utente sta per scrivere un nuovo '- '
        if context.context_type == 'streams_list_level':
            return self._get_new_stream_snippet(document_text)

        # STREAM START: riga con '- ', ha priorita' su tutto il resto
        if context.context_type == 'stream_start':
            return self._get_stream_context_completions(context.current_text, document_text)

        # Contesto 'value': suggerisce snippet envelope se il parametro
        # e' numerico (is_smart=True, non-interno).
        # DEVE stare prima del gate root level altrimenti viene bloccato
        # quando in_stream_element non viene rilevato correttamente.
        if context.context_type == 'value':
            return self._get_envelope_completions(context, document_text)

        # ROOT LEVEL: solo 'streams:' come snippet
        if not context.in_stream_element and context.parent_path == []:
            if context.context_type == 'key':
                return self._get_root_completions(context.current_text)
            return []

        # Solo in 'key' suggeriamo chiavi
        if context.context_type != 'key':
            return []

        # DENTRO DEPHASE
        if context.parent_path == ['dephase']:
            return self._get_dephase_completions(context.current_text, context, document_text)

        # DENTRO UN BLOCCO (grain, pointer, pitch, ...)
        if context.parent_path:
            return self._get_block_param_completions(context, document_text)

        # STREAM ELEMENT LEVEL (in_stream_element=True, parent_path=[])
        if context.in_stream_element:
            return self._get_stream_level_completions(context, document_text)

        return []

    # -------------------------------------------------------------------------
    # ROOT LEVEL
    # -------------------------------------------------------------------------

    def _get_root_completions(self, current_text: str) -> List[CompletionItem]:
        """
        A root level mostriamo solo 'streams:' come snippet espanso
        con i campi obbligatori di uno stream.
        """
        if current_text and not 'streams'.startswith(current_text.lower()):
            return []

        # Snippet: streams: + primo elemento con campi obbligatori
        snippet = (
            'streams:\n'
            '  - stream_id: "${1:nome_stream}"\n'
            '    onset: ${2:0.0}\n'
            '    duration: ${3:10.0}\n'
            '    sample: "${4:file.wav}"'
        )
        return [CompletionItem(
            label='streams',
            insert_text=snippet,
            insert_text_format=InsertTextFormat.Snippet,
            kind=CompletionItemKind.Module,
            detail='streams block',
            documentation=MarkupContent(
                kind=MarkupKind.Markdown,
                value=(
                    '**streams**\n\n'
                    'Blocco principale. Contiene la lista degli stream granulari.\n\n'
                    'Inserisce il blocco con i campi obbligatori del primo stream.'
                ),
            ),
        )]

    # -------------------------------------------------------------------------
    # STREAM ELEMENT LEVEL
    # -------------------------------------------------------------------------

    def _get_stream_level_completions(self, context: YamlContext,
                                       document_text: str) -> List[CompletionItem]:
        """
        Completamenti disponibili dentro un elemento stream (parent_path=[]).
        Include: stream context keys + parametri root + block keys.

        Attivo SOLO a indent_level == 2 (esattamente 4 spazi dall'inizio riga).
        A livelli diversi i parametri stream non devono comparire:
        - indent 0: root level (solo 'streams:')
        - indent 1: riga con '- ' (stream_start)
        - indent 2: parametri diretti dello stream  <- SOLO QUI
        - indent 3+: parametri di blocchi annidati (grain, pointer, pitch)
        """
        if context.indent_level != 2:
            return []

        already_present = self._extract_present_keys(document_text, context)
        prefix = context.current_text.lower()
        items = []

        # 1. Stream context keys (stream_id, onset, duration, sample, solo, mute, ...)
        for key in self._bridge.get_stream_context_keys():
            if prefix and not key.lower().startswith(prefix):
                continue
            if key in already_present:
                continue
            doc = _STREAM_CONTEXT_DOCS.get(key, f'Chiave stream: {key}')
            items.append(CompletionItem(
                label=key,
                insert_text=key + ': ',
                kind=CompletionItemKind.Field,
                detail='stream context',
                documentation=MarkupContent(
                    kind=MarkupKind.Markdown,
                    value=f'**{key}**\n\n{doc}',
                ),
            ))

        # Tieni traccia delle label gia' inserite: aggiornato a ogni sezione
        # per evitare duplicati tra stream context keys, parametri e block keys.
        # Es. solo/mute sono sia stream context keys che ParameterInfo.
        inserted_labels = {item.label for item in items}

        # 2. Parametri root-level dello stream (density, volume, fill_factor, ...)
        root_params = [
            p for p in self._bridge.get_completion_parameters()
            if '.' not in p.yaml_path
        ]
        for p in root_params:
            if prefix and not p.yaml_path.lower().startswith(prefix):
                continue
            if p.yaml_path in already_present:
                continue
            if p.name in inserted_labels or p.yaml_path in inserted_labels:
                continue
            items.append(self._build_item_local(p))  # gia' ha TRIGGER_SUGGEST
            inserted_labels.add(p.yaml_path.split('.')[-1])

        # 3. Block keys (grain, pointer, pitch, dephase)
        block_keys = list(self._bridge.get_block_keys())
        if 'dephase' not in block_keys and self._bridge.get_dephase_keys():
            block_keys.append('dephase')

        inserted_labels = {item.label for item in items}

        for key in block_keys:
            if prefix and not key.lower().startswith(prefix):
                continue
            if key in already_present:
                continue
            if key in inserted_labels:
                continue
            items.append(CompletionItem(
                label=key,
                insert_text=key + ':\n  $0',
                insert_text_format=InsertTextFormat.Snippet,
                kind=CompletionItemKind.Module,
                detail='block',
                documentation=MarkupContent(
                    kind=MarkupKind.Markdown,
                    value=f'**{key}**\n\nBlocco di parametri annidati.',
                ),
                command=TRIGGER_SUGGEST,
            ))

        return items

    # -------------------------------------------------------------------------
    # STREAM CONTEXT (su riga '- ')
    # -------------------------------------------------------------------------

    def _get_new_stream_snippet(self,
                                document_text: str = '') -> List[CompletionItem]:
        """
        Snippet per un nuovo elemento stream.
        Mostrato quando il cursore e' al livello della lista streams:
        (2 spazi di indentazione, riga vuota).
        Inserisce '- ' seguito dai campi obbligatori.
        Il counter stream_id e' calcolato dal numero di stream gia' presenti.
        """
        stream_id_default = _next_stream_id(document_text)
        snippet = (
            f'- stream_id: "${{1:{stream_id_default}}}"\n'
            '  onset: ${2:0.0}\n'
            '  duration: ${3:10.0}\n'
            '  sample: "${4:file.wav}"\n'
            '  $0'
        )
        return [CompletionItem(
            label='- (nuovo stream)',
            insert_text=snippet,
            insert_text_format=InsertTextFormat.Snippet,
            kind=CompletionItemKind.Module,
            detail='stream_id, onset, duration, sample',
            documentation=MarkupContent(
                kind=MarkupKind.Markdown,
                value=(
                    '**Nuovo elemento stream**\n\n'
                    'Inserisce il trattino `- ` e i quattro campi obbligatori.\n'
                    'Premi **Tab** per spostarti tra i campi.'
                ),
            ),
        )]

    def _get_stream_context_completions(self,
                                        current_text: str,
                                        document_text: str = '') -> List[CompletionItem]:
        """
        Completion per le chiavi di contesto stream sulla riga '- '.

        Il primo item e' un SNIPPET che inserisce tutti e quattro i campi
        obbligatori in una volta sola. Gli altri item permettono di inserire
        le chiavi singolarmente se necessario.
        """
        items = []

        # Item principale: snippet con tutti i campi obbligatori.
        # Usa tab stops ($1, $2, ...) per navigare tra i campi con Tab.
        # $0 posiziona il cursore finale dopo l'ultimo campo.
        stream_id_default = _next_stream_id(document_text)
        obligatory_snippet = (
            f'stream_id: "${{1:{stream_id_default}}}"\n'
            'onset: ${2:0.0}\n'
            'duration: ${3:10.0}\n'
            'sample: "${4:file.wav}"\n'
            '$0'
        )
        items.append(CompletionItem(
            label='stream (obbligatori)',
            insert_text=obligatory_snippet,
            insert_text_format=InsertTextFormat.Snippet,
            kind=CompletionItemKind.Module,
            detail='stream_id, onset, duration, sample',
            documentation=MarkupContent(
                kind=MarkupKind.Markdown,
                value=(
                    '**Nuovo stream**\n\n'
                    'Inserisce i quattro campi obbligatori:\n'
                    '- `stream_id`: identificatore univoco\n'
                    '- `onset`: tempo di inizio in secondi\n'
                    '- `duration`: durata in secondi\n'
                    '- `sample`: percorso file audio\n\n'
                    'Premi **Tab** per spostarti tra i campi.'
                ),
            ),
            command=TRIGGER_SUGGEST,
        ))

        # Items singoli: utili quando lo stream esiste gia' e si vuole
        # aggiungere una chiave specifica.
        keys = self._bridge.get_stream_context_keys()
        prefix = current_text.lower()
        for key in keys:
            if prefix and not key.lower().startswith(prefix):
                continue
            # Salta i campi gia' coperti dallo snippet obbligatorio
            if key in ('stream_id', 'onset', 'duration', 'sample'):
                continue
            doc = _STREAM_CONTEXT_DOCS.get(key, f'Chiave stream: {key}')
            items.append(CompletionItem(
                label=key,
                insert_text=key + ': ',
                kind=CompletionItemKind.Field,
                detail='stream context',
                documentation=MarkupContent(
                    kind=MarkupKind.Markdown,
                    value=f'**{key}**\n\n{doc}',
                ),
            ))
        return items

    # -------------------------------------------------------------------------
    # DENTRO UN BLOCCO (grain, pointer, pitch)
    # -------------------------------------------------------------------------

    def _get_block_param_completions(self, context: YamlContext,
                                       document_text: str = '') -> List[CompletionItem]:
        """
        Parametri di un blocco annidato.
        Label: chiave locale ('duration', non 'grain.duration').
        Esclude i parametri gia' presenti nel blocco corrente.
        """
        prefix_path = '.'.join(context.parent_path) + '.'
        # Include tutti i parametri del blocco, anche is_smart=False.
        # I parametri non-smart (es. pointer.start) non aprono il menu
        # envelope quando accettati (nessun TRIGGER_SUGGEST).
        candidates = [
            p for p in self._bridge.get_all_parameters()
            if p.yaml_path.startswith(prefix_path)
            and not p.is_internal
        ]
        already_present = self._extract_present_keys(document_text, context)
        text_prefix = context.current_text.lower()
        items = []
        for p in candidates:
            local_key = p.yaml_path.split('.')[-1]
            if text_prefix and not local_key.lower().startswith(text_prefix):
                continue
            if local_key in already_present:
                continue
            # Solo i parametri smart aprono il menu envelope
            if p.is_smart:
                items.append(self._build_item_local(p))
            else:
                items.append(self._build_item_raw(p))
        # Aggiungi chiavi statiche speciali del blocco (es. loop_unit per pointer)
        items.extend(self._get_block_static_extras(context, already_present, text_prefix))
        return items

    # -------------------------------------------------------------------------
    # DENTRO DEPHASE
    # -------------------------------------------------------------------------

    def _get_dephase_completions(self,
                                  current_text: str,
                                  context=None,
                                  document_text: str = '') -> List[CompletionItem]:
        """Completion per le chiavi del blocco dephase:."""
        dephase_keys = self._bridge.get_dephase_keys()
        prefix = current_text.lower()
        already_present = self._extract_present_keys(document_text, context)
        items = []
        for key in dephase_keys:
            if prefix and not key.lower().startswith(prefix):
                continue
            if key in already_present:
                continue
            items.append(CompletionItem(
                label=key,
                insert_text=key + ': ',
                kind=CompletionItemKind.Field,
                detail='dephase key',
                documentation=MarkupContent(
                    kind=MarkupKind.Markdown,
                    value=f'**{key}**\n\nControllo dephase per `{key}`.',
                ),
                command=TRIGGER_SUGGEST,
            ))
        return items

    def _get_envelope_completions(self, context,
                                   document_text: str = '') -> List[CompletionItem]:
        """
        Snippet envelope per il contesto 'value'.

        Viene chiamato quando il cursore e' dopo il ':' di un parametro.
        Usa context.current_key per identificare il parametro e filtrare
        gli snippet tramite EnvelopeSnippetProvider.

        L'end_time di default degli snippet compact viene calcolato
        dal contesto dello stream corrente:
        - time_mode: normalized -> end_time = 1.0
        - altrimenti            -> end_time = onset dello stream
        """
        if not context.current_key:
            return []

        # Calcola end_time dal contesto dello stream corrente
        end_time = self._get_end_time_from_context(context, document_text)

        # Caso speciale: 'dephase' come parametro diretto o chiave dentro dephase.
        # In entrambi i casi i bounds sono [0, 100].
        is_dephase_direct = (
            context.current_key == 'dephase'
            and context.parent_path == []
        )
        is_dephase_sub = context.parent_path == ['dephase']

        if is_dephase_direct or is_dephase_sub:
            return self._envelope_provider.get_snippets_with_bounds_and_end_time(
                y_min=0.0, y_max=100.0, end_time=end_time
            )

        yaml_path_candidates = []
        yaml_path_candidates.append(context.current_key)
        if context.parent_path:
            full_path = '.'.join(context.parent_path) + '.' + context.current_key
            yaml_path_candidates.insert(0, full_path)

        # Usa get_snippets_for_parameter_with_context che combina
        # bounds del parametro + end_time dinamico
        for candidate in yaml_path_candidates:
            items = self._envelope_provider.get_snippets_for_parameter_with_context(
                candidate, end_time
            )
            if items:
                return items

        return []

    def _get_end_time_from_context(self, context,
                                    document_text: str) -> float:
        """
        Calcola il valore default di end_time per gli snippet compact.

        Regole:
        - Se time_mode: normalized -> 1.0 (tempo normalizzato [0, 1])
        - Altrimenti               -> onset dello stream corrente
                                      (il loop parte dall'onset e finisce
                                       a onset + durata, ma usiamo onset
                                       come punto di riferimento iniziale)
        """
        from granular_ls.yaml_analyzer import YamlAnalyzer
        stream_ctx = YamlAnalyzer.get_stream_context_at_line(
            document_text, context.cursor_line
        )
        if stream_ctx['time_mode'] == 'normalized':
            return 1.0
        return stream_ctx['duration']

    # -------------------------------------------------------------------------
    # HELPERS
    # -------------------------------------------------------------------------

    def _build_item_raw(self, param: ParameterInfo) -> CompletionItem:
        """
        CompletionItem per parametri non-smart (is_smart=False).
        Non allega TRIGGER_SUGGEST: questi parametri non accettano envelope.
        """
        local_key = param.yaml_path.split('.')[-1]
        doc = _STREAM_CONTEXT_DOCS.get(local_key)
        if doc is None:
            doc = self._bridge.get_documentation(param)
        return CompletionItem(
            label=local_key,
            insert_text=local_key + ': ',
            kind=CompletionItemKind.Field,
            detail='raw value (no envelope)',
            documentation=MarkupContent(
                kind=MarkupKind.Markdown,
                value=doc,
            ),
            # Nessun command: non apre il menu envelope
        )

    def _get_block_static_extras(
        self, context, already_present: set, text_prefix: str
    ) -> List[CompletionItem]:
        """
        Chiavi statiche che non sono in nessuno schema ma appartengono
        a blocchi specifici. Attualmente: loop_unit per pointer.
        """
        items = []
        block = context.parent_path[0] if context.parent_path else ''

        BLOCK_EXTRAS = {
            'pointer': [
                ('loop_unit', ': ', 'Meta-parametro: unita dei loop.',
                 _STREAM_CONTEXT_DOCS.get('loop_unit', '')),
            ],
        }

        for key, insert_suffix, detail, doc in BLOCK_EXTRAS.get(block, []):
            if text_prefix and not key.lower().startswith(text_prefix):
                continue
            if key in already_present:
                continue
            items.append(CompletionItem(
                label=key,
                insert_text=key + insert_suffix,
                kind=CompletionItemKind.Field,
                detail=detail,
                documentation=MarkupContent(
                    kind=MarkupKind.Markdown,
                    value=f'**{key}**\n\n{doc}',
                ),
                # Nessun TRIGGER_SUGGEST: loop_unit accetta solo stringhe
            ))
        return items

    def _build_item_local(self, param: ParameterInfo) -> CompletionItem:
        """
        Costruisce un CompletionItem con label = chiave locale.
        Es. param.yaml_path='grain.duration' -> label='duration', insert='duration: '

        Allega TRIGGER_SUGGEST come command: quando l'utente accetta questo
        item, VSCode apre automaticamente il menu per il valore (envelope snippets).
        """
        local_key = param.yaml_path.split('.')[-1]
        detail = ''
        if param.min_val is not None and param.max_val is not None:
            detail = f'[{param.min_val}, {param.max_val}]'
        doc = self._bridge.get_documentation(param)
        return CompletionItem(
            label=local_key,
            insert_text=local_key + ': ',
            kind=CompletionItemKind.Field,
            detail=detail,
            documentation=MarkupContent(
                kind=MarkupKind.Markdown,
                value=doc,
            ),
            command=TRIGGER_SUGGEST,
        )

    def _extract_present_keys(self, document_text: Optional[str],
                               context=None) -> Set[str]:
        """
        Estrae le chiavi gia' scritte nello scope corrente.

        Lo scope e' determinato dal contesto:
        - Se in_stream_element=True e parent_path=[]: solo le chiavi
          dello stream corrente (tra il suo '- ' e il prossimo '- ').
        - Se parent_path non vuoto: solo le chiavi del blocco corrente
          (grain, pointer, pitch, dephase) nello stream corrente.
        - Altrimenti: scan globale (root level).

        Questo garantisce che parametri di altri stream o altri blocchi
        non vengano esclusi dal menu di autocompletamento.
        """
        if not document_text:
            return set()

        lines = document_text.splitlines()

        # Determina i confini dello scope corrente
        start_line = 0
        end_line = len(lines)

        if context is not None and context.in_stream_element:
            cursor = context.cursor_line

            # Trova l'inizio dello stream corrente (marcatore '- ' a indent 2)
            stream_start = None
            for i in range(cursor, -1, -1):
                raw = lines[i] if i < len(lines) else ''
                stripped = raw.strip()
                leading = len(raw) - len(raw.lstrip())
                if (stripped.startswith('- ') or stripped == '-') and leading == 2:
                    stream_start = i
                    break

            if stream_start is not None:
                start_line = stream_start
                # Trova la fine dello stream (prossimo '- ' a indent 2 o EOF)
                for i in range(stream_start + 1, len(lines)):
                    raw = lines[i]
                    stripped = raw.strip()
                    leading = len(raw) - len(raw.lstrip())
                    if (stripped.startswith('- ') or stripped == '-') and leading == 2:
                        end_line = i
                        break

            # Se siamo dentro un blocco (parent_path non vuoto),
            # riduci ulteriormente lo scope al blocco corrente
            if context.parent_path:
                block_name = context.parent_path[0]
                block_start = None
                for i in range(start_line, end_line):
                    raw = lines[i] if i < len(lines) else ''
                    stripped = raw.strip()
                    # Riga tipo '    grain:' o '    pointer:'
                    if stripped == block_name + ':' or stripped.startswith(block_name + ':'):
                        block_start = i
                        break
                if block_start is not None:
                    start_line = block_start + 1
                    # Fine del blocco: riga a indent <= indent del blocco
                    block_raw = lines[block_start]
                    block_indent = len(block_raw) - len(block_raw.lstrip())
                    for i in range(block_start + 1, end_line):
                        raw = lines[i] if i < len(lines) else ''
                        if not raw.strip():
                            continue
                        curr_indent = len(raw) - len(raw.lstrip())
                        if curr_indent <= block_indent:
                            end_line = i
                            break

        # Calcola l'indentazione massima accettabile per le chiavi dello scope.
        # Tutto cio' che e' piu' profondo del blocco corrente + 1 livello
        # appartiene a sotto-blocchi (envelope dict, points, ecc.) e va ignorato.
        if context is not None and context.parent_path:
            # Dentro un blocco (grain, pointer, pitch): accetta solo indent == indent blocco+1
            max_leading = (context.indent_level) * 2  # indent_level e' gia' al livello del param
        else:
            max_leading = None  # nessun limite

        present = set()
        for line in lines[start_line:end_line]:
            # Filtra per indentazione massima se definita
            if max_leading is not None:
                raw_leading = len(line) - len(line.lstrip())
                if raw_leading > max_leading:
                    continue
            stripped = line.strip()
            if stripped.startswith('- '):
                stripped = stripped[2:].strip()
            if stripped.startswith('#') or not stripped:
                continue
            match = re.match(r'^([a-zA-Z_][a-zA-Z0-9_.]*)\s*:', stripped)
            if match:
                present.add(match.group(1))
        return present

    # -------------------------------------------------------------------------
    # METODI LEGACY (usati dai test esistenti)
    # -------------------------------------------------------------------------

    def _filter_by_parent_path(self,
                                parent_path: List[str]) -> List[ParameterInfo]:
        all_params = self._bridge.get_completion_parameters()
        if not parent_path:
            return [p for p in all_params if '.' not in p.yaml_path]
        prefix = '.'.join(parent_path) + '.'
        return [p for p in all_params if p.yaml_path.startswith(prefix)]

    def _filter_by_prefix(self, candidates: List[ParameterInfo],
                          current_text: str) -> List[ParameterInfo]:
        if not current_text:
            return candidates
        prefix_lower = current_text.lower()
        result = []
        for p in candidates:
            local_key = p.yaml_path.split('.')[-1]
            if local_key.lower().startswith(prefix_lower):
                result.append(p)
        return result

    def _build_item(self, param: ParameterInfo,
                    parent_path: List[str]) -> CompletionItem:
        return self._build_item_local(param)
