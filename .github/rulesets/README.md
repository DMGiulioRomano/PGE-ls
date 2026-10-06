# Ruleset

I ruleset non vivono nel repository: vivono nelle impostazioni, e l'unico
posto in cui GitHub li tiene e' il database. Il JSON qui accanto e' percio'
una **copia versionata** di cio' che e' stato importato, non la sorgente da
cui GitHub legge: modificarlo non cambia niente da solo. Sta qui perche'
altrimenti la regola esisterebbe solo in una schermata che nessuno rilegge.

## Che cosa impone

`main-closes-issue.json` pretende che lo status check `closes-issue` sia verde
sul branch di default. Quel check e' il job del workflow
`.github/workflows/pr-closes-issue.yml`, che rifiuta una PR il cui corpo non
dichiari `Closes #N` (o un `No issue: <motivo>`).

**I ruleset non sanno leggere il corpo di una pull request.** Le loro regole
coprono protezioni di branch, check richiesti e pattern sui metadati —
messaggio di commit, email, nome di branch o di tag — e nessuna guarda la
descrizione. Per questo la regola e' in due pezzi: il workflow misura, il
ruleset pretende.

## Come si importa

Settings → Rules → Rulesets → New ruleset → **Import a ruleset** → scegli
`.github/rulesets/main-closes-issue.json`.

Dopo l'import, il nome del check va scelto da una lista che GitHub popola con
i contesti **visti di recente**: se `closes-issue` non c'e' ancora, apri prima
una PR qualunque (basta che il workflow giri una volta), poi importa.

## Il prezzo, che e' meglio sapere prima

Uno status check richiesto non vale solo al merge: vale su **tutto** cio' che
entra nel branch. Con questo ruleset attivo un `git push` diretto su `main`
viene rifiutato, perche' il commit che arriva non ha check che siano passati.
In questo repo il prezzo e' gia' pagato: il `CLAUDE.md` vieta il commit
diretto su `main` per qualunque modifica, documentazione e refusi compresi,
quindi il ruleset non toglie niente che il flusso ammetta. Altrove si paga:
il flusso di `~/.claude/rules/git-workflow.md` ammette il commit diretto su
`main` per le modifiche banali (README, `.gitignore`, refusi), e dopo l'import
quelle passano da una PR come le altre.

Se e' un prezzo troppo alto, le vie sono due e sono entrambe dichiarate:

- **Bypass per se stessi.** Nel ruleset, Bypass list → Add bypass →
  *Repository admin* → *Always*. Il push diretto torna possibile e il merge
  resta possibile anche col check rosso: la regola diventa un promemoria
  rumoroso invece di un cancello. Il check si vede comunque, rosso, prima di
  premere Merge.
- **Non importarlo.** Senza ruleset il check gira e si vede rosso sulla PR, ma
  non impedisce niente. Restano il template, che mette la riga davanti agli
  occhi, e la regola di `~/.claude/rules/git-workflow.md`, che la fa scrivere
  a Claude Code di default: il grosso del lavoro lo fanno quei due, il ruleset
  copre il caso in cui si tira via la riga a mano.
