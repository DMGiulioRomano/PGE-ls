#!/bin/bash
# setup.sh - Setup automatico di pge-ls
# Uso: bash setup.sh
# Eseguire dalla cartella Downloads DOPO aver estratto lo zip.

set -e  # esci subito se un comando fallisce

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$SCRIPT_DIR"
CLIENT_DIR="$PROJECT_DIR/vscode-client"
VSCODE_DIR="$CLIENT_DIR/.vscode"

echo "==> pge-ls setup"
echo "    directory: $PROJECT_DIR"
echo ""

# -----------------------------------------------------------------------
# 1. Python: verifica versione e dipendenze
# -----------------------------------------------------------------------
echo "[1/4] Controllo Python e dipendenze..."

PYTHON=""
for candidate in python3.11 python3.12 python3.10 python3; do
    if command -v "$candidate" &>/dev/null; then
        PYTHON="$candidate"
        break
    fi
done

if [ -z "$PYTHON" ]; then
    echo "    ERRORE: Python non trovato. Installa Python 3.10+ con Homebrew:"
    echo "    brew install python@3.11"
    exit 1
fi

echo "    Python trovato: $PYTHON ($($PYTHON --version))"

# Installa le dipendenze Python se mancano
$PYTHON -c "import pygls" 2>/dev/null || {
    echo "    Installazione dipendenze Python..."
    $PYTHON -m pip install "pygls==1.3.1" lsprotocol pytest --quiet
}
echo "    Dipendenze Python OK"

# -----------------------------------------------------------------------
# 2. Test Python
# -----------------------------------------------------------------------
echo ""
echo "[2/4] Lancio test Python..."
cd "$PROJECT_DIR"
$PYTHON -m pytest tests/ -q --tb=short
echo "    Test OK"

# -----------------------------------------------------------------------
# 3. Node.js: installa dipendenze npm
# -----------------------------------------------------------------------
echo ""
echo "[3/4] Controllo Node.js e dipendenze npm..."

if ! command -v node &>/dev/null; then
    echo "    ERRORE: Node.js non trovato. Installa da https://nodejs.org"
    exit 1
fi

echo "    Node: $(node --version)  npm: $(npm --version)"
cd "$CLIENT_DIR"
npm install --silent
echo "    Dipendenze npm OK"

# -----------------------------------------------------------------------
# 4. Crea .vscode/launch.json
# -----------------------------------------------------------------------
echo ""
echo "[4/4] Creazione .vscode/launch.json..."
mkdir -p "$VSCODE_DIR"

cat > "$VSCODE_DIR/launch.json" << 'EOF'
{
  "version": "0.2.0",
  "configurations": [
    {
      "name": "Launch Extension",
      "type": "extensionHost",
      "request": "launch",
      "args": [
        "--extensionDevelopmentPath=${workspaceFolder}"
      ]
    }
  ]
}
EOF

echo "    .vscode/launch.json creato"

# -----------------------------------------------------------------------
# Riepilogo finale
# -----------------------------------------------------------------------
echo ""
echo "======================================"
echo "  Setup completato con successo!"
echo "======================================"
echo ""
echo "  Prossimi passi:"
echo ""
echo "  1. Apri VSCode sulla cartella vscode-client:"
echo "     code $CLIENT_DIR"
echo ""
echo "  2. In VSCode verifica settings.json (Cmd+Shift+P -> Open User Settings JSON):"
echo "     \"pgeLs.pythonPath\": \"$PYTHON\","
echo "     \"pgeLs.granularSrcPath\": \"/path/al/tuo/progetto/src\""
echo ""
echo "  3. Premi F5 per avviare l'Extension Development Host."
echo ""
echo "  4. Nella nuova finestra apri un file PGE_*.yaml e testa l'autocompletion."
echo ""
