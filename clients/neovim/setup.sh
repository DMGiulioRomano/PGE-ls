#!/bin/bash
# setup.sh — installa il client Neovim per il PGE Language Server.
#
# Uso:
#   bash clients/neovim/setup.sh
#
# Cosa fa (idempotente — rieseguibile senza rompere una config esistente):
#   1. rileva l'OS (macOS / Linux; Windows solo documentato nel README)
#   2. verifica Neovim >= 0.8 e Python >= 3.10
#   3. crea/aggiorna il virtualenv .venv/ nella root del repo e installa le deps
#   4. risolve le path assolute (python del venv, server.py, snapshot/src)
#   5. genera ~/.config/nvim/lua/pge-ls.lua dal template via sed
#   6. aggiunge require('pge-ls') a init.lua se manca, senza duplicati
#
# Variabili d'ambiente opzionali:
#   PGE_SRC           directory 'src' del Python Granular Engine. Se impostata e
#                     valida, la config userà --src (schema live) invece dello
#                     snapshot pre-generato.
#   XDG_CONFIG_HOME   rispettata per individuare la config di Neovim.

set -e

# --- Path del repo --------------------------------------------------------
# clients/neovim/setup.sh -> la root è due livelli sopra.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
TEMPLATE="$SCRIPT_DIR/pge-ls.lua.template"

echo "==> pge-ls — setup client Neovim"
echo "    repo: $ROOT"
echo ""

# --- Helper: confronto versioni (a >= b, dotted, base-10) -----------------
version_ge() {
  local a="$1" b="$2" i ai bi
  local IFS=.
  local -a av=($a) bv=($b)
  for i in 0 1 2; do
    ai=${av[i]:-0}; bi=${bv[i]:-0}
    if ((10#$ai > 10#$bi)); then return 0; fi
    if ((10#$ai < 10#$bi)); then return 1; fi
  done
  return 0
}

# -----------------------------------------------------------------------
# 1. Rilevamento OS
# -----------------------------------------------------------------------
echo "[1/6] Rilevamento sistema operativo..."
OS="$(uname -s)"
case "$OS" in
  Darwin) echo "    macOS rilevato" ;;
  Linux)  echo "    Linux rilevato" ;;
  MINGW*|MSYS*|CYGWIN*)
    echo "    ERRORE: Windows non è supportato da questo script."
    echo "    Segui la sezione 'Windows (setup manuale)' in clients/neovim/README.md."
    exit 1
    ;;
  *)
    echo "    ERRORE: sistema operativo non riconosciuto ($OS)."
    echo "    Configura manualmente seguendo clients/neovim/README.md."
    exit 1
    ;;
esac

# Directory di configurazione di Neovim (rispetta XDG_CONFIG_HOME su macOS/Linux).
NVIM_CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}/nvim"
echo "    config Neovim: $NVIM_CONFIG"

# -----------------------------------------------------------------------
# 2. Verifica Neovim >= 0.8 e Python >= 3.10
# -----------------------------------------------------------------------
echo ""
echo "[2/6] Controllo prerequisiti..."

if ! command -v nvim &>/dev/null; then
  echo "    ERRORE: Neovim non trovato. Installa Neovim >= 0.8."
  echo "    macOS: brew install neovim   —   Linux: usa il package manager."
  exit 1
fi
NVIM_VER="$(nvim --version 2>/dev/null | head -1 | grep -oE '[0-9]+\.[0-9]+(\.[0-9]+)?' | head -1)"
if [ -z "$NVIM_VER" ] || ! version_ge "$NVIM_VER" "0.8"; then
  echo "    ERRORE: serve Neovim >= 0.8 (trovato: ${NVIM_VER:-sconosciuto})."
  exit 1
fi
echo "    Neovim $NVIM_VER OK"

PYTHON=""
for candidate in python3.12 python3.11 python3.10 python3; do
  if command -v "$candidate" &>/dev/null; then
    PY_VER="$("$candidate" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || echo "")"
    if [ -n "$PY_VER" ] && version_ge "$PY_VER" "3.10"; then
      PYTHON="$candidate"
      break
    fi
  fi
done
if [ -z "$PYTHON" ]; then
  echo "    ERRORE: serve Python >= 3.10. Installa python@3.11."
  echo "    macOS: brew install python@3.11   —   Linux: usa il package manager."
  exit 1
fi
echo "    Python $("$PYTHON" --version 2>&1 | awk '{print $2}') OK ($PYTHON)"

