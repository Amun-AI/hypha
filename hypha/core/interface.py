"""Provide interface functions for the core."""
import logging
import random
import sys
from contextvars import ContextVar, copy_context
from functools import partial
from typing import Dict

import pkg_resources
import shortuuid
from starlette.routing import Mount

from hypha.core import ServiceInfo, UserInfo, WorkspaceInfo
from hypha.core.auth import parse_token
from hypha.core.store import RedisStore

logging.basicConfig(stream=sys.stdout)
logger = logging.getLogger("imjoy-core")
logger.setLevel(logging.INFO)


def parse_user(token):
    """Parse user info from a token."""
    if token:
        user_info = parse_token(token)
        uid = user_info.id
        logger.info("User connected: %s", uid)
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
        logger.info("Anonymized User connected: %s", uid)

    if uid == "root":
        logger.error("Root user is not allowed to connect remotely")
        raise Exception("Root user is not allowed to connect remotely")

    return user_info


class CoreInterface:
    """Represent the interface of the ImJoy core."""

    # pylint: disable=no-self-use, too-many-instance-attributes, too-many-public-methods

    def __init__(
        self,
        app,
        app_controller=None,
        public_base_url=None,
        local_base_url=None,
    ):
        """Set up instance."""
        self.current_user = ContextVar("current_user")
        self.current_workspace = ContextVar("current_workspace")
        self.store = RedisStore.get_instance()
        self.event_bus = self.store.get_event_bus()
        self._all_users: Dict[str, UserInfo] = {}  # uid:user_info
        self._all_workspaces: Dict[str, WorkspaceInfo] = {}  # wid:workspace_info
        self._workspace_loader = None
        self._app = app
        self.app_controller = app_controller
        self.disconnect_delay = 1
        self._codecs = {}
        self._disconnected_plugins = []
        self.public_base_url = public_base_url
        self.local_base_url = local_base_url
        self._public_services: List[ServiceInfo] = []
        self._ready = False
        self.load_extensions()

        # def remove_empty_workspace(plugin):
        #     # Remove the user completely if no plugins exists
        #     user_info = plugin.user_info
        #     if len(user_info.get_plugins()) <= 0:
        #         del self._all_users[user_info.id]
        #         logger.info(
        #             "Removing user (%s) completely since the user "
        #             "has no other plugin connected.",
        #             user_info.id,
        #         )
        #     # Remove the user completely if no plugins exists
        #     workspace = plugin.workspace
        #     if len(workspace.get_plugins()) <= 0 and not workspace.persistent:
        #         logger.info(
        #             "Removing workspace (%s) completely "
        #             "since there is no other plugin connected.",
        #             workspace.name,
        #         )
        #         self.unregister_workspace(workspace)

        # self.event_bus.on("plugin_terminated", remove_empty_workspace)
        self._public_workspace = WorkspaceInfo.parse_obj(
            {
                "name": "public",
                "persistent": True,
                "owners": ["root"],
                "allow_list": [],
                "deny_list": [],
                "visibility": "public",
                "read_only": True,
            }
        )
        self._public_workspace_interface = None

    def get_user_info_from_token(self, token):
        """Get user info from token."""
        user_info = parse_user(token)
        return user_info

    async def check_permission(self, workspace: str, user_info: UserInfo):
        """Check user permission for a workspace."""
        if not isinstance(workspace, str):
            workspace = workspace.name
        manager = await self.store.get_workspace_manager(workspace, setup=False)
        return await manager.check_permission(user_info, workspace)

    async def get_workspace(self, name, load=True):
        """Return the workspace."""
        try:
            manager = await self.store.get_workspace_manager(name, setup=False)
            return await manager.get_workspace_info(name)
        except KeyError:
            if load and self._workspace_loader:
                try:
                    workspace = await self._workspace_loader(
                        name, await self.store.setup_root_user()
                    )
                    if workspace:
                        self._all_workspaces[workspace.name] = workspace
                except Exception:  # pylint: disable=broad-except
                    logger.exception("Failed to load workspace %s", name)
                else:
                    return workspace
        return None

    def set_workspace_loader(self, loader):
        """Set the workspace loader."""
        self._workspace_loader = loader

    def load_extensions(self):
        """Load hypha extensions."""
        # Support hypha extensions
        # See how it works:
        # https://packaging.python.org/guides/creating-and-discovering-plugins/
        for entry_point in pkg_resources.iter_entry_points("hypha_extension"):
            try:
                setup_extension = entry_point.load()
                setup_extension(self)
            except Exception:
                logger.exception("Failed to setup extension: %s", entry_point.name)
                raise

    def register_router(self, router):
        """Register a router."""
        self._app.include_router(router)

    async def list_public_services(self, query=None):
        """List all public services."""
        return await self._public_workspace_interface.list_services(query)

    async def get_public_service(self, query=None):
        """Get public service."""
        return await self._public_workspace_interface.get_service(query)

    async def get_service_as_user(
        self,
        workspace_name: str,
        service_id: str,
        user_info: UserInfo = None,
    ):
        """Get service as a specified user."""
        assert "/" not in service_id
        user_info = user_info or await self.store.setup_root_user()

        if ":" not in service_id:
            wm = await self.store.get_workspace_manager(workspace_name)
            services = await wm.list_services(context={"user": user_info})
            services = list(
                filter(
                    lambda service: service["id"].endswith(
                        ":" + service_id if ":" not in service_id else service_id
                    ),
                    services,
                )
            )
            if not services:
                raise Exception(f"Service {service_id} not found")
            service = random.choice(services)
            service_id = service["id"]

        rpc = self.store.create_rpc(
            "http-client-" + shortuuid.uuid(), workspace_name, user_info=user_info
        )
        if "/" not in service_id:
            service_id = f"{workspace_name}/{service_id}"
        service = await rpc.get_remote_service(service_id, timeout=5)
        # Patch the workspace name
        service["config"]["workspace"] = workspace_name
        return service

    def register_public_service(self, service: dict):
        """Register a service."""
        assert not self._ready, "Cannot register public service after ready"

        if "name" not in service or "type" not in service:
            raise Exception("Service should at least contain `name` and `type`")

        # TODO: check if it's already exists
        service["config"] = service.get("config", {})
        assert isinstance(
            service["config"], dict
        ), "service.config must be a dictionary"
        service["config"]["workspace"] = "public"
        assert (
            "visibility" not in service
        ), "`visibility` should be placed inside `config`"
        assert (
            "require_context" not in service
        ), "`require_context` should be placed inside `config`"
        formated_service = ServiceInfo.parse_obj(service)
        # Force to require context
        formated_service.config.require_context = True
        service_dict = formated_service.dict()

        for key in service_dict:
            if callable(service_dict[key]):

                def wrap_func(func, *args, context=None, **kwargs):
                    user_info = UserInfo.parse_obj(context["user"])
                    self.current_user.set(user_info)
                    source_workspace = context["from"].split("/")[0]
                    self.current_workspace.set(source_workspace)
                    ctx = copy_context()
                    return ctx.run(func, *args, **kwargs)

                wrapped = partial(wrap_func, service_dict[key])
                wrapped.__name__ = key
                setattr(formated_service, key, wrapped)
        # service["_rintf"] = True
        # Note: service can set its `visibility` to `public` or `protected`
        self._public_services.append(ServiceInfo.parse_obj(formated_service))
        return {
            "id": formated_service.id,
            "workspace": "public",
            "name": formated_service.name,
        }

    def is_ready(self):
        """Check if the server is alive."""
        return self._ready

    async def init(self, loop):
        """Initialize the core interface."""
        await self.store.init(loop)
        await self.store.register_workspace(self._public_workspace, overwrite=True)
        manager = await self.store.get_workspace_manager("public")
        self._public_workspace_interface = await manager.get_workspace()

        for service in self._public_services:
            try:
                await self._public_workspace_interface.register_service(service.dict())
            except Exception:  # pylint: disable=broad-except
                logger.exception("Failed to register public service: %s", service)
                raise
        self._ready = True

    def mount_app(self, path, app, name=None, priority=-1):
        """Mount an app to fastapi."""
        route = Mount(path, app, name=name)
        # remove existing path
        routes_remove = [route for route in self._app.routes if route.path == path]
        for rou in routes_remove:
            self._app.routes.remove(rou)
        # The default priority is -1 which assumes the last one is websocket
        self._app.routes.insert(priority, route)

    def umount_app(self, path):
        """Unmount an app to fastapi."""
        routes_remove = [route for route in self._app.routes if route.path == path]
        for route in routes_remove:
            self._app.routes.remove(route)
