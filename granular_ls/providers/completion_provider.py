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
from pathlib import Path
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
from granular_ls.voice_strategies import (
    VOICE_STRATEGY_REGISTRY,
    VOICE_DIMENSIONS,
    VOICE_TOP_LEVEL_KEYS,
    VOICE_ENVELOPE_PARAMS,
    VOICES_BLOCK_DOC,
    get_strategies_for_dimension,
    get_strategy_spec,
    find_kwarg_in_dimension,
    get_top_level_doc,
)

# Chiavi flag: presenti senza valore, non accettano envelope
_FLAG_KEYS = {'mute', 'solo', 'range_always_active'}

# Chiavi con completions sul valore: aprono il menu automaticamente dopo ': '
_VALUE_TRIGGER_KEYS = {'sample', 'distribution_mode', 'time_mode', 'loop_unit'}

# Documentazione statica per le stream context keys
_STREAM_CONTEXT_DOCS = {
    'stream_id':           'Identificatore univoco dello stream (stringa).',
    'onset':               'Tempo di inizio dello stream in secondi.',
    'duration':            'Durata totale dello stream in secondi.',
    'sample':              'Percorso relativo al file audio sorgente (.wav).',
    'time_mode':           "Modalita tempo: 'absolute' (default) | 'normalized'.",
    'time_scale':          'Moltiplicatore globale dei tempi (default: 1.0).',
    'range_always_active': 'Se True, il range si applica anche senza dephase.',
    'distribution_mode':   None,  # generata dinamicamente
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

    def __init__(self, bridge: SchemaBridge, refs_dir: Optional[Path] = None):
        self._bridge = bridge
        self._envelope_provider = EnvelopeSnippetProvider(bridge)
        self._refs_dir = refs_dir

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

        # Contesto 'value' su chiave 'sample': mostra i file disponibili in refs/
        if context.context_type == 'value' and context.current_key == 'sample':
            return self._get_sample_completions(context.current_text)

        # Contesto 'value' su chiave 'distribution_mode': mostra le modalita' disponibili
        if context.context_type == 'value' and context.current_key == 'distribution_mode':
            return self._get_distribution_mode_completions(context.current_text)

        # Contesto 'value' su chiave 'time_mode': mostra i valori disponibili
        if context.context_type == 'value' and context.current_key == 'time_mode':
            return self._get_time_mode_completions(context.current_text)

        # Contesto 'value' su num_voices o scatter dentro voices: (envelope-capable)
        if (context.context_type == 'value'
                and context.parent_path == ['voices']
                and context.current_key in VOICE_ENVELOPE_PARAMS):
            bounds = VOICE_ENVELOPE_PARAMS[context.current_key]
            end_time = self._get_end_time_from_context(context, document_text)
            return self._envelope_provider.get_snippets_with_bounds_and_end_time(
                y_min=bounds['min_val'],
                y_max=bounds['max_val'],
                end_time=end_time,
            )

        # Contesto 'value' su 'strategy' dentro un blocco dimension di voices
        if (context.context_type == 'value'
                and context.current_key == 'strategy'
                and len(context.parent_path) >= 2
                and context.parent_path[0] == 'voices'
                and context.parent_path[1] in VOICE_DIMENSIONS):
            return self._get_voice_strategy_name_completions(context)

        # Contesto 'value' su kwarg enum di una voice strategy (es. chord: )
        if (context.context_type == 'value'
                and len(context.parent_path) >= 2
                and context.parent_path[0] == 'voices'):
            enum_items = self._get_voice_kwarg_enum_completions(context)
            if enum_items is not None:
                return enum_items

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

        # DENTRO IL BLOCCO VOICES (parent_path inizia con 'voices')
        if context.parent_path and context.parent_path[0] == 'voices':
            return self._get_voice_completions(context, document_text)

        # DENTRO UN BLOCCO (grain, pointer, pitch, ...)
        if context.parent_path:
            # Verifica che il blocco abbia effettivamente parametri nel bridge.
            # Se non ne ha (es. mute:, solo: trattati come blocchi dall'analizzatore
            # per via dell'auto-indent dopo ':'), si fa fallback al livello stream.
            prefix_path = '.'.join(context.parent_path) + '.'
            has_params = any(
                not p.is_internal and p.yaml_path.startswith(prefix_path)
                for p in self._bridge.get_all_parameters()
            )
            if has_params:
                return self._get_block_param_completions(context, document_text)
            # Fallback solo per flag keys (mute, solo): l'auto-indent di VSCode
            # dopo ':' porta il cursore un livello piu' in profondo, ma questi
            # non hanno figli. Tutti gli altri casi (envelope, blocchi sconosciuti)
            # devono restituire vuoto.
            if (len(context.parent_path) == 1
                    and context.parent_path[0] in _FLAG_KEYS
                    and context.in_stream_element):
                return self._get_stream_level_completions_fallback(context, document_text)
            return []

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
        if context.leading_spaces != 4:
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
            if key == 'distribution_mode':
                modes = self._bridge.get_distribution_modes()
                doc = (
                    "Variazione stocastica dei parametri: controlla la distribuzione "
                    "applicata quando un parametro ha `mod_range`.\n\n"
                    "Modalita' disponibili: " + ', '.join(f'`{m}`' for m in modes) +
                    "\n\nDefault: `uniform`"
                )
            else:
                doc = _STREAM_CONTEXT_DOCS.get(key, f'Chiave stream: {key}')
            if key in _FLAG_KEYS:
                # Flag senza valore: inserisce solo il nome, va a capo e apre il menu
                items.append(CompletionItem(
                    label=key,
                    insert_text=key + ':\n$0',
                    insert_text_format=InsertTextFormat.Snippet,
                    kind=CompletionItemKind.Field,
                    detail='flag (no value)',
                    documentation=MarkupContent(
                        kind=MarkupKind.Markdown,
                        value=f'**{key}**\n\n{doc}',
                    ),
                    command=TRIGGER_SUGGEST,
                ))
            else:
                items.append(CompletionItem(
                    label=key,
                    insert_text=key + ': ',
                    kind=CompletionItemKind.Field,
                    detail='stream context',
                    documentation=MarkupContent(
                        kind=MarkupKind.Markdown,
                        value=f'**{key}**\n\n{doc}',
                    ),
                    command=TRIGGER_SUGGEST if key in _VALUE_TRIGGER_KEYS else None,
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

        # 3. Block keys (grain, pointer, pitch, dephase, voices)
        block_keys = list(self._bridge.get_block_keys())
        if 'dephase' not in block_keys and self._bridge.get_dephase_keys():
            block_keys.append('dephase')
        if 'voices' not in block_keys:
            block_keys.append('voices')

        inserted_labels = {item.label for item in items}

        for key in block_keys:
            if prefix and not key.lower().startswith(prefix):
                continue
            if key in already_present:
                continue
            if key in inserted_labels:
                continue
            if key == 'voices':
                block_doc = VOICES_BLOCK_DOC
            else:
                block_doc = f'**{key}**\n\nBlocco di parametri annidati.'
            items.append(CompletionItem(
                label=key,
                insert_text=key + ':\n  $0',
                insert_text_format=InsertTextFormat.Snippet,
                kind=CompletionItemKind.Module,
                detail='block',
                documentation=MarkupContent(
                    kind=MarkupKind.Markdown,
                    value=block_doc,
                ),
                command=TRIGGER_SUGGEST,
            ))

        return items

    # -------------------------------------------------------------------------
    # STREAM CONTEXT (su riga '- ')
    # -------------------------------------------------------------------------

    def _get_stream_level_completions_fallback(self, context: YamlContext,
                                                document_text: str) -> List[CompletionItem]:
        """
        Fallback per quando il parent_path punta a un 'blocco' senza parametri
        (es. mute:, solo: che l'analizzatore scambia per blocchi per via
        dell'auto-indent VSCode dopo i ':').
        Ricicla _get_stream_level_completions ignorando il gate indent_level.
        """
        from dataclasses import replace
        fallback_ctx = replace(context, parent_path=[], indent_level=2, leading_spaces=4)
        return self._get_stream_level_completions(fallback_ctx, document_text)

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
            if key == 'distribution_mode':
                modes = self._bridge.get_distribution_modes()
                doc = (
                    "Variazione stocastica dei parametri: controlla la distribuzione "
                    "applicata quando un parametro ha `mod_range`.\n\n"
                    "Modalita' disponibili: " + ', '.join(f'`{m}`' for m in modes) +
                    "\n\nDefault: `uniform`"
                )
            else:
                doc = _STREAM_CONTEXT_DOCS.get(key, f'Chiave stream: {key}')
            if key in _FLAG_KEYS:
                items.append(CompletionItem(
                    label=key,
                    insert_text=key + ':\n$0',
                    insert_text_format=InsertTextFormat.Snippet,
                    kind=CompletionItemKind.Field,
                    detail='flag (no value)',
                    documentation=MarkupContent(
                        kind=MarkupKind.Markdown,
                        value=f'**{key}**\n\n{doc}',
                    ),
                    command=TRIGGER_SUGGEST,
                ))
            else:
                items.append(CompletionItem(
                    label=key,
                    insert_text=key + ': ',
                    kind=CompletionItemKind.Field,
                    detail='stream context',
                    documentation=MarkupContent(
                        kind=MarkupKind.Markdown,
                        value=f'**{key}**\n\n{doc}',
                    ),
                    command=TRIGGER_SUGGEST if key in _VALUE_TRIGGER_KEYS else None,
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
    # DISTRIBUTION MODE COMPLETIONS
    # -------------------------------------------------------------------------

    def _get_distribution_mode_completions(self, current_text: str) -> List[CompletionItem]:
        """
        Elenca le modalita' di distribuzione disponibili per distribution_mode.
        Lette dinamicamente dal bridge (DistributionFactory o fallback statico).
        """
        modes = self._bridge.get_distribution_modes()
        prefix = current_text.strip().strip('"\'').lower()
        items = []
        _MODE_DOCS = {
            'uniform': (
                'Distribuzione **uniforme**: la variazione stocastica e` applicata '
                'estraendo valori con probabilita` uniforme nel range `[v - mod_range, v + mod_range]`.'
            ),
            'gaussian': (
                'Distribuzione **gaussiana**: la variazione stocastica segue una '
                'curva a campana centrata sul valore nominale. '
                'I valori estremi sono meno probabili rispetto a `uniform`.'
            ),
        }
        for mode in modes:
            if prefix and not mode.lower().startswith(prefix):
                continue
            items.append(CompletionItem(
                label=mode,
                insert_text=f'"{mode}"',
                kind=CompletionItemKind.EnumMember,
                detail='distribution mode',
                documentation=MarkupContent(
                    kind=MarkupKind.Markdown,
                    value=_MODE_DOCS.get(mode, f'Modalita` di distribuzione: `{mode}`.'),
                ),
            ))
        return items

    def _get_time_mode_completions(self, current_text: str) -> List[CompletionItem]:
        """Valori disponibili per time_mode: absolute (default) | normalized."""
        _MODES = {
            'absolute': (
                'Tempi degli envelope in **secondi assoluti** (default).\n\n'
                'I breakpoints `[t, v]` usano `t` in secondi reali.'
            ),
            'normalized': (
                'Tempi degli envelope **normalizzati** in `[0.0, 1.0]`.\n\n'
                'I breakpoints `[t, v]` usano `t` come frazione della durata dello stream.'
            ),
        }
        prefix = current_text.strip().strip('"\'').lower()
        return [
            CompletionItem(
                label=mode,
                insert_text=f'"{mode}"\n$0',
                insert_text_format=InsertTextFormat.Snippet,
                kind=CompletionItemKind.EnumMember,
                detail='time mode',
                documentation=MarkupContent(
                    kind=MarkupKind.Markdown,
                    value=f'`{mode}`\n\n{doc}',
                ),
                command=TRIGGER_SUGGEST,
            )
            for mode, doc in _MODES.items()
            if not prefix or mode.startswith(prefix)
        ]

    # -------------------------------------------------------------------------
    # SAMPLE FILE COMPLETIONS
    # -------------------------------------------------------------------------

    def _get_sample_completions(self, current_text: str) -> List[CompletionItem]:
        """
        Elenca i file presenti in refs/ come CompletionItem per la chiave 'sample'.

        Mostra tutti i file (non le sottodirectory) presenti in refs/.
        Filtra per prefisso se l'utente ha gia' digitato parte del nome.
        """
        if self._refs_dir is None or not self._refs_dir.is_dir():
            return []

        prefix = current_text.strip().strip('"\'').lower()
        items = []
        EXCLUDED = {'.DS_Store', '.gitkeep'}
        for path in sorted(self._refs_dir.iterdir()):
            if not path.is_file():
                continue
            filename = path.name
            if filename in EXCLUDED:
                continue
            if prefix and not filename.lower().startswith(prefix):
                continue
            items.append(CompletionItem(
                label=filename,
                insert_text=f'"{filename}"',
                kind=CompletionItemKind.File,
                detail=path.suffix.lstrip('.').upper() if path.suffix else 'file',
                documentation=MarkupContent(
                    kind=MarkupKind.Markdown,
                    value=f'**{filename}**\n\nFile audio in `refs/`.',
                ),
            ))
        return items

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

    # -------------------------------------------------------------------------
    # VOICES: autocompletamento per il blocco voices e le sue strategy
    # -------------------------------------------------------------------------

    def _get_voice_completions(self, context: YamlContext,
                                document_text: str) -> List[CompletionItem]:
        """
        Dispatcher per il contesto voices.
        parent_path[0] e' garantito == 'voices' dal chiamante.
        """
        if len(context.parent_path) == 1:
            # Dentro voices: - suggerisce num_voices + dimensioni
            return self._get_voice_top_level_completions(context, document_text)
        if (len(context.parent_path) == 2
                and context.parent_path[1] in VOICE_DIMENSIONS):
            # Dentro una dimensione (es. voices.pitch) - suggerisce strategy + kwargs
            return self._get_voice_dimension_completions(context, document_text)
        return []

    def _get_voice_top_level_completions(self, context: YamlContext,
                                          document_text: str) -> List[CompletionItem]:
        """
        Completamenti al primo livello dentro voices:
        num_voices, pitch, onset_offset, pointer, pan.
        """
        already_present = self._extract_present_keys(document_text, context)
        prefix = context.current_text.lower()
        items = []

        # num_voices e scatter: envelope-capable, bounds da VOICE_ENVELOPE_PARAMS
        for key, bounds in VOICE_ENVELOPE_PARAMS.items():
            if key in already_present:
                continue
            if prefix and not key.startswith(prefix):
                continue
            doc = get_top_level_doc(key) or f'**{key}**'
            detail = f'[{bounds["min_val"]}, {bounds["max_val"]}]'
            items.append(CompletionItem(
                label=key,
                insert_text=key + ': ',
                kind=CompletionItemKind.Field,
                detail=detail,
                documentation=MarkupContent(kind=MarkupKind.Markdown, value=doc),
                command=TRIGGER_SUGGEST,
            ))

        # Dimensioni (pitch, onset_offset, pointer, pan)
        for dim in VOICE_DIMENSIONS:
            if dim in already_present:
                continue
            if prefix and not dim.startswith(prefix):
                continue
            doc = get_top_level_doc(dim) or f'**{dim}**\n\nDimensione voce.'
            strategies = get_strategies_for_dimension(dim)
            items.append(CompletionItem(
                label=dim,
                insert_text=dim + ':\n  strategy: ',
                insert_text_format=InsertTextFormat.PlainText,
                kind=CompletionItemKind.Module,
                detail=f'voice dim — {", ".join(strategies)}',
                documentation=MarkupContent(kind=MarkupKind.Markdown, value=doc),
                command=TRIGGER_SUGGEST,
            ))

        return items

    def _get_voice_dimension_completions(self, context: YamlContext,
                                          document_text: str) -> List[CompletionItem]:
        """
        Completamenti dentro un blocco dimensione (es. voices.pitch).
        Suggerisce 'strategy:' e i kwargs della strategy attiva.
        """
        dim = context.parent_path[1]
        already_present = self._extract_present_keys_in_voice_block(
            document_text, context
        )
        prefix = context.current_text.lower()
        items: List[CompletionItem] = []

        # 'strategy:' se non ancora presente
        if 'strategy' not in already_present:
            if not prefix or 'strategy'.startswith(prefix):
                strategies = get_strategies_for_dimension(dim)
                doc = (
                    f"**strategy**\n\nStrategy di voce per la dimensione `{dim}`.\n\n"
                    f"Valori disponibili: {', '.join(f'`{s}`' for s in strategies)}"
                )
                items.append(CompletionItem(
                    label='strategy',
                    insert_text='strategy: ',
                    kind=CompletionItemKind.Field,
                    detail=f'voice {dim} strategy',
                    documentation=MarkupContent(kind=MarkupKind.Markdown, value=doc),
                    command=TRIGGER_SUGGEST,
                ))

        # Kwargs della strategy attiva (se 'strategy' e' gia' scritta nel blocco)
        current_strategy = self._get_strategy_for_voice_block(document_text, context)
        if current_strategy:
            spec = get_strategy_spec(dim, current_strategy)
            if spec:
                for kwarg_name, kwarg_spec in spec.kwargs.items():
                    if kwarg_name in already_present:
                        continue
                    if prefix and not kwarg_name.startswith(prefix):
                        continue
                    items.append(self._build_voice_kwarg_item(kwarg_spec))

        return items

    def _get_voice_strategy_name_completions(
        self, context: YamlContext
    ) -> List[CompletionItem]:
        """
        Valori per 'strategy:' dentro un blocco dimensione voices.
        Restituisce i nomi delle strategy disponibili per la dimensione.
        """
        dim = context.parent_path[1]
        prefix = context.current_text.lower().strip().strip('"\'')
        items = []
        for strategy_name in get_strategies_for_dimension(dim):
            if prefix and not strategy_name.startswith(prefix):
                continue
            spec = get_strategy_spec(dim, strategy_name)
            kwargs_list = ', '.join(spec.kwargs.keys()) if spec else ''
            doc = spec.description if spec else ''
            items.append(CompletionItem(
                label=strategy_name,
                insert_text=strategy_name,
                kind=CompletionItemKind.EnumMember,
                detail=f'kwargs: {kwargs_list}' if kwargs_list else 'strategy',
                documentation=MarkupContent(
                    kind=MarkupKind.Markdown,
                    value=f'**{strategy_name}**\n\n{doc}',
                ),
            ))
        return items

    def _get_voice_kwarg_enum_completions(
        self, context: YamlContext
    ) -> Optional[List[CompletionItem]]:
        """
        Valori enum per i kwargs di tipo 'enum' (es. chord: ).
        Restituisce una lista se il kwarg e' di tipo enum, None altrimenti.
        """
        if len(context.parent_path) < 2 or context.parent_path[1] not in VOICE_DIMENSIONS:
            return None
        dim = context.parent_path[1]
        kwarg_spec = find_kwarg_in_dimension(dim, context.current_key)
        if kwarg_spec is None or kwarg_spec.type != 'enum':
            return None
        if kwarg_spec.enum_values is None:
            return None

        prefix = context.current_text.lower().strip().strip('"\'')
        items = []
        for val in kwarg_spec.enum_values:
            if prefix and not val.startswith(prefix):
                continue
            items.append(CompletionItem(
                label=val,
                insert_text=val,
                kind=CompletionItemKind.EnumMember,
                detail=kwarg_spec.type,
                documentation=MarkupContent(
                    kind=MarkupKind.Markdown,
                    value=f'`{val}`',
                ),
            ))
        return items

    def _build_voice_kwarg_item(self,
                                 kwarg_spec) -> CompletionItem:
        """CompletionItem per un kwarg di voice strategy."""
        detail = kwarg_spec.type
        if kwarg_spec.min_val is not None:
            detail += f' ≥ {kwarg_spec.min_val}'
        if kwarg_spec.type == 'enum' and kwarg_spec.enum_values:
            detail = ' | '.join(kwarg_spec.enum_values[:5])
            if len(kwarg_spec.enum_values) > 5:
                detail += ' …'
        trigger = TRIGGER_SUGGEST if kwarg_spec.type == 'enum' else None
        return CompletionItem(
            label=kwarg_spec.name,
            insert_text=kwarg_spec.name + ': ',
            kind=CompletionItemKind.Field,
            detail=detail,
            documentation=MarkupContent(
                kind=MarkupKind.Markdown,
                value=f'**{kwarg_spec.name}**\n\n{kwarg_spec.description}',
            ),
            command=trigger,
        )

    def _get_strategy_for_voice_block(self, document_text: str,
                                       context: YamlContext) -> Optional[str]:
        """
        Legge il valore di 'strategy:' nel blocco dimensione corrente.
        Usato per sapere quali kwargs suggerire.
        """
        if not document_text or len(context.parent_path) < 2:
            return None

        dim_name = context.parent_path[1]
        lines = document_text.splitlines()
        cursor = context.cursor_line

        # Indentazione del blocco dimensione (es. pitch: a 6 spazi)
        dim_header_leading = (context.indent_level - 1) * 2

        # Trova l'header del blocco dimensione risalendo dal cursore
        dim_start = None
        for i in range(cursor, -1, -1):
            if i >= len(lines):
                continue
            raw = lines[i]
            stripped = raw.strip()
            leading = len(raw) - len(raw.lstrip())
            if leading == 2 and (stripped.startswith('- ') or stripped == '-'):
                break  # usciti dallo stream
            if leading == dim_header_leading and stripped.startswith(dim_name + ':'):
                dim_start = i
                break

        if dim_start is None:
            return None

        # Scansiona in avanti nel blocco cercando 'strategy:'
        strategy_leading = context.indent_level * 2
        for i in range(dim_start + 1, len(lines)):
            raw = lines[i]
            stripped = raw.strip()
            leading = len(raw) - len(raw.lstrip())
            if stripped and leading <= dim_header_leading:
                break  # usciti dal blocco
            if leading == strategy_leading:
                m = re.match(r'^strategy\s*:\s*(.+)', stripped)
                if m:
                    return m.group(1).strip().strip('"\'')

        return None

    def _extract_present_keys_in_voice_block(self, document_text: str,
                                               context: YamlContext) -> Set[str]:
        """
        Estrae le chiavi presenti nel blocco voice corrente con scope corretto.

        A differenza di _extract_present_keys, gestisce la gerarchia a due livelli
        (voices -> dimension) senza includere chiavi di blocchi fratelli.

        Per parent_path=['voices']: chiavi dentro voices: (indent 3, 6 spazi).
        Per parent_path=['voices','pitch']: chiavi dentro pitch: (indent 4, 8 spazi).
        """
        if not document_text or not context.parent_path:
            return set()

        lines = document_text.splitlines()
        cursor = context.cursor_line

        # Il blocco corrente e' l'ultimo elemento di parent_path
        block_name = context.parent_path[-1]
        # Indentazione dell'header del blocco (es. 'voices:' a 4 spazi, 'pitch:' a 6)
        header_leading = (len(context.parent_path)) * 2   # 2 per voices, 6 per pitch
        # Indentazione delle chiavi dentro il blocco
        keys_leading = (len(context.parent_path) + 1) * 2  # 6 per voices, 8 per pitch

        # 1. Trova l'inizio dello stream
        stream_start = None
        for i in range(cursor, -1, -1):
            if i >= len(lines):
                continue
            raw = lines[i]
            stripped = raw.strip()
            leading = len(raw) - len(raw.lstrip())
            if (stripped.startswith('- ') or stripped == '-') and leading == 2:
                stream_start = i
                break
        if stream_start is None:
            return set()

        # 2. Trova la fine dello stream
        stream_end = len(lines)
        for i in range(stream_start + 1, len(lines)):
            raw = lines[i]
            stripped = raw.strip()
            leading = len(raw) - len(raw.lstrip())
            if (stripped.startswith('- ') or stripped == '-') and leading == 2:
                stream_end = i
                break

        # 3. Trova l'header del blocco corrente risalendo dal cursore
        block_start = None
        for i in range(cursor, stream_start - 1, -1):
            if i >= len(lines):
                continue
            raw = lines[i]
            stripped = raw.strip()
            leading = len(raw) - len(raw.lstrip())
            if leading == header_leading and (
                stripped == block_name + ':'
                or stripped.startswith(block_name + ':')
            ):
                block_start = i + 1
                break
        if block_start is None:
            return set()

        # 4. Trova la fine del blocco
        block_end = stream_end
        for i in range(block_start, stream_end):
            if i >= len(lines):
                break
            raw = lines[i]
            stripped = raw.strip()
            if not stripped:
                continue
            leading = len(raw) - len(raw.lstrip())
            if leading <= header_leading:
                block_end = i
                break

        # 5. Raccoglie chiavi a keys_leading spazi
        present: Set[str] = set()
        for line in lines[block_start:block_end]:
            raw_leading = len(line) - len(line.lstrip())
            if raw_leading != keys_leading:
                continue
            stripped = line.strip()
            if stripped.startswith('#') or not stripped:
                continue
            m = re.match(r'^([a-zA-Z_][a-zA-Z0-9_]*)\s*:', stripped)
            if m:
                present.add(m.group(1))
        return present

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
