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

import re
from typing import Optional, Tuple

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

# Parametri del blocco pointer che dipendono da loop_unit / time_mode
_POINTER_UNIT_PARAMS = {'start', 'loop_start', 'loop_end', 'loop_dur'}

# Documentazione per le chiavi blocco di primo livello (pointer, pitch, grain, dephase, voices)
_BLOCK_KEY_DOCS = {
    'pointer': (
        "**pointer** — Testa di lettura nel sample\n\n"
        "Controlla come il motore naviga nel file audio sorgente durante la sintesi granulare.\n\n"
        "**Parametri:**\n"
        "- `start` — Posizione iniziale di lettura (default: `0.0`)\n"
        "- `speed_ratio` — Velocità di scorrimento della testa (default: `1.0`)\n"
        "- `loop_start` — Inizio del loop\n"
        "- `loop_end` — Fine del loop *(esclusivo con `loop_dur`)*\n"
        "- `loop_dur` — Durata del loop — ha priorità su `loop_end`\n"
        "- `loop_unit` — Unità dei parametri loop: `absolute` \\| `normalized`\n\n"
        "> Tutti i parametri accettano envelope `[[t, v], ...]` tranne\n"
        "> `loop_unit` (meta-parametro) e `start` (valore raw)."
    ),
    'pitch': (
        "**pitch** — Intonazione dei grani\n\n"
        "Controlla l'altezza percepita dei grani sintetizzati.\n\n"
        "**Parametri (mutuamente esclusivi):**\n"
        "- `ratio` — Rapporto di pitch (1.0 = originale, 2.0 = ottava sopra)\n"
        "- `semitones` — Trasposizione in semitoni\n\n"
        "**Variazione stocastica:**\n"
        "- `range` — Ampiezza della deviazione casuale (condivisa tra `ratio` e `semitones`)"
    ),
    'grain': (
        "**grain** — Parametri dei grani\n\n"
        "Controlla le caratteristiche dei singoli grani audio generati.\n\n"
        "**Parametri:**\n"
        "- `duration` — Durata del grano in secondi (default: `0.05`)\n"
        "- `envelope` — Forma dell'inviluppo del grano (default: `hanning`)\n"
        "- `reverse` — Probabilità di inversione del grano (0.0–1.0)"
    ),
    'dephase': (
        "**dephase** — Randomizzazione inter-grano\n\n"
        "Aggiunge dispersione casuale a parametri individuali tra i grani.\n\n"
        "**Valori accettati:**\n"
        "- `false` — nessuna randomizzazione\n"
        "- `true` — randomizzazione globale con valori di default\n"
        "- `float` — probabilità uniforme per tutti i parametri (0.0–1.0)\n"
        "- `envelope [[t, v], ...]` — modulazione nel tempo\n"
        "- `dict` — controllo per-parametro (chiave = nome parametro)\n\n"
        "**Esempio dict:**\n"
        "```yaml\n"
        "dephase:\n"
        "  duration: 0.3\n"
        "  pitch: 0.1\n"
        "  volume: 0.5\n"
        "```\n\n"
        "Le chiavi accettate nel dict corrispondono ai `dephase_key` dei parametri sintetizzabili."
    ),
}


