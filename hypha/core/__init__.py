"""Provide the ImJoy core API interface."""
import asyncio
import inspect
import io
import json
import logging
import sys
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple, Union
import msgpack
from pydantic import (  # pylint: disable=no-name-in-module
    BaseModel,
    EmailStr,
    PrivateAttr,
    constr,
    SerializeAsAny,
    ConfigDict,
    AnyHttpUrl,
)

from hypha.utils import EventBus

logging.basicConfig(stream=sys.stdout)
logger = logging.getLogger("core")
logger.setLevel(logging.INFO)


class TokenConfig(BaseModel):
    """Represent a token configuration."""

    scopes: List[str]
    expires_in: Optional[float] = None
    email: Optional[EmailStr] = None


class VisibilityEnum(str, Enum):
    """Represent the visibility of the workspace."""

    public = "public"
    protected = "protected"


class StatusEnum(str, Enum):
    """Represent the status of a component."""

    ready = "ready"
    initializing = "initializing"
    not_initialized = "not_initialized"


class ServiceConfig(BaseModel):
    """Represent service config."""

    model_config = ConfigDict(extra="allow")

    visibility: VisibilityEnum = VisibilityEnum.protected
    require_context: Union[Tuple[str], List[str], bool] = False
    workspace: str = None
    flags: List[str] = []


class ServiceInfo(BaseModel):
    """Represent service."""

    model_config = ConfigDict(extra="allow")

    config: SerializeAsAny[ServiceConfig]
    id: str
    name: str
    type: str
    description: Optional[constr(max_length=256)] = "" # type: ignore
    docs: Optional[Dict[str, constr(max_length=1024)]] = None # type: ignore
    app_id: Optional[str] = None

    def is_singleton(self):
        """Check if the service is singleton."""
        return "single-instance" in self.config.flags

    def to_redis_dict(self):
        data = self.model_dump()
        data["config"] = self.config.model_dump_json()
        return {
            k: json.dumps(v) if not isinstance(v, str) else v for k, v in data.items()
        }

    @classmethod
    def model_validate(cls, data):
        data = data.copy()
        data["config"] = ServiceConfig.model_validate(data["config"])
        return super().model_validate(data)

class UserTokenInfo(BaseModel):
    """Represent user profile."""
    token: constr(max_length=1024) # type: ignore
    email: Optional[EmailStr] = None
    email_verified: Optional[bool] = None
    name: Optional[constr(max_length=64)] = None # type: ignore
    nickname: Optional[constr(max_length=64)] = None # type: ignore
    user_id: Optional[constr(max_length=64)] = None # type: ignore
    picture: Optional[AnyHttpUrl] = None

class UserInfo(BaseModel):
    """Represent user info."""

    id: str
    roles: List[str]
    is_anonymous: bool
    email: Optional[EmailStr] = None
    parent: Optional[str] = None
    scopes: Optional[List[str]] = None  # a list of workspace
    expires_at: Optional[float] = None
    _metadata: Dict[str, Any] = PrivateAttr(
        default_factory=lambda: {}
    )  # e.g. s3 credential

    def get_metadata(self, key=None) -> Dict[str, Any]:
        """Return the metadata."""
        if key:
            return self._metadata.get(key)
        return self._metadata

    def set_metadata(self, key, value):
        """Set the metadata."""
        self._metadata[key] = value


class ClientInfo(BaseModel):
    """Represent service."""

    id: str
    parent: Optional[str] = None
    name: Optional[str] = None
    workspace: str
    services: List[SerializeAsAny[ServiceInfo]] = []
    user_info: UserInfo

    @classmethod
    def model_validate(cls, data):
        data = data.copy()
        data["user_info"] = UserInfo.model_validate(data["user_info"])
        data["services"] = [
            ServiceInfo.model_validate(service) for service in data["services"]
        ]
        return super().model_validate(data)


