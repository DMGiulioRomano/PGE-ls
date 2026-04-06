# granular_ls/schema_bridge.py
"""
SchemaBridge - Facade tra i moduli del progetto granulare e il Language Server.

Design Patterns applicati:
  - Facade:           nasconde la complessita' di parameter_schema + parameter_definitions
  - Value Object:     ParameterInfo e' frozen, identificato dai valori
  - Multiple Constructors via Class Methods: tre factory per tre contesti d'uso

Responsabilita':
  Leggere ParameterSpec e ParameterBounds dal progetto granulare e
  trasformarli in ParameterInfo, struttura interna stabile e indipendente
  dai moduli sorgente.
"""

import json
import sys
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional


# =============================================================================
# VALUE OBJECT
# =============================================================================

@dataclass(frozen=True)
class ParameterInfo:
    """
    Fusione di ParameterSpec + ParameterBounds in un unico Value Object.

    I provider (completion, hover, diagnostic) non devono mai fare due
    lookup separati: un oggetto contiene tutti i dati di un parametro.

    is_internal viene calcolato automaticamente da SchemaBridge:
    True se yaml_path inizia con '_' (convenzione del progetto granulare
    per i parametri non esposti nel YAML).
    """
    name: str
    yaml_path: str
    default: Any
    is_smart: bool
    exclusive_group: Optional[str]
    group_priority: int
    min_val: Optional[float]
    max_val: Optional[float]
    min_range: Optional[float]
    max_range: Optional[float]
    variation_mode: str
    is_internal: bool


# =============================================================================
# FACADE
# =============================================================================