def _get_effective_unit_mode(document_text: str,
                              cursor_line: int) -> Tuple[str, str]:
    """
    Determina l'unita' di misura effettiva per i parametri del blocco pointer.

    Logica (speculare al motore):
        loop_unit = params.get('loop_unit') or config.time_mode

    1. Cerca 'loop_unit' nel blocco pointer: dello stesso stream.
    2. Se assente, cerca 'time_mode' nello stream padre.
    3. Se nessuno dei due e' presente, default 'absolute'.

    Returns:
        (mode, source) dove:
            mode   : 'normalized' | 'absolute'
            source : 'loop_unit' | 'time_mode' | 'default'
    """
    if not document_text:
        return ('absolute', 'default')

    lines = document_text.split('\n')

    # --- Trova i confini dello stream corrente ---
    stream_start = None
    stream_end = len(lines)
    for i in range(cursor_line, -1, -1):
        raw = lines[i] if i < len(lines) else ''
        stripped = raw.strip()
        leading = len(raw) - len(raw.lstrip())
        if (stripped.startswith('- ') or stripped == '-') and leading == 2:
            stream_start = i
            break
    if stream_start is None:
        return ('absolute', 'default')
    for i in range(stream_start + 1, len(lines)):
        raw = lines[i]
        stripped = raw.strip()
        leading = len(raw) - len(raw.lstrip())
        if (stripped.startswith('- ') or stripped == '-') and leading == 2:
            stream_end = i
            break

    # --- Cerca loop_unit dentro il blocco pointer: (a 6 spazi) ---
    pointer_start = None
    for i in range(stream_start, stream_end):
        raw = lines[i]
        stripped = raw.strip()
        leading = len(raw) - len(raw.lstrip())
        if leading == 4 and (stripped == 'pointer:' or stripped.startswith('pointer:')):
            pointer_start = i
            break

    if pointer_start is not None:
        pointer_end = stream_end
        for i in range(pointer_start + 1, stream_end):
            raw = lines[i]
            if not raw.strip():
                continue
            if (len(raw) - len(raw.lstrip())) <= 4:
                pointer_end = i
                break
        for i in range(pointer_start + 1, pointer_end):
            raw = lines[i]
            stripped = raw.strip()
            if len(raw) - len(raw.lstrip()) != 6:
                continue
            m = re.match(r'^loop_unit\s*:\s*(.+)', stripped)
            if m:
                val = m.group(1).strip().strip('"\'')
                mode = 'normalized' if val == 'normalized' else 'absolute'
                return (mode, 'loop_unit')

    # --- Fallback: cerca time_mode nello stream ---
    for i in range(stream_start, stream_end):
        raw = lines[i]
        stripped = raw.strip()
        if stripped.startswith('- '):
            stripped = stripped[2:].strip()
        leading = len(raw) - len(raw.lstrip())
        if leading > 4:
            continue
        m = re.match(r'^time_mode\s*:\s*(.+)', stripped)
        if m:
            val = m.group(1).strip().strip('"\'')
            mode = 'normalized' if val == 'normalized' else 'absolute'
            return (mode, 'time_mode')

    return ('absolute', 'default')


def _get_stream_duration(document_text: str, cursor_line: int) -> Optional[float]:
    """
    Estrae il valore di 'duration' dello stream corrente dal documento.

    Usa la stessa logica di boundary detection di _get_effective_unit_mode.
    Restituisce None se 'duration' non e' presente o non e' un numero valido.
    """
    if not document_text:
        return None

    lines = document_text.split('\n')

    # Trova i confini dello stream corrente
    stream_start = None
    stream_end = len(lines)
    for i in range(cursor_line, -1, -1):
        raw = lines[i] if i < len(lines) else ''
        stripped = raw.strip()
        leading = len(raw) - len(raw.lstrip())
        if (stripped.startswith('- ') or stripped == '-') and leading == 2:
            stream_start = i
            break
    if stream_start is None:
        return None
    for i in range(stream_start + 1, len(lines)):
        raw = lines[i]
        stripped = raw.strip()
        leading = len(raw) - len(raw.lstrip())
        if (stripped.startswith('- ') or stripped == '-') and leading == 2:
            stream_end = i
            break

    # Cerca 'duration' a indentazione 4 (o inline dopo '- ')
    for i in range(stream_start, stream_end):
        raw = lines[i]
        stripped = raw.strip()
        if stripped.startswith('- '):
            stripped = stripped[2:].strip()
        leading = len(raw) - len(raw.lstrip())
        if leading > 4:
            continue
        m = re.match(r'^duration\s*:\s*([0-9]*\.?[0-9]+)', stripped)
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                return None

    return None


