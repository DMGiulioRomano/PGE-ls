# tests/test_yaml_analyzer.py
"""
Suite TDD - FASE RED per yaml_analyzer.py

Modulo sotto test (non ancora esistente):
    granular_ls/yaml_analyzer.py

Responsabilita' del modulo:
    1. is_pge_file(uri): decide se un file va analizzato
    2. YamlContext: Value Object che descrive il contesto del cursore
    3. YamlAnalyzer.get_context(): analizza testo + posizione cursore
       e ritorna un YamlContext

Il problema fondamentale:
    Un YAML mentre viene digitato non e' mai valido.
    Il parser deve essere TOLLERANTE: legge riga per riga,
    usa l'indentazione come segnale di gerarchia, non si
    affida mai a un parser YAML completo sul testo parziale.

Struttura YamlContext:
    context_type : 'key' | 'value' | 'unknown'
        'key'     = il cursore sta scrivendo il nome di una chiave
        'value'   = il cursore sta scrivendo il valore di una chiave
        'unknown' = non e' possibile determinare il contesto

    current_text : str
        Il testo parziale gia' scritto prima del cursore sulla riga corrente.
        Es. 'dur' se l'utente sta scrivendo 'duration'.
        Stringa vuota se il cursore e' all'inizio di una riga vuota.

    parent_path : list[str]
        La lista di chiavi antenate dalla radice fino al blocco corrente.
        Es. ['grain'] se il cursore e' dentro il blocco grain:.
        Es. ['pointer'] se il cursore e' dentro pointer:.
        Es. [] se il cursore e' al primo livello.

    indent_level : int
        Il livello di indentazione della riga corrente (0 = root).
        Calcolato come numero di spazi iniziali diviso 2.

Organizzazione:
    1.  is_pge_file - filtro nome file
    2.  YamlContext - costruzione e campi
    3.  get_context - documento vuoto
    4.  get_context - riga vuota a root level
    5.  get_context - chiave parziale a root level
    6.  get_context - chiave parziale in blocco annidato
    7.  get_context - cursore su valore (dopo i due punti)
    8.  get_context - cursore su chiave completa con due punti
    9.  get_context - blocchi annidati profondi (grain.duration)
    10. get_context - documento con errori di sintassi (tolleranza)
    11. get_context - posizione cursore fuori range
    12. Edge cases - documento con solo commenti, righe vuote
"""

import pytest
from dataclasses import fields

from granular_ls.yaml_analyzer import (
    is_pge_file,
    YamlContext,
    YamlAnalyzer,
)


# =============================================================================
# 1. is_pge_file
# =============================================================================

class TestIsPgeFile:
    """Filtro sul nome file: solo PGE_*.yaml o PGE_*.yml."""

    def test_file_pge_yaml_riconosciuto(self):
        assert is_pge_file("file:///path/to/PGE_granular.yaml") is True

    def test_file_pge_yml_riconosciuto(self):
        assert is_pge_file("file:///path/to/PGE_config.yml") is True

    def test_file_pge_senza_path_riconosciuto(self):
        assert is_pge_file("file:///PGE_test.yaml") is True

    def test_file_senza_prefisso_ignorato(self):
        assert is_pge_file("file:///path/to/pino.yaml") is False

    def test_file_prefisso_minuscolo_ignorato(self):
        """pge_ minuscolo non deve passare: case-sensitive."""
        assert is_pge_file("file:///path/to/pge_file.yaml") is False

    def test_file_prefisso_misto_ignorato(self):
        assert is_pge_file("file:///path/to/Pge_file.yaml") is False

    def test_file_non_yaml_ignorato(self):
        assert is_pge_file("file:///path/to/PGE_file.txt") is False

    def test_file_json_ignorato(self):
        assert is_pge_file("file:///path/to/PGE_file.json") is False

    def test_file_pge_sottodirectory_profonda(self):
        assert is_pge_file("file:///a/b/c/d/PGE_deep.yaml") is True

    def test_uri_senza_schema_file(self):
        """URI senza 'file://' deve comunque funzionare."""
        assert is_pge_file("PGE_direct.yaml") is True

    def test_stringa_vuota_ritorna_false(self):
        assert is_pge_file("") is False

    def test_solo_estensione_yaml_non_basta(self):
        assert is_pge_file("file:///config.yaml") is False

    def test_pge_nel_mezzo_del_nome_non_basta(self):
        """PGE deve essere all'inizio del nome file, non nel mezzo."""
        assert is_pge_file("file:///my_PGE_config.yaml") is False

    def test_pge_underscore_inizia_il_nome_file(self):
        """PGE_ deve essere esattamente l'inizio del nome file."""
        assert is_pge_file("file:///PGE_a.yaml") is True


