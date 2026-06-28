#!/bin/bash
# build.sh — aggiorna i file bundled e pacchettizza i client
#
# Uso:
#   bash build.sh                  # build solo VSCode
#   bash build.sh --all            # build VSCode + Pulsar
#   bash build.sh --pulsar         # build solo Pulsar
#   bash build.sh --neovim         # installa il client Neovim (setup.sh)
#   bash build.sh --install        # build VSCode e installa in VSCode
#
set -e

ROOT="$(cd "$(dirname "$0")" && pwd)"

generate_snapshot() {
  # Genera lo snapshot JSON dello schema accanto al server bundled, così il
  # .vsix funziona anche senza pgeLs.granularSrcPath (server.py lo carica come
  # default se presente accanto a sé). Richiede il src di PythonGranularEngine:
  # env PGE_SRC o checkout sibling. Se assente, avvisa e prosegue (pacchetto
  # degradato: schema vuoto finché l'utente non imposta granularSrcPath).
  local dest="$1"
  local pge_src="${PGE_SRC:-$ROOT/../PythonGranularEngine/src}"
  if [ ! -d "$pge_src" ]; then
    echo "==> ATTENZIONE: src di PythonGranularEngine non trovato ($pge_src)."
    echo "    schema_snapshot.json NON generato: il .vsix avrà schema vuoto"
    echo "    finché l'utente non imposta pgeLs.granularSrcPath."
    echo "    Imposta PGE_SRC=/path/a/PythonGranularEngine/src per includerlo."
    return 0
  fi
  echo "==> Genero schema_snapshot.json da $pge_src..."
  PYTHONPATH="$ROOT" python3 - "$pge_src" "$dest/schema_snapshot.json" <<'PYEOF'
import sys
from granular_ls.schema_bridge import SchemaBridge
src, out = sys.argv[1], sys.argv[2]
with open(out, 'w') as fh:
    fh.write(SchemaBridge.from_python_path(src).generate_snapshot())
print(f"    scritto {out}")
PYEOF
}

sync_python_files() {
  local dest="$1"
  echo "==> Sincronizzo file Python in $dest..."
  cp "$ROOT/server.py" "$dest/server.py"
  cp "$ROOT/envelope_gui.py" "$dest/envelope_gui.py"
  rm -rf "$dest/granular_ls"
  cp -r "$ROOT/granular_ls" "$dest/granular_ls"
  find "$dest/granular_ls" -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
  find "$dest/granular_ls" -name "*.pyc" -delete 2>/dev/null || true
  generate_snapshot "$dest"
}

build_vscode() {
  local client="$ROOT/clients/vscode"
  sync_python_files "$client"
  echo "==> Installo dipendenze npm (VSCode)..."
  cd "$client"
  npm install --silent
  echo "==> Pacchettizzando VSCode..."
  npx @vscode/vsce package
  VSIX=$(ls "$client"/*.vsix | sort -V | tail -1)
  echo "==> Creato: $VSIX"
  if [[ "$1" == "--install" ]]; then
    echo "==> Installando in VSCode..."
    code --install-extension "$VSIX"
    echo "==> Installato. Riavvia VSCode per applicare le modifiche."
  fi
}

build_neovim() {
  # Il client Neovim non bundla il server: punta direttamente a server.py nella
  # root. Qui deleghiamo allo script di setup, che è già idempotente.
  echo "==> Setup client Neovim..."
  bash "$ROOT/clients/neovim/setup.sh"
}

build_pulsar() {
  local client="$ROOT/clients/pulsar"
  sync_python_files "$client"
  echo "==> Installo dipendenze npm (Pulsar)..."
  cd "$client"
  npm install --silent
  echo "==> Pacchetto Pulsar pronto in $client"
  echo "    Per installare in Pulsar:"
  echo "    cd ~/.pulsar/packages && ln -s $client pge-ls"
}

case "$1" in
  --all)
    build_vscode
    build_pulsar
    ;;
  --pulsar)
    build_pulsar
    ;;
  --neovim)
    build_neovim
    ;;
  --install)
    build_vscode --install
    ;;
  *)
    build_vscode
    ;;
esac
