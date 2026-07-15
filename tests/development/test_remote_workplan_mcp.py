from __future__ import annotations

import pytest

from development.remote_workplan.mcp_server import Principal, RemoteWorkplanMCP, create_fastmcp


class FakeGatewayError(Exception):
    def __init__(self, code: str, message: str, current_revision: int | None = None):
        self.code = code
        self.message = message
        self.current_revision = current_revision


class FakeGateway:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def operation(**kwargs):
            self.calls.append((name, kwargs))
            return {"operation": name, "arguments": kwargs}

        return operation


@pytest.fixture
def gateway():
    return FakeGateway()


@pytest.fixture
def facade(gateway):
    return RemoteWorkplanMCP(gateway)


def worker():
    return Principal(actor="codex-seat-1", role="worker", credential="worker-secret")


def coordinator():
    return Principal(actor="coordinator-1", role="coordinator", credential="coordinator-secret")


def test_tools_are_role_scoped(facade):
    assert facade.list_tools(worker()) == (
        "list",
        "claim",
        "bind",
        "heartbeat",
        "checkpoint",
        "update",
        "block",
        "resume",
        "submit",
    )
    assert facade.list_tools(coordinator()) == (
        "list",
        "qualify",
        "revalidate",
        "requeue",
        "reassign",
        "cancel",
        "integrate",
        "accept",
        "backend_epoch",
    )


@pytest.mark.parametrize(
    ("tool", "method"),
    [
        ("list", "list_cards"),
        ("claim", "claim"),
        ("bind", "bind_worktree"),
        ("heartbeat", "heartbeat"),
        ("checkpoint", "checkpoint"),
        ("update", "checkpoint"),
        ("block", "block"),
        ("resume", "resume"),
        ("submit", "submit"),
    ],
)
async def test_worker_tool_mapping(facade, gateway, tool, method):
    response = await facade.invoke(tool, worker(), {"card_id": "META-2"})

    assert response["ok"] is True
    assert gateway.calls == [
        (method, {"card_id": "META-2", "credential": "worker-secret"})
    ]


@pytest.mark.parametrize(
    ("tool", "method"),
    [
        ("qualify", "qualify_card"),
        ("revalidate", "revalidate"),
        ("requeue", "requeue"),
        ("reassign", "reassign"),
        ("cancel", "cancel"),
        ("integrate", "integrate"),
        ("accept", "accept"),
        ("backend_epoch", "backend_epoch"),
    ],
)
async def test_coordinator_tool_mapping(facade, gateway, tool, method):
    response = await facade.invoke(tool, coordinator(), {"card_id": "META-2"})

    assert response["ok"] is True
    assert gateway.calls == [
        (method, {"card_id": "META-2", "credential": "coordinator-secret"})
    ]


async def test_worker_cannot_self_approve(facade, gateway):
    for tool in ("complete", "integrate", "accept", "backend_epoch", "cancel", "reassign"):
        response = await facade.invoke(tool, worker(), {"card_id": "META-2"})
        assert response == {
            "ok": False,
            "error": {
                "code": "forbidden_tool",
                "message": "tool is not available to this role",
                "details": {"tool": tool, "role": "worker"},
            },
        }
    assert gateway.calls == []


async def test_coordinator_cannot_use_worker_mutations(facade, gateway):
    response = await facade.invoke("claim", coordinator(), {"card_id": "META-2"})
    assert response["ok"] is False
    assert response["error"]["code"] == "forbidden_tool"
    assert gateway.calls == []


async def test_transport_credential_overrides_payload_auth(facade, gateway):
    await facade.invoke(
        "claim",
        worker(),
        {"actor": "impersonated", "role": "coordinator", "credential": "injected"},
    )
    assert gateway.calls == [("claim", {"credential": "worker-secret"})]


async def test_gateway_error_has_stable_shape_and_revision():
    class FailingGateway(FakeGateway):
        def claim(self, **kwargs):
            raise FakeGatewayError("stale_revision", "revision is stale", 12)

    response = await RemoteWorkplanMCP(FailingGateway()).claim(worker(), card_id="META-2")
    assert response == {
        "ok": False,
        "error": {
            "code": "stale_revision",
            "message": "revision is stale",
            "details": {"current_revision": 12},
        },
    }


async def test_unexpected_error_does_not_leak_credentials():
    class FailingGateway(FakeGateway):
        def claim(self, **kwargs):
            raise RuntimeError("Bearer secret-oauth-token")

    response = await RemoteWorkplanMCP(FailingGateway()).claim(worker())
    assert response == {
        "ok": False,
        "error": {"code": "internal_error", "message": "gateway operation failed"},
    }
    assert "secret-oauth-token" not in str(response)


async def test_gateway_results_are_recursively_redacted():
    class CredentialGateway(FakeGateway):
        async def claim(self, **kwargs):
            return {
                "card_id": "META-2",
                "fencing_token": 42,
                "host_credential": "host-secret",
                "nested": {"oauth_token": "oauth-secret", "safe": "visible"},
            }

    response = await RemoteWorkplanMCP(CredentialGateway()).claim(worker())
    assert response == {
        "ok": True,
        "result": {
            "card_id": "META-2",
            "fencing_token": 42,
            "host_credential": "[REDACTED]",
            "nested": {"oauth_token": "[REDACTED]", "safe": "visible"},
        },
    }


async def test_typed_gateway_error_message_redacts_bearer_secret():
    class FailingGateway(FakeGateway):
        def claim(self, **kwargs):
            raise FakeGatewayError("denied", "OAuth failed for Bearer secret-token")

    response = await RemoteWorkplanMCP(FailingGateway()).claim(worker())
    assert response["error"]["message"] == "OAuth failed for Bearer [REDACTED]"
    assert "secret-token" not in str(response)


def test_principal_rejects_unknown_role_and_empty_actor():
    with pytest.raises(ValueError):
        Principal(actor="seat", role="admin")
    with pytest.raises(ValueError):
        Principal(actor="", role="worker")


def test_principal_repr_does_not_expose_credential():
    principal = Principal(actor="seat", role="worker", credential="super-secret")
    assert "super-secret" not in repr(principal)


async def test_real_fastmcp_server_constructs_and_uses_transport_auth(gateway):
    pytest.importorskip("mcp.server.fastmcp")
    server = create_fastmcp(gateway, worker)
    await server.call_tool("claim", {"arguments": {"credential": "injected"}})
    assert gateway.calls == [("claim", {"credential": "worker-secret"})]
