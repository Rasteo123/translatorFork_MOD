from PyQt6.QtCore import QObject, pyqtSlot

from .ssh_tunnel import SshTunnelManager


class GlobalProxyController(QObject):
    """Relays proxy settings and supervises the optional local SSH tunnel."""

    def __init__(self, event_bus, tunnel_manager=None):
        super().__init__()
        self.event_bus = event_bus
        self._tunnel = (
            tunnel_manager
            if tunnel_manager is not None
            else SshTunnelManager(parent=self)
        )
        self._tunnel_config = None
        self._current_settings = {}
        self._tunnel_state = "down"
        self._tunnel_message = ""

        event_bus.event_posted.connect(self.on_event)
        self._tunnel.status_changed.connect(self._on_tunnel_status_changed)
        print("[PROXY] GlobalProxyController инициализирован.")

    @pyqtSlot(dict)
    def on_event(self, event: dict):
        if not isinstance(event, dict):
            return
        if event.get("event") not in {"proxy_started", "proxy_settings_changed"}:
            return
        self.apply_settings(event.get("data", {}))

    def apply_settings(self, settings: dict) -> None:
        """Apply settings immediately, including during headless app startup."""
        self._current_settings = dict(settings) if isinstance(settings, dict) else {}
        self._sync_tunnel(self._current_settings)
        self._emit_current_status()

    def _sync_tunnel(self, settings: dict) -> None:
        wants_tunnel = (
            bool(settings.get("enabled"))
            and settings.get("tunnel_mode") == "ssh"
        )
        if not wants_tunnel:
            # active=False can also mean the manager is waiting on its retry timer.
            self._tunnel.stop()
            self._tunnel_config = None
            self._tunnel_state = "down"
            self._tunnel_message = ""
            return

        try:
            params = {
                "ssh_host": self._required_text(settings.get("ssh_host")),
                "ssh_user": self._required_text(settings.get("ssh_user")),
                "ssh_key_path": self._required_text(settings.get("ssh_key_path")),
            }
        except (TypeError, ValueError):
            self._stop_with_configuration_error("Настройки SSH-туннеля заполнены не полностью")
            return
        try:
            params["ssh_port"] = self._validated_port(settings.get("ssh_port", 22))
            params["local_port"] = self._validated_port(settings.get("port"))
        except (TypeError, ValueError):
            self._stop_with_configuration_error("Некорректный порт SSH-туннеля")
            return

        config = tuple(params.items())
        if config == self._tunnel_config:
            # The manager may currently be waiting for its own backoff timer.
            return

        if self._tunnel_config is not None or self._tunnel.active:
            self._tunnel.stop()
        self._tunnel_config = config
        self._tunnel_state = "connecting"
        self._tunnel_message = ""
        self._tunnel.start(**params)

    @staticmethod
    def _required_text(value) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("required text is missing")
        return value.strip()

    @staticmethod
    def _validated_port(value) -> int:
        if isinstance(value, bool):
            raise ValueError("boolean is not a port")
        if isinstance(value, int):
            port = value
        elif isinstance(value, str):
            normalized = value.strip()
            if not normalized.isascii() or not normalized.isdecimal():
                raise ValueError("port must contain decimal digits")
            port = int(normalized)
        else:
            raise ValueError("port must be an integer")
        if not 1 <= port <= 65535:
            raise ValueError("port is out of range")
        return port

    def _stop_with_configuration_error(self, message: str) -> None:
        self._tunnel.stop()
        self._tunnel_config = None
        self._tunnel_state = "error"
        self._tunnel_message = message

    def _on_tunnel_status_changed(self, state: str, message: str) -> None:
        self._tunnel_state = state
        self._tunnel_message = message
        self._emit_current_status()

    def _emit_current_status(self) -> None:
        data = dict(self._current_settings)
        if data.get("tunnel_mode") == "ssh":
            data["tunnel_state"] = self._tunnel_state
            data["tunnel_message"] = self._tunnel_message
        self.event_bus.event_posted.emit({
            "event": "current_proxy_status",
            "source": "GlobalProxyController",
            "data": data,
        })

    def shutdown(self) -> None:
        # Always call stop: active=False can mean the manager is awaiting retry.
        self._tunnel.stop()
        self._tunnel_config = None