# =============================================================================
# 2. YamlContext - Value Object
# =============================================================================

class TestYamlContext:
    """YamlContext e' un dataclass frozen con i campi attesi."""

    def test_costruzione_base(self):
        ctx = YamlContext(
            context_type='key',
            current_text='den',
            parent_path=[],
            indent_level=0,
        )
        assert ctx.context_type == 'key'
        assert ctx.current_text == 'den'
        assert ctx.parent_path == []
        assert ctx.indent_level == 0

    def test_e_frozen(self):
        from dataclasses import FrozenInstanceError
        ctx = YamlContext(
            context_type='key',
            current_text='',
            parent_path=[],
            indent_level=0,
        )
        with pytest.raises(FrozenInstanceError):
            ctx.context_type = 'value'

    def test_campi_attesi(self):
        expected = {'context_type', 'current_text', 'parent_path', 
                    'indent_level', 'in_stream_element', 'current_key', 'cursor_line'}
        actual = {f.name for f in fields(YamlContext)}
        assert expected == actual

    def test_context_type_validi(self):
        """I tre tipi validi sono key, value, unknown."""
        for ct in ('key', 'value', 'unknown'):
            ctx = YamlContext(context_type=ct, current_text='',
                             parent_path=[], indent_level=0)
            assert ctx.context_type == ct

    def test_parent_path_e_lista(self):
        ctx = YamlContext(
            context_type='key',
            current_text='',
            parent_path=['grain'],
            indent_level=1,
        )
        assert isinstance(ctx.parent_path, list)
        assert ctx.parent_path == ['grain']

    def test_parent_path_annidato(self):
        ctx = YamlContext(
            context_type='key',
            current_text='',
            parent_path=['streams', 'pointer'],
            indent_level=2,
        )
        assert ctx.parent_path == ['streams', 'pointer']


# =============================================================================
# 3. YamlAnalyzer - documento vuoto
# =============================================================================

class TestGetContextDocumentoVuoto:

    def test_documento_vuoto_ritorna_context_unknown(self):
        ctx = YamlAnalyzer.get_context("", line=0, character=0)
        assert isinstance(ctx, YamlContext)

    def test_documento_vuoto_context_type_key(self):
        """In un documento vuoto, l'utente puo' solo scrivere una chiave."""
        ctx = YamlAnalyzer.get_context("", line=0, character=0)
        assert ctx.context_type in ('key', 'unknown')

    def test_documento_vuoto_parent_path_vuoto(self):
        ctx = YamlAnalyzer.get_context("", line=0, character=0)
        assert ctx.parent_path == []

    def test_documento_vuoto_indent_level_zero(self):
        ctx = YamlAnalyzer.get_context("", line=0, character=0)
        assert ctx.indent_level == 0


# =============================================================================
# 4. get_context - riga vuota a root level
# =============================================================================

class TestGetContextRigaVuotaRoot:
    """
    Documento con alcune chiavi, cursore su una riga vuota al root level.
    L'utente sta per scrivere una nuova chiave di primo livello.
    """

    YAML = "density: 100\n\n"

    def test_riga_vuota_root_context_type_key(self):
        ctx = YamlAnalyzer.get_context(self.YAML, line=1, character=0)
        assert ctx.context_type == 'key'

    def test_riga_vuota_root_current_text_vuoto(self):
        ctx = YamlAnalyzer.get_context(self.YAML, line=1, character=0)
        assert ctx.current_text == ''

    def test_riga_vuota_root_parent_path_vuoto(self):
        ctx = YamlAnalyzer.get_context(self.YAML, line=1, character=0)
        assert ctx.parent_path == []

    def test_riga_vuota_root_indent_level_zero(self):
        ctx = YamlAnalyzer.get_context(self.YAML, line=1, character=0)
        assert ctx.indent_level == 0