# -----------------------------------------------------------------------
# 3. Virtualenv + dipendenze
# -----------------------------------------------------------------------
echo ""
echo "[3/6] Virtualenv e dipendenze Python..."
VENV_DIR="$ROOT/.venv"
if [ ! -d "$VENV_DIR" ]; then
  echo "    Creazione virtualenv in .venv/ ..."
  "$PYTHON" -m venv "$VENV_DIR"
fi
PYTHON_BIN="$VENV_DIR/bin/python"
echo "    Installazione dipendenze da requirements.txt..."
"$VENV_DIR/bin/pip" install --quiet -r "$ROOT/requirements.txt"
echo "    Dipendenze OK"

# -----------------------------------------------------------------------
# 4. Risoluzione path assolute (schema)
# -----------------------------------------------------------------------
echo ""
echo "[4/6] Risoluzione path..."
SERVER="$ROOT/server.py"

# Sorgente dello schema, in ordine di preferenza:
#   - PGE_SRC esportata e valida  -> --src (schema live)
#   - clients/vscode/schema_snapshot.json (mantenuto da build.sh) -> --snapshot
#   - assente -> vuoto (il server parte in degraded mode; vedi avviso)
SCHEMA=""
if [ -n "$PGE_SRC" ] && [ -d "$PGE_SRC" ]; then
  SCHEMA="$(cd "$PGE_SRC" && pwd)"
  echo "    Schema live da PGE_SRC: $SCHEMA (--src)"
else
  SNAPSHOT="$ROOT/clients/vscode/schema_snapshot.json"
  if [ -f "$SNAPSHOT" ]; then
    SCHEMA="$SNAPSHOT"
    echo "    Snapshot schema: $SCHEMA (--snapshot)"
  else
    echo "    AVVISO: schema_snapshot.json assente in clients/vscode/."
    echo "            Esegui 'bash build.sh' per generarlo, oppure esporta"
    echo "            PGE_SRC=/path/a/PythonGranularEngine/src e rilancia."
    echo "            Senza schema l'LSP parte ma senza autocompletion."
  fi
fi
echo "    Server: $SERVER"
echo "    Python: $PYTHON_BIN"

# -----------------------------------------------------------------------
# 5. Genera lua/pge-ls.lua dal template
# -----------------------------------------------------------------------
echo ""
echo "[5/6] Generazione config Lua..."
if [ ! -f "$TEMPLATE" ]; then
  echo "    ERRORE: template non trovato: $TEMPLATE"
  exit 1
fi
LUA_DIR="$NVIM_CONFIG/lua"
LUA_OUT="$LUA_DIR/pge-ls.lua"
mkdir -p "$LUA_DIR"

# Delimitatore sed '|': le path POSIX non lo contengono. Sostituzione atomica.
sed -e "s|%%PYTHON%%|$PYTHON_BIN|g" \
    -e "s|%%SERVER%%|$SERVER|g" \
    -e "s|%%SNAPSHOT%%|$SCHEMA|g" \
    "$TEMPLATE" > "$LUA_OUT"
echo "    Scritto $LUA_OUT"

# -----------------------------------------------------------------------
# 6. require('pge-ls') in init.lua (idempotente)
# -----------------------------------------------------------------------
echo ""
echo "[6/6] Aggancio in init.lua..."
INIT_LUA="$NVIM_CONFIG/init.lua"
touch "$INIT_LUA"
if grep -Eq "require\(['\"]pge-ls['\"]\)" "$INIT_LUA"; then
  echo "    require('pge-ls') già presente — niente da fare"
else
  {
    echo ""
    echo "-- PGE-ls (Python Granular Engine LSP) — aggiunto da clients/neovim/setup.sh"
    echo "require('pge-ls')"
  } >> "$INIT_LUA"
  echo "    require('pge-ls') aggiunto a $INIT_LUA"
fi

# -----------------------------------------------------------------------
# Riepilogo
# -----------------------------------------------------------------------
echo ""
echo "======================================"
echo "  Setup Neovim completato"
echo "======================================"
echo ""
echo "  Apri un file PGE_*.yaml in Neovim e verifica con :LspInfo che"
echo "  il client 'pge-ls' sia attivo. Completion: <C-x><C-o> (omnifunc)."
echo ""
if [ -z "$SCHEMA" ]; then
  echo "  NOTA: nessuno schema caricato. Genera lo snapshot con 'bash build.sh'"
  echo "        o esporta PGE_SRC e rilancia questo setup."
  echo ""
fi
