"""
tests/test_read_direction.py

Suite per `granular_ls/read_direction.py` — il mirror della semantica di
`grain.read_direction` (PGE #207, PR #208).

Cosa copre, e perché il bridge non basta:

Il bridge espone la chiave con bounds `[-1, 1]`, che letti come intervallo
continuo accettano `0` e `0.5` — entrambi rifiutati dal motore. Il dominio è
l'insieme `{-1, +1}` e lo schema non ha modo di esprimerlo; lo stesso vale per
l'interpolazione `step`, imposta. Queste sono le due regole di semantica.

Attorno a loro il motore ha aggiunto guard sulle macro-forme (arità dei
gruppi, cicli del formato compatto, percentuali del pattern, distribuzione
temporale): condizioni che altrove risalgono come `ValueError` nudi o non
risalgono affatto, e che su questa chiave sono errori tipizzati e quindi
staticamente decidibili dal testo YAML.

Il confine: `end_time <= time_offset` resta al motore, perché dipende
dall'offset accumulato dagli elementi che precedono il ciclo in una lista
mista. Qui si verifica solo il segno.

La parità con il motore vero è in `tests/test_pge_parity.py`; qui i valori
sono ricopiati a mano, come nel resto della suite.
"""

import pytest

from granular_ls.read_direction import (
    READ_DIRECTION_PATH,
    READ_DIRECTION_VALUES,
    REQUIRED_INTERP,
    TIME_DISTRIBUTION_NAMES,
    ReadDirectionIssue,
    check_read_direction,
)


def accetta(raw) -> bool:
    """True se il motore accetterebbe questo valore."""
    return check_read_direction(raw) is None


def hint(raw) -> str:
    issue = check_read_direction(raw)
    assert issue is not None, f"atteso un rifiuto per {raw!r}"
    return issue.hint


# =============================================================================
# 1. Costanti della superficie
# =============================================================================

class TestCostanti:

    def test_path_e_quello_dello_yaml(self):
        assert READ_DIRECTION_PATH == 'grain.read_direction'

    def test_dominio_a_due_valori(self):
        assert READ_DIRECTION_VALUES == (-1.0, 1.0)

    def test_interp_imposto(self):
        assert REQUIRED_INTERP == 'step'

    def test_nomi_distribuzione_con_alias(self):
        # Alias compresi: il motore li accetta entrambi.
        for nome in ('linear', 'exponential', 'exp', 'logarithmic', 'log',
                     'geometric', 'geo', 'power'):
            assert nome in TIME_DISTRIBUTION_NAMES


# =============================================================================
# 2. Scalare — il dominio è un insieme, non un intervallo
# =============================================================================

class TestScalare:

    @pytest.mark.parametrize('valore', [1, -1, 1.0, -1.0])
    def test_i_due_versi_sono_accettati(self, valore):
        assert accetta(valore)

    def test_zero_rifiutato_pur_essendo_nei_bounds(self):
        # Il caso che il check generico sui bounds [-1, 1] lascerebbe passare.
        assert not accetta(0)

    @pytest.mark.parametrize('valore', [0.5, -0.5, 0.3, 2, -2, 100])
    def test_valori_intermedi_e_fuori_scala_rifiutati(self, valore):
        assert not accetta(valore)

    def test_intero_enorme_non_alza_overflow(self):
        # Il confronto è sull'appartenenza e non converte: float() su un int
        # più grande di ogni double alzerebbe OverflowError proprio dentro
        # chi deve produrre una diagnostica.
        assert not accetta(10 ** 400)

    @pytest.mark.parametrize('valore', [True, False])
    def test_booleani_rifiutati(self, valore):
        # `true` non è `+1` da nessuna parte.
        assert not accetta(valore)

    def test_chiave_vuota_e_un_errore(self):
        # A differenza di grain.reverse, dove la chiave vuota è la sintassi.
        assert not accetta(None)

    @pytest.mark.parametrize('valore', ['avanti', 'x', {}, ()])
    def test_forme_non_riconosciute_rifiutate(self, valore):
        assert not accetta(valore)


# =============================================================================
# 3. Envelope — lista di breakpoint
# =============================================================================

class TestEnvelopeLista:

    def test_spezzata_di_versi_accettata(self):
        assert accetta([[0, 1], [12, -1], [20, 1]])

    def test_un_solo_breakpoint_accettato(self):
        assert accetta([[0, 1]])

    def test_valore_intermedio_in_un_breakpoint_rifiutato(self):
        assert not accetta([[0, 1], [12, 0.5]])

    def test_lista_vuota_rifiutata(self):
        assert not accetta([])

    def test_tag_per_punto_step_accettato(self):
        # Ridondante ma valido.
        assert accetta([[0, 1, 'step'], [12, -1]])

    def test_tag_per_punto_diverso_da_step_rifiutato(self):
        assert not accetta([[0, 1, 'linear'], [12, -1]])

    def test_forma_dict_per_punto_accettata(self):
        assert accetta([{'t': 0, 'v': 1}, {'t': 5, 'v': -1}])

    def test_forma_dict_per_punto_con_tempo_non_numerico_rifiutata(self):
        assert not accetta([{'t': 'x', 'v': 1}, {'t': 5, 'v': -1}])

    def test_forma_dict_per_punto_con_interp_rifiutata(self):
        assert not accetta([{'t': 0, 'v': 1, 'type': 'linear'}])


