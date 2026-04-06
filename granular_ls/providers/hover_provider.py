# granular_ls/providers/hover_provider.py
"""
HoverProvider - Mostra documentazione quando il cursore e' su una chiave YAML.

Responsabilita':
    Dato un YamlContext, trovare il parametro corrispondente alla chiave
    sotto il cursore e restituire un Hover LSP con documentazione Markdown.
    Ritorna None se il cursore non e' su una chiave conosciuta.

Risoluzione del nome chiave:
    Il current_text del YamlContext puo' contenere:
    - La chiave locale ('duration' dentro grain:)
    - Lo yaml_path completo ('grain.duration')
    - Una chiave di root ('density')

    La risoluzione avviene in questo ordine:
    1. Cerca current_text come yaml_path completo nel bridge
    2. Se parent_path non e' vuoto, costruisce il path completo
       parent_path + '.' + current_text e cerca nel bridge

    Se nessuna strategia trova il parametro, ritorna None.
"""

from typing import Optional

from lsprotocol.types import Hover, MarkupContent, MarkupKind

# Documentazione statica per le stream context keys e le dephase keys.
# Importata anche dal CompletionProvider per coerenza.
_STREAM_CONTEXT_DOCS = {
    'stream_id':           'Identificatore univoco dello stream (stringa).',
    'onset':               'Tempo di inizio dello stream in secondi.',
    'duration':            'Durata totale dello stream in secondi.',
    'sample':              'Percorso relativo al file audio sorgente (.wav).',
    'time_mode':           "Modalita tempo degli envelope: absolute (default) | normalized.",
    'time_scale':          'Moltiplicatore globale dei tempi (default: 1.0).',
    'range_always_active': 'Se True, il range si applica anche senza dephase (default: False).',
    'distribution_mode':   None,  # generata dinamicamente da get_distribution_modes()
    'dephase':             "Randomizzazione inter-grano. Bool, float, envelope o dict per-parametro.",
    'solo':                (
        "Modalita ascolto esclusivo: quando presente su uno stream, SOLO gli stream "
        "con questo flag vengono renderizzati. Gli altri vengono ignorati dal Generator. "
        "Utile per isolare un singolo layer durante la composizione senza rimuovere "
        "gli altri dal file YAML."
    ),
    'mute':                (
        "Silenzia questo stream senza rimuoverlo dal YAML. "
        "Lo stream rimane configurato ma non viene renderizzato dal Generator. "
        "Utile per disabilitare temporaneamente un layer sonoro mantenendo "
        "la configurazione per uso futuro."
    ),
    # Chiavi del blocco pointer
    'start': (
        "**Posizione iniziale di lettura nel sample.**\n\n"
        "Valore scalare in secondi (o normalizzato se `loop_unit: normalized`).\n\n"
        "> **Non accetta envelope.** E' un valore raw processato direttamente\n"
        "> dal PointerController prima del pipeline standard.\n\n"
        "Default: `0.0` (inizio del sample).\n\n"
        "Se `loop_unit` o `time_mode` e' `normalized`, il valore e' in [0.0, 1.0]\n"
        "e viene moltiplicato per la durata del sample sorgente."
    ),
    'loop_unit': (
        "**Meta-parametro: unita di misura dei parametri loop.**\n\n"
        "Controlla come vengono interpretati `loop_start`, `loop_end`, `loop_dur` e `start`.\n\n"
        "Valori accettati:\n"
        "- `normalized`: i valori sono in \\[0.0, 1.0\\] e vengono scalati per la\n"
        "  durata del sample sorgente (`sample_dur_sec`). Comodo per definire\n"
        "  loop indipendentemente dalla lunghezza del sample.\n"
        "- `absolute` (o assente): i valori sono in **secondi assoluti**.\n\n"
        "Se `loop_unit` non e' specificato, il sistema usa `time_mode` dello stream\n"
        "come fallback.\n\n"
        "> **Non e' un parametro sintetizzabile.** Non accetta envelope o range.\n"
        "> E' un meta-parametro che modifica l'interpretazione degli altri."
    ),
}

from granular_ls.schema_bridge import SchemaBridge, ParameterInfo
from granular_ls.yaml_analyzer import YamlContext
from granular_ls.voice_strategies import (
    VOICE_DIMENSIONS,
    VOICE_TOP_LEVEL_KEYS,
    VOICES_BLOCK_DOC,
    get_strategy_spec,
    get_strategies_for_dimension,
    find_kwarg_in_dimension,
    get_top_level_doc,
)


