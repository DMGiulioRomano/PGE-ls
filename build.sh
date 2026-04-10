#!/bin/bash
# build.sh — aggiorna i file bundled e pacchettizza i client
#
# Uso:
#   bash build.sh                  # build solo VSCode
#   bash build.sh --all            # build VSCode + Pulsar
#   bash build.sh --pulsar         # build solo Pulsar
#   bash build.sh --install        # build VSCode e installa in VSCode
#
set -e

ROOT="$(cd "$(dirname "$0")" && pwd)"

sync_python_files() {
  local dest="$1"
  echo "==> Sincronizzo file Python in $dest..."
  cp "$ROOT/server.py" "$dest/server.py"
  cp "$ROOT/envelope_gui.py" "$dest/envelope_gui.py"
  rm -rf "$dest/granular_ls"
  cp -r "$ROOT/granular_ls" "$dest/granular_ls"
  find "$dest/granular_ls" -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
  find "$dest/granular_ls" -name "*.pyc" -delete 2>/dev/null || true
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
  --install)
    build_vscode --install
    ;;
  *)
    build_vscode
    ;;
esac
