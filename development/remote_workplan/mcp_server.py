"""Role-scoped MCP facade for the development workplan gateway.

This module deliberately depends only on an injected gateway object.  In
particular, it does not import the Meta-Harness product runtime or the concrete
gateway implementation, which keeps development-plane credentials out of the
runtime dependency graph.
"""

from __future__ import annotations

import inspect
import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Mapping, Protocol, runtime_checkable


_REDACTED = "[REDACTED]"
_SENSITIVE_PARTS = (
    "credential",
    "secret",
    "access_token",
    "refresh_token",
    "oauth",
    "password",
    "authorization",
)


@runtime_checkable
class Gateway(Protocol):
    """Structural interface consumed by :class:`RemoteWorkplanMCP`."""

    def list_cards(self, **kwargs: Any) -> Any: ...


@dataclass(frozen=True, slots=True)
class Principal:
    """Authenticated development-plane identity.

    Authentication is intentionally external to this facade.  The caller must
    validate a credential and construct a principal before invoking a tool.
    """

    actor: str
    role: str
    credential: str | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not self.actor or self.role not in {"worker", "coordinator"}:
            raise ValueError("principal requires an actor and a supported role")


class FacadeError(Exception):
    """Stable, credential-safe facade error."""

    def __init__(self, code: str, message: str, **details: Any) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details


_WORKER_TOOLS: dict[str, str] = {
    "list": "list_cards",
    "claim": "claim",
    "bind": "bind_worktree",
    "heartbeat": "heartbeat",
    "checkpoint": "checkpoint",
    "update": "checkpoint",
    "block": "block",
    "resume": "resume",
    "submit": "submit",
}

_COORDINATOR_TOOLS: dict[str, str] = {
    "list": "list_cards",
    "qualify": "qualify_card",
    "revalidate": "revalidate",
    "requeue": "requeue",
    "reassign": "reassign",
    "cancel": "cancel",
    "integrate": "integrate",
    "accept": "accept",
    "backend_epoch": "backend_epoch",
}

_TOOLS_BY_ROLE = {"worker": _WORKER_TOOLS, "coordinator": _COORDINATOR_TOOLS}


def _is_sensitive(key: object) -> bool:
    normalized = str(key).lower().replace("-", "_")
    # A fencing token is versioned concurrency state, not an authentication
    # secret. The authenticated claimant must receive it to fence stale writers.
    if normalized == "fencing_token":
        return False
    return any(part in normalized for part in _SENSITIVE_PARTS)


def _safe(value: Any, *, key: object = "") -> Any:
    """Recursively redact credentials from gateway results and error details."""

    if _is_sensitive(key):
        return _REDACTED
    if isinstance(value, Mapping):
        return {str(k): _safe(v, key=k) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_safe(item) for item in value]
    if isinstance(value, str):
        value = re.sub(r"(?i)\bbearer\s+\S+", f"Bearer {_REDACTED}", value)
        value = re.sub(
            r"(?i)\b(oauth(?:_token)?|access_token|refresh_token|password|credential)\s*[=:]\s*\S+",
            lambda match: f"{match.group(1)}={_REDACTED}",
            value,
        )
        return value
    if value is None or isinstance(value, (int, float, bool)):
        return value
    return str(value)


def _error(code: str, message: str, **details: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"code": code, "message": _safe(message)}
    cleaned = _safe(details)
    if cleaned:
        payload["details"] = cleaned
    return {"ok": False, "error": payload}


