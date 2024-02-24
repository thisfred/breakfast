from pathlib import Path

import pytest
import pytest_lsp
from lsprotocol.types import (
    ExecuteCommandParams,
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


@pytest.mark.skip("getting error that client does not know workspace/applyEdit")
@pytest.mark.asyncio
async def test_slide_statements_command_should_respond_with_edit(
    client: LanguageClient,
):
    test_uri = f"file://{Path(__file__).parent / 'data' / 'slide.py'}"
    params = ExecuteCommandParams(
        command="breakfast.slideStatements",
        arguments=[{"uri": test_uri, "line": 1}],
    )
    result = await client.workspace_execute_command_async(params)

    assert result == 10
