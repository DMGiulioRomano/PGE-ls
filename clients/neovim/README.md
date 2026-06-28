# PGE Language Server — client Neovim

Collega Neovim (es. dentro WezTerm) allo stesso server LSP usato dal client
VSCode: completion, hover e diagnostica sui file `PGE_*.yaml` / `PGE_*.yml` del
**Python Granular Engine**.

A differenza di VSCode il client Neovim **non bundla** il server: punta
direttamente a `server.py` nella root del repo, eseguito via il `.venv/` locale.

## Prerequisiti

- **Neovim >= 0.8** (per `vim.lsp.start`; `reuse_client` esplicito su >= 0.10)
- **Python >= 3.10**
- Lo **snapshot dello schema** `clients/vscode/schema_snapshot.json` per avere
  l'autocompletion. Si genera con `bash build.sh` dalla root del repo (richiede
  un checkout del Python Granular Engine o `PGE_SRC`). In alternativa vedi
  [Configurazione avanzata](#configurazione-avanzata-schema-live-pge_src).

## Installazione (un comando)

Dalla root del repo:

```bash
bash clients/neovim/setup.sh
```

Lo script è **idempotente**: rieseguirlo rigenera la config senza duplicare
l'aggancio in `init.lua`. Cosa fa:

1. rileva l'OS (macOS / Linux) e individua la config Neovim rispettando
   `$XDG_CONFIG_HOME` (default `~/.config/nvim`);
2. verifica Neovim >= 0.8 e Python >= 3.10;
3. crea/aggiorna il virtualenv `.venv/` nella root e installa le dipendenze;
4. risolve le path assolute (python del venv, `server.py`, snapshot/src);
5. genera `~/.config/nvim/lua/pge-ls.lua` da `pge-ls.lua.template`;
6. aggiunge `require('pge-ls')` a `init.lua` se manca, senza duplicati.

In alternativa, dalla root: `bash build.sh --neovim`.

> **`init.vim` invece di `init.lua`?** Lo script aggancia solo `init.lua`. Se usi
> `init.vim`, aggiungi a mano la riga: `lua require('pge-ls')`.

## Verifica

1. Apri un file `PGE_*.yaml` in Neovim.
2. `:LspInfo` deve mostrare il client **`pge-ls`** attivo (attached al buffer).
3. Completion via omnifunc: `<C-x><C-o>` in inserimento (o il tuo completion
   plugin, es. nvim-cmp/blink, configurato sull'LSP).
4. Hover: `:lua vim.lsp.buf.hover()` (o `K` se mappato).

L'LSP si attiva **solo** sui file il cui nome inizia con `PGE_` ed estensione
`.yaml`/`.yml`; gli altri YAML non vengono toccati.

## Configurazione avanzata (schema live, `PGE_SRC`)

Per sviluppare sul Python Granular Engine e riflettere subito le modifiche allo
schema senza rigenerare lo snapshot, esporta `PGE_SRC` con la directory `src`
del progetto **prima** del setup:

```bash
PGE_SRC=/path/a/PythonGranularEngine/src bash clients/neovim/setup.sh
```

In questo caso la config generata avvia il server con `--src <PGE_SRC>` (schema
importato a runtime) invece di `--snapshot`. La distinzione è automatica nel
template: una **directory** baked → `--src`, un **file `.json`** → `--snapshot`.

`PGE_DEPS=skip` salta l'installazione delle dipendenze (`pip install -r
requirements.txt`): utile per rilanciare velocemente il setup quando il `.venv/`
è già pronto, o in CI dove le dipendenze sono pre-provviste.

## Test end-to-end (headless)

Dopo aver eseguito `setup.sh`, puoi verificare in modo automatico che il client
si avvii correttamente:

```bash
bash clients/neovim/tests/run-e2e.sh
```

Lancia Neovim in modalità headless (`clients/neovim/tests/e2e.lua`) e verifica
che il client LSP `pge-ls` si attivi **solo** sui file `PGE_*.yaml` e completi
l'handshake LSP via stdio. È lo stesso test che gira in CI (job `neovim-e2e`),
dove Neovim stable viene installato dal tarball ufficiale.

## Disinstallazione

```bash
rm ~/.config/nvim/lua/pge-ls.lua
```

e rimuovi la riga `require('pge-ls')` (e il commento sopra) da
`~/.config/nvim/init.lua`.

## Windows (setup manuale)

Non c'è uno script per Windows: lo script bash copre solo macOS e Linux. Per
configurare Neovim su Windows a mano:

1. **Dipendenze Python**: crea il venv e installa le deps dalla root del repo:

   ```powershell
   python -m venv .venv
   .\.venv\Scripts\pip install -r requirements.txt
   ```

2. **Genera lo snapshot** (se hai il Python Granular Engine): `bash build.sh`
   sotto Git Bash/WSL, oppure usa `PGE_SRC`.

3. **Config Lua**: crea `%LOCALAPPDATA%\nvim\lua\pge-ls.lua` copiando
   `pge-ls.lua.template` e sostituendo i placeholder con path assolute. Usa
   **forward slash** (`/`) anche su Windows: Neovim li accetta e non vanno
   escapati come i backslash.

   ```lua
   local python = [[C:/path/al/repo/.venv/Scripts/python.exe]]
   local server = [[C:/path/al/repo/server.py]]
   local schema = [[C:/path/al/repo/clients/vscode/schema_snapshot.json]]
   ```

4. **Aggancio**: aggiungi `require('pge-ls')` al tuo `init.lua`
   (`%LOCALAPPDATA%\nvim\init.lua`).

## File di questa cartella

| File | Ruolo |
|------|------|
| `pge-ls.lua.template` | Template Lua versionato; placeholder `%%PYTHON%%`, `%%SERVER%%`, `%%SNAPSHOT%%` sostituiti dallo script. Autocmd su `PGE_*.yaml` → `vim.lsp.start`. |
| `setup.sh` | Script di automazione macOS/Linux idempotente. |
| `tests/run-e2e.sh` + `tests/e2e.lua` | Test end-to-end headless (anche in CI). |
| `README.md` | Questo file. |

Il file generato `~/.config/nvim/lua/pge-ls.lua` **non** è versionato nel repo:
contiene path assolute specifiche della macchina.
