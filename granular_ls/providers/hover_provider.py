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
