-- e2e.lua — asserzioni headless per il client Neovim di PGE-ls.
--
-- Eseguito da run-e2e.sh dentro `nvim --headless`: la init reale dell'utente ha
-- già registrato l'autocmd via require('pge-ls'). Qui creiamo due file su disco
-- (uno PGE_*, uno no), li apriamo e verifichiamo che il client LSP 'pge-ls'
-- parta SOLO sul file PGE_* (R2) e che completi l'handshake LSP via stdio (R1).
--
-- Esce con codice != 0 (cquit) al primo fallimento, 0 (qa!) se tutto ok.

local function die(code, msg)
  if msg then io.stderr:write('E2E: ' .. msg .. '\n') end
  vim.cmd('cquit ' .. code)
end

-- vim.lsp.get_clients (>=0.10) con fallback a get_active_clients (0.8/0.9).
local function get_clients(filter)
  local fn = vim.lsp.get_clients or vim.lsp.get_active_clients
  return fn(filter)
end

local function pge_clients(filter)
  local out = {}
  for _, c in ipairs(get_clients(filter)) do
    if c.name == 'pge-ls' then
      table.insert(out, c)
    end
  end
  return out
end

-- Fixture su disco: BufReadPre scatta solo su file effettivamente letti.
local dir = vim.fn.tempname()
vim.fn.mkdir(dir, 'p')
local function write(path, content)
  local f = assert(io.open(path, 'w'))
  f:write(content)
  f:close()
end
local pge_file   = dir .. '/PGE_e2e.yaml'
local plain_file = dir .. '/plain.yaml'
write(pge_file, 'streams:\n  - stream_id: "e2e"\n    duration: 1.0\n')
write(plain_file, 'foo: bar\n')

-- 1) File YAML non-PGE: nessun client 'pge-ls' deve partire (R2).
vim.cmd('edit ' .. vim.fn.fnameescape(plain_file))
vim.wait(1500, function() return false end)
if #pge_clients() > 0 then
  die(1, "pge-ls attivato su un file YAML non-PGE ('plain.yaml') — R2 violato")
end

-- 2) File PGE_*.yaml: il client deve partire e rispondere a initialize (R1).
vim.cmd('edit ' .. vim.fn.fnameescape(pge_file))
local ok = vim.wait(20000, function()
  local cs = pge_clients()
  return #cs > 0 and cs[1].server_capabilities ~= nil
end, 200)
if not ok then
  die(1, "pge-ls non attivato o senza risposta a initialize su 'PGE_e2e.yaml' — R1")
end

-- 3) Il client risulta attached al buffer PGE corrente.
local buf = vim.api.nvim_get_current_buf()
if #pge_clients({ bufnr = buf }) == 0 then
  die(1, 'pge-ls non risulta attached al buffer PGE corrente')
end

io.stdout:write('E2E OK: pge-ls attivo solo su PGE_*.yaml, handshake LSP completato\n')
vim.cmd('qa!')
