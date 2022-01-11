"""Test the hypha server."""
import os
import subprocess
import sys
import asyncio


import pytest
from hypha.websocket_client import connect_to_server
from . import SIO_PORT, SIO_PORT2, WS_SERVER_URL, find_item

# All test coroutines will be treated as marked.
pytestmark = pytest.mark.asyncio


async def test_connect_to_server(socketio_server):
    """Test connecting to the server."""

    class ImJoyPlugin:
        """Represent a test plugin."""

        def __init__(self, ws):
            self._ws = ws

        async def setup(self):
            """Set up the plugin."""
            await self._ws.log("initialized")

        async def run(self, ctx):
            """Run the plugin."""
            await self._ws.log("hello world")

    # test workspace is an exception, so it can pass directly
    # rpc = await connect_to_server(
    #     {"name": "my plugin", "workspace": "public", "server_url": WS_SERVER_URL}
    # )
    with pytest.raises(Exception, match=r".*Permission denied for.*"):
        rpc = await connect_to_server(
            {"name": "my plugin", "workspace": "test", "server_url": WS_SERVER_URL}
        )
    wm = await connect_to_server({"name": "my plugin", "server_url": WS_SERVER_URL})
    rpc = wm.rpc
    await rpc.register_service(ImJoyPlugin(wm))
    await wm.log("hello")