class Card(BaseModel):
    """Represent resource description file object."""

    model_config = ConfigDict(extra="allow")

    name: str
    id: str
    tags: List[str]
    documentation: Optional[str] = None
    covers: Optional[List[str]] = None
    badges: Optional[List[str]] = None
    authors: Optional[List[Dict[str, str]]] = None
    attachments: Optional[Dict[str, List[Any]]] = None
    config: Optional[Dict[str, Any]] = None
    type: str
    format_version: str = "0.2.1"
    version: str = "0.1.0"
    links: Optional[List[str]] = None
    maintainers: Optional[List[Dict[str, str]]] = None
    license: Optional[str] = None
    git_repo: Optional[str] = None
    source: Optional[str] = None
    services: Optional[List[SerializeAsAny[ServiceInfo]]] = None

    @classmethod
    def model_validate(cls, data):
        data = data.copy()
        if "services" in data and data["services"] is not None:
            data["services"] = [
                ServiceInfo.model_validate(service) for service in data["services"]
            ]
        return super().model_validate(data)


class WorkspaceInfo(BaseModel):
    """Represent a workspace."""

    name: str
    persistent: bool
    owners: List[str]
    read_only: bool = False
    description: Optional[str] = None
    icon: Optional[str] = None
    covers: Optional[List[str]] = None
    docs: Optional[str] = None
    applications: Optional[Dict[str, Card]] = {}  # installed applications
    config: Optional[Dict[str, Any]] = {}  # workspace custom config

    @classmethod
    def model_validate(cls, data):
        data = data.copy()
        if "applications" in data and data["applications"] is not None:
            data["applications"] = {
                k: Card.model_validate(v) for k, v in data["applications"].items()
            }
        return super().model_validate(data)


class RedisRPCConnection:
    """Represent a Redis connection for handling RPC-like messaging."""

    def __init__(
        self,
        event_bus: EventBus,
        workspace: str,
        client_id: str,
        user_info: UserInfo,
        manager_id: str,
    ):
        """Initialize Redis RPC Connection."""
        assert workspace and "/" not in client_id, "Invalid workspace or client ID"
        self._workspace = workspace
        self._client_id = client_id
        self._user_info = user_info.model_dump()
        self._stop = False
        self._event_bus = event_bus
        self._handle_connect = None
        self._disconnect_handler = None
        self._handle_message = None
        self.manager_id = manager_id

    def on_disconnected(self, handler):
        """Register a disconnection event handler."""
        self._disconnect_handler = handler

    def on_connect(self, handler):
        """Register a connection open event handler."""
        self._handle_connect = handler
        assert inspect.iscoroutinefunction(
            handler
        ), "Connect handler must be a coroutine"

    def on_message(self, handler: Callable):
        """Set message handler."""
        self._handle_message = handler
        self._event_bus.on(f"{self._workspace}/{self._client_id}:msg", handler)
        # for broadcast messages
        self._event_bus.on(f"{self._workspace}/*:msg", handler)
        if self._handle_connect:
            asyncio.create_task(self._handle_connect(self))

    async def emit_message(self, data: Union[dict, bytes]):
        """Send message after packing additional info."""
        assert isinstance(data, bytes), "Data must be bytes"
        if self._stop:
            raise ValueError(
                f"Connection has already been closed (client: {self._workspace}/{self._client_id})"
            )
        unpacker = msgpack.Unpacker(io.BytesIO(data))
        message = unpacker.unpack()
        pos = unpacker.tell()
        target_id = message.get("to")
        if "/" not in target_id:
            if "/workspace-manager-" in target_id:
                raise ValueError(
                    f"Invalid target ID: {target_id}, it appears that the target is a workspace manager (target_id should starts with */)"
                )
            target_id = f"{self._workspace}/{target_id}"

        source_id = f"{self._workspace}/{self._client_id}"

        message.update(
            {
                "ws": target_id.split("/")[0]
                if self._workspace == "*"
                else self._workspace,
                "to": target_id,
                "from": source_id,
                "user": self._user_info,
            }
        )

        packed_message = msgpack.packb(message) + data[pos:]
        # logger.info(f"Sending message to channel {target_id}:msg")
        self._event_bus.emit(f"{target_id}:msg", packed_message)

    async def disconnect(self, reason=None):
        """Handle disconnection."""
        self._stop = True
        if self._handle_message:
            self._event_bus.off(
                f"{self._workspace}/{self._client_id}:msg", self._handle_message
            )
            self._event_bus.off(f"{self._workspace}/*:msg", self._handle_message)

        self._handle_message = None
        logger.info(f"Redis Connection Disconnected: {reason}")
        if self._disconnect_handler:
            await self._disconnect_handler(reason)


