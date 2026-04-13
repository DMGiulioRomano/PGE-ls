'use strict';

const path = require('path');
const { spawn } = require('child_process');
const vscode = require('vscode');
const {
    LanguageClient,
    TransportKind,
} = require('vscode-languageclient/node');

/** @type {LanguageClient | undefined} */
let client;

function isPgeFile(uri) {
    if (!uri) return false;
    const filename = path.basename(uri.fsPath || uri.toString());
    return filename.startsWith('PGE_') &&
           (filename.endsWith('.yaml') || filename.endsWith('.yml'));
}

/**
 * Ritorna true se la riga corrente e' nel formato 'chiave: '
 * con il cursore dopo i due punti (contesto value, nessun valore ancora).
 */
function isOnValueContext(document, position) {
    const line = document.lineAt(position.line).text;
    const upToCursor = line.substring(0, position.character);
    // Pattern: spazi + chiave + ': ' con cursore alla fine
    return /^\s+[a-zA-Z_][a-zA-Z0-9_]*:\s*$/.test(upToCursor) ||
           /^\s*-\s+[a-zA-Z_][a-zA-Z0-9_]*:\s*$/.test(upToCursor);
}

async function activate(context) {
    const config = vscode.workspace.getConfiguration('pgeLs');
    const pythonPath = config.get('pythonPath') || 'python';
    const serverScript = path.join(context.extensionPath, 'server.py');

    const serverArgs = [serverScript];
    const granularSrcPath = config.get('granularSrcPath');
    if (granularSrcPath) serverArgs.push('--src', granularSrcPath);
    const snapshotPath = config.get('snapshotPath');
    if (snapshotPath) serverArgs.push('--snapshot', snapshotPath);

    const serverOptions = {
        command: pythonPath,
        args: serverArgs,
        transport: TransportKind.stdio,
        options: { env: process.env },
    };

    const clientOptions = {
        documentSelector: [
            { scheme: 'file', language: 'yaml', pattern: '**/PGE_*.yaml' },
            { scheme: 'file', language: 'yaml', pattern: '**/PGE_*.yml' },
        ],
        synchronize: {
            fileEvents: vscode.workspace.createFileSystemWatcher(
                '**/{PGE_*.yaml,PGE_*.yml}'
            ),
        },
        outputChannelName: 'PGE Language Server',
    };

    client = new LanguageClient(
        'pge-ls',
        'PGE Language Server',
        serverOptions,
        clientOptions,
    );

    await client.start();

    // Configura l'editor per i file PGE_*.yaml:
    // - Tab NON accetta i suggerimenti (solo Invio lo fa)
    // - acceptSuggestionOnCommitCharacter disabilitato: nessun
    //   carattere speciale accetta il suggerimento automaticamente
    const editorConfig = vscode.workspace.getConfiguration('editor',
        { languageId: 'yaml' });
    await editorConfig.update(
        'tabCompletion',
        'off',
        vscode.ConfigurationTarget.Global,
        true   // languageId-specific
    );
    await editorConfig.update(
        'acceptSuggestionOnCommitCharacter',
        false,
        vscode.ConfigurationTarget.Global,
        true
    );
    await editorConfig.update(
        'acceptSuggestionOnEnter',
        'on',
        vscode.ConfigurationTarget.Global,
        true
    );

    // -------------------------------------------------------------------------
    // Trigger automatico degli snippet envelope
    //
    // Il menu envelope deve aprirsi in questi scenari:
    //
    // 1. Accettazione di un parametro: quando l'utente sceglie 'density' dal
    //    menu, il server inserisce 'density: ' e allega command:triggerSuggest.
    //    Questo e' gia' gestito lato server (command sul CompletionItem).
    //
    // 2. Newline dopo 'chiave: ': l'utente scrive manualmente 'pan: ' e preme
    //    Invio. Dobbiamo rilevare se la riga precedente era in contesto value.
    //
    // 3. Delete/Backspace: l'utente cancella caratteri e si ritrova in
    //    contesto value (riga tipo 'density: ' con cursore alla fine).
    //
    // 4. Tab per indentare: gia' gestito dal listener precedente.
    // -------------------------------------------------------------------------
    const changeDisposable = vscode.workspace.onDidChangeTextDocument(event => {
        const editor = vscode.window.activeTextEditor;
        if (!editor) return;
        if (editor.document !== event.document) return;
        if (!isPgeFile(editor.document.uri)) return;

        for (const change of event.contentChanges) {
            const text = change.text;
            const cursorPos = editor.selection.active;

            // Caso 1: Tab / doppio spazio su riga vuota (indentazione)
            if (text === '  ' || text === '    ' || text === '\t') {
                setTimeout(() => {
                    vscode.commands.executeCommand('editor.action.triggerSuggest');
                }, 50);
                break;
            }

            // Caso 2: Newline (Invio)
            // Dopo il newline, controlla se la riga PRECEDENTE era in value context.
            // Es: l'utente ha scritto 'density: ' e premi Invio -> trigger sulla
            // nuova riga (ma in realta' vogliamo il trigger sulla riga precedente
            // prima che l'utente prema invio, gestito dal command sul CompletionItem).
            // Qui gestiamo solo il newline generico per il trigger normale.
            if (text === '\n' || text === '\r\n') {
                setTimeout(() => {
                    vscode.commands.executeCommand('editor.action.triggerSuggest');
                }, 100);
                break;
            }

            // Caso 3: Delete o Backspace (testo cancellato, nulla inserito)
            if (text === '' && change.rangeLength > 0) {
                setTimeout(() => {
                    const pos = editor.selection.active;
                    const doc = editor.document;
                    // Se dopo la cancellazione siamo in contesto value, apri il menu
                    if (isOnValueContext(doc, pos)) {
                        vscode.commands.executeCommand('editor.action.triggerSuggest');
                    } else {
                        // Altrimenti trigger generico (per block keys etc.)
                        const lineText = doc.lineAt(pos.line).text.trim();
                        if (lineText === '') {
                            vscode.commands.executeCommand('editor.action.triggerSuggest');
                        }
                    }
                }, 50);
                break;
            }

            // Caso 4: L'utente ha scritto ': ' manualmente
            // Quando digita il punto e virgola + spazio di una chiave
            if (text === ' ' || text === ': ') {
                setTimeout(() => {
                    const pos = editor.selection.active;
                    const doc = editor.document;
                    if (isOnValueContext(doc, pos)) {
                        vscode.commands.executeCommand('editor.action.triggerSuggest');
                    }
                }, 50);
                break;
            }
        }
    });

    context.subscriptions.push(changeDisposable);

    // -------------------------------------------------------------------------
    // Comando: inserisci envelope con N punti equidistanziati
    // Palette: "PGE: Inserisci envelope con N punti"
    // -------------------------------------------------------------------------
    const envelopeDisposable = vscode.commands.registerCommand(
        'pge-ls.insertEnvelope',
        async () => {
            const editor = vscode.window.activeTextEditor;
            if (!editor || !isPgeFile(editor.document.uri)) {
                vscode.window.showWarningMessage(
                    'Apri un file PGE_*.yaml per usare questo comando.'
                );
                return;
            }

            const input = await vscode.window.showInputBox({
                title: 'Envelope equidistanziato',
                prompt: 'Numero di breakpoints da inserire (minimo 2)',
                placeHolder: 'es. 5',
                validateInput: v => {
                    const n = parseInt(v, 10);
                    if (isNaN(n) || n < 2) return 'Inserisci un numero intero >= 2';
                    return null;
                },
            });
            if (!input) return;

            const nPoints = parseInt(input, 10);
            const pos = editor.selection.active;

            let insertText;
            try {
                insertText = await client.sendRequest('workspace/executeCommand', {
                    command: 'pge.buildEnvelope',
                    arguments: [
                        editor.document.uri.toString(),
                        pos.line,
                        pos.character,
                        nPoints,
                    ],
                });
            } catch (err) {
                vscode.window.showErrorMessage(`PGE LS: errore generazione envelope — ${err.message}`);
                return;
            }

            if (!insertText) return;

            await editor.edit(eb => eb.insert(pos, insertText));
        }
    );
    context.subscriptions.push(envelopeDisposable);

    // -------------------------------------------------------------------------
    // Comando: apri GUI grafica per disegnare envelope
    // Palette: "PGE: Apri editor envelope grafico"
    // -------------------------------------------------------------------------
    const GUI_TIMEOUT_MS = 300_000;   // 5 minuti

    /**
     * Lancia envelope_gui.py come subprocess con args arbitrari.
     * Risolve con il testo stdout (o '') se la GUI viene chiusa senza output.
     * Rigetta in caso di errore subprocess.
     */
    function runEnvelopeGui(pythonPath, args) {
        return new Promise((resolve, reject) => {
            const child = spawn(pythonPath, args);

            let stdout = '';
            let stderr = '';
            child.stdout.on('data', d => { stdout += d.toString(); });
            child.stderr.on('data', d => { stderr += d.toString(); });

            const timer = setTimeout(() => {
                child.kill();
                resolve('');   // timeout: nessun inserimento
            }, GUI_TIMEOUT_MS);

            child.on('close', code => {
                clearTimeout(timer);
                if (code !== 0) {
                    const msg = stderr.trim() || `exit code ${code}`;
                    reject(new Error(msg));
                } else {
                    resolve(stdout.trim());
                }
            });

            child.on('error', err => {
                clearTimeout(timer);
                reject(err);
            });
        });
    }

    const guiEditorDisposable = vscode.commands.registerCommand(
        'pge-ls.openEnvelopeEditor',
        async () => {
            const editor = vscode.window.activeTextEditor;
            if (!editor || !isPgeFile(editor.document.uri)) {
                vscode.window.showWarningMessage(
                    'Apri un file PGE_*.yaml per usare questo comando.'
                );
                return;
            }

            const pos = editor.selection.active;

            // 1. Chiedi al server il contesto (bounds + end_time)
            let ctx;
            try {
                ctx = await client.sendRequest('workspace/executeCommand', {
                    command: 'pge.getEnvelopeContext',
                    arguments: [
                        editor.document.uri.toString(),
                        pos.line,
                        pos.character,
                    ],
                });
            } catch (err) {
                vscode.window.showErrorMessage(`PGE LS: errore contesto envelope — ${err.message}`);
                return;
            }

            // 2. Lancia la GUI
            const config = vscode.workspace.getConfiguration('pgeLs');
            const guiPythonPath = config.get('guiPythonPath') || config.get('pythonPath') || 'python';
            const guiScript  = path.join(context.extensionPath, 'envelope_gui.py');

            let result;
            try {
                result = await runEnvelopeGui(guiPythonPath, [
                    guiScript,
                    `--ymin=${ctx.y_min}`,
                    `--ymax=${ctx.y_max}`,
                    `--end_time=${ctx.end_time}`,
                ]);
            } catch (err) {
                const msg = err.message || String(err);
                const isTkMissing = msg.includes('tkinter') || msg.includes('python3-tk');
                const detail = isTkMissing
                    ? 'tkinter non disponibile. Su macOS: brew install python-tk  — poi imposta pgeLs.guiPythonPath sul Python con tkinter.'
                    : msg;
                vscode.window.showErrorMessage(`PGE LS: errore GUI envelope — ${detail}`);
                return;
            }

            // 3. Inserisci il risultato (se non vuoto)
            if (result) {
                await editor.edit(eb => eb.insert(pos, ' ' + result));
            }
        }
    );
    context.subscriptions.push(guiEditorDisposable);

    // -------------------------------------------------------------------------
    // Cmd+Click su un valore envelope → apre la GUI pre-popolata con i punti
    // esistenti. Dopo la modifica, il testo originale viene sostituito in-place.
    // -------------------------------------------------------------------------

    /**
     * Lancia la GUI con dati pre-popolati e sostituisce il testo nel documento.
     * @param {object} envelopeData  risposta di pge.getEnvelopeAtCursor
     * @param {vscode.TextDocument} document
     */
    async function openEnvelopeEditorForEditing(envelopeData, document) {
        const cfg = vscode.workspace.getConfiguration('pgeLs');
        const guiPy     = cfg.get('guiPythonPath') || cfg.get('pythonPath') || 'python';
        const guiScript = path.join(context.extensionPath, 'envelope_gui.py');

        const args = [
            guiScript,
            `--ymin=${envelopeData.y_min}`,
            `--ymax=${envelopeData.y_max}`,
            `--end_time=${envelopeData.end_time}`,
            `--struttura=${envelopeData.struttura}`,
        ];
        if (envelopeData.struttura === 'misto') {
            args.push(`--segments=${JSON.stringify(envelopeData.segments)}`);
        } else {
            args.push(`--points=${JSON.stringify(envelopeData.points)}`);
            args.push(`--interp=${envelopeData.interp}`);
            args.push(`--loop-dist=${envelopeData.loop_dist}`);
            args.push(`--nreps=${envelopeData.n_reps}`);
            args.push(`--ratio=${envelopeData.ratio}`);
            args.push(`--exponent=${envelopeData.exponent}`);
        }

        let result;
        try {
            result = await runEnvelopeGui(guiPy, args);
        } catch (err) {
            vscode.window.showErrorMessage(`PGE LS: errore GUI envelope — ${err.message}`);
            return;
        }

        if (!result) return;   // annullato dall'utente

        // Sostituisce il valore originale (inline o block multi-riga)
        const r = envelopeData.replace_range;
        const isBlock = r.end_line !== undefined && r.end_line !== r.line;
        const endLine = r.end_line !== undefined ? r.end_line : r.line;
        const replaceRange = new vscode.Range(
            new vscode.Position(r.line, r.start_char),
            new vscode.Position(endLine, r.end_char),
        );
        const edit = new vscode.WorkspaceEdit();
        // Per block YAML start_char supera la fine della riga chiave (es. "key:" senza spazio
        // inline): VSCode clamp all'EOL, quindi il valore si incollerebbe senza spazio.
        // Prepend ' ' come fa il path di nuovo inserimento.
        edit.replace(document.uri, replaceRange, isBlock ? ' ' + result : result);
        await vscode.workspace.applyEdit(edit);
    }

    const pgeDocSelector = [
        { scheme: 'file', language: 'yaml', pattern: '**/PGE_*.yaml' },
        { scheme: 'file', language: 'yaml', pattern: '**/PGE_*.yml' },
    ];

    const definitionDisposable = vscode.languages.registerDefinitionProvider(
        pgeDocSelector,
        {
            async provideDefinition(document, position) {
                if (!isPgeFile(document.uri)) return null;

                let envelopeData;
                try {
                    envelopeData = await client.sendRequest('workspace/executeCommand', {
                        command: 'pge.getEnvelopeAtCursor',
                        arguments: [
                            document.uri.toString(),
                            position.line,
                            position.character,
                        ],
                    });
                } catch {
                    return null;
                }

                if (!envelopeData) return null;

                // Apre la GUI in background (non blocca il definition provider)
                openEnvelopeEditorForEditing(envelopeData, document).catch(err =>
                    vscode.window.showErrorMessage(`PGE LS: ${err.message}`)
                );

                // Ritorna la posizione corrente come "definizione di se stesso"
                // per evitare il toast "No definition found"
                return new vscode.Location(document.uri, position);
            },
        }
    );
    context.subscriptions.push(definitionDisposable);

    // -------------------------------------------------------------------------
    // Semantic token colors: registra il colore per 'pge-normalized'
    // nel workspace settings (scrive in .vscode/settings.json).
    // Eseguito solo se la chiave non esiste ancora, per non sovrascrivere
    // personalizzazioni dell'utente.
    // -------------------------------------------------------------------------
    try {
        const stConfig = vscode.workspace.getConfiguration(
            'editor.semanticTokenColorCustomizations'
        );
        const existing = stConfig.get('rules') || {};
        if (!existing['pge-normalized'] || !existing['pge-block-key']) {
            const merged = Object.assign({}, existing, {
                'pge-normalized': { foreground: '#4ec9b0' },
                'pge-block-key':  { foreground: '#c586c0' },
            });
            await vscode.workspace
                .getConfiguration('editor')
                .update(
                    'semanticTokenColorCustomizations',
                    { rules: merged },
                    vscode.ConfigurationTarget.Workspace,
                );
        }
    } catch (_) {
        // Ignora errori (es. workspace read-only)
    }

    vscode.window.setStatusBarMessage('$(check) PGE LS attivo', 5000);
}

async function deactivate() {
    if (client) {
        await client.stop();
    }
}

module.exports = { activate, deactivate };