def _unit_mode_note(mode: str, source: str, duration: Optional[float] = None) -> str:
    """Costruisce la nota Markdown sull'unita' effettiva da appendere all'hover."""
    if mode == 'normalized':
        source_label = {
            'loop_unit': '`loop_unit: normalized`',
            'time_mode': '`time_mode: normalized` (fallback)',
        }.get(source, 'modalita\' normalized')
        note = (
            '\n\n---\n'
            f'**Unità effettiva: `normalized`** — da {source_label}\n\n'
            '> Il valore è in \\[0.0, 1.0\\] e viene scalato per la durata '
            'del sample sorgente (`sample_dur_sec`).'
        )
        if duration is not None:
            note += (
                f'\n\n**Limite dinamico:** `[0.0, 1.0]` '
                f'→ `[0.0 s, {duration} s]` '
                f'(da `duration: {duration}` dello stream)'
            )
        return note
    else:
        source_label = {
            'loop_unit': '`loop_unit: absolute`',
            'time_mode': '`time_mode: absolute`',
            'default':   'default (nessun `loop_unit` o `time_mode` specificato)',
        }.get(source, 'modalita\' absolute')
        note = (
            '\n\n---\n'
            f'**Unità effettiva: `absolute`** — {source_label}\n\n'
            '> Il valore è in **secondi assoluti**.'
        )
        if duration is not None:
            note += (
                f'\n\n**Limite dinamico:** `[0.0 s, {duration} s]` '
                f'(da `duration: {duration}` dello stream)'
            )
        return note