# =============================================================================
# 5. get_context - chiave parziale a root level
# =============================================================================

class TestGetContextChiaveParziale:
    """
    L'utente sta scrivendo 'den' e si aspetta di vedere 'density'.
    Il cursore e' sulla riga con testo parziale, dopo l'ultimo carattere.
    """

    def test_chiave_parziale_context_type_key(self):
        yaml = "den"
        ctx = YamlAnalyzer.get_context(yaml, line=0, character=3)
        assert ctx.context_type == 'key'

    def test_chiave_parziale_current_text_corretto(self):
        yaml = "den"
        ctx = YamlAnalyzer.get_context(yaml, line=0, character=3)
        assert ctx.current_text == 'den'

    def test_chiave_parziale_parent_path_vuoto(self):
        yaml = "den"
        ctx = YamlAnalyzer.get_context(yaml, line=0, character=3)
        assert ctx.parent_path == []

    def test_chiave_parziale_indent_level_zero(self):
        yaml = "den"
        ctx = YamlAnalyzer.get_context(yaml, line=0, character=3)
        assert ctx.indent_level == 0

    def test_chiave_vuota_all_inizio_riga(self):
        """Cursore all'inizio di una riga con solo spazi."""
        yaml = "density: 100\n"
        ctx = YamlAnalyzer.get_context(yaml, line=1, character=0)
        assert ctx.current_text == ''

    def test_chiave_parziale_seconda_riga(self):
        yaml = "density: 100\ngr"
        ctx = YamlAnalyzer.get_context(yaml, line=1, character=2)
        assert ctx.context_type == 'key'
        assert ctx.current_text == 'gr'


# =============================================================================
# 6. get_context - chiave parziale in blocco annidato
# =============================================================================

class TestGetContextChiaveInBloccoAnnidato:
    """
    L'utente e' dentro un blocco 'grain:' e sta scrivendo 'dur'.
    Il parent_path deve essere ['grain'], indent_level 1.
    """

    YAML_GRAIN = "grain:\n  dur"

    def test_chiave_annidato_context_type_key(self):
        ctx = YamlAnalyzer.get_context(self.YAML_GRAIN, line=1, character=5)
        assert ctx.context_type == 'key'

    def test_chiave_annidato_current_text(self):
        ctx = YamlAnalyzer.get_context(self.YAML_GRAIN, line=1, character=5)
        assert ctx.current_text == 'dur'

    def test_chiave_annidato_parent_path(self):
        ctx = YamlAnalyzer.get_context(self.YAML_GRAIN, line=1, character=5)
        assert ctx.parent_path == ['grain']

    def test_chiave_annidato_indent_level(self):
        ctx = YamlAnalyzer.get_context(self.YAML_GRAIN, line=1, character=5)
        assert ctx.indent_level == 1

    def test_blocco_pointer_parent_path(self):
        yaml = "pointer:\n  spe"
        ctx = YamlAnalyzer.get_context(yaml, line=1, character=5)
        assert ctx.parent_path == ['pointer']

    def test_blocco_pitch_parent_path(self):
        yaml = "pitch:\n  sem"
        ctx = YamlAnalyzer.get_context(yaml, line=1, character=5)
        assert ctx.parent_path == ['pitch']

    def test_riga_vuota_dentro_blocco(self):
        """Riga vuota indentata dentro 'grain:' -> parent_path = ['grain']."""
        yaml = "grain:\n  duration: 0.05\n  "
        ctx = YamlAnalyzer.get_context(yaml, line=2, character=2)
        assert ctx.parent_path == ['grain']
        assert ctx.context_type == 'key'
        assert ctx.current_text == ''


# =============================================================================
# 7. get_context - cursore su valore
# =============================================================================

