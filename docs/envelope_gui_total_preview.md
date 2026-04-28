# Anteprima Totale — gestione del tempo

## Asse X: tempo assoluto globale

In anteprima totale l'asse X rappresenta **tempo assoluto** (non locale al segmento).
`xlim` viene impostato a `[0, total_end * 1.02]` dove:

```
total_end = max(fine dell'ultimo segmento, self.end_time)
```

La linea rossa fissa indica `self.end_time` (fine stream dichiarata dall'utente).

---

## Come ogni tipo di segmento contribuisce all'asse X

### Segmento Breakpoints

I punti hanno già coordinate X in **tempo assoluto**. La fine del segmento è:

```python
max(t for t, _ in seg['points'])
```

### Segmento Loop

Usa due campi distinti:

| Campo | Significato |
|-------|-------------|
| `abs_start` | Inizio assoluto del loop nell'asse temporale globale |
| `duration` | Durata totale del loop (somma di tutte le ripetizioni) |

I punti interni sono in **percentuale `[0, 100]`** e vengono espansi in coordinate assolute al rendering (`_get_total_preview_data`):

```python
fracs = [t / 100.0 for t in ts]
rep_ts = [abs_start + t_s + f * rep_dur for f in fracs]
```

---

## Decorazioni visive

| Elemento | Colore | Significato |
|----------|--------|-------------|
| Bande `axvspan` | colori distinti per segmento | estensione temporale di ogni segmento |
| Linee verticali tratteggiate | giallo `_SEL` | confini interni draggabili tra segmenti |
| Linea verticale continua | rosso `#ff4444` | fine stream (`self.end_time`) |
| Etichette S1, S2, ... | bianco, alpha 0.6 | identificano il segmento sopra la banda |

---

## Boundary drag

Trascinare una linea gialla sposta il confine tra segmento `i` (sinistro) e `i+1` (destro).
I vincoli sono: `prev_boundary + eps < new_x < next_boundary - eps`.

| Tipo segmento | Lato sinistro | Lato destro |
|---------------|---------------|-------------|
| **Breakpoints** | punti rescalati proporzionalmente verso `new_x` | tutti i punti traslati di `delta = new_x - old_start` |
| **Loop** | `duration = new_x - abs_start` | `abs_start = new_x` |

Implementato in `_apply_boundary_drag` (envelope_gui.py).

---

## Calcolo `total_end` e confini interni

```python
# Fine assoluta dell'ultimo segmento
def _total_end_time(self) -> float:
    for seg in self._segments:
        if seg['type'] == 'loop':
            end = max(end, seg['abs_start'] + seg['duration'])
        else:
            end = max(end, max(t for t, v in seg['points']))

# Posizioni X dei confini interni (len = n_segs - 1)
def _seg_boundary_xs(self) -> list:
    for seg in self._segments[:-1]:
        if seg['type'] == 'loop':
            x = seg['abs_start'] + seg['duration']
        else:
            x = max(t for t, _ in seg['points'])
```
