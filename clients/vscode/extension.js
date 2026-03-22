'use strict';

const path = require('path');
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

    vscode.window.setStatusBarMessage('$(check) PGE LS attivo', 5000);
}

async function deactivate() {
    if (client) {
        await client.stop();
    }
}

module.exports = { activate, deactivate };
