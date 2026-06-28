#!/bin/bash
# run-e2e.sh — test end-to-end headless del client Neovim.
#
# Presuppone che `bash clients/neovim/setup.sh` sia già stato eseguito: legge la
# config generata in ~/.config/nvim (rispettando XDG_CONFIG_HOME), lancia Neovim
# in modalità headless e delega le asserzioni a e2e.lua, che verifica che il
# client LSP 'pge-ls' si attivi solo sui file PGE_*.yaml.
#
# Uso:  bash clients/neovim/tests/run-e2e.sh
# Exit: 0 se le asserzioni passano, != 0 altrimenti (anche su timeout).

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
NVIM_CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}/nvim"
INIT_LUA="$NVIM_CONFIG/init.lua"
LUA_MOD="$NVIM_CONFIG/lua/pge-ls.lua"
E2E="$SCRIPT_DIR/e2e.lua"

if ! command -v nvim &>/dev/null; then
  echo "ERRORE: nvim non trovato nel PATH." >&2
  exit 1
fi
if [ ! -f "$LUA_MOD" ] || [ ! -f "$INIT_LUA" ]; then
  echo "ERRORE: config Neovim non trovata ($LUA_MOD)." >&2
  echo "        Esegui prima: bash clients/neovim/setup.sh" >&2
  exit 1
fi

echo "==> $(nvim --version | head -1)"
echo "==> config: $NVIM_CONFIG"

# nvim --headless carica la init reale (con require('pge-ls')) e poi esegue
# e2e.lua. timeout evita un hang se il server non risponde mai.
RUN=(nvim --headless -c "luafile $E2E")
if command -v timeout &>/dev/null; then
  timeout 120 "${RUN[@]}"
else
  "${RUN[@]}"
fi