class TestGetContextValore:
    """
    L'utente ha scritto 'density: ' e il cursore e' dopo il due punti.
    context_type deve essere 'value'.
    """

    def test_dopo_due_punti_spazio_context_type_value(self):
        yaml = "density: "
        ctx = YamlAnalyzer.get_context(yaml, line=0, character=9)
        assert ctx.context_type == 'value'

    def test_valore_parziale_context_type_value(self):
        yaml = "density: 10"
        ctx = YamlAnalyzer.get_context(yaml, line=0, character=11)
        assert ctx.context_type == 'value'

    def test_valore_current_text_e_testo_dopo_due_punti(self):
        yaml = "density: 10"
        ctx = YamlAnalyzer.get_context(yaml, line=0, character=11)
        assert ctx.current_text == '10'

    def test_valore_current_text_vuoto_subito_dopo_due_punti(self):
        yaml = "density: "
        ctx = YamlAnalyzer.get_context(yaml, line=0, character=9)
        assert ctx.current_text == ''

    def test_valore_annidato_context_type_value(self):
        yaml = "grain:\n  duration: 0.0"
        ctx = YamlAnalyzer.get_context(yaml, line=1, character=13)
        assert ctx.context_type == 'value'

    def test_valore_annidato_parent_path(self):
        yaml = "grain:\n  duration: 0.0"
        ctx = YamlAnalyzer.get_context(yaml, line=1, character=13)
        assert ctx.parent_path == ['grain']


# =============================================================================
# 8. get_context - chiave completa con due punti
# =============================================================================

class TestGetContextChiaveCompleta:
    """
    L'utente ha appena scritto 'density:' senza spazio dopo.
    Siamo ancora in context 'key' perche' non c'e' ancora un valore.
    """

    def test_chiave_completa_senza_spazio_context_key(self):
        yaml = "density:"
        ctx = YamlAnalyzer.get_context(yaml, line=0, character=8)
        assert ctx.context_type in ('key', 'value')

    def test_chiave_con_due_punti_e_spazio_context_value(self):
        yaml = "density: "
        ctx = YamlAnalyzer.get_context(yaml, line=0, character=9)
        assert ctx.context_type == 'value'


# =============================================================================
# 9. get_context - blocchi annidati profondi
# =============================================================================

class TestGetContextAnnidamentoProfondo:
    """
    La struttura YAML reale del progetto ha annidamento a due livelli:
    streams -> stream_id, grain, pointer, pitch, density.
    grain -> duration, envelope, reverse.
    pointer -> speed, start, jitter.
    """

    def test_grain_duration_parent_path(self):
        yaml = "grain:\n  duration: "
        ctx = YamlAnalyzer.get_context(yaml, line=1, character=12)
        assert ctx.parent_path == ['grain']
        assert ctx.context_type == 'value'

    def test_pointer_speed_parent_path(self):
        yaml = "pointer:\n  speed: "
        ctx = YamlAnalyzer.get_context(yaml, line=1, character=9)
        assert ctx.parent_path == ['pointer']
        assert ctx.context_type == 'value'

    def test_rientro_al_livello_superiore(self):
        """
        Dopo un blocco annidato, se l'indentazione torna a zero,
        il parent_path deve tornare vuoto.
        """
        yaml = "grain:\n  duration: 0.05\nden"
        ctx = YamlAnalyzer.get_context(yaml, line=2, character=3)
        assert ctx.parent_path == []
        assert ctx.current_text == 'den'

    def test_parent_path_con_tre_livelli(self):
        """
        Struttura con tre livelli di indentazione.
        """
        yaml = "streams:\n  grain:\n    dur"
        ctx = YamlAnalyzer.get_context(yaml, line=2, character=7)
        assert 'grain' in ctx.parent_path
        assert ctx.indent_level == 2


# =============================================================================
# 10. get_context - tolleranza agli errori di sintassi
# =============================================================================

