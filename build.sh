#!/bin/bash
# build.sh — aggiorna i file bundled e pacchettizza l'estensione VSCode
#
# Uso:
#   bash build.sh           # pacchettizza con la versione in package.json
#   bash build.sh --install  # pacchettizza e installa in VSCode
#
set -e

ROOT="$(cd "$(dirname "$0")" && pwd)"
CLIENT="$ROOT/vscode-client"

echo "==> Aggiorno i file bundled in vscode-client/..."
cp "$ROOT/server.py" "$CLIENT/server.py"
rm -rf "$CLIENT/granular_ls"
cp -r "$ROOT/granular_ls" "$CLIENT/granular_ls"

# Rimuove cache Python
find "$CLIENT/granular_ls" -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
find "$CLIENT/granular_ls" -name "*.pyc" -delete 2>/dev/null || true

echo "==> Installo dipendenze npm..."
cd "$CLIENT"
npm install --silent

echo "==> Pacchettizzando..."
npx @vscode/vsce package --no-dependencies

VSIX=$(ls "$CLIENT"/*.vsix | sort -V | tail -1)
echo "==> Creato: $VSIX"

if [[ "$1" == "--install" ]]; then
    echo "==> Installando in VSCode..."
    code --install-extension "$VSIX"
    echo "==> Installato. Riavvia VSCode per applicare le modifiche."
fi
