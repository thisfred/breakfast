local configs = require 'lspconfig.configs'
local lspconfig = require 'lspconfig'

local function lsp_client(name)
    return assert(vim.lsp.get_active_clients({bufnr = vim.api.nvim_get_current_buf(), name=name})[1],
        ("No %s client found for the current buffer"):format( name )
    )
end

local on_attach = function(client, bufnr)
  local bufopts = { noremap=true, silent=true, buffer=bufnr }
  if client.name == 'breakfast' then
    vim.keymap.set('n', '<leader>ca', vim.lsp.buf.code_action, bufopts)
    vim.keymap.set('v', '<leader>ca', vim.lsp.buf.code_action, bufopts)
    vim.keymap.set('n', '<leader>r', vim.lsp.buf.rename, bufopts)
    vim.keymap.set('n', '<leader>gd', vim.lsp.buf.definition, bufopts)
    vim.keymap.set('n', 'gr', vim.lsp.buf.references, bufopts)
    vim.keymap.set('n', 'gi', vim.lsp.buf.implementation, bufopts)
  end
end

if not configs.breakfast then
    configs.breakfast = {
        default_config = {
            name = 'breakfast',
            -- cmd = {'lsp-devtools', 'agent', '--', 'breakfast-lsp'},
            cmd = {'breakfast-lsp'},
            filetypes = { 'python' },
        },
    }
end


lspconfig.breakfast.setup {
  on_attach = on_attach,
  root_dir = lspconfig.util.root_pattern('.git'),
  commands = {},
}