class TestGetContextTolleranzaErrori:
    """
    Il parser deve sopravvivere a YAML malformato.
    Non deve mai lanciare eccezioni, deve restituire
    il miglior contesto possibile o 'unknown'.
    """

    def test_yaml_senza_due_punti_non_solleva(self):
        yaml = "density 100"
        ctx = YamlAnalyzer.get_context(yaml, line=0, character=7)
        assert isinstance(ctx, YamlContext)

    def test_indentazione_inconsistente_non_solleva(self):
        yaml = "grain:\n   duration: 0.05\n  envelope: hanning"
        ctx = YamlAnalyzer.get_context(yaml, line=2, character=10)
        assert isinstance(ctx, YamlContext)

    def test_caratteri_speciali_non_sollevano(self):
        yaml = "density: {broken: yaml"
        ctx = YamlAnalyzer.get_context(yaml, line=0, character=10)
        assert isinstance(ctx, YamlContext)

    def test_riga_con_solo_spazi_non_solleva(self):
        yaml = "density: 100\n   \n"
        ctx = YamlAnalyzer.get_context(yaml, line=1, character=2)
        assert isinstance(ctx, YamlContext)

    def test_yaml_con_tab_non_solleva(self):
        """Tab invece di spazi: YAML non valido ma non deve esplodere."""
        yaml = "grain:\n\tduration: 0.05"
        ctx = YamlAnalyzer.get_context(yaml, line=1, character=10)
        assert isinstance(ctx, YamlContext)

    def test_documento_con_solo_commenti_non_solleva(self):
        yaml = "# questo e' un commento\n# altro commento\n"
        ctx = YamlAnalyzer.get_context(yaml, line=2, character=0)
        assert isinstance(ctx, YamlContext)

    def test_valori_multilinea_non_sollevano(self):
        yaml = "description: |\n  riga uno\n  riga due\nden"
        ctx = YamlAnalyzer.get_context(yaml, line=3, character=3)
        assert isinstance(ctx, YamlContext)


# =============================================================================
# 11. get_context - posizione cursore fuori range
# =============================================================================

class TestGetContextPosizioneOutOfRange:
    """
    Posizioni del cursore fuori dal documento non devono sollevare.
    """

    def test_riga_fuori_range_non_solleva(self):
        yaml = "density: 100\n"
        ctx = YamlAnalyzer.get_context(yaml, line=99, character=0)
        assert isinstance(ctx, YamlContext)

    def test_character_fuori_range_non_solleva(self):
        yaml = "density: 100"
        ctx = YamlAnalyzer.get_context(yaml, line=0, character=999)
        assert isinstance(ctx, YamlContext)

    def test_riga_negativa_non_solleva(self):
        yaml = "density: 100"
        ctx = YamlAnalyzer.get_context(yaml, line=-1, character=0)
        assert isinstance(ctx, YamlContext)


# =============================================================================
# 12. Edge cases - commenti, righe vuote, liste
# =============================================================================

class TestEdgeCases:

    def test_riga_commento_context_unknown(self):
        """Una riga che inizia con # e' un commento, non una chiave."""
        yaml = "# commento"
        ctx = YamlAnalyzer.get_context(yaml, line=0, character=5)
        assert ctx.context_type == 'unknown'

    def test_cursore_dopo_commento_inline(self):
        """Cursore su una riga con valore e commento inline."""
        yaml = "density: 100  # grani al secondo"
        ctx = YamlAnalyzer.get_context(yaml, line=0, character=11)
        assert ctx.context_type == 'value'

    def test_riga_lista_non_e_chiave(self):
        """Una voce di lista (inizia con -) non e' una chiave parametro."""
        yaml = "streams:\n  - stream_id: test"
        ctx = YamlAnalyzer.get_context(yaml, line=1, character=4)
        assert isinstance(ctx, YamlContext)

    def test_documento_con_solo_newline(self):
        yaml = "\n\n\n"
        ctx = YamlAnalyzer.get_context(yaml, line=1, character=0)
        assert ctx.parent_path == []
        assert ctx.indent_level == 0

    def test_get_context_ritorna_sempre_yaml_context(self):
        """Invariante fondamentale: get_context ritorna SEMPRE YamlContext."""
        casi = [
            ("", 0, 0),
            ("grain:\n  dur", 1, 5),
            ("density: 100", 0, 11),
            ("{ invalid", 0, 5),
            ("\t\t", 0, 1),
        ]
        for text, line, char in casi:
            result = YamlAnalyzer.get_context(text, line, char)
            assert isinstance(result, YamlContext), (
                f"get_context({text!r}, {line}, {char}) non ha ritornato YamlContext"
            )


