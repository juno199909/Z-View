from __future__ import annotations

from dataclasses import dataclass, field

from Common.models import TransportSettings


@dataclass(slots=True)
class TransportProfile:
    settings: TransportSettings = field(default_factory=TransportSettings)
    connection_mode: str = "persistent_websocket"
    session_resumption: bool = True

    def to_dict(self) -> dict:
        payload = self.settings.to_dict()
        payload.update(
            {
                "connection_mode": self.connection_mode,
                "session_resumption": self.session_resumption,
            }
        )
        return payload
