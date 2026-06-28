'use strict';

// Client Pulsar per PGE-ls.
//
// server.py e' editor-agnostic: gira gia' su VSCode. Qui lo si avvia tramite
// AutoLanguageClient (il fork Pulsar di atom-languageclient), che fornisce
// gratuitamente autocompletamento, hover e diagnostica via LSP standard.
//
// I file Python (server.py, granular_ls/, lo snapshot dello schema) vengono
// copiati accanto a questo modulo da `bash build.sh --pulsar`: vanno sempre
// modificate le copie nella root del repo, mai quelle bundled qui.

const path = require('path');
const cp = require('child_process');
const { AutoLanguageClient } = require('@savetheclocktower/atom-languageclient');

// Il server va attivato solo sui file PGE_*.yaml / PGE_*.yml, come nel client
// VSCode (isPgeFile in clients/vscode/extension.js). La sola grammatica YAML
// attiverebbe il server su qualsiasi documento .yaml.
function isPgeFile(filePath) {
  if (!filePath) return false;
  const base = path.basename(filePath);
  return base.startsWith('PGE_') &&
         (base.endsWith('.yaml') || base.endsWith('.yml'));
}

class PGELanguageClient extends AutoLanguageClient {
  getGrammarScopes() { return ['source.yaml', 'text.yaml']; }
  getLanguageName() { return 'PGE-YAML'; }
  getServerName() { return 'PGE Language Server'; }
  getConnectionType() { return 'stdio'; }

  // Restringe l'avvio ai soli file PGE_*.yaml: senza questo override il server
  // partirebbe per qualunque editor con grammatica YAML.
  shouldStartForEditor(editor) {
    return super.shouldStartForEditor(editor) && isPgeFile(editor.getPath());
  }

  startServerProcess(projectPath) {
    const pythonPath = atom.config.get('pge-ls.pythonPath') || 'python';
    const serverScript = path.join(__dirname, '..', 'server.py');

    // Stessi flag del client VSCode: --src (sorgente PGE live) oppure
    // --snapshot (JSON alternativo). Senza nessuno dei due, server.py carica
    // lo schema_snapshot.json bundled accanto a se' se presente.
    const args = [serverScript];
    const granularSrcPath = atom.config.get('pge-ls.granularSrcPath');
    if (granularSrcPath) args.push('--src', granularSrcPath);
    const snapshotPath = atom.config.get('pge-ls.snapshotPath');
    if (snapshotPath) args.push('--snapshot', snapshotPath);

    // logger e' impostato da activate() prima di questo metodo nel flow reale;
    // l'optional chaining lo rende invocabile anche in isolamento (e2e).
    this.logger?.debug(`PGE LS: ${pythonPath} ${args.join(' ')}`);

    const childProcess = cp.spawn(pythonPath, args, {
      cwd: projectPath || path.join(__dirname, '..'),
      env: process.env,
    });
    childProcess.on('error', err =>
      atom.notifications.addError(
        'PGE Language Server: impossibile avviare il processo Python.',
        {
          dismissable: true,
          description:
            'Comando: `' + pythonPath + '`. Verifica `pge-ls.pythonPath` ' +
            'nelle impostazioni del pacchetto.\n\n' + err.message,
        }
      )
    );
    return childProcess;
  }
}

module.exports = new PGELanguageClient();
