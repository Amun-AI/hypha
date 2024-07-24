"""Test workspace loader."""
import pytest
import asyncio
from hypha_rpc import connect_to_server

from . import (
    SERVER_URL,
    SERVER_URL_REDIS_1,
    SERVER_URL_REDIS_2,
    SIO_PORT2,
    WS_SERVER_URL,
    find_item,
)

# All test coroutines will be treated as marked.
pytestmark = pytest.mark.asyncio


async def test_workspace_loader(fastapi_server, test_user_token):
    """Test workspace loader."""
    async with connect_to_server(
        {
            "name": "my app",
            "server_url": WS_SERVER_URL,
            "client_id": "my-app",
            "token": test_user_token,
        }
    ) as api:
        ws = await api.create_workspace(
            dict(
                name="test-2",
                owners=[],
                visibility="protected",
                persistent=True,
                read_only=False,
            ),
        )
        token = await api.generate_token({"scopes": [ws.name], "expires_in": 3600000})
        workspaces = await api.list_workspaces()
        assert find_item(workspaces, "name", ws.name)

    async with connect_to_server(
        {
            "name": "my app",
            "server_url": WS_SERVER_URL,
            "client_id": "my-app",
            "workspace": ws.name,
            "token": token,
        }
    ) as api:
        assert api.config.workspace == ws.name

        workspaces = await api.list_workspaces()
        assert find_item(workspaces, "name", ws.name)