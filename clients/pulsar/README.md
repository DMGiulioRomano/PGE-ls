# PGE Language Server — client Pulsar

Client [Pulsar](https://pulsar-edit.dev/) per **PGE-ls**: autocompletamento,
documentazione hover e diagnostica in tempo reale per i file di configurazione
`PGE_*.yaml` del **Python Granular Engine**.

Il server LSP (`server.py`) e' lo stesso del client VSCode: questo pacchetto lo
avvia tramite `AutoLanguageClient` e ottiene le funzionalita' LSP standard
(completion, hover, diagnostica).

## Requisiti

- [Pulsar](https://pulsar-edit.dev/)
- Python 3.10+ con `pygls>=1.3.1` e `lsprotocol`

```bash
pip install pygls lsprotocol
```

## Installazione

Il pacchetto non e' autosufficiente nel repo: i file Python (`server.py`,
`granular_ls/`, lo `schema_snapshot.json`) vengono copiati qui da `build.sh`.
Dalla root del repo:

```bash
bash build.sh --pulsar
```

Poi collega il pacchetto a Pulsar:

```bash
cd ~/.pulsar/packages && ln -s /percorso/a/PGE-ls/clients/pulsar pge-ls
```

Riavvia Pulsar e apri un file `PGE_*.yaml`.

## Impostazioni

In `Settings > Packages > pge-ls`:

| Impostazione | Default | Descrizione |
|---|---|---|
| `pythonPath` | `python` | Percorso all'interprete Python |
| `granularSrcPath` | `` | Percorso alla cartella `src` del progetto granulare. Se vuoto, usa lo snapshot bundled |
| `snapshotPath` | `` | Percorso a uno snapshot JSON alternativo (usato solo se `granularSrcPath` e' vuoto) |

> Senza `granularSrcPath` ne' uno `snapshotPath`, e senza lo
> `schema_snapshot.json` generato da `build.sh`, il server parte con schema
> vuoto: restano i metadati statici (pitch, voci) ma spariscono i parametri
> schema-driven (`density`, `grain.*`, `pointer.*`, `volume`, bounds di `pan`).

## Attivazione

Il server parte solo sui file il cui nome inizia con `PGE_` e termina in
`.yaml` o `.yml`, esattamente come il client VSCode.

## Test (e2e)

Due livelli, entrambi eseguiti dal job `pulsar-e2e` in CI
(`.github/workflows/ci.yml`):

- **`e2e/lsp-protocol-e2e.js`** (Node, senza GUI) — carica `lib/main.js`, usa
  il suo `startServerProcess()` per lanciare `server.py` come farebbe l'editor e
  fa un handshake LSP reale (`initialize` → `didOpen` di un `PGE_*.yaml` →
  `completion` nel blocco `pitch`), verificando le unità pitch statiche. Veloce,
  deterministico, indipendente dal motore. Richiede il bundle
  (`bash build.sh --pulsar`) e `pygls`/`lsprotocol`:

  ```bash
  bash ../../build.sh --pulsar
  PGE_PYTHON=python3 node e2e/lsp-protocol-e2e.js
  ```

- **`spec/pge-ls-spec.js`** (Jasmine, dentro Pulsar) — attiva il pacchetto, apre
  un `PGE_*.yaml` e verifica che il language server venga avviato dall'editor;
  controlla anche che NON parta per un `.yaml` non-PGE. In CI gira headless via
  `xvfb-run pulsar --test`.