class HoverProvider:
    """
    Produce Hover LSP per le chiavi YAML del progetto granulare.

    Costruzione:
        provider = HoverProvider(bridge)

    Uso:
        hover = provider.get_hover(yaml_context)
        # ritorna Hover oppure None
    """

    def __init__(self, bridge: SchemaBridge):
        self._bridge = bridge

    def get_hover(self, context: YamlContext) -> Optional[Hover]:
        """
        Produce un Hover per la chiave sotto il cursore.

        Args:
            context: YamlContext prodotto da YamlAnalyzer.get_context()

        Returns:
            Hover LSP se il cursore e' su una chiave conosciuta.
            None in tutti gli altri casi (valore, commento, chiave sconosciuta).
        """
        if context.context_type != 'key':
            return None

        if not context.current_text:
            return None

        # Contesto dephase: hover sulla dephase key
        if context.parent_path == ['dephase']:
            return self._build_dephase_key_hover(context.current_text)

        # Contesto voices: hover sulle chiavi del blocco voices
        if context.parent_path and context.parent_path[0] == 'voices':
            voice_hover = self._build_voice_hover(context)
            if voice_hover is not None:
                return voice_hover

        # Hover su 'voices' come chiave al livello stream
        if context.current_text == 'voices' and not context.parent_path:
            return Hover(
                contents=MarkupContent(
                    kind=MarkupKind.Markdown,
                    value=VOICES_BLOCK_DOC,
                )
            )

        # Usa la parola COMPLETA alla posizione cursore, non solo il prefisso.
        # Necessario perche' YamlAnalyzer taglia a line_up_to_cursor,
        # quindi se il cursore e' a meta' di 'density' current_text='den'.
        # Per hover vogliamo la parola intera.
        full_word = context.current_text  # fallback al prefisso

        # Prova prima come parametro del bridge
        param = self._resolve_parameter(context)
        if param is not None:
            if param.is_internal:
                return None
            return self._build_hover(param)

        # Prova come stream context key
        return self._build_stream_context_hover(full_word)

    # -------------------------------------------------------------------------
    # RISOLUZIONE PARAMETRO
    # -------------------------------------------------------------------------

    def _resolve_parameter(self,
                            context: YamlContext) -> Optional[ParameterInfo]:
        """
        Trova il ParameterInfo corrispondente al contesto del cursore.

        Nota: get_parameter() del bridge cerca per name Python (grain_duration),
        non per yaml_path (grain.duration). Usiamo _find_by_yaml_path().

        Strategia 1: current_text come yaml_path completo.
        Strategia 2: parent_path + '.' + current_text come yaml_path.
        """
        current = context.current_text

        param = self._find_by_yaml_path(current)
        if param is not None:
            return param

        if context.parent_path:
            full_path = '.'.join(context.parent_path) + '.' + current
            param = self._find_by_yaml_path(full_path)
            if param is not None:
                return param

        return None


    def _build_stream_context_hover(self,
                                     key_name: str) -> 'Optional[Hover]':
        """
        Hover per le stream context keys non in GRANULAR_PARAMETERS.
        Usa la documentazione statica da _STREAM_CONTEXT_DOCS.
        """
        if key_name == 'distribution_mode':
            modes = self._bridge.get_distribution_modes()
            modes_list = '\n'.join(f'- `{m}`' for m in modes)
            doc = (
                "Controlla come viene applicata la variazione stocastica quando un "
                "parametro ha un `mod_range` (spread). Si applica a tutti i parametri "
                "dello stream che hanno una variazione definita.\n\n"
                f"**Modalita' disponibili:**\n{modes_list}\n\n"
                "Default: `uniform`\n\n"
                "> Il sistema e' estensibile via `DistributionFactory.register()`: "
                "le modalita' mostrate qui vengono lette dinamicamente."
            )
            return Hover(
                contents=MarkupContent(kind=MarkupKind.Markdown, value=f'**{key_name}**\n\n{doc}')
            )

        doc = _STREAM_CONTEXT_DOCS.get(key_name)
        if doc is None:
            return None

        full_text = f'**{key_name}**\n\n{doc}'
        return Hover(
            contents=MarkupContent(
                kind=MarkupKind.Markdown,
                value=full_text,
            )
        )

    def _build_dephase_key_hover(self, key_name: str) -> 'Optional[Hover]':
        """
        Hover per una chiave dentro il blocco dephase:.
        Verifica che la chiave sia una dephase key valida del bridge.
        """
        valid_keys = self._bridge.get_dephase_keys()
        if key_name not in valid_keys:
            return None

        doc = (
            f"Controllo dephase per il parametro **{key_name}**.\n\n"
            f"Accetta: `false`, `true`, un valore float (0-1 o 0-100), "
            f"o un envelope `[[t, v], ...]`."
        )
        return Hover(
            contents=MarkupContent(
                kind=MarkupKind.Markdown,
                value=f'**{key_name}** (dephase)\n\n{doc}',
            )
        )

    def _find_by_yaml_path(self, yaml_path: str) -> Optional[ParameterInfo]:
        """Cerca un parametro per yaml_path, non per name Python."""
        for param in self._bridge.get_all_parameters():
            if param.yaml_path == yaml_path:
                return param
        return None

    # -------------------------------------------------------------------------
    # VOICES HOVER
    # -------------------------------------------------------------------------

    def _build_voice_hover(self, context: YamlContext) -> 'Optional[Hover]':
        """
        Hover per chiavi dentro il blocco voices.

        Casi gestiti:
          parent_path=['voices']:        chiave top-level (num_voices, pitch, ...)
          parent_path=['voices', dim]:   strategy o kwarg di una dimensione
        """
        word = context.current_text
        parent = context.parent_path

        if len(parent) == 1:
            # Dentro voices: - hover su num_voices o su una dimensione
            return self._build_voice_top_key_hover(word)

        if len(parent) == 2 and parent[1] in VOICE_DIMENSIONS:
            dim = parent[1]
            # 'strategy' come chiave
            if word == 'strategy':
                return self._build_voice_strategy_key_hover(dim)
            # Kwarg di una strategy
            return self._build_voice_kwarg_hover(dim, word)

        return None

    def _build_voice_top_key_hover(self, key: str) -> 'Optional[Hover]':
        """Hover per chiavi di primo livello dentro voices: (num_voices, pitch, ...)."""
        doc = get_top_level_doc(key)
        if doc:
            return Hover(
                contents=MarkupContent(kind=MarkupKind.Markdown, value=doc)
            )
        return None

    def _build_voice_strategy_key_hover(self, dim: str) -> 'Optional[Hover]':
        """Hover sulla chiave 'strategy' dentro un blocco dimensione."""
        strategies = get_strategies_for_dimension(dim)
        lines = [f'**strategy** — Tipo di strategy per la dimensione `{dim}`.\n']
        for name in strategies:
            spec = get_strategy_spec(dim, name)
            kwargs_str = ', '.join(f'`{k}`' for k in spec.kwargs) if spec else ''
            desc_first_line = spec.description.split('\n')[0] if spec else ''
            lines.append(f'- **`{name}`** — {desc_first_line}')
            if kwargs_str:
                lines.append(f'  kwargs: {kwargs_str}')
        return Hover(
            contents=MarkupContent(
                kind=MarkupKind.Markdown,
                value='\n'.join(lines),
            )
        )

    def _build_voice_kwarg_hover(self, dim: str,
                                  kwarg_name: str) -> 'Optional[Hover]':
        """Hover su un kwarg di voice strategy."""
        kwarg_spec = find_kwarg_in_dimension(dim, kwarg_name)
        if kwarg_spec is None:
            return None

        header = f'**{kwarg_name}** (`{dim}` strategy kwarg)\n\n'
        meta = []
        meta.append(f'Tipo: `{kwarg_spec.type}`')
        if kwarg_spec.required:
            meta.append('Richiesto: `true`')
        if kwarg_spec.min_val is not None:
            meta.append(f'Min: `{kwarg_spec.min_val}`')
        if kwarg_spec.max_val is not None:
            meta.append(f'Max: `{kwarg_spec.max_val}`')
        if kwarg_spec.enum_values:
            meta.append('Valori: ' + ', '.join(f'`{v}`' for v in kwarg_spec.enum_values))

        meta_str = ' · '.join(meta)
        full = header + meta_str + '\n\n' + kwarg_spec.description
        return Hover(
            contents=MarkupContent(kind=MarkupKind.Markdown, value=full)
        )

    # -------------------------------------------------------------------------
    # COSTRUZIONE Hover
    # -------------------------------------------------------------------------

    def _build_hover(self, param: ParameterInfo) -> Hover:
        """
        Costruisce un Hover LSP per il parametro dato.

        Il contenuto e' Markdown per sfruttare la formattazione
        nel tooltip di VSCode. Deleghiamo la generazione del testo
        al bridge che conosce gia' il formato (DRY: non duplichiamo
        la logica di documentazione).
        """
        doc_text = self._bridge.get_documentation(param)

        # Aggiungiamo un'intestazione Markdown con il nome del parametro.
        # Questo rende il tooltip immediatamente riconoscibile.
        header = f'**{param.name}**\n\n'
        full_text = header + doc_text

        return Hover(
            contents=MarkupContent(
                kind=MarkupKind.Markdown,
                value=full_text,
            )
        )
