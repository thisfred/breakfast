from pathlib import Path

import pytest_lsp
from lsprotocol.types import (
    InitializeParams,
)
from pytest_lsp import ClientServerConfig, LanguageClient, client_capabilities


@pytest_lsp.fixture(config=ClientServerConfig(server_command=["breakfast-lsp"]))
async def client(lsp_client: LanguageClient):
    # Maybe at some point parametrize the client to do testing for VSCode and EmAcS as
    # well
    capabilities = client_capabilities("neovim")
    params = InitializeParams(
        capabilities=capabilities,
        root_uri=f"file://{Path(__name__).parent.parent}",
    )
    await lsp_client.initialize_session(params)

    yield

    await lsp_client.shutdown_session()
