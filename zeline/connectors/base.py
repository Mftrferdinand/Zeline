"""Base class every Zeline connector implements."""
from __future__ import annotations

import abc


class BaseConnector(abc.ABC):
    """Contract for an external-service connector.

    Subclasses set ``id``, ``name``, ``description`` and ``auth_kind``
    (``"pat"`` for a pasted token, ``"oauth2"`` for a browser flow), then
    implement the four lifecycle methods below.
    """

    id: str = ""
    name: str = ""
    description: str = ""
    auth_kind: str = "pat"  # "pat" | "oauth2"

    @abc.abstractmethod
    def connect(self, **kwargs) -> str:
        """Link the service. Returns a human-readable result line.

        Must validate the credential before storing it; on failure return a
        string starting with ``"ERROR:"`` and store nothing.
        """

    @abc.abstractmethod
    def disconnect(self) -> str:
        """Unlink the service and delete the stored credential."""

    @abc.abstractmethod
    def status(self) -> dict:
        """Return ``{"connected": bool, "detail": str}``. Never include secrets."""

    def is_connected(self) -> bool:
        """True when :meth:`status` reports a live connection."""
        try:
            return bool(self.status().get("connected"))
        except Exception:
            return False
