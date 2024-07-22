import asyncio
import logging
import sys

from fastapi import Query, WebSocket, status
from starlette.websockets import WebSocketDisconnect

from hypha.core import UserInfo
from hypha.core.store import RedisRPCConnection, RedisStore
from hypha.core.auth import (
    parse_reconnection_token,
    generate_reconnection_token,
    parse_token,
)
import shortuuid
import json

logging.basicConfig(stream=sys.stdout)
logger = logging.getLogger("websocket-server")
logger.setLevel(logging.INFO)


class WebsocketServer:
    def __init__(self, store: RedisStore, path="/ws"):
        """Initialize websocket server with the store and set up the endpoint."""
        self.store = store
        app = store._app
        self._stop = False

        @app.websocket(path)
        async def websocket_endpoint(
            websocket: WebSocket,
            workspace: str = Query(None),
            client_id: str = Query(None),
            token: str = Query(None),
            reconnection_token: str = Query(None),
        ):
            await websocket.accept()
            # If none of the authentication parameters are provided, wait for the first message
            if not workspace and not client_id and not token and not reconnection_token:
                # Wait for the first message which should contain the authentication information
                auth_info = await websocket.receive_text()
                # Parse the authentication information, e.g., JSON with token and/or reconnection_token
                try:
                    auth_data = json.loads(auth_info)
                    token = auth_data.get("token")
                    reconnection_token = auth_data.get("reconnection_token")
                    # Optionally, you can also update workspace and client_id if they are sent in the first message
                    workspace = auth_data.get("workspace")
                    client_id = auth_data.get("client_id")
                except json.JSONDecodeError:
                    logger.error("Failed to decode authentication information")
                    self.disconnect(
                        websocket,
                        reason="Failed to decode authentication information",
                        code=status.WS_1003_UNSUPPORTED_DATA,
                    )
                    return
            else:
                logger.warning("Rejecting legacy imjoy-rpc client (%s)", client_id)
                websocket.close(
                    code=status.WS_1008_POLICY_VIOLATION,
                    reason="Connection rejected, please  upgrade to hypha-rpc which pass the authentication information in the first message",
                )
                return

            try:
                await self.handle_websocket_connection(
                    websocket, workspace, client_id, token, reconnection_token
                )
            except Exception as e:
                if (
                    websocket.client_state.name == "CONNECTED"
                    or websocket.client_state.name == "CONNECTING"
                ):
                    logger.error(f"Error handling WebSocket connection: {str(e)}")
                    await self.disconnect(
                        websocket,
                        reason=f"Error handling WebSocket connection: {str(e)}",
                        code=status.WS_1011_INTERNAL_ERROR,
                    )

    async def handle_websocket_connection(
        self, websocket, workspace, client_id, token, reconnection_token
    ):
        if client_id is None:
            logger.error("Missing query parameters: client_id")
            await self.disconnect(
                websocket,
                reason="Missing query parameters: client_id",
                code=status.WS_1003_UNSUPPORTED_DATA,
            )
            return

        try:
            user_info, workspace = await self.authenticate_user(
                token, reconnection_token, client_id, workspace
            )

            workspace = await self.setup_workspace_and_permissions(user_info, workspace)
            await self.check_client(websocket, client_id, workspace, user_info)

            await self.establish_websocket_communication(
                websocket,
                workspace,
                client_id,
                user_info,
            )

        except Exception as e:
            logger.exception(f"Error handling WebSocket connection: {str(e)}")
            await self.disconnect(
                websocket,
                reason=f"Error handling WebSocket connection: {str(e)}",
                code=status.WS_1011_INTERNAL_ERROR,
            )

    async def check_client(self, websocket, client_id, workspace, user_info):
        """Check if the client is already connected."""
        # check if client already exists
        if await self.store.client_exists(client_id, workspace):
            async with self.store.connect_to_workspace(
                workspace, "check-client-exists", user_info, timeout=5
            ) as ws:
                if await ws.ping(f"{workspace}/{client_id}") == "pong":
                    reason = (
                        f"Client already exists and is active: {workspace}/{client_id}"
                    )
                    logger.error(reason)
                    raise RuntimeError(reason)
                else:
                    logger.info(
                        f"Client already exists but is inactive: {workspace}/{client_id}"
                    )
                    await self.store.delete_client(client_id, workspace, user_info)

    async def authenticate_user(self, token, reconnection_token, client_id, workspace):
        """Authenticate user and handle reconnection or token authentication."""
        # Ensure actual implementation calls for parse_reconnection_token and parse_token
        user_info = None
        try:
            if reconnection_token:
                user_info, ws, cid = parse_reconnection_token(reconnection_token)
                if workspace and workspace != ws:
                    logger.error("Workspace mismatch, disconnecting")
                    raise RuntimeError("Workspace mismatch, disconnecting")
                elif cid != client_id:
                    logger.error("Client id mismatch, disconnecting")
                    raise RuntimeError("Client id mismatch, disconnecting")
                return user_info, ws
            else:
                if token:
                    user_info = parse_token(token)
                else:
                    user_info = UserInfo(
                        id=shortuuid.uuid(),
                        is_anonymous=True,
                        email=None,
                        parent=None,
                        roles=[],
                        scopes=[],
                        expires_at=None,
                    )
                return user_info, workspace
        except Exception as e:
            logger.error(f"Authentication error: {str(e)}")
            raise RuntimeError(f"Authentication error: {str(e)}")

    async def setup_workspace_and_permissions(self, user_info, workspace):
        """Setup workspace and check permissions."""
        if workspace is None:
            workspace = user_info.id

        assert workspace != "*", "Dynamic workspace is not allowed for this endpoint"
        # Anonymous and Temporary users are not allowed to create persistant workspaces
        persistent = (
            not user_info.is_anonymous and "temporary-test-user" not in user_info.roles
        )

        # Ensure calls to store for workspace existence and permissions check
        workspace_exists = await self.store.workspace_exists(workspace)
        if not workspace_exists:
            assert (
                workspace == user_info.id
            ), "User can only connect to a pre-existing workspace or their own workspace"
            # Simplified logic for workspace creation, ensure this matches the actual store method signatures
            await self.store.register_workspace(
                {
                    "name": workspace,
                    "persistent": persistent,
                    "owners": [user_info.id],
                    "visibility": "protected",
                    "read_only": user_info.is_anonymous,
                }
            )
            logger.info(f"Created workspace: {workspace}")

        if not await self.store.check_permission(user_info, workspace):
            logger.error(f"Permission denied for workspace: {workspace}")
            raise PermissionError(f"Permission denied for workspace: {workspace}")
        return workspace

    async def establish_websocket_communication(
        self,
        websocket,
        workspace,
        client_id,
        user_info,
    ):
        """Establish and manage websocket communication."""
        conn = None
        try:
            conn = RedisRPCConnection(
                self.store._event_bus, workspace, client_id, user_info
            )
            conn.on_message(websocket.send_bytes)
            reconnection_token = generate_reconnection_token(
                user_info, workspace, client_id, expires_in=2 * 24 * 60 * 60
            )
            conn_info = {
                "manager_id": self.store.manager_id,
                "workspace": workspace,
                "client_id": client_id,
                "user": user_info.model_dump(),
                "reconnection_token": reconnection_token,
            }
            conn_info["success"] = True
            await websocket.send_text(json.dumps(conn_info))
            while not self._stop:
                data = await websocket.receive_bytes()
                await conn.emit_message(data)
        except WebSocketDisconnect as exp:
            await self.handle_disconnection(
                workspace,
                client_id,
                user_info,
                exp.code,
                exp,
            )
        except Exception as e:
            await self.handle_disconnection(
                workspace,
                client_id,
                user_info,
                status.WS_1011_INTERNAL_ERROR,
                e,
            )
        finally:
            if conn:
                await conn.disconnect()

    async def handle_disconnection(
        self, workspace: str, client_id: str, user_info: UserInfo, code, exp
    ):
        """Handle client disconnection with delayed removal for unexpected disconnections."""
        try:
            await self.store.delete_client(client_id, workspace, user_info)
            if code in [status.WS_1000_NORMAL_CLOSURE, status.WS_1001_GOING_AWAY]:
                # Client disconnected normally, remove immediately
                logger.info(f"Client disconnected normally: {workspace}/{client_id}")
            else:
                logger.info(
                    f"Client disconnected unexpectedly: {workspace}/{client_id}, code: {code}, error: {exp}"
                )
        except Exception as e:
            logger.error(f"Error handling disconnection: {str(e)}")

    async def disconnect(self, websocket, reason, code=status.WS_1000_NORMAL_CLOSURE):
        """Disconnect the websocket connection."""
        # if not closed
        if websocket.client_state.name != "CLOSED":
            logger.info(f"Disconnecting websocket client with reason: {reason}")
            await websocket.send_text(json.dumps({"error": reason, "success": False}))
            await websocket.close(code)

    async def is_alive(self):
        """Check if the server is alive."""
        return True

    async def stop(self):
        """Stop the server."""
        self._stop = True