# =============================================================================
# 4. Envelope — dict {points, type}
# =============================================================================

class TestEnvelopeDict:

    def test_points_senza_type_accettato(self):
        assert accetta({'points': [[0, 1], [12, -1]]})

    def test_type_step_esplicito_accettato(self):
        assert accetta({'type': 'step', 'points': [[0, 1], [12, -1]]})

    def test_type_linear_rifiutato(self):
        assert not accetta({'type': 'linear', 'points': [[0, 1], [12, -1]]})

    def test_dict_senza_points_rifiutato(self):
        assert not accetta({'type': 'step'})

    def test_altre_chiavi_del_dict_non_disturbano(self):
        # time_unit sopravvive alla normalizzazione del motore.
        assert accetta({'points': [[0, 1], [12, -1]], 'time_unit': 'normalized'})

    def test_la_grammatica_dei_due_ingressi_e_la_stessa(self):
        # Quello che è accettato come lista nuda è accettato dentro `points`:
        # il motore costruisce entrambe le forme allo stesso modo.
        compatto = [[[0, 1], [50, -1]], 2.0, 2]
        assert accetta(compatto)
        assert accetta({'points': compatto})


# =============================================================================
# 5. BP group [points, interp]
# =============================================================================

class TestBpGroup:

    def test_gruppo_step_accettato(self):
        assert accetta([[[0, 1], [5, -1]], 'step'])

    def test_gruppo_con_interp_diverso_rifiutato(self):
        assert not accetta([[[0, 1], [5, -1]], 'linear'])

    def test_gruppo_con_un_punto_solo_rifiutato(self):
        # Senza due punti non ha segmenti interni: niente a cui applicare
        # l'interpolazione.
        assert not accetta([[[0, 1]], 'step'])

    def test_gruppo_vuoto_rifiutato(self):
        assert not accetta([[], 'step'])

    def test_valore_intermedio_dentro_il_gruppo_rifiutato(self):
        assert not accetta([[[0, 1], [5, 0.4]], 'step'])


# =============================================================================
# 6. Formato compatto [pattern, end_time, n_reps, interp?, time_dist?, wrap?]
# =============================================================================

class TestFormatoCompatto:

    def test_ciclo_minimo_accettato(self):
        assert accetta([[[0, 1], [50, -1]], 2.0, 2])

    def test_interp_step_esplicito_accettato(self):
        assert accetta([[[0, 1], [50, -1]], 2.0, 2, 'step'])

    def test_interp_diverso_rifiutato(self):
        assert not accetta([[[0, 1], [50, -1]], 2.0, 2, 'linear'])

    @pytest.mark.parametrize('n_reps', [0, -1])
    def test_meno_di_un_ciclo_rifiutato(self, n_reps):
        assert not accetta([[[0, 1], [50, -1]], 2.0, n_reps])

    def test_n_reps_booleano_rifiutato(self):
        # `True < 1` è falso: senza il guard sul tipo il motore renderebbe un
        # ciclo in silenzio.
        assert not accetta([[[0, 1], [50, -1]], 2.0, True])

    @pytest.mark.parametrize('end_time', [0, -1.0])
    def test_end_time_non_positivo_rifiutato(self, end_time):
        assert not accetta([[[0, 1], [50, -1]], end_time, 2])

    def test_end_time_booleano_rifiutato(self):
        assert not accetta([[[0, 1], [50, -1]], True, 2])

    def test_pattern_vuoto_rifiutato(self):
        assert not accetta([[], 2.0, 2])

    @pytest.mark.parametrize('x', [150, -10, 101])
    def test_percentuale_fuori_da_0_100_rifiutata(self, x):
        assert not accetta([[[0, 1], [x, -1]], 2.0, 2])

    @pytest.mark.parametrize('x', [0, 100])
    def test_gli_estremi_della_percentuale_sono_validi(self, x):
        assert accetta([[[x, 1]], 2.0, 2])

    def test_percentuali_che_tornano_indietro_rifiutate(self):
        # Entrambe in range, ma con step il valore dichiarato per primo non
        # comparirebbe in nessun grano.
        assert not accetta([[[100, 1], [0, -1]], 2.0, 2])

    def test_percentuale_ripetuta_ammessa(self):
        # È la discontinuità, non un errore.
        assert accetta([[[50, 1], [50, -1]], 2.0, 2])

    def test_macro_forma_dentro_il_pattern_rifiutata(self):
        # Un BP group è lungo 2 e si infila nel filtro sulla lunghezza del
        # pattern; il motore poi ci fa `x_pct / 100.0`.
        assert not accetta([[[[0, 1], [5, -1]], 'step'], 2.0, 2])

    def test_wrap_booleano_accettato(self):
        assert accetta([[[0, 1], [50, -1]], 2.0, 2, 'step', 'linear', True])