class RemoteWorkplanMCP:
    """Direct Python facade implementing role and tool boundaries."""

    def __init__(self, gateway: Gateway) -> None:
        self._gateway = gateway

    def list_tools(self, principal: Principal) -> tuple[str, ...]:
        return tuple(_TOOLS_BY_ROLE[principal.role])

    async def invoke(
        self,
        tool: str,
        principal: Principal,
        arguments: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Invoke a role-authorized tool and return a stable result envelope."""

        tools = _TOOLS_BY_ROLE[principal.role]
        method_name = tools.get(tool)
        if method_name is None:
            return _error(
                "forbidden_tool",
                "tool is not available to this role",
                tool=tool,
                role=principal.role,
            )

        supplied = dict(arguments or {})
        supplied.update(kwargs)
        # The credential is resolved by the transport authentication layer, not
        # accepted from MCP tool arguments. The gateway derives the authoritative
        # actor and role/scopes from it, preventing facade-level impersonation.
        supplied.pop("actor", None)
        supplied.pop("role", None)
        supplied.pop("credential", None)
        if principal.credential is not None:
            supplied["credential"] = principal.credential

        method = getattr(self._gateway, method_name, None)
        if method is None or not callable(method):
            return _error("gateway_unavailable", "gateway operation is unavailable", tool=tool)

        try:
            result = method(**supplied)
            if inspect.isawaitable(result):
                result = await result
            return {"ok": True, "result": _safe(result)}
        except Exception as exc:  # gateway errors are intentionally duck typed
            code = getattr(exc, "code", None)
            message = getattr(exc, "message", None)
            if isinstance(code, str) and isinstance(message, str):
                details: dict[str, Any] = {}
                revision = getattr(exc, "current_revision", None)
                if revision is not None:
                    details["current_revision"] = revision
                return _error(code, message, **details)
            if isinstance(exc, FacadeError):
                return _error(exc.code, exc.message, **exc.details)
            # Never serialize repr(exc): arbitrary adapters may include a bearer
            # token, OAuth exchange, database URL, or other credential in it.
            return _error("internal_error", "gateway operation failed")

    async def list(self, principal: Principal, **kwargs: Any) -> dict[str, Any]:
        return await self.invoke("list", principal, kwargs)

    async def claim(self, principal: Principal, **kwargs: Any) -> dict[str, Any]:
        return await self.invoke("claim", principal, kwargs)

    async def bind(self, principal: Principal, **kwargs: Any) -> dict[str, Any]:
        return await self.invoke("bind", principal, kwargs)

    async def heartbeat(self, principal: Principal, **kwargs: Any) -> dict[str, Any]:
        return await self.invoke("heartbeat", principal, kwargs)

    async def checkpoint(self, principal: Principal, **kwargs: Any) -> dict[str, Any]:
        return await self.invoke("checkpoint", principal, kwargs)

    async def update(self, principal: Principal, **kwargs: Any) -> dict[str, Any]:
        return await self.invoke("update", principal, kwargs)

    async def block(self, principal: Principal, **kwargs: Any) -> dict[str, Any]:
        return await self.invoke("block", principal, kwargs)

    async def resume(self, principal: Principal, **kwargs: Any) -> dict[str, Any]:
        return await self.invoke("resume", principal, kwargs)

    async def submit(self, principal: Principal, **kwargs: Any) -> dict[str, Any]:
        return await self.invoke("submit", principal, kwargs)

    async def revalidate(self, principal: Principal, **kwargs: Any) -> dict[str, Any]:
        return await self.invoke("revalidate", principal, kwargs)

    async def qualify(self, principal: Principal, **kwargs: Any) -> dict[str, Any]:
        return await self.invoke("qualify", principal, kwargs)

    async def requeue(self, principal: Principal, **kwargs: Any) -> dict[str, Any]:
        return await self.invoke("requeue", principal, kwargs)

    async def reassign(self, principal: Principal, **kwargs: Any) -> dict[str, Any]:
        return await self.invoke("reassign", principal, kwargs)

    async def cancel(self, principal: Principal, **kwargs: Any) -> dict[str, Any]:
        return await self.invoke("cancel", principal, kwargs)

    async def integrate(self, principal: Principal, **kwargs: Any) -> dict[str, Any]:
        return await self.invoke("integrate", principal, kwargs)

    async def accept(self, principal: Principal, **kwargs: Any) -> dict[str, Any]:
        return await self.invoke("accept", principal, kwargs)

    async def backend_epoch(self, principal: Principal, **kwargs: Any) -> dict[str, Any]:
        return await self.invoke("backend_epoch", principal, kwargs)


AuthResolver = Callable[[], Principal | Awaitable[Principal]]


def create_fastmcp(
    gateway: Gateway,
    authenticate: AuthResolver,
    *,
    name: str = "remote-workplan",
) -> Any:
    """Lazily construct a FastMCP server around the direct facade.

    ``mcp`` remains an optional development dependency.  The resolver is
    invoked inside every tool call and is responsible for authenticating the
    transport request; raw credentials are never arguments or results.
    """

    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:  # pragma: no cover - depends on optional extra
        raise RuntimeError("install the 'mcp' extra to construct a FastMCP server") from exc

    server = FastMCP(name)
    facade = RemoteWorkplanMCP(gateway)

    async def call(tool: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        principal = authenticate()
        if inspect.isawaitable(principal):
            principal = await principal
        if not isinstance(principal, Principal):
            return _error("unauthenticated", "authentication failed")
        return await facade.invoke(tool, principal, arguments)

    # Expose the operation names for MCP discovery. Authorization remains
    # centralized in the facade, so discovery never implies permission.
    def make_operation(bound_tool: str) -> Callable[..., Awaitable[dict[str, Any]]]:
        async def operation(arguments: dict[str, Any] | None = None) -> dict[str, Any]:
            return await call(bound_tool, arguments)

        return operation

    for tool_name in dict.fromkeys((*_WORKER_TOOLS, *_COORDINATOR_TOOLS)):
        operation = make_operation(tool_name)
        operation.__name__ = f"workplan_{tool_name}"
        server.tool(name=tool_name)(operation)
    return server


__all__ = [
    "FacadeError",
    "Gateway",
    "Principal",
    "RemoteWorkplanMCP",
    "create_fastmcp",
]
