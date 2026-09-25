# tests/test_time_distributions.py
"""
La coppia (parametro, n_reps) che trabocca: la regola di PGE #212 (issue #45).

Le tre potenze del registro — `ratio ** n_reps`, `rate ** -i`,
`(i + 1) ** exponent` — con abbastanza cicli non stanno in un float, e il
motore lo dice con un `ParameterBoundError` che nomina la coppia. Nessuno dei
due valori è fuori posto da solo: `ratio: 10` e `n_reps: 400` sono legittimi,
è insieme che esplodono.

I numeri attesi qui sotto non sono dedotti: sono le soglie che il motore
produce davvero, lette da `TimeDistributionFactory` su ogni `n_reps`. Il
controllo che resti così è in `tests/test_pge_parity.py`, che le ribisezione
sul motore vero; qui sono fissate perché la suite senza motore sappia già
dove sta il bordo.

Il bordo dipende dalla **grafia**: `ratio: 10` e `ratio: 10.0` non traboccano
allo stesso `n_reps`, perché con un intero Python calcola la potenza su interi
illimitati e trabocca solo la divisione che segue. Il language server legge lo
YAML con lo stesso PyYAML del motore, quindi la distinzione arriva intatta e
il conto si fa con la stessa aritmetica — nessuna banda di tolleranza.
"""

import time

import pytest

from granular_ls.time_distributions import (
    OVERFLOW_REMEDIES,
    check_time_distribution,
    overflow_hint,
)


def _geo(ratio):
    return {'type': 'geometric', 'ratio': ratio}


def _exp(rate):
    return {'type': 'exponential', 'rate': rate}


def _pow(exponent):
    return {'type': 'power', 'exponent': exponent}


def _trabocca(spec, n_reps) -> bool:
    issue = check_time_distribution(spec, n_reps)
    return issue is not None and issue.kind == 'overflow'


# =============================================================================
# 1. I bordi, per grafia
# =============================================================================

class TestBordiGeometric:

    def test_ratio_intero_trabocca_a_310(self):
        """Con `ratio` intero la potenza è esatta: trabocca il quoziente."""
        assert not _trabocca(_geo(10), 309)
        assert _trabocca(_geo(10), 310)

    def test_ratio_float_trabocca_a_309(self):
        """Stesso numero in grafia float: trabocca la potenza, un ciclo prima."""
        assert not _trabocca(_geo(10.0), 308)
        assert _trabocca(_geo(10.0), 309)

    def test_ratio_2_trabocca_a_1024_in_entrambe_le_grafie(self):
        for ratio in (2, 2.0):
            assert not _trabocca(_geo(ratio), 1023), ratio
            assert _trabocca(_geo(ratio), 1024), ratio

    def test_alias_geo(self):
        assert _trabocca({'type': 'geo', 'ratio': 2}, 1024)

    def test_ratio_minore_di_uno_non_trabocca(self):
        """La potenza tende a zero: nessun `n_reps` la fa esplodere."""
        assert not _trabocca(_geo(0.5), 100_000)

    def test_ratio_prossimo_a_uno_e_linear(self):
        """Entro 1e-6 da 1 il motore devia su `linear` prima di elevare."""
        assert not _trabocca(_geo(1 + 1e-7), 10 ** 12)

    def test_ratio_true_vale_uno(self):
        assert not _trabocca(_geo(True), 10 ** 12)

    def test_nome_nudo_usa_il_default_del_costruttore(self):
        """`'geometric'` senza parametri è `ratio: 1.5`, che trabocca anche lui."""
        assert not _trabocca('geometric', 1748)
        assert _trabocca('geometric', 1751)

    def test_la_somma_che_trabocca_prima_della_potenza(self):
        """Con `ratio` float fra 1 e 2 c'è un tratto dove `1.5 ** n_reps` sta
        ancora in un float ma la somma geometrica, divisa per `1 - ratio`, no.
        Lì il motore non alza il `ParameterBoundError` ma un
        `ZeroDivisionError` nudo — la somma infinita fa durare zero ogni ciclo,
        e poi divide per la loro somma. Il render fallisce comunque: tacere
        sarebbe dire che rende."""
        assert _trabocca('geometric', 1749)
        assert _trabocca('geometric', 1750)
        issue = check_time_distribution('geometric', 1749)
        assert issue.formula == '(1 - ratio ** n_reps) / (1 - ratio)'


