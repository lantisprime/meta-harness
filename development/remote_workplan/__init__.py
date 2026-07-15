"""Development-only transactional workplan coordination.

This package deliberately does not import the Meta-Harness product runtime.  It
is an engineering control-plane helper used by coding-agent seats.
"""

from .gateway import GatewayError, RemoteWorkplanGateway

__all__ = ["GatewayError", "RemoteWorkplanGateway"]