# =============================================================================
# MODIFICA B - Contesto lista streams
# =============================================================================

class TestGetContextListaStreams:
    """
    La struttura YAML reale ha gli stream in una lista:

        streams:
          - stream_id: "x"    <- indent 4, primo param dopo il trattino
            onset: 0.0        <- indent 4, parametro diretto dello stream
            grain:            <- indent 4, blocco figlio
              duration: 0.05  <- indent 6, parametro annidato

    Il parser deve:
    - Riconoscere '- ' come marcatore di nuovo stream
    - NON propagare 'streams' nel parent_path dei parametri figli
    - Trattare i parametri dentro ogni elemento lista come se fossero
      a root level (parent_path=[]) rispetto al loro stream
    - Riconoscere blocchi annidati dentro lo stream (grain, pointer, pitch)
    """

    YAML_STREAM_SEMPLICE = "streams:\n  - stream_id: \"test\"\n    onset: 0.0\n"
    YAML_CON_GRAIN = "streams:\n  - stream_id: \"s1\"\n    onset: 0.0\n    grain:\n      duration: 0.05\n"

    def test_parametro_diretto_stream_non_ha_streams_nel_parent_path(self):
        """
        'onset' dentro streams[0] deve avere parent_path=[]
        non parent_path=['streams'].
        """
        yaml = "streams:\n  - onset: 0.0\n"
        # Cursore su 'onset' alla riga 1, dopo il trattino e spazi
        ctx = YamlAnalyzer.get_context(yaml, line=1, character=6)
        assert 'streams' not in ctx.parent_path

    def test_blocco_grain_dentro_stream_parent_path_e_grain(self):
        """
        'duration' dentro grain: dentro uno stream deve avere
        parent_path=['grain'], non parent_path=['streams', 'grain'].
        """
        yaml = "streams:\n  - stream_id: s1\n    grain:\n      dur"
        ctx = YamlAnalyzer.get_context(yaml, line=3, character=9)
        assert ctx.parent_path == ['grain']
        assert 'streams' not in ctx.parent_path

    def test_parametro_stream_e_key_context(self):
        """Un parametro diretto dello stream e' in context 'key'."""
        yaml = "streams:\n  - ons"
        ctx = YamlAnalyzer.get_context(yaml, line=1, character=7)
        assert ctx.context_type == 'key'

    def test_parametro_stream_current_text_corretto(self):
        """current_text contiene il testo dopo il trattino e spazi."""
        yaml = "streams:\n  - ons"
        ctx = YamlAnalyzer.get_context(yaml, line=1, character=7)
        assert ctx.current_text == 'ons'

    def test_riga_trattino_sola_e_stream_start(self):
        """
        Una riga con solo '  - ' (trattino senza parametri)
        indica l'inizio di un nuovo elemento stream.
        context_type deve essere 'stream_start'.
        """
        yaml = "streams:\n  - "
        ctx = YamlAnalyzer.get_context(yaml, line=1, character=4)
        assert ctx.context_type == 'stream_start'

    def test_stream_start_parent_path_vuoto(self):
        yaml = "streams:\n  - "
        ctx = YamlAnalyzer.get_context(yaml, line=1, character=4)
        assert ctx.parent_path == []

    def test_secondo_stream_non_influenzato_dal_primo(self):
        """
        I parametri del secondo stream non devono avere parent_path
        contaminato dai blocchi del primo stream.
        """
        yaml = (
            "streams:\n"
            "  - stream_id: s1\n"
            "    grain:\n"
            "      duration: 0.05\n"
            "  - ons"
        )
        ctx = YamlAnalyzer.get_context(yaml, line=4, character=7)
        assert ctx.parent_path == []
        assert ctx.current_text == 'ons'

    def test_blocco_pointer_dentro_stream_parent_path(self):
        """pointer: dentro uno stream genera parent_path=['pointer']."""
        yaml = "streams:\n  - stream_id: s1\n    pointer:\n      spe"
        ctx = YamlAnalyzer.get_context(yaml, line=3, character=9)
        assert ctx.parent_path == ['pointer']
        assert 'streams' not in ctx.parent_path

    def test_valore_dentro_stream_e_value_context(self):
        """Un valore dentro uno stream e' context_type='value'."""
        yaml = "streams:\n  - onset: 0.0"
        ctx = YamlAnalyzer.get_context(yaml, line=1, character=14)
        assert ctx.context_type == 'value'

    def test_riga_trattino_con_testo_e_key(self):
        """
        Una riga '  - stream_id: x' con cursore su 'stream_id'
        deve essere context_type='key' con current_text='stream_id'.
        """
        yaml = "streams:\n  - stream_id: x"
        ctx = YamlAnalyzer.get_context(yaml, line=1, character=14)
        assert ctx.context_type in ('key', 'value')