class RedisEventBus:
    """Represent a redis event bus."""

    def __init__(self, redis, logger=None) -> None:
        """Initialize the event bus."""
        self._redis = redis
        self._handle_connect = None
        self._stop = False
        self._local_event_bus = EventBus(logger)
        self._redis_event_bus = EventBus(logger)
        self._logger = logger

    async def init(self):
        """Setup the event bus."""
        loop = asyncio.get_running_loop()
        self._ready = loop.create_future()
        self._loop = loop
        loop.create_task(self._subscribe_redis())
        await self._ready

    def on(self, event_name, func):
        """Register a callback for an event from Redis."""
        self._redis_event_bus.on(event_name, func)

    def off(self, event_name, func=None):
        """Unregister a callback for an event from Redis."""
        self._redis_event_bus.off(event_name, func)

    def once(self, event_name, func):
        """Register a callback for an event and remove it once triggered."""

        def once_wrapper(*args, **kwargs):
            result = func(*args, **kwargs)
            self._redis_event_bus.off(event_name, once_wrapper)
            return result

        self._redis_event_bus.on(event_name, once_wrapper)

    async def wait_for(self, event_name, match=None, timeout=None):
        """Wait for an event from either local or Redis event bus."""
        local_future = self._local_event_bus.wait_for(event_name, match, timeout)
        redis_future = self._redis_event_bus.wait_for(event_name, match, timeout)
        done, pending = await asyncio.wait(
            [local_future, redis_future], return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
        return done.pop().result()

    def on_local(self, event_name, func):
        """Register a callback for a local event."""
        return self._local_event_bus.on(event_name, func)

    def off_local(self, event_name, func=None):
        """Unregister a callback for a local event."""
        return self._local_event_bus.off(event_name, func)

    def once_local(self, event_name, func):
        """Register a callback for a local event and remove it once triggered."""

        def once_wrapper(*args, **kwargs):
            result = func(*args, **kwargs)
            self._local_event_bus.off(event_name, once_wrapper)
            return result

        return self._local_event_bus.on(event_name, once_wrapper)

    async def wait_for_local(self, event_name, match=None, timeout=None):
        """Wait for local event."""
        return await self._local_event_bus.wait_for(event_name, match, timeout)

    async def emit_local(self, event_name, data=None):
        """Emit a local event."""
        if not self._ready.done():
            self._ready.set_result(True)
        local_task = self._local_event_bus.emit(event_name, data)
        if asyncio.iscoroutine(local_task):
            await local_task

    def emit(self, event_name, data):
        """Emit an event."""
        if not self._ready.done():
            self._ready.set_result(True)
        tasks = []

        # Emit locally
        local_task = self._local_event_bus.emit(event_name, data)
        if asyncio.iscoroutine(local_task):
            tasks.append(local_task)

        # Emit globally via Redis
        data_type = "b:"
        if isinstance(data, dict):
            data = json.dumps(data)
            data_type = "d:"
        elif isinstance(data, str):
            data_type = "s:"
            data = data.encode("utf-8")
        else:
            assert data and isinstance(
                data, (str, bytes)
            ), "Data must be a string or bytes"
        global_task = self._loop.create_task(
            self._redis.publish("event:" + data_type + event_name, data)
        )
        tasks.append(global_task)

        if tasks:
            return asyncio.gather(*tasks)
        else:
            fut = asyncio.get_event_loop().create_future()
            fut.set_result(None)
            return fut

    def stop(self):
        self._stop = True

    async def _subscribe_redis(self):
        pubsub = self._redis.pubsub()
        self._stop = False
        try:
            await pubsub.psubscribe("event:*")
            self._ready.set_result(True)
            while self._stop is False:
                msg = await pubsub.get_message(ignore_subscribe_messages=True)
                if msg:
                    channel = msg["channel"].decode("utf-8")
                    if channel.startswith("event:b:"):
                        event_type = channel[8:]
                        data = msg["data"]
                        self._redis_event_bus.emit(event_type, data)
                    elif channel.startswith("event:d:"):
                        event_type = channel[8:]
                        data = json.loads(msg["data"])
                        self._redis_event_bus.emit(event_type, data)
                    elif channel.startswith("event:s:"):
                        event_type = channel[8:]
                        data = msg["data"].decode("utf-8")
                        self._redis_event_bus.emit(event_type, data)
                    else:
                        self._logger.info("Unknown channel: %s", channel)
                await asyncio.sleep(0.01)
        except Exception as exp:
            self._ready.set_exception(exp)