def test_plugin_runner(socketio_server):
    """Test the plugin runner."""
    with subprocess.Popen(
        [
            sys.executable,
            "-m",
            "hypha.runner",
            f"--server-url={WS_SERVER_URL}",
            "--quit-on-ready",
            os.path.join(os.path.dirname(__file__), "example_plugin.py"),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ) as proc:
        out, err = proc.communicate()
        assert err.decode("utf8") == ""
        output = out.decode("utf8")
        assert "Generated token: " in output and "@imjoy@" in output
        assert "echo: a message" in output


def test_plugin_runner_subpath(socketio_subpath_server):
    """Test the plugin runner with subpath server."""
    with subprocess.Popen(
        [
            sys.executable,
            "-m",
            "hypha.runner",
            f"--server-url=ws://127.0.0.1:{SIO_PORT2}/my/engine/ws",
            "--quit-on-ready",
            os.path.join(os.path.dirname(__file__), "example_plugin.py"),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ) as proc:
        out, err = proc.communicate()
        assert err.decode("utf8") == ""
        output = out.decode("utf8")
        assert "Generated token: " in output and "@imjoy@" in output
        assert "echo: a message" in output


async def test_plugin_runner_workspace(socketio_server):
    """Test the plugin runner with workspace."""
    api = await connect_to_server(
        {
            "name": "my second plugin",
            "server_url": WS_SERVER_URL,
        }
    )
    token = await api.generate_token()
    assert "@imjoy@" in token

    # The following code without passing the token should fail
    # Here we assert the output message contains "permission denied"
    with subprocess.Popen(
        [
            sys.executable,
            "-m",
            "hypha.runner",
            f"--server-url={WS_SERVER_URL}",
            f"--workspace={api.config['workspace']}",
            # f"--token={token}",
            "--quit-on-ready",
            os.path.join(os.path.dirname(__file__), "example_plugin.py"),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ) as proc:
        out, err = proc.communicate()
        assert proc.returncode == 1
        assert err.decode("utf8") == ""
        output = out.decode("utf8")
        assert "Permission denied for " in output

    # now with the token, it should pass
    with subprocess.Popen(
        [
            sys.executable,
            "-m",
            "hypha.runner",
            f"--server-url={WS_SERVER_URL}",
            f"--workspace={api.config['workspace']}",
            f"--token={token}",
            "--quit-on-ready",
            os.path.join(os.path.dirname(__file__), "example_plugin.py"),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ) as proc:
        out, err = proc.communicate()
        assert proc.returncode == 0
        assert err.decode("utf8") == ""
        output = out.decode("utf8")
        assert "Generated token: " in output and "@imjoy@" in output
        assert "echo: a message" in output


async def test_workspace(socketio_server):
    """Test the plugin runner."""
    api = await connect_to_server(
        {"client_id": "my-plugin", "name": "my plugin", "server_url": WS_SERVER_URL, "method_timeout": 1000}
    )
    await api.log("hi")
    with pytest.raises(
        Exception, match=r".*Scopes must be empty or contains only the workspace name*"
    ):
        await api.generate_token({"scopes": ["my-test-workspace"]})
    token = await api.generate_token()
    assert "@imjoy@" in token

    ws = await api.create_workspace(
        {
            "name": "my-test-workspace",
            "owners": ["user1@imjoy.io", "user2@imjoy.io"],
            "allow_list": [],
            "deny_list": [],
            "visibility": "protected",  # or public
        },
        overwrite=True,
    )
    await ws.log("hello")
    with pytest.raises(Exception, match=r".*Services can only be registered from the same workspace.*"):
        service_info = await ws.register_service(
            {
                "id": "test_service",
                "name": "test_service",
                "type": "#test",
                "add3": lambda x: x + 3,
            }
        )

    def test(context=None):
        return context

    

    # we should not get it because api is in another workspace
    ss2 = await api.list_services({"type": "#test"})
    assert len(ss2) == 0

    # let's generate a token for the my-test-workspace
    token = await ws.generate_token()

    # now if we connect directly to the workspace
    # we should be able to get the my-test-workspace services
    api2 = await connect_to_server(
        {
            "client_id": "my-plugin-2",
            "name": "my plugin 2",
            "workspace": "my-test-workspace",
            "server_url": WS_SERVER_URL,
            "token": token,
            "method_timeout": 100,
        }
    )
    await api2.log("hi")

    service_info = await api2.register_service(
        {
            "name": "test_service_2",
            "type": "#test",
            "config": {"require_context": True},
            "test": test,
        }
    )
    service = await api2.get_service(service_info)
    context = await service.test()
    assert "from" in context and "to" in context and "user" in context

    assert api2.config["workspace"] == "my-test-workspace"
    await api2.export({"foo": "bar"})
    services = api2.rpc.get_all_local_services()
    clients = await api2.list_clients()
    # assert "my-plugin-2" in clients
    # assert "my-plugin" in clients # The service provider of the workspace
    ss3 = await api2.list_services({"type": "#test"})
    assert len(ss3) == 1

    plugin = await api2.get_plugin("my plugin 2")
    assert plugin.foo == "bar"

    await api2.export({"foo2": "bar2"})
    plugin = await api2.get_plugin("my plugin 2")
    assert plugin.foo is None
    assert plugin.foo2 == "bar2"

    plugins = await api2.list_plugins()
    assert find_item(plugins, "name", "my plugin 2")

    with pytest.raises(Exception, match=r".*Service not found.*"):
        await api.get_plugin("my plugin 2")

    ws2 = await api.get_workspace("my-test-workspace")
    assert ws.config == ws2.config

    await ws2.set({"docs": "https://imjoy.io"})
    with pytest.raises(Exception, match=r".*Changing workspace name is not allowed.*"):
        await ws2.set({"name": "new-name"})

    with pytest.raises(Exception):
        await ws2.set({"covers": [], "non-exist-key": 999})

    # state = asyncio.Future()

    # def set_state(data):
    #     """Test function for set the state to a value."""
    #     state.set_result(data)

    # await ws2.on("set-state", set_state)

    # await ws2.emit("set-state", 9978)

    # assert await state == 9978

    # await ws2.off("set-state")

    await api.disconnect()


async def test_services(socketio_server):
    """Test services."""
    api = await connect_to_server({"name": "my plugin", "server_url": WS_SERVER_URL})

    token = await api.generate_token()
    assert "@imjoy@" in token

    service_info = await api.register_service(
        {
            "name": "test_service",
            "type": "#test",
            "idx": 1,
        }
    )
    service = await api.get_service(service_info)
    assert service["name"] == "test_service"
    assert len(await api.list_services({"name": "test_service"})) == 1

    service_info = await api.register_service(
        {
            "name": "test_service",
            "type": "#test",
            "idx": 2,
        },
        overwrite=True,
    )
    # It should be overwritten because it's from the same provider
    assert len(await api.list_services({"name": "test_service"})) == 1

    api2 = await connect_to_server(
        {
            "name": "my plugin 2",
            "server_url": WS_SERVER_URL,
            "token": token,
            "workspace": api.config.workspace,
        }
    )
    assert api2.config["workspace"] == api.config["workspace"]
    service_info = await api2.register_service(
        {
            "name": "test_service",
            "type": "#test",
            "idx": 3,
        }
    )
    # It should be co-exist because it's from a different provider
    assert len(await api.list_services({"name": "test_service"})) == 2
    assert len(await api2.list_services({"name": "test_service"})) == 2

    # service_info = await api.register_service(
    #     {
    #         "name": "test_service",
    #         "type": "#test",
    #         "idx": 4,
    #         "config": {"flags": ["single-instance"]},  # mark it as single instance
    #     },
    #     overwrite=True,
    # )
    # # it should remove other services because it's single instance service
    # assert len(await api.list_services({"name": "test_service"})) == 1
    # assert (await api.get_service("test_service"))["idx"] == 4
