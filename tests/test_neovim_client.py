# tests/test_neovim_client.py
"""
Test statici per il client Neovim (clients/neovim/).

Il client Neovim è fatto di artefatti non-Python (template Lua, script bash,
README) e non tocca server.py. Qui non si avvia Neovim — i test verificano in
modo statico che gli artefatti rispettino i requisiti della issue #26:

    R1  server.py avviato via stdio (cmd = {python, server, ...})
    R2  attivazione SOLO su PGE_*.yaml / PGE_*.yml
    R3  setup.sh idempotente (require non duplicato)
    R4  rileva macOS/Linux, documenta Windows
    R5  config Lua con path assolute risolte dallo script (placeholder + sed)
    R6  struttura coerente con clients/vscode/
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
NVIM_DIR = ROOT / "clients" / "neovim"
TEMPLATE = NVIM_DIR / "pge-ls.lua.template"
SETUP = NVIM_DIR / "setup.sh"
README = NVIM_DIR / "readme.md"  # case-insensitive lookup sotto
BUILD = ROOT / "build.sh"
CLAUDE_MD = ROOT / "CLAUDE.md"
E2E_LUA = NVIM_DIR / "tests" / "e2e.lua"
RUN_E2E = NVIM_DIR / "tests" / "run-e2e.sh"
CI_YML = ROOT / ".github" / "workflows" / "ci.yml"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _readme_path() -> Path:
    for name in ("README.md", "readme.md"):
        cand = NVIM_DIR / name
        if cand.exists():
            return cand
    raise AssertionError("README del client Neovim mancante")


# ---------------------------------------------------------------------------
# Struttura (U-output / R6)
# ---------------------------------------------------------------------------

def test_neovim_dir_has_expected_files():
    assert TEMPLATE.exists(), "pge-ls.lua.template mancante"
    assert SETUP.exists(), "setup.sh mancante"
    assert _readme_path().exists(), "README mancante"


def test_generated_lua_is_not_committed():
    # Solo il template è versionato; il file generato ha path assolute di macchina.
    assert not (NVIM_DIR / "pge-ls.lua").exists()


# ---------------------------------------------------------------------------
# Template Lua (U1 / R1, R2, R5)
# ---------------------------------------------------------------------------

def test_template_has_three_placeholders():
    txt = _read(TEMPLATE)
    for ph in ("%%PYTHON%%", "%%SERVER%%", "%%SNAPSHOT%%"):
        assert ph in txt, f"placeholder {ph} assente nel template"


def test_template_activates_only_on_pge_files():
    txt = _read(TEMPLATE)
    assert "nvim_create_autocmd" in txt
    assert "BufReadPre" in txt
    # R2: pattern ristretto ai file PGE_*, sia .yaml che .yml
    assert "PGE_*.yaml" in txt
    assert "PGE_*.yml" in txt


def test_template_starts_server_via_stdio():
    txt = _read(TEMPLATE)
    # R1: vim.lsp.start costruisce la cmd con python + server.py
    assert "vim.lsp.start" in txt
    assert "{ python, server }" in txt


def test_template_guards_reuse_client_for_old_nvim():
    txt = _read(TEMPLATE)
    # Risk mitigation: guard su nvim-0.10 per reuse_client
    assert "nvim-0.10" in txt
    assert "reuse_client" in txt


def test_template_picks_src_or_snapshot_flag():
    txt = _read(TEMPLATE)
    assert "--snapshot" in txt
    assert "--src" in txt
    assert "isdirectory" in txt  # discrimina dir(src) vs file json(snapshot)


# ---------------------------------------------------------------------------
# Script setup.sh (U2 / R3, R4, R5)
# ---------------------------------------------------------------------------

def test_setup_is_executable():
    mode = SETUP.stat().st_mode
    assert mode & 0o111, "setup.sh non è eseguibile"


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash non disponibile")
def test_setup_passes_bash_syntax_check():
    res = subprocess.run(
        ["bash", "-n", str(SETUP)], capture_output=True, text=True
    )
    assert res.returncode == 0, res.stderr


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash non disponibile")
def test_build_passes_bash_syntax_check():
    res = subprocess.run(
        ["bash", "-n", str(BUILD)], capture_output=True, text=True
    )
    assert res.returncode == 0, res.stderr


def test_setup_detects_os():
    txt = _read(SETUP)
    assert "uname -s" in txt
    assert "Darwin" in txt and "Linux" in txt  # R4
    # Windows: rilevato per dare errore con rimando al README, non silenzioso
    assert "MINGW" in txt or "MSYS" in txt or "CYGWIN" in txt


def test_setup_respects_xdg_config_home():
    txt = _read(SETUP)
    assert "XDG_CONFIG_HOME" in txt
    assert ".config/nvim" in txt


def test_setup_checks_versions():
    txt = _read(SETUP)
    assert "0.8" in txt   # Neovim minima
    assert "3.10" in txt  # Python minima
    assert "version_ge" in txt


def test_setup_creates_venv_in_root():
    txt = _read(SETUP)
    assert ".venv" in txt
    assert "requirements.txt" in txt


def test_setup_substitutes_placeholders_with_sed():
    txt = _read(SETUP)
    for ph in ("%%PYTHON%%", "%%SERVER%%", "%%SNAPSHOT%%"):
        assert ph in txt, f"setup.sh non sostituisce {ph}"
    assert "sed" in txt


def test_setup_injects_require_idempotently():
    txt = _read(SETUP)
    assert "require('pge-ls')" in txt
    # R3: l'append avviene solo se il require non è già presente
    assert "grep -Eq" in txt
    assert "pge-ls" in txt


def test_setup_supports_skip_deps():
    # PGE_DEPS=skip salta il pip install (CI / venv già provvisto)
    txt = _read(SETUP)
    assert "PGE_DEPS" in txt
    assert "skip" in txt


# ---------------------------------------------------------------------------
# Test end-to-end headless (artefatti) + job CI
# ---------------------------------------------------------------------------

def test_e2e_artifacts_exist():
    assert E2E_LUA.exists(), "e2e.lua mancante"
    assert RUN_E2E.exists(), "run-e2e.sh mancante"


def test_run_e2e_is_executable():
    assert RUN_E2E.stat().st_mode & 0o111, "run-e2e.sh non è eseguibile"


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash non disponibile")
def test_run_e2e_passes_bash_syntax_check():
    res = subprocess.run(
        ["bash", "-n", str(RUN_E2E)], capture_output=True, text=True
    )
    assert res.returncode == 0, res.stderr


def test_e2e_lua_asserts_activation():
    txt = _read(E2E_LUA)
    # Verifica attivazione del client 'pge-ls' e handshake (server_capabilities)
    assert "pge-ls" in txt
    assert "server_capabilities" in txt
    # API client con fallback per nvim < 0.10
    assert "get_clients" in txt and "get_active_clients" in txt
    # Esce con codice non-zero su fallimento
    assert "cquit" in txt


def test_ci_has_neovim_e2e_job():
    txt = _read(CI_YML)
    assert "neovim-e2e" in txt
    assert "run-e2e.sh" in txt
    # Neovim installato dal tarball stable (apt è troppo vecchio)
    assert "neovim/releases/download/stable" in txt


# ---------------------------------------------------------------------------
# Integrazione leggera: la sostituzione sed non lascia placeholder residui
# ---------------------------------------------------------------------------

def test_sed_substitution_leaves_no_placeholder():
    template = _read(TEMPLATE)
    subs = {
        "%%PYTHON%%": "/abs/path with space/.venv/bin/python",
        "%%SERVER%%": "/abs/path with space/server.py",
        "%%SNAPSHOT%%": "/abs/path with space/clients/vscode/schema_snapshot.json",
    }
    out = template
    for ph, val in subs.items():
        out = out.replace(ph, val)
    assert "%%" not in out, "placeholder residui dopo sostituzione"
    # le path finiscono dentro literal Lua [[ ]]
    assert "[[/abs/path with space/server.py]]" in out


# ---------------------------------------------------------------------------
# build.sh + CLAUDE.md (U4)
# ---------------------------------------------------------------------------

def test_build_has_neovim_case():
    txt = _read(BUILD)
    assert "--neovim" in txt
    assert "build_neovim" in txt
    assert "clients/neovim/setup.sh" in txt


def test_claude_md_documents_neovim_client():
    txt = _read(CLAUDE_MD)
    assert "clients/neovim" in txt
    assert "--neovim" in txt


# ---------------------------------------------------------------------------
# README (U3 / R4)
# ---------------------------------------------------------------------------

def test_readme_documents_windows_and_advanced_config():
    txt = _read(_readme_path())
    assert re.search(r"Windows", txt), "manca la sezione Windows"
    assert "LOCALAPPDATA" in txt
    assert "PGE_SRC" in txt  # configurazione avanzata --src