class TestBordiExponential:

    def test_rate_mezzo_trabocca_a_1025(self):
        assert not _trabocca(_exp(0.5), 1024)
        assert _trabocca(_exp(0.5), 1025)

    def test_a_1024_i_cicli_collassano_ma_il_motore_rende(self):
        """A 1024 il peso massimo sta in un float e la loro somma no: il motore
        divide per infinito, ogni ciclo dura zero, e non alza niente. È un
        envelope collassato, non un errore — e un language server che lo
        chiamasse errore sarebbe più severo del motore."""
        assert check_time_distribution(_exp(0.5), 1024) is None

    def test_rate_maggiore_di_uno_non_trabocca(self):
        """`rate ** -i` decresce: con `rate >= 1` nessun peso supera 1."""
        for rate in (2, 2.0, 1, 1.0):
            assert not _trabocca(_exp(rate), 100_000), rate

    def test_alias_exp(self):
        assert _trabocca({'type': 'exp', 'rate': 0.5}, 1025)

    def test_rate_intero_che_da_solo_non_sta_in_un_float(self):
        """Con esponente negativo `int ** -i` passa dai float, e un intero di
        piu' di 308 cifre non ci entra: il motore alza dentro il suo `try` e
        lo riveste con l'errore della coppia. A `n_reps: 1` l'unico peso e'
        `rate ** 0`, che resta intero, e rende."""
        assert check_time_distribution(_exp(10 ** 400), 1) is None
        issue = check_time_distribution(_exp(10 ** 400), 2)
        assert issue is not None and issue.kind == 'overflow'
        assert (issue.param, issue.formula) == ('rate', 'rate ** -i')


class TestBordiPower:

    def test_esponente_float_trabocca_a_114(self):
        assert not _trabocca(_pow(150.0), 113)
        assert _trabocca(_pow(150.0), 114)

    def test_esponente_intero_non_trabocca_mai(self):
        """`(i + 1) ** 150` fra interi è esatto: Python non trabocca, e la
        divisione che normalizza i pesi torna fra 0 e 1."""
        assert not _trabocca(_pow(150), 100_000)

    def test_esponente_negativo_non_trabocca(self):
        assert not _trabocca(_pow(-150.0), 100_000)

    def test_esponente_bool_non_trabocca(self):
        """`true ** n` fa 1: il motore non ha mai alzato niente."""
        assert not _trabocca(_pow(True), 100_000)

    def test_collasso_prima_della_soglia_non_e_errore(self):
        """Stesso tratto di `exponential`: pesi in un float, somma no."""
        assert check_time_distribution(_pow(100.5), 1140) is None
        assert _trabocca(_pow(100.5), 1168)


class TestSenzaPotenze:

    @pytest.mark.parametrize('spec', ['linear', 'logarithmic', 'log', None,
                                      {'type': 'logarithmic', 'base': 1.5},
                                      'exponential', 'exp'])
    def test_non_traboccano(self, spec):
        assert check_time_distribution(spec, 10 ** 9) is None


# =============================================================================
# 2. Il contratto della funzione
# =============================================================================

class TestContratto:

    def test_senza_n_reps_non_si_guarda_la_coppia(self):
        """La firma storica resta: chi non passa `n_reps` non chiede l'overflow."""
        assert check_time_distribution(_geo(10)) is None

    def test_il_nome_viene_prima(self):
        """Un nome sbagliato non ha potenze da calcolare."""
        issue = check_time_distribution({'type': 'bogus', 'ratio': 10}, 10 ** 6)
        assert issue.kind == 'name'

    def test_i_bound_vengono_prima(self):
        issue = check_time_distribution(_geo(0), 10 ** 6)
        assert issue.kind == 'params'

    def test_n_reps_non_valido_non_e_una_coppia(self):
        """`n_reps < 1` è un altro errore, e non tocca a questa regola."""
        assert check_time_distribution(_geo(10), 0) is None
        assert check_time_distribution(_geo(10), -5) is None

    def test_n_reps_true_vale_uno(self):
        assert check_time_distribution(_geo(10), True) is None

    @pytest.mark.parametrize('spec, param, valore, formula', [
        (_geo(10), 'ratio', 10, 'ratio ** n_reps'),
        (_exp(0.5), 'rate', 0.5, 'rate ** -i'),
        (_pow(150.0), 'exponent', 150.0, '(i + 1) ** exponent'),
        ('geometric', 'ratio', 1.5, 'ratio ** n_reps'),
    ])
    def test_la_coppia_e_nominata(self, spec, param, valore, formula):
        issue = check_time_distribution(spec, 5000)
        assert issue.kind == 'overflow'
        assert issue.param == param
        assert issue.param_value == valore
        assert issue.n_reps == 5000
        assert issue.formula == formula

    def test_il_valore_resta_quello_scritto(self):
        spec = _geo(10)
        assert check_time_distribution(spec, 5000).value is spec


