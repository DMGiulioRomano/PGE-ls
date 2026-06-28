'use strict';

// e2e di protocollo LSP per il client Pulsar.
//
// Non avvia Pulsar: stubba i moduli host (`atom`, `electron`), carica il vero
// `lib/main.js` e usa il suo `startServerProcess()` per lanciare `server.py`
// ESATTAMENTE come farebbe l'editor (stesso comando, stessi flag, stesso cwd).
// Poi parla LSP su stdio: initialize -> initialized -> didOpen(PGE_*.yaml) ->
// completion dentro un blocco `pitch:`, e verifica che il server risponda con
// le unita' pitch statiche (semitones/ratio/...). Quella superficie non dipende
// dal motore PGE, quindi il test e' deterministico anche in CI senza il
// checkout dell'engine.
//
// Exit 0 = ok; exit !=0 = fallimento (usato come gate nel job CI).

const path = require('path');
const os = require('os');
const fs = require('fs');
const Module = require('module');

// --- Stub dei moduli host forniti solo da Pulsar/Electron a runtime ----------
const origLoad = Module._load;
Module._load = function (request) {
  if (request === 'atom') {
    return {
      Point: class { constructor(r, c) { this.row = r; this.column = c; } },
      Range: class {},
      Disposable: class { dispose() {} },
      CompositeDisposable: class { add() {} dispose() {} },
      Emitter: class { on() { return { dispose() {} }; } emit() {} dispose() {} },
      TextBuffer: class {}, File: class {}, Directory: class {},
    };
  }
  if (request === 'electron') {
    return { shell: { openExternal() {}, openPath() {} } };
  }
  return origLoad.apply(this, arguments);
};

const PYTHON = process.env.PGE_PYTHON || process.env.PYTHON || 'python3';
global.atom = {
  config: {
    get(key) {
      if (key === 'pge-ls.pythonPath') return PYTHON;
      return '';
    },
  },
  notifications: { addError() {}, addWarning() {}, addInfo() {} },
};

// Carica il client reale e ottieni il child process che spawnerebbe l'editor.
const client = require('../lib/main.js');

const workDir = fs.mkdtempSync(path.join(os.tmpdir(), 'pge-ls-lsp-e2e-'));
const docPath = path.join(workDir, 'PGE_test.yaml');
const docText =
  'streams:\n' +              // 0
  '  - stream_id: "s1"\n' +   // 1
  '    duration: 10.0\n' +    // 2
  '    pitch:\n' +            // 3
  '      \n';                 // 4 (6 spazi: contesto value dentro pitch)
fs.writeFileSync(docPath, docText);
const docUri = 'file://' + docPath;

function fail(msg) {
  console.error('FAIL:', msg);
  try { child.kill(); } catch (_) {}
  process.exit(1);
}

const child = client.startServerProcess(workDir);
if (!child || !child.pid) fail('startServerProcess non ha restituito un processo');
console.log(`server avviato: pid ${child.pid} (python=${PYTHON})`);

child.on('error', (e) => fail('spawn error: ' + e.message));
child.on('exit', (code, sig) => {
  if (!done) fail(`server uscito prematuramente (code=${code} sig=${sig})`);
});
let stderr = '';
child.stderr.on('data', (d) => { stderr += d.toString(); });

// --- Minimal LSP client su stdio (framing Content-Length) --------------------
let seq = 0;
const pending = new Map();
function send(method, params, isNotification) {
  const msg = { jsonrpc: '2.0', method, params };
  let id;
  if (!isNotification) { id = ++seq; msg.id = id; }
  const body = Buffer.from(JSON.stringify(msg), 'utf8');
  child.stdin.write(`Content-Length: ${body.length}\r\n\r\n`);
  child.stdin.write(body);
  return id;
}
function request(method, params) {
  return new Promise((resolve, reject) => {
    const id = send(method, params, false);
    pending.set(id, { resolve, reject });
  });
}
function notify(method, params) { send(method, params, true); }

let buf = Buffer.alloc(0);
child.stdout.on('data', (chunk) => {
  buf = Buffer.concat([buf, chunk]);
  for (;;) {
    const headerEnd = buf.indexOf('\r\n\r\n');
    if (headerEnd < 0) break;
    const header = buf.slice(0, headerEnd).toString('ascii');
    const m = /content-length:\s*(\d+)/i.exec(header);
    if (!m) { buf = buf.slice(headerEnd + 4); continue; }
    const len = parseInt(m[1], 10);
    const start = headerEnd + 4;
    if (buf.length < start + len) break;
    const json = buf.slice(start, start + len).toString('utf8');
    buf = buf.slice(start + len);
    let msg;
    try { msg = JSON.parse(json); } catch (_) { continue; }
    if (msg.id !== undefined && pending.has(msg.id)) {
      const p = pending.get(msg.id);
      pending.delete(msg.id);
      if (msg.error) p.reject(new Error(JSON.stringify(msg.error)));
      else p.resolve(msg.result);
    }
    // le notifiche (publishDiagnostics, window/logMessage) vengono ignorate
  }
});

const TIMEOUT_MS = 30000;
const timer = setTimeout(() => fail(`timeout dopo ${TIMEOUT_MS}ms (stderr: ${stderr.slice(-400)})`), TIMEOUT_MS);

let done = false;
(async () => {
  const init = await request('initialize', {
    processId: process.pid,
    rootUri: 'file://' + workDir,
    capabilities: {
      textDocument: {
        completion: { completionItem: { snippetSupport: true } },
        hover: {}, publishDiagnostics: {},
      },
    },
    workspaceFolders: [{ uri: 'file://' + workDir, name: 'pge-ls-e2e' }],
  });
  if (!init || !init.capabilities) fail('initialize senza capabilities');
  console.log('initialize OK — capabilities ricevute');

  notify('initialized', {});
  notify('textDocument/didOpen', {
    textDocument: { uri: docUri, languageId: 'yaml', version: 1, text: docText },
  });

  // attesa breve perche' il server processi il didOpen
  await new Promise((r) => setTimeout(r, 400));

  const completion = await request('textDocument/completion', {
    textDocument: { uri: docUri },
    position: { line: 4, character: 6 },
    context: { triggerKind: 1 },
  });

  const items = Array.isArray(completion) ? completion
    : (completion && Array.isArray(completion.items) ? completion.items : []);
  const labels = items.map((it) => it.label);
  console.log(`completion: ${items.length} item — esempi: ${labels.slice(0, 12).join(', ')}`);

  // La superficie pitch statica deve esserci sempre (indipendente dall'engine).
  const attese = ['semitones', 'ratio'];
  const mancanti = attese.filter((k) => !labels.includes(k));
  if (items.length === 0) fail('nessun completion item nel blocco pitch');
  if (mancanti.length) fail(`unita\' pitch attese mancanti: ${mancanti.join(', ')}`);
  console.log('completion contiene le unita\' pitch statiche attese (semitones, ratio)');

  // shutdown pulito
  await request('shutdown', null).catch(() => {});
  notify('exit', null);
  done = true;
  clearTimeout(timer);
  setTimeout(() => { try { child.kill(); } catch (_) {} process.exit(0); }, 200);
})().catch((e) => fail('eccezione: ' + e.message));