# =============================================================================
# 7. Distribuzione temporale (quinto elemento)
# =============================================================================

class TestDistribuzioneTemporale:

    @pytest.mark.parametrize('nome', ['linear', 'exponential', 'exp',
                                      'logarithmic', 'log', 'geometric',
                                      'geo', 'power'])
    def test_nomi_del_registro_accettati(self, nome):
        assert accetta([[[0, 1], [50, -1]], 2.0, 2, 'step', nome])

    def test_nome_ignoto_rifiutato(self):
        assert not accetta([[[0, 1], [50, -1]], 2.0, 2, 'step', 'bogus'])

    def test_hint_del_nome_elenca_i_validi(self):
        # È l'errore più frequente: l'elenco vale più della spiegazione.
        h = hint([[[0, 1], [50, -1]], 2.0, 2, 'step', 'bogus'])
        assert 'exponential' in h and 'geometric' in h

    def test_type_ignoto_nel_dict_rifiutato(self):
        assert not accetta([[[0, 1], [50, -1]], 2.0, 2, 'step',
                            {'type': 'bogus'}])

    def test_type_non_stringa_rifiutato(self):
        # Nel motore arriverebbe a `.lower()` e alzerebbe AttributeError.
        assert not accetta([[[0, 1], [50, -1]], 2.0, 2, 'step', {'type': 5}])

    def test_omessa_accettata(self):
        assert accetta([[[0, 1], [50, -1]], 2.0, 2, 'step', None])

    @pytest.mark.parametrize('spec,valido', [
        ({'type': 'geometric', 'ratio': 1.5}, True),
        ({'type': 'geometric', 'ratio': 0}, False),
        ({'type': 'exponential', 'rate': 2}, True),
        ({'type': 'exponential', 'rate': 0}, False),
        ({'type': 'logarithmic', 'base': 2}, True),
        ({'type': 'logarithmic', 'base': 1}, False),
        ({'type': 'power', 'exponent': 2}, True),
        ({'type': 'power', 'exponent': 'x'}, False),
    ])
    def test_bound_dei_costruttori(self, spec, valido):
        assert accetta([[[0, 1], [50, -1]], 2.0, 2, 'step', spec]) is valido

    def test_parametro_estraneo_al_tipo_rifiutato(self):
        # Il costruttore lo rifiuterebbe come kwarg inatteso.
        assert not accetta([[[0, 1], [50, -1]], 2.0, 2, 'step',
                            {'type': 'exponential', 'ratio': 1.5}])

    def test_dict_senza_type_e_linear_e_linear_non_prende_parametri(self):
        assert not accetta([[[0, 1], [50, -1]], 2.0, 2, 'step', {'ratio': 1.5}])

    def test_hint_senza_type_lo_dice(self):
        # Prima il motore incolpava un 'linear' che l'utente non aveva scritto.
        h = hint([[[0, 1], [50, -1]], 2.0, 2, 'step', {'ratio': 1.5}])
        assert 'type' in h and 'linear' in h


# =============================================================================
# 8. Liste miste
# =============================================================================

class TestListeMiste:

    def test_breakpoint_seguito_da_ciclo(self):
        assert accetta([[0, 1], [[[0, 1], [50, -1]], 5.0, 2]])

    def test_errore_dentro_il_ciclo_di_una_lista_mista(self):
        assert not accetta([[0, 1], [[[0, 1], [50, -1]], 5.0, 2, 'linear']])


# =============================================================================
# 9. Struttura dell'issue
# =============================================================================

class TestIssue:

    def test_issue_porta_valore_e_hint(self):
        issue = check_read_direction(0.5)
        assert isinstance(issue, ReadDirectionIssue)
        assert issue.value == 0.5
        assert issue.hint

    def test_un_solo_problema_per_volta(self):
        # Come il motore, che alla prima violazione solleva: il primo in
        # ordine di controllo, non una lista.
        issue = check_read_direction([[[0, 1], [50, 0.5]], 2.0, 0])
        # n_reps è controllato prima dei punti del pattern.
        assert issue.value == 0

    def test_issue_e_immutabile(self):
        issue = check_read_direction(0)
        with pytest.raises(Exception):
            issue.value = 1