# =============================================================================
# 3. Il costo resta limitato
# =============================================================================

class TestCostoLimitato:
    """Con `ratio` intero il motore calcola `ratio ** n_reps` su interi
    illimitati: a `n_reps` enorme sono centinaia di megabyte. Il language
    server gira a ogni tasto premuto, quindi decide coi logaritmi dove il
    verdetto è certo e rifà il conto esatto solo vicino al bordo."""

    @pytest.mark.parametrize('spec, n_reps', [
        (_geo(10), 10 ** 9),
        (_geo(10 ** 300), 3),
        (_geo(1.5), 10 ** 400),
        (_exp(0.5), 10 ** 400),
        (_pow(150.0), 10 ** 30),
    ])
    def test_trabocca_senza_calcolare_la_potenza(self, spec, n_reps):
        inizio = time.perf_counter()
        assert _trabocca(spec, n_reps)
        assert time.perf_counter() - inizio < 0.5

    def test_il_bordo_con_ratio_intero_enorme(self):
        """`n_reps: 1` è `(1 - r) / (1 - r)`, cioè 1; a 2 il quoziente è
        `r + 1`, che con `r = 10 ** 300` sta ancora in un float; a 3 è
        `r ** 2 + r + 1`, che no."""
        assert check_time_distribution(_geo(10 ** 300), 1) is None
        assert check_time_distribution(_geo(10 ** 300), 2) is None
        assert _trabocca(_geo(10 ** 300), 3)

    def test_un_ratio_che_da_solo_non_sta_in_un_float_non_solleva(self):
        """Più di 308 cifre: il motore alza un `OverflowError` nudo sul
        confronto con 1, prima della coppia. Non è questa regola a dirlo — ma
        la regola non deve sollevare, perché il provider su un'eccezione
        spegne tutte le diagnostiche del documento."""
        assert check_time_distribution(_geo(10 ** 400), 2) is None

    def test_esponente_intero_enorme_non_calcola_niente(self):
        inizio = time.perf_counter()
        assert check_time_distribution(_pow(10 ** 6), 10 ** 9) is None
        assert time.perf_counter() - inizio < 0.5


# =============================================================================
# 4. Il messaggio
# =============================================================================

class TestOverflowHint:

    def test_nomina_entrambi_i_valori_e_la_formula(self):
        h = overflow_hint(check_time_distribution(_geo(10), 400))
        assert 'ratio=10' in h
        assert 'n_reps=400' in h
        assert 'ratio ** n_reps' in h

    def test_dice_che_e_la_coppia(self):
        h = overflow_hint(check_time_distribution(_geo(10), 400))
        assert 'coppia' in h

    @pytest.mark.parametrize('spec, rimedio', [
        (_geo(10), 'avvicina ratio a 1'),
        (_exp(0.5), 'avvicina rate a 1'),
        (_pow(150.0), 'riduci exponent in valore assoluto'),
    ])
    def test_il_rimedio_segue_il_parametro(self, spec, rimedio):
        """PGE #216: per `exponent` 1 è un valore ordinario, non la via
        d'uscita — a traboccare è l'ordine di grandezza."""
        h = overflow_hint(check_time_distribution(spec, 5000))
        assert rimedio in h
        assert 'Riduci n_reps' in h

    def test_tre_rimedi(self):
        assert set(OVERFLOW_REMEDIES) == {'ratio', 'rate', 'exponent'}