from granular_ls.schema_bridge import SchemaBridge, ParameterInfo
from granular_ls.yaml_analyzer import YamlContext
from granular_ls.voice_strategies import (
    VOICE_DIMENSIONS,
    VOICE_TOP_LEVEL_KEYS,
    VOICE_ENVELOPE_KEYS,
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

    def get_hover(self, context: YamlContext,
                  document_text: str = '') -> Optional[Hover]:
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
            voice_hover = self._build_voice_hover(context, document_text)
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

        # Hover su chiavi blocco (pointer, pitch, grain, dephase) al livello stream
        if not context.parent_path and context.current_text in _BLOCK_KEY_DOCS:
            return Hover(
                contents=MarkupContent(
                    kind=MarkupKind.Markdown,
                    value=_BLOCK_KEY_DOCS[context.current_text],
                )
            )

        # Hover su grain.envelope (chiave o valore-finestratura)
        if context.parent_path == ['grain']:
            grain_hover = self._build_grain_envelope_hover(context.current_text)
            if grain_hover is not None:
                return grain_hover

        # Usa la parola COMPLETA alla posizione cursore, non solo il prefisso.
        # Necessario perche' YamlAnalyzer taglia a line_up_to_cursor,
        # quindi se il cursore e' a meta' di 'density' current_text='den'.
        # Per hover vogliamo la parola intera.
        full_word = context.current_text  # fallback al prefisso

        # Determina se siamo su un parametro pointer sensibile all'unità
        is_pointer_param = (
            context.parent_path == ['pointer']
            and full_word in _POINTER_UNIT_PARAMS
        )

        # Prova prima come parametro del bridge
        param = self._resolve_parameter(context)
        if param is not None:
            if param.is_internal:
                return None
            hover = self._build_hover(param)
            if is_pointer_param:
                hover = self._append_unit_note(hover, document_text,
                                               context.cursor_line)
            return hover

        # Prova come stream context key
        hover = self._build_stream_context_hover(full_word)
        if hover is not None and is_pointer_param:
            hover = self._append_unit_note(hover, document_text,
                                           context.cursor_line)
        return hover

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

    def _build_grain_envelope_hover(self, word: str) -> 'Optional[Hover]':
        """
        Hover per grain.envelope: chiave o nome di finestratura.

        - 'envelope' → lista di tutte le finestrature disponibili
        - nome valido (es. 'hanning') → descrizione della finestratura
        """
        _ENVELOPE_DOC = {
            'hamming':         ('GEN20', 'Finestra Hamming. Buona soppressione dei lobi laterali, leggero effetto di arrotondamento.'),
            'hanning':         ('GEN20', 'Finestra Hanning (von Hann). Ottimo compromesso tra risoluzione e soppressione spettrale.'),
            'bartlett':        ('GEN20', 'Finestra Bartlett (triangolare). Forma semplice, roll-off morbido.'),
            'blackman':        ('GEN20', 'Finestra Blackman. Alta soppressione dei lobi laterali, minor risoluzione.'),
            'blackman_harris': ('GEN20', 'Finestra Blackman-Harris. Soppressione molto alta, indicata per analisi spettrale.'),
            'gaussian':        ('GEN20', 'Finestra Gaussiana. Forma a campana, buona sia nel dominio del tempo che della frequenza.'),
            'kaiser':          ('GEN20', 'Finestra Kaiser-Bessel. Parametro β=6, bilanciamento tra mainlobe e sidelobe.'),
            'rectangle':       ('GEN20', 'Finestra rettangolare (Dirichlet). Nessun windowing: grano con attacco e rilascio netti.'),
            'sinc':            ('GEN20', 'Finestra Sinc. Risposta impulsiva di un filtro passa-basso ideale.'),
            'half_sine':       ('GEN09', 'Mezzo seno. Attacco e rilascio a coseno, forma morbida e naturale.'),
            'expodec':         ('GEN16', 'Decadimento esponenziale. Attacco rapido, coda lunga — simile a una percussione.'),
            'expodec_strong':  ('GEN16', 'Decadimento esponenziale forte (strength=10). Coda più ripida di expodec.'),
            'exporise':        ('GEN16', 'Salita esponenziale. Attacco morbido, corpo pieno — effetto swell.'),
            'exporise_strong': ('GEN16', 'Salita esponenziale forte (strength=10). Salita più rapida di exporise.'),
            'rexpodec':        ('GEN16', 'Decadimento esponenziale inverso. Variante speculare di expodec.'),
            'rexporise':       ('GEN16', 'Salita esponenziale inversa. Variante speculare di exporise.'),
        }

        valid_names = self._bridge.get_grain_envelope_names()

        if word == 'envelope':
            families = {
                'GEN20 (simmetrico)': ['hamming', 'hanning', 'bartlett', 'blackman',
                                        'blackman_harris', 'gaussian', 'kaiser',
                                        'rectangle', 'sinc'],
                'GEN09': ['half_sine'],
                'GEN16 (asimmetrico)': ['expodec', 'expodec_strong', 'exporise',
                                         'exporise_strong', 'rexpodec', 'rexporise'],
            }
            lines = ['**envelope** — Finestratura del grano (windowing function).\n']
            lines.append('Accetta un nome singolo, una lista `[a, b, ...]`, o `all`.\n')
            for family, members in families.items():
                available = [m for m in members if m in valid_names]
                if available:
                    lines.append(f'**{family}:** ' + ', '.join(f'`{m}`' for m in available))
            return Hover(contents=MarkupContent(kind=MarkupKind.Markdown,
                                                value='\n'.join(lines)))

        if word in valid_names and word in _ENVELOPE_DOC:
            gen, desc = _ENVELOPE_DOC[word]
            text = f'**{word}** (`{gen}`)\n\n{desc}'
            return Hover(contents=MarkupContent(kind=MarkupKind.Markdown, value=text))

        return None

    def _find_by_yaml_path(self, yaml_path: str) -> Optional[ParameterInfo]:
        """Cerca un parametro per yaml_path, non per name Python."""
        for param in self._bridge.get_all_parameters():
            if param.yaml_path == yaml_path:
                return param
        return None

    # -------------------------------------------------------------------------
    # VOICES HOVER
    # -------------------------------------------------------------------------

    def _build_voice_hover(self, context: YamlContext,
                            document_text: str = '') -> 'Optional[Hover]':
        """
        Hover per chiavi dentro il blocco voices.

        Casi gestiti:
          parent_path=['voices']:        chiave top-level (num_voices, pitch, ...)
                                         oppure parola dentro un inline dict dimension
          parent_path=['voices', dim]:   strategy o kwarg di una dimensione (block style)
        """
        word = context.current_text
        parent = context.parent_path

        if len(parent) == 1:
            # Primo tentativo: parola è una chiave top-level di voices
            hover = self._build_voice_top_key_hover(word)
            if hover is not None:
                return hover
            # Fallback: potrebbe essere dentro un inline dict, es. "pan: {strategy: additive}"
            return self._build_voice_inline_dict_hover(word, document_text,
                                                       context.cursor_line)

        if len(parent) == 2 and parent[1] in VOICE_DIMENSIONS:
            dim = parent[1]
            # 'strategy' come chiave
            if word == 'strategy':
                return self._build_voice_strategy_key_hover(dim)
            # Nome di strategy valido
            if word in get_strategies_for_dimension(dim):
                return self._build_voice_strategy_value_hover(dim, word)
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

    def _build_voice_strategy_value_hover(self, dim: str,
                                           strategy_name: str) -> 'Optional[Hover]':
        """Hover sul valore di strategy (es. 'additive' in 'strategy: additive')."""
        spec = get_strategy_spec(dim, strategy_name)
        if spec is None:
            return None
        kwargs_lines = []
        for kwarg_name, kwarg_spec in spec.kwargs.items():
            meta = f'`{kwarg_spec.type}`'
            if kwarg_spec.required:
                meta += ' · richiesto'
            if kwarg_spec.enum_values:
                meta += ' · valori: ' + ', '.join(f'`{v}`' for v in kwarg_spec.enum_values)
            elif kwarg_spec.min_val is not None or kwarg_spec.max_val is not None:
                bounds = f'[{kwarg_spec.min_val}, {kwarg_spec.max_val}]'
                meta += f' · range: `{bounds}`'
            kwargs_lines.append(f'- **`{kwarg_name}`** ({meta}) — {kwarg_spec.description}')
        header = f'**{strategy_name}** (strategy `{dim}`)\n\n{spec.description}'
        if kwargs_lines:
            header += '\n\n**kwargs:**\n' + '\n'.join(kwargs_lines)
        return Hover(
            contents=MarkupContent(kind=MarkupKind.Markdown, value=header)
        )

    def _build_voice_inline_dict_hover(self, word: str, document_text: str,
                                        cursor_line: int) -> 'Optional[Hover]':
        """
        Hover per parole dentro un inline dict voices, es.:
          pan: {strategy: additive, spread: 0}
        YamlAnalyzer non parsifica gli inline dict, quindi usiamo
        il testo della riga per ricostruire il contesto.
        """
        lines = document_text.split('\n')
        if cursor_line >= len(lines):
            return None
        raw_line = lines[cursor_line]
        m = re.match(r'^\s*([a-zA-Z_]\w*)\s*:\s*\{(.+)', raw_line)
        if not m:
            return None
        dim = m.group(1)
        if dim not in VOICE_DIMENSIONS:
            return None
        # Parola è il nome della dimensione stessa
        if word == dim:
            return self._build_voice_top_key_hover(dim)
        # Parola è 'strategy' (la chiave)
        if word == 'strategy':
            return self._build_voice_strategy_key_hover(dim)
        # Parola è un nome di strategy valido
        valid_strategies = get_strategies_for_dimension(dim)
        if word in valid_strategies:
            return self._build_voice_strategy_value_hover(dim, word)
        # Parola è un kwarg
        return self._build_voice_kwarg_hover(dim, word)

    # -------------------------------------------------------------------------
    # COSTRUZIONE Hover
    # -------------------------------------------------------------------------

    def _append_unit_note(self, hover: Hover, document_text: str,
                          cursor_line: int) -> Hover:
        """Aggiunge la nota sull'unità effettiva a un Hover esistente."""
        mode, source = _get_effective_unit_mode(document_text, cursor_line)
        duration = _get_stream_duration(document_text, cursor_line)
        note = _unit_mode_note(mode, source, duration)
        old_value = hover.contents.value if hover.contents else ''
        return Hover(
            contents=MarkupContent(
                kind=MarkupKind.Markdown,
                value=old_value + note,
            )
        )

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