class TestGetContextStreamStartType:
    """
    Verifica il nuovo context_type='stream_start'.
    Viene prodotto quando il cursore e' su una riga con solo '- '
    (il trattino marcatore di nuovo elemento lista).
    """

    def test_stream_start_su_trattino_solo(self):
        yaml = "streams:\n  - "
        ctx = YamlAnalyzer.get_context(yaml, line=1, character=4)
        assert ctx.context_type == 'stream_start'

    def test_stream_start_current_text_vuoto(self):
        yaml = "streams:\n  - "
        ctx = YamlAnalyzer.get_context(yaml, line=1, character=4)
        assert ctx.current_text == ''

    def test_stream_start_indent_level_uno(self):
        """Il trattino e' a indent 1 (2 spazi)."""
        yaml = "streams:\n  - "
        ctx = YamlAnalyzer.get_context(yaml, line=1, character=4)
        assert ctx.indent_level == 1

    def test_non_stream_start_se_ha_contenuto(self):
        """'  - onset: ' non e' stream_start perche' ha una chiave."""
        yaml = "streams:\n  - onset: "
        ctx = YamlAnalyzer.get_context(yaml, line=1, character=12)
        assert ctx.context_type != 'stream_start'


# =============================================================================
# current_key su YamlContext
# =============================================================================

class TestYamlContextCurrentKey:
    """
    current_key e' la chiave della riga corrente quando context_type == 'value'.
    E' vuoto in tutti gli altri contesti.
    """

    def test_current_key_vuoto_in_context_key(self):
        """In contesto 'key' current_key e' sempre vuoto."""
        yaml = "streams:\n  - density: "
        # cursore prima dei due punti -> contesto key
        ctx = YamlAnalyzer.get_context(yaml, line=1, character=6)
        if ctx.context_type == 'key':
            assert ctx.current_key == ''

    def test_current_key_popolato_in_context_value(self):
        """In contesto 'value' current_key contiene la chiave della riga."""
        yaml = "streams:\n  - density: 100"
        ctx = YamlAnalyzer.get_context(yaml, line=1, character=16)
        assert ctx.context_type == 'value'
        assert ctx.current_key == 'density'

    def test_current_key_con_parametro_volume(self):
        yaml = "streams:\n  - stream_id: s1\n    volume: -6.0"
        ctx = YamlAnalyzer.get_context(yaml, line=2, character=14)
        assert ctx.context_type == 'value'
        assert ctx.current_key == 'volume'

    def test_current_key_vuoto_se_stream_start(self):
        yaml = "streams:\n  - "
        ctx = YamlAnalyzer.get_context(yaml, line=1, character=4)
        assert ctx.context_type == 'stream_start'
        assert ctx.current_key == ''

    def test_current_key_vuoto_su_riga_senza_valore(self):
        """Riga tipo 'grain:' (blocco) non ha current_key."""
        yaml = "streams:\n  - stream_id: s1\n    grain:\n      "
        ctx = YamlAnalyzer.get_context(yaml, line=2, character=9)
        assert ctx.current_key == ''

    def test_current_key_su_riga_con_trattino(self):
        """Riga con '- chiave: valore' deve estrarre la chiave."""
        yaml = "streams:\n  - onset: 0.0"
        ctx = YamlAnalyzer.get_context(yaml, line=1, character=14)
        assert ctx.context_type == 'value'
        assert ctx.current_key == 'onset'

    def test_current_key_campo_in_dataclass(self):
        """current_key deve essere un campo del dataclass."""
        from dataclasses import fields
        field_names = {f.name for f in fields(YamlContext)}
        assert 'current_key' in field_names


