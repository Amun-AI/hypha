"""Provide an s3 interface."""
import asyncio
import logging
import sys

from fastapi import Query, WebSocket, status
from starlette.websockets import WebSocketDisconnect

from hypha.core import ClientInfo, UserInfo
from hypha.core.store import RedisRPCConnection, RedisStore
from hypha.core.auth import parse_token
import shortuuid

logging.basicConfig(stream=sys.stdout)
logger = logging.getLogger("websocket")
logger.setLevel(logging.INFO)


class WebsocketServer:
    """Represent an Websocket server."""

    # pylint: disable=too-many-statements

    def __init__(self, core_interface, path="/ws", allow_origins="*") -> None:
        """Set up the socketio server."""
        if allow_origins == ["*"]:
            allow_origins = "*"

        store = RedisStore.get_instance()

        self.core_interface = core_interface
        app = core_interface._app

        @app.websocket(path)
        async def websocket_endpoint(
            websocket: WebSocket,
            workspace: str = Query(None),
            client_id: str = Query(None),
            token: str = Query(None),
        ):
            if client_id is None:
                logger.error("Missing query parameters: workspace, client_id")
                await websocket.close(code=status.WS_1003_UNSUPPORTED_DATA)
                return

            if token:
                try:
                    user_info = parse_token(token)
                    uid = user_info.id
                except Exception:
                    logger.error("Invalid token: %s", token)
                    await websocket.close(code=status.WS_1003_UNSUPPORTED_DATA)
                    return
            else:
                uid = shortuuid.uuid()
                user_info = UserInfo(
                    id=uid,
                    is_anonymous=True,
                    email=None,
                    parent=None,
                    roles=[],
                    scopes=[],
                    expires_at=None,
                )
                await store.register_user(user_info)
                logger.info("Anonymized User connected: %s", uid)

            if workspace is None:
                workspace = uid
                await store.register_workspace(
                    dict(
                        name=uid,
                        owners=[uid],
                        visibility="protected",
                        persistent=False,
                        read_only=False,
                    ),
                    overwrite=False,
                )

            workspace_manager = await store.get_workspace_manager(workspace)
            if not await workspace_manager.check_permission(user_info):
                logger.error(
                    "Permission denied (client: %s, workspace: %s)",
                    client_id,
                    workspace,
                )
                await websocket.close(code=status.WS_1003_UNSUPPORTED_DATA)
                return

            if await workspace_manager.check_client_exists(client_id):
                logger.error(
                    "Another client with the same id %s already connected to workspace: %s",
                    client_id,
                    workspace,
                )
                await websocket.close(code=status.WS_1013_TRY_AGAIN_LATER)
                # await workspace_manager.delete_client(client_id)
                return

            await websocket.accept()

            conn = RedisRPCConnection(
                workspace_manager._redis,
                workspace_manager._workspace,
                client_id,
                user_info,
            )
            conn.on_message(websocket.send_bytes)

            await workspace_manager.register_client(
                ClientInfo(id=client_id, user_info=user_info)
            )
            try:
                while True:
                    data = await websocket.receive_bytes()
                    await conn.emit_message(data)
            except WebSocketDisconnect as exp:
                if exp.code != status.WS_1000_NORMAL_CLOSURE:
                    logger.warning(
                        f"websocket disconnect from the server (code={exp.code})"
                    )
            finally:
                await workspace_manager.delete_client(client_id)
                if user_info.is_anonymous:
                    await store.delete_user(user_info.id)

    async def is_alive(self):
        """Check if the server is alive."""
        return True
