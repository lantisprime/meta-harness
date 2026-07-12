"""Local logical secret bindings with callback-only plaintext delivery."""
from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from typing import TypeVar

from metaharness.blueprints.models import SecretBindingRef


class SecretBindingNotConfiguredError(LookupError):
    """A logical binding has no configured local value."""


class SecretBindingUseError(RuntimeError):
    """An authorized consumer failed without reflecting secret exception text."""


_T = TypeVar("_T")
_CREDENTIAL_SHAPED_BINDING = re.compile(
    r"(?:sk-(?:live-|test-)?|xox[baprs]-)[a-z0-9.-]{8,}", re.IGNORECASE
)


def validate_secret_binding_name(binding: str) -> str:
    """Validate a logical name and reject names shaped like credential values."""
    name = SecretBindingRef(binding=binding).binding
    if _CREDENTIAL_SHAPED_BINDING.search(name):
        raise ValueError("secret binding name resembles credential material")
    return name


class LocalSecretBindingRegistry:
    """In-process binding registry intended to be owned by Settings/state.

    The registry is never serialized. ``use`` is the sole plaintext access path:
    it invokes an authorized provider callback at the last possible moment and
    suppresses callback exception text so a provider cannot accidentally echo a
    credential into an API error or journal event.
    """

    def __init__(self, values: Mapping[str, str] | None = None) -> None:
        self._values: dict[str, str] = {}
        for binding, value in (values or {}).items():
            self.configure(binding, value)

    def configure(self, binding: str, value: str) -> None:
        name = validate_secret_binding_name(binding)
        if not value:
            raise ValueError("secret binding value cannot be empty")
        self._values[name] = value

    def remove(self, binding: str) -> bool:
        return self._values.pop(binding, None) is not None

    def is_configured(self, binding: str) -> bool:
        return binding in self._values

    def use(self, binding: str, consumer: Callable[[str], _T]) -> _T:
        if binding not in self._values:
            raise SecretBindingNotConfiguredError(
                f"secret binding {binding!r} is not configured"
            )
        plaintext = self._values[binding]
        try:
            return consumer(plaintext)
        except Exception as exc:
            raise SecretBindingUseError(
                f"secret binding {binding!r} consumer failed ({type(exc).__name__})"
            ) from None