# =============================================================================
# get_stream_context_at_line - estrae onset e time_mode dello stream corrente
# =============================================================================

class TestGetStreamContextAtLine:
    """
    get_stream_context_at_line(text, line) risale dal cursore al marcatore
    '- ' piu' vicino e ne estrae i valori di onset e time_mode.

    Ritorna un dict con chiavi 'duration' (float) e 'time_mode' (str).
    Valori di default se non trovati: duration=0.0, time_mode='absolute'.
    """

    YAML_BASE = (
        "streams:\n"
        "  - stream_id: s1\n"
        "    onset: 3.5\n"
        "    duration: 10.0\n"
        "    density: \n"
    )

    YAML_NORMALIZED = (
        "streams:\n"
        "  - stream_id: s1\n"
        "    onset: 2.0\n"
        "    time_mode: normalized\n"
        "    density: \n"
    )

    YAML_TWO_STREAMS = (
        "streams:\n"
        "  - stream_id: s1\n"
        "    onset: 1.0\n"
        "    density: 100\n"
        "  - stream_id: s2\n"
        "    onset: 5.5\n"
        "    time_mode: normalized\n"
        "    density: \n"
    )

    def test_ritorna_dict(self):
        result = YamlAnalyzer.get_stream_context_at_line(self.YAML_BASE, 4)
        assert isinstance(result, dict)

    def test_chiavi_presenti(self):
        result = YamlAnalyzer.get_stream_context_at_line(self.YAML_BASE, 4)
        assert 'duration' in result
        assert 'time_mode' in result

    def test_duration_corretta(self):
        result = YamlAnalyzer.get_stream_context_at_line(self.YAML_BASE, 4)
        assert result["duration"] == 10.0

    def test_time_mode_default_absolute(self):
        """Senza time_mode esplicito il default e' 'absolute'."""
        result = YamlAnalyzer.get_stream_context_at_line(self.YAML_BASE, 4)
        assert result['time_mode'] == 'absolute'

    def test_time_mode_normalized(self):
        result = YamlAnalyzer.get_stream_context_at_line(
            self.YAML_NORMALIZED, 4)
        assert result['time_mode'] == 'normalized'

    def test_duration_default_zero_se_assente(self):
        yaml = "streams:\n  - stream_id: s1\n    density: \n"
        result = YamlAnalyzer.get_stream_context_at_line(yaml, 2)
        assert result['duration'] == 0.0

    def test_secondo_stream_non_contamina_il_primo(self):
        """Il secondo stream ha onset=5.5 e time_mode=normalized."""
        result = YamlAnalyzer.get_stream_context_at_line(
            self.YAML_TWO_STREAMS, 7)
        assert result['duration'] == 0.0  # no duration esplicita nel secondo stream
        assert result['time_mode'] == 'normalized'

    def test_primo_stream_non_influenzato_dal_secondo(self):
        """Il primo stream ha onset=1.0 e nessun time_mode."""
        result = YamlAnalyzer.get_stream_context_at_line(
            self.YAML_TWO_STREAMS, 3)
        assert result['duration'] == 0.0  # no duration esplicita nel primo stream
        assert result['time_mode'] == 'absolute'

    def test_linea_fuori_stream_ritorna_default(self):
        """A root level (fuori da streams) ritorna valori default."""
        result = YamlAnalyzer.get_stream_context_at_line(self.YAML_BASE, 0)
        assert result['duration'] == 0.0
        assert result['time_mode'] == 'absolute'

    def test_duration_float_parsing(self):
        """duration viene parsata come float anche se scritta come intero."""
        yaml = "streams:\n  - duration: 7\n    density: \n"
        result = YamlAnalyzer.get_stream_context_at_line(yaml, 2)
        assert result['duration'] == 7.0
        assert isinstance(result['duration'], float)
