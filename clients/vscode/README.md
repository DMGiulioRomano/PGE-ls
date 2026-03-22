# PGE Language Server

Autocompletamento intelligente, documentazione hover e diagnostica in tempo reale per i file di configurazione `PGE_*.yaml` del **Python Granular Engine**.

## Funzionalità

- **Autocompletamento** — parametri stream, blocchi (grain, pointer, pitch, dephase), envelope con 11 formati
- **Snippet envelope dinamici** — i valori Y usano i bounds del parametro, end_time dalla durata dello stream
- **Hover** — documentazione su ogni parametro con range, variation mode, gruppo esclusivo
- **Diagnostica** — errori bounds, exclusive group (ratio/semitones, density/fill_factor), campi obbligatori mancanti, chiavi duplicate, bounds envelope
- **Go to file** — `Cmd+Click` sul valore di `sample:` apre il file audio in `refs/`

## Requisiti

- Python 3.10+
- Dipendenze Python: `pygls>=1.3.1` e `lsprotocol`

Installa le dipendenze:

```bash
pip install pygls lsprotocol
```

## Impostazioni

| Impostazione | Default | Descrizione |
|---|---|---|
| `pgeLs.pythonPath` | `python` | Percorso all'interprete Python |
| `pgeLs.granularSrcPath` | `` | Percorso alla cartella `src` del progetto |
| `pgeLs.snapshotPath` | `` | Percorso a uno snapshot JSON alternativo |

### Configurazione raccomandata

Apri `Preferences > Settings`, cerca `pgeLs` e imposta:

```json
{
  "pgeLs.pythonPath": "python3.11",
  "pgeLs.granularSrcPath": "/path/to/easy/src"
}
```

Oppure nel `settings.json` del workspace (`.vscode/settings.json`):

```json
{
  "pgeLs.granularSrcPath": "${workspaceFolder}/../easy/src"
}
```

## Installazione

### Dal file .vsix (uso personale)

```bash
code --install-extension pge-ls-0.1.0.vsix
```

Oppure `Cmd+Shift+P` → `Extensions: Install from VSIX...`

## Struttura file YAML supportata

```yaml
streams:
  - stream_id: "nome"
    onset: 0.0
    duration: 30.0
    sample: "file.wav"
    density:
      - [0.0, 100.0]
      - [30.0, 4000.0]
    pitch:
      ratio: 1.5
    pointer:
      loop_start: 0.2
      loop_end: 0.8
      loop_unit: normalized
      start: 0.0
    grain:
      duration: 0.05
    dephase:
      volume: 40.0
```
