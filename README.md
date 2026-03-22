# pge-ls

Language server VSCode per i file di configurazione `PGE_*.yaml` del [Python Granular Engine](https://github.com/DMGiulioRomano/easy).

Fornisce autocompletamento intelligente, documentazione hover e diagnostica in tempo reale mentre scrivi le configurazioni di sintesi granulare.

---

## Funzionalità

- **Autocompletamento** — parametri stream, blocchi (`grain`, `pointer`, `pitch`, `dephase`), 11 formati di envelope
- **Snippet dinamici** — `stream_id` con counter automatico, `end_time` dalla `duration` dello stream, valori Y dai bounds del parametro
- **Hover** — documentazione su ogni parametro: range, variation mode, exclusive group
- **Diagnostica** — bounds scalari e envelope, exclusive group per stream, campi obbligatori mancanti, chiavi duplicate
- **Go to file** — `Cmd+Click` su `sample:` apre il file audio da `refs/`

---

## Struttura del repository

```
pge-ls/
  granular_ls/          # moduli Python del server LSP
    providers/
      completion_provider.py
      diagnostic_provider.py
      hover_provider.py
    envelope_snippets.py
    schema_bridge.py
    yaml_analyzer.py
  tests/                # suite TDD (434 test)
  server.py             # entry point pygls
  setup.sh              # installazione per sviluppo
  vscode-client/        # estensione VSCode
    extension.js        # client LSP
    server.py           # copia bundled del server
    granular_ls/        # copia bundled dei moduli
    package.json        # manifesto estensione
    icon.png
    README.md
```

---

## Installazione rapida

Scarica il `.vsix` dalla pagina [Releases](../../releases):

```bash
code --install-extension pge-ls-0.1.0.vsix
```

Installa le dipendenze Python:

```bash
pip install pygls lsprotocol
```

Configura in `Preferences > Settings` → `pgeLs`:

| Impostazione | Descrizione |
|---|---|
| `pgeLs.pythonPath` | Percorso a Python (es. `python3.11`) |
| `pgeLs.granularSrcPath` | Percorso alla cartella `src` del progetto |

---

## Sviluppo

```bash
git clone https://github.com/TUO_USERNAME/pge-ls.git
cd pge-ls
pip install pygls lsprotocol pytest
python -m pytest tests/ -q
```

### Aggiornare e ri-pacchettizzare

```bash
cp server.py vscode-client/server.py
cp -r granular_ls vscode-client/granular_ls
cd vscode-client
npm install
npx @vscode/vsce package --no-dependencies
```

---

## Licenza

MIT
