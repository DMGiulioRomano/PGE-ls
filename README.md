# pge-ls

Language server (client VSCode e Pulsar) per i file di configurazione `PGE_*.yaml` del [Python Granular Engine](https://github.com/DMGiulioRomano/PythonGranularEngine).

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
    pitch_units.py
    voice_strategies.py
    schema_bridge.py
    yaml_analyzer.py
  tests/                # suite TDD (pytest)
  server.py             # entry point pygls
  envelope_gui.py       # GUI envelope (matplotlib), lanciata dal client
  setup.sh              # installazione per sviluppo
  build.sh              # sync delle copie bundled + packaging del .vsix
  clients/
    vscode/             # estensione VSCode
      extension.js      # client LSP
      package.json      # manifesto estensione
      icon.png
      README.md
      # server.py + granular_ls/ qui sono copie bundled generate da build.sh
      # (gitignored): modifica sempre le copie root, mai queste.
    pulsar/             # pacchetto Pulsar
      lib/main.js       # client LSP (AutoLanguageClient)
      package.json      # manifesto pacchetto
      README.md
      # server.py + granular_ls/ qui sono copie bundled generate da build.sh
      # (gitignored): modifica sempre le copie root, mai queste.
```

---

## Installazione rapida

Scarica il `.vsix` dalla pagina [Releases](../../releases) (la versione segue `clients/vscode/package.json`, attualmente `0.3.0`):

```bash
code --install-extension pge-ls-0.3.0.vsix
```

Installa le dipendenze Python:

```bash
pip install pygls lsprotocol
```

Configura in `Preferences > Settings` → `pgeLs`:

| Impostazione | Descrizione |
|---|---|
| `pgeLs.pythonPath` | Percorso a Python (es. `python3.11`) |
| `pgeLs.granularSrcPath` | Percorso alla cartella `src` del progetto granulare (abilita la superficie schema-driven: density, grain, pointer, volume, ...) |

> Senza `granularSrcPath` (o uno `snapshotPath`) il server parte con schema vuoto:
> restano i metadati statici (pitch, voci), ma spariscono i parametri
> schema-driven. Vedi `clients/vscode/package.json` per tutte le impostazioni.

---

## Sviluppo

```bash
git clone https://github.com/DMGiulioRomano/PGE-ls.git
cd PGE-ls
pip install pygls lsprotocol pytest PyYAML
python -m pytest tests/ -q
```

Il test di parità con PGE (`tests/test_pge_parity.py`) verifica che i mirror
statici (nomi strategy, accordi, bounds delle unità pitch, nomi finestra) non
divergano dal motore. Salta se PGE non è disponibile; per eseguirlo, clona
`PythonGranularEngine` come sibling oppure imposta `PGE_SRC`:

```bash
PGE_SRC=/percorso/a/PythonGranularEngine/src python -m pytest tests/test_pge_parity.py -q
```

### Aggiornare e ri-pacchettizzare

`build.sh` sincronizza le copie bundled (`server.py`, `envelope_gui.py`,
`granular_ls/`, lo `schema_snapshot.json`) dentro la cartella del client e
pacchettizza:

```bash
bash build.sh             # build del client VSCode (.vsix)
bash build.sh --install   # build e installazione diretta in VSCode
bash build.sh --pulsar    # build del pacchetto Pulsar (clients/pulsar/)
bash build.sh --all       # build di entrambi i client
```

Modifica sempre le copie nella root del repo, mai quelle sotto `clients/vscode/`
(vengono sovrascritte da `build.sh`).

---

## Licenza

MIT