class SchemaBridge:
    """
    Facade che espone i dati schema del progetto granulare al Language Server.

    Costruzione:
        SchemaBridge(raw_data)          - da dict normalizzato (test)
        SchemaBridge.from_python_path() - importando i moduli Python reali
        SchemaBridge.from_snapshot()    - da file JSON pre-generato

    Il costruttore principale riceve raw_data nella forma:
        {
            'specs': [ { 'name': ..., 'yaml_path': ..., ... }, ... ],
            'bounds': { 'nome_param': { 'min_val': ..., 'max_val': ..., ... } }
        }
    """

    def __init__(self, raw_data: dict):
        # KeyError deliberato se mancano le chiavi obbligatorie.
        # Fail fast: meglio un errore esplicito in costruzione che un
        # AttributeError silenzioso piu' avanti.
        specs = raw_data['specs']
        bounds = raw_data['bounds']


        # Lista statica fallback delle stream context keys.
        # Usata quando from_python_path non riesce a importare StreamContext/StreamConfig.
        _STATIC_STREAM_CONTEXT_KEYS = [
            'stream_id', 'onset', 'duration', 'sample',   # StreamContext
            'dephase', 'range_always_active', 'time_mode', # StreamConfig
            'time_scale', 'distribution_mode',
            'solo', 'mute',                                # Generator flags
        ]
        self._stream_context_keys: List[str] = (
            raw_data.get('stream_context_keys') or _STATIC_STREAM_CONTEXT_KEYS
        )

        # Modalita' di distribuzione disponibili.
        # Lette da DistributionFactory in from_python_path; fallback statico.
        _STATIC_DISTRIBUTION_MODES = ['uniform', 'gaussian']
        self._distribution_modes: List[str] = (
            raw_data.get('distribution_modes') or _STATIC_DISTRIBUTION_MODES
        )


        # Conserviamo gli spec raw per get_dephase_keys()
        self._raw_specs = specs

        # Costruiamo il registry interno: name -> ParameterInfo
        # Il registry e' un dict per accesso O(1) in get_parameter().
        self._params: Dict[str, ParameterInfo] = {}

        # Espandiamo gli specs con i parametri range_path automaticamente.
        # Se un spec ha range_path='volume_range', creiamo un ParameterInfo
        # aggiuntivo per quel parametro range con bounds da min_range/max_range.
        expanded_specs = list(specs)
        existing_names = {s['name'] for s in specs}
        for spec in specs:
            rp = spec.get('range_path')
            if not rp or rp.startswith('_'):
                continue
            range_name = spec['name'] + '_range'
            if range_name in existing_names:
                continue
            # Bounds del range: presi da min_range/max_range del parametro padre
            pb = bounds.get(spec['name'])
            range_bounds = None
            if pb and (pb.get('min_range', 0) != 0 or pb.get('max_range', 0) != 0):
                range_bounds = {
                    'min_val': pb.get('min_range', 0.0),
                    'max_val': pb.get('max_range', 0.0),
                    'min_range': 0.0, 'max_range': 0.0,
                    'default_jitter': 0.0, 'variation_mode': 'additive',
                }
            expanded_specs.append({
                'name': range_name,
                'yaml_path': rp,
                'default': 0.0,
                'is_smart': True,
                'exclusive_group': None,
                'group_priority': 99,
                'range_path': None,
                'dephase_key': None,
            })
            if range_bounds:
                bounds = dict(bounds)
                bounds[range_name] = range_bounds
            existing_names.add(range_name)

        for spec in expanded_specs:
            name = spec['name']
            yaml_path = spec['yaml_path']
            is_internal = yaml_path.startswith('_')
            b = bounds.get(name)

            self._params[name] = ParameterInfo(
                name=name,
                yaml_path=yaml_path,
                default=spec.get('default'),
                is_smart=spec.get('is_smart', True),
                exclusive_group=spec.get('exclusive_group'),
                group_priority=spec.get('group_priority', 99),
                min_val=b['min_val'] if b else None,
                max_val=b['max_val'] if b else None,
                min_range=b.get('min_range') if b else None,
                max_range=b.get('max_range') if b else None,
                variation_mode=b.get('variation_mode', 'additive') if b else 'additive',
                is_internal=is_internal,
            )

        # Conserviamo il dizionario bounds completo (include parametri come
        # 'scatter' e 'num_voices' che hanno bounds in GRANULAR_PARAMETERS
        # ma nessun ParameterSpec in ALL_SCHEMAS). Serve per get_raw_bounds().
        self._raw_bounds: dict = bounds

        # solo e mute sono mutuamente esclusivi: uno stream non puo' essere
        # sia in ascolto esclusivo che silenziato simultaneamente.
        for flag_name, priority in (('solo', 1), ('mute', 2)):
            if flag_name not in self._params:
                self._params[flag_name] = ParameterInfo(
                    name=flag_name,
                    yaml_path=flag_name,
                    default=None,
                    is_smart=True,
                    exclusive_group='stream_mode',
                    group_priority=priority,
                    min_val=None,
                    max_val=None,
                    min_range=None,
                    max_range=None,
                    variation_mode='additive',
                    is_internal=False,
                )

    # -------------------------------------------------------------------------
    # QUERY API
    # -------------------------------------------------------------------------

    def get_all_parameters(self) -> List[ParameterInfo]:
        """Tutti i parametri, inclusi quelli interni e is_smart=False."""
        return list(self._params.values())

    def get_completion_parameters(self) -> List[ParameterInfo]:
        """
        Solo i parametri candidati per l'autocompletion.

        Esclusi:
          - is_smart=False: valori raw, non configurabili dall'utente
          - is_internal=True: yaml_path che inizia con '_', non appaiono nel YAML
        """
        return [
            p for p in self._params.values()
            if p.is_smart and not p.is_internal
        ]

    def get_parameter(self, name: str) -> Optional[ParameterInfo]:
        """Accesso singolo per nome. Ritorna None se il nome non esiste."""
        return self._params.get(name)

    def get_exclusive_groups(self) -> Dict[str, List[ParameterInfo]]:
        """
        Mappa gruppo -> lista di ParameterInfo ordinata per group_priority.

        Usata dal DiagnosticProvider per rilevare violazioni di
        mutua esclusivita' (es. fill_factor e density nello stesso YAML).
        """
        groups: Dict[str, List[ParameterInfo]] = {}

        for p in self._params.values():
            if p.exclusive_group is None:
                continue
            if p.exclusive_group not in groups:
                groups[p.exclusive_group] = []
            groups[p.exclusive_group].append(p)

        # Ordiniamo per group_priority: il provider puo' usare l'ordine
        # per sapere quale parametro ha precedenza in caso di conflitto.
        for group_name in groups:
            groups[group_name].sort(key=lambda p: p.group_priority)

        return groups

    def get_yaml_keys(self) -> List[str]:
        """
        Lista dei yaml_path dei parametri candidati per la completion.

        Sono i valori che appaiono come chiavi nel YAML dell'utente,
        non i nomi Python interni (name). La distinzione e' importante:
        ad esempio 'grain_duration' ha yaml_path 'grain.duration'.

        Nessun duplicato garantito.
        """
        seen = set()
        keys = []
        for p in self.get_completion_parameters():
            if p.yaml_path not in seen:
                seen.add(p.yaml_path)
                keys.append(p.yaml_path)
        return keys


    def get_stream_context_keys(self) -> List[str]:
        """
        Ritorna le chiavi di contesto valide per ogni elemento stream.

        Include campi di StreamContext, StreamConfig (esclusi interni),
        piu' i flag speciali del Generator:
          solo  -> mette lo stream in ascolto esclusivo: solo gli stream
                   con questo flag vengono renderizzati, gli altri ignorati.
                   Utile per isolare un singolo stream durante la composizione.
          mute  -> silenzia lo stream senza rimuoverlo dal YAML.
                   Utile per disabilitare temporaneamente un layer sonoro.
        """
        return list(self._stream_context_keys)

    def get_block_keys(self) -> List[str]:
        """
        Ritorna i prefissi unici degli yaml_path annidati.
        Da 'grain.duration' ricava 'grain'. Root-level e interni ignorati.
        """
        seen = set()
        keys = []
        for p in self.get_all_parameters():
            if p.is_internal:
                continue
            if '.' in p.yaml_path:
                prefix = p.yaml_path.split('.')[0]
                if prefix not in seen:
                    seen.add(prefix)
                    keys.append(prefix)
        return keys

    def get_dephase_keys(self) -> List[str]:
        """
        Ritorna le chiavi valide del blocco dephase:, derivate dai
        valori unici di dephase_key negli ParameterSpec degli schema.
        Non hardcoded: si aggiorna automaticamente con nuovi parametri.
        """
        seen = set()
        keys = []
        for spec in self._raw_specs:
            dk = spec.get('dephase_key')
            if dk and dk not in seen:
                seen.add(dk)
                keys.append(dk)
        return keys

    def get_distribution_modes(self) -> List[str]:
        """Lista delle modalita' di distribuzione disponibili (es. uniform, gaussian)."""
        return list(self._distribution_modes)

    def get_raw_bounds(self, param_name: str) -> Optional[dict]:
        """
        Restituisce i bounds raw per un parametro, anche se non ha un ParameterSpec.

        Utile per parametri come 'scatter' e 'num_voices' che vivono in
        GRANULAR_PARAMETERS ma vengono parsati fuori da ALL_SCHEMAS
        (es. direttamente in _init_voice_manager di stream.py).

        Returns:
            Dict con min_val, max_val, variation_mode, ecc. oppure None.
        """
        return self._raw_bounds.get(param_name)

    def get_documentation(self, param: ParameterInfo) -> str:
        """
        Stringa di documentazione leggibile per il provider Hover.

        Formato scelto per essere leggibile nell'hover tooltip di VSCode,
        che interpreta Markdown nella stringa documentation di LSP.
        """
        lines = []

        if param.min_val is not None and param.max_val is not None:
            lines.append(f"Range: [{param.min_val}, {param.max_val}]")

        if param.variation_mode:
            lines.append(f"Variation mode: {param.variation_mode}")

        if param.exclusive_group:
            lines.append(f"Exclusive group: {param.exclusive_group}")

        if param.default is not None:
            lines.append(f"Default: {param.default}")

        if not lines:
            lines.append(f"Parameter: {param.name}")

        return "\n".join(lines)

    # -------------------------------------------------------------------------
    # SERIALIZZAZIONE
    # -------------------------------------------------------------------------

    def generate_snapshot(self) -> str:
        """
        Serializza il bridge in JSON.

        Lo snapshot e' leggibile da from_snapshot() e permette al Language
        Server di funzionare senza importare i moduli Python del progetto
        granulare (utile in ambienti CI o distribuzioni offline).

        ParameterInfo contiene 'default: Any' che puo' essere None.
        json.dumps gestisce None come null, e' compatibile.
        """
        # extra_bounds: parametri con bounds ma senza ParameterSpec (es. scatter,
        # num_voices). Necessari per get_raw_bounds() in modalita' snapshot.
        extra_bounds = {
            name: b
            for name, b in self._raw_bounds.items()
            if name not in self._params
        }
        data = {
            'parameters': [asdict(p) for p in self._params.values()],
            'distribution_modes': self._distribution_modes,
            'extra_bounds': extra_bounds,
        }
        return json.dumps(data, indent=2)

    # -------------------------------------------------------------------------
    # FACTORY METHODS
    # -------------------------------------------------------------------------

    @classmethod
    def from_python_path(cls, src_path: str) -> 'SchemaBridge':
        """
        Costruisce il bridge importando i moduli Python del progetto granulare.

        Aggiunge src_path a sys.path temporaneamente, importa
        parameter_schema e parameter_definitions, legge i dati,
        costruisce raw_data e chiama __init__.

        Il path deve essere la directory 'src' del progetto granulare,
        quella che contiene il package 'parameters/'.

        Raises:
            FileNotFoundError: se src_path non esiste
            ModuleNotFoundError: se i moduli non sono trovati
        """
        import os
        if not os.path.exists(src_path):
            raise FileNotFoundError(f"Path non trovato: {src_path}")

        # Aggiungiamo src_path a sys.path per permettere l'import.
        # Lo inseriamo in posizione 0 per avere precedenza su altri moduli
        # con lo stesso nome che potrebbero gia' essere in sys.path.
        if src_path not in sys.path:
            sys.path.insert(0, src_path)

        try:
            # Import dinamico: i moduli potrebbero non essere disponibili
            # nell'ambiente del Language Server, solo nel progetto granulare.
            from parameters import parameter_schema, parameter_definitions

            # Schemi che vivono in un sotto-blocco YAML omonimo.
            # Es. PITCH_PARAMETER_SCHEMA ha yaml_path='ratio' ma nel YAML
            # sta dentro pitch: { ratio: ... }, quindi il path completo e' 'pitch.ratio'.
            SCHEMA_BLOCK_PREFIXES = {
                'pointer': 'pointer',
                'pitch': 'pitch',
            }

            specs = []
            for schema_name, schema_list in parameter_schema.ALL_SCHEMAS.items():
                block_prefix = SCHEMA_BLOCK_PREFIXES.get(schema_name, '')
                for spec in schema_list:
                    yaml_path = spec.yaml_path
                    # Aggiungi prefisso solo se:
                    # - lo schema ha un prefisso definito
                    # - lo yaml_path non e' gia' prefissato (no '.')
                    # - non e' un path interno (no '_')
                    if (block_prefix
                            and not yaml_path.startswith('_')
                            and '.' not in yaml_path):
                        yaml_path = block_prefix + '.' + yaml_path
                    specs.append({
                        'name': spec.name,
                        'yaml_path': yaml_path,
                        'default': spec.default,
                        'is_smart': spec.is_smart,
                        'exclusive_group': spec.exclusive_group,
                        'group_priority': spec.group_priority,
                        'range_path': spec.range_path,
                        'dephase_key': spec.dephase_key,
                    })

            bounds = {}
            for name, b in parameter_definitions.GRANULAR_PARAMETERS.items():
                bounds[name] = {
                    'min_val': b.min_val,
                    'max_val': b.max_val,
                    'min_range': b.min_range,
                    'max_range': b.max_range,
                    'default_jitter': b.default_jitter,
                    'variation_mode': b.variation_mode,
                }

            # Carica stream_context_keys dai dataclass reali
            stream_context_keys = []
            try:
                from dataclasses import fields as dc_fields
                from core.stream_config import StreamContext, StreamConfig

                # Campi di StreamContext (escluso sample_dur_sec)
                for f in dc_fields(StreamContext):
                    if f.name != 'sample_dur_sec':
                        stream_context_keys.append(f.name)

                # Campi di StreamConfig (escluso context)
                for f in dc_fields(StreamConfig):
                    if f.name != 'context':
                        stream_context_keys.append(f.name)

                # Flag speciali del Generator
                stream_context_keys.extend(['solo', 'mute'])

            except ImportError:
                # StreamContext/StreamConfig non disponibili: usa lista vuota
                # Il costruttore usera' la lista statica di fallback
                stream_context_keys = []

            # Aggiungi parametri range_path.
            # Ogni ParameterSpec con range_path non-None definisce un parametro
            # aggiuntivo (es. volume_range, pan_range, grain.duration_range)
            # i cui bounds sono min_range/max_range del parametro principale.
            for schema_list in parameter_schema.ALL_SCHEMAS.values():
                for spec in schema_list:
                    if not spec.range_path:
                        continue
                    # Costruisce lo yaml_path del range param
                    # range_path puo' essere 'volume_range' o 'grain.duration_range'
                    range_yaml_path = spec.range_path
                    # Se lo schema ha un prefisso di blocco, aggiungilo
                    # (es. POINTER_PARAMETER_SCHEMA -> 'pointer.offset_range')
                    block_prefix = SCHEMA_BLOCK_PREFIXES.get(
                        [k for k, v in parameter_schema.ALL_SCHEMAS.items()
                         if spec in v][0], ''
                    )
                    if (block_prefix
                            and not range_yaml_path.startswith('_')
                            and '.' not in range_yaml_path):
                        range_yaml_path = block_prefix + '.' + range_yaml_path

                    range_name = spec.name + '_range'
                    # Evita duplicati
                    if any(s['name'] == range_name for s in specs):
                        continue

                    specs.append({
                        'name': range_name,
                        'yaml_path': range_yaml_path,
                        'default': 0.0,
                        'is_smart': True,
                        'exclusive_group': None,
                        'group_priority': 99,
                        'range_path': None,
                        'dephase_key': None,
                    })

                    # Bounds: usa min_range/max_range del parametro padre
                    parent_name = spec.name
                    if parent_name in parameter_definitions.GRANULAR_PARAMETERS:
                        pb = parameter_definitions.GRANULAR_PARAMETERS[parent_name]
                        bounds[range_name] = {
                            'min_val': pb.min_range,
                            'max_val': pb.max_range,
                            'min_range': 0.0,
                            'max_range': 0.0,
                            'default_jitter': 0.0,
                            'variation_mode': 'additive',
                        }

            raw_data = {
                'specs': specs,
                'bounds': bounds,
            }
            if stream_context_keys:
                raw_data['stream_context_keys'] = stream_context_keys

            # Carica le modalita' di distribuzione da DistributionFactory
            try:
                from parameters.distribution_factory import DistributionFactory
                raw_data['distribution_modes'] = list(DistributionFactory._registry.keys())
            except Exception:
                pass  # Usa il fallback statico nel costruttore

            return cls(raw_data)

        finally:
            # Puliamo sys.path dopo l'import per non inquinare l'ambiente
            # del Language Server con path del progetto dell'utente.
            if src_path in sys.path:
                sys.path.remove(src_path)

    @classmethod
    def from_snapshot(cls, path: str) -> 'SchemaBridge':
        """
        Costruisce il bridge da un file JSON generato da generate_snapshot().

        Raises:
            FileNotFoundError: se il file non esiste
            json.JSONDecodeError: se il JSON e' malformato
        """
        import os
        if not os.path.exists(path):
            raise FileNotFoundError(f"Snapshot non trovato: {path}")

        with open(path, 'r') as f:
            data = json.loads(f.read())

        # Ricostruiamo raw_data dal formato snapshot.
        # Lo snapshot salva ParameterInfo gia' fuso: dobbiamo separare
        # di nuovo in specs e bounds per passare al costruttore standard.
        specs = []
        bounds = {}

        for p in data['parameters']:
            specs.append({
                'name': p['name'],
                'yaml_path': p['yaml_path'],
                'default': p['default'],
                'is_smart': p['is_smart'],
                'exclusive_group': p['exclusive_group'],
                'group_priority': p['group_priority'],
                'range_path': None,
                'dephase_key': None,
            })
            if p['min_val'] is not None:
                bounds[p['name']] = {
                    'min_val': p['min_val'],
                    'max_val': p['max_val'],
                    'min_range': p['min_range'],
                    'max_range': p['max_range'],
                    'default_jitter': 0.0,
                    'variation_mode': p['variation_mode'],
                }

        # Ripristina extra_bounds (parametri senza ParameterSpec come scatter,
        # num_voices) in modo che get_raw_bounds() funzioni in modalita' snapshot.
        for name, b in data.get('extra_bounds', {}).items():
            if name not in bounds:
                bounds[name] = b

        raw = {'specs': specs, 'bounds': bounds}
        if 'distribution_modes' in data:
            raw['distribution_modes'] = data['distribution_modes']
        return cls(raw)
