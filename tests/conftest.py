"""Riga di chiusura della run: con quale motore e' girata la parita' (#70).

Un verde senza parita' e un verde con parita' si stampano uguali. Questa riga
li distingue, ed e' nel terminal summary perche' sopravvive a `-q` (l'header
del report no).
"""
from tests.pge_engine import summary_line


def pytest_terminal_summary(terminalreporter):
    terminalreporter.write_line(summary_line())
