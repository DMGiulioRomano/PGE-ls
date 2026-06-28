'use strict';

// e2e dentro Pulsar reale (eseguito da `pulsar --test clients/pulsar/spec`).
//
// Attiva il pacchetto, apre un file PGE_*.yaml e verifica che il language
// server venga effettivamente avviato dall'editor (ServerManager._activeServers
// si popola solo dopo che il server e' connesso e inizializzato). Verifica
// inoltre che NON parta per un .yaml qualsiasi.
//
// Richiede python con pygls/lsprotocol e il bundle (server.py + granular_ls/)
// generato da `build.sh --pulsar`. Il path di python arriva da PGE_PYTHON.

const path = require('path');
const os = require('os');
const fs = require('fs');

const PGE_DOC =
  'streams:\n' +
  '  - stream_id: "s1"\n' +
  '    duration: 10.0\n' +
  '    pitch:\n' +
  '      \n';

describe('pge-ls — client Pulsar (e2e)', () => {
  let workDir;

  beforeEach(() => {
    workDir = fs.mkdtempSync(path.join(os.tmpdir(), 'pge-ls-spec-'));
    atom.config.set('pge-ls.pythonPath', process.env.PGE_PYTHON || 'python3');

    waitsForPromise(() => atom.packages.activatePackage('language-yaml'));
    waitsForPromise(() => atom.packages.activatePackage('pge-ls'));
  });

  it('si attiva e avvia il language server per un file PGE_*.yaml', () => {
    const docPath = path.join(workDir, 'PGE_test.yaml');
    fs.writeFileSync(docPath, PGE_DOC);

    let mainModule;
    let editor;

    runs(() => {
      mainModule = atom.packages.getActivePackage('pge-ls').mainModule;
      expect(mainModule).toBeTruthy();
    });

    waitsForPromise(() => atom.workspace.open(docPath).then((e) => { editor = e; }));

    runs(() => {
      expect(path.basename(editor.getPath())).toBe('PGE_test.yaml');
      // l'override di attivazione deve riconoscere il file
      expect(mainModule.shouldStartForEditor(editor)).toBe(true);
    });

    // Aprendo l'editor, il ServerManager avvia il server in modo asincrono:
    // _activeServers si popola solo a server avviato e inizializzato.
    waitsFor('avvio del language server', 45000, () => {
      const servers = mainModule._serverManager.getActiveServers();
      return servers && servers.length > 0;
    });

    runs(() => {
      const servers = mainModule._serverManager.getActiveServers();
      expect(servers.length).toBeGreaterThan(0);
    });
  });

  it('non avvia il server per un .yaml non-PGE', () => {
    const otherPath = path.join(workDir, 'other.yaml');
    fs.writeFileSync(otherPath, 'foo: bar\n');

    let mainModule;
    let editor;

    runs(() => {
      mainModule = atom.packages.getActivePackage('pge-ls').mainModule;
    });

    waitsForPromise(() => atom.workspace.open(otherPath).then((e) => { editor = e; }));

    runs(() => {
      expect(mainModule.shouldStartForEditor(editor)).toBe(false);
    });
  });
});
