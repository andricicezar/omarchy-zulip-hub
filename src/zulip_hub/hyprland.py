from __future__ import annotations

import json
import re
from typing import Any, Callable, Sequence

from . import commands
from .commands import CommandError
from .config import BridgeConfig, WorkspaceConfig


class HyprlandError(RuntimeError):
    """Hyprland state or dispatch is unavailable."""


class HyprlandController:
    def __init__(
        self,
        config: WorkspaceConfig,
        launch_command: Sequence[str],
        *,
        run: Callable[..., Any] = commands.run,
        spawn: Callable[..., Any] = commands.spawn,
    ):
        self.config = config
        self.launch_command = list(launch_command)
        self.run = run
        self.spawn = spawn
        self.class_regex = re.compile(config.class_pattern)

    def clients(self) -> list[dict[str, Any]]:
        payload = self._json_command(["hyprctl", "-j", "clients"], "clients Hyprland")
        if not isinstance(payload, list):
            raise HyprlandError("réponse clients Hyprland invalide")
        return [client for client in payload if isinstance(client, dict)]

    def monitors(self) -> list[dict[str, Any]]:
        payload = self._json_command(["hyprctl", "-j", "monitors"], "moniteurs Hyprland")
        if not isinstance(payload, list):
            raise HyprlandError("réponse moniteurs Hyprland invalide")
        return [monitor for monitor in payload if isinstance(monitor, dict)]

    def matching_client(self) -> dict[str, Any] | None:
        for client in self.clients():
            candidates = (client.get("class", ""), client.get("initialClass", ""))
            if any(self.class_regex.fullmatch(str(value)) for value in candidates):
                return client
        return None

    def workspace_visible(self) -> bool:
        expected = f"special:{self.config.name}"
        for monitor in self.monitors():
            special = monitor.get("specialWorkspace", {})
            if isinstance(special, dict) and special.get("name") == expected:
                return True
        return False

    def toggle(self) -> str:
        client = self.matching_client()
        if self.workspace_visible():
            self.dispatch("togglespecialworkspace", self.config.name)
            return "hidden"

        expected_workspace = f"special:{self.config.name}"
        if client is not None:
            workspace = client.get("workspace", {})
            current_name = workspace.get("name") if isinstance(workspace, dict) else ""
            address = str(client.get("address", ""))
            if current_name != expected_workspace and address:
                self.dispatch(
                    "movetoworkspacesilent", f"{expected_workspace},address:{address}"
                )
            self.dispatch("togglespecialworkspace", self.config.name)
            return "shown"

        if not self.launch_command:
            raise HyprlandError("aucune commande de lancement Zulip disponible")
        self.dispatch("togglespecialworkspace", self.config.name)
        try:
            self.spawn(self.launch_command)
        except (CommandError, OSError) as exc:
            # Avoid leaving an empty special workspace visible after a failed launch.
            try:
                self.dispatch("togglespecialworkspace", self.config.name)
            except HyprlandError:
                pass
            raise HyprlandError("impossible de lancer Zulip") from exc
        return "launched"

    def status(self) -> dict[str, Any]:
        client = self.matching_client()
        return {
            "workspace": f"special:{self.config.name}",
            "visible": self.workspace_visible(),
            "client": None if client is None else {
                "address": client.get("address", ""),
                "class": client.get("class", ""),
                "initialClass": client.get("initialClass", ""),
                "title": client.get("title", ""),
                "workspace": client.get("workspace", {}),
            },
            "launch_command": self.launch_command,
        }

    def dispatch(self, dispatcher: str, argument: str) -> None:
        result = self._run(["hyprctl", "dispatch", dispatcher, argument])
        if result.returncode != 0:
            raise HyprlandError(f"dispatch Hyprland refusé: {dispatcher}")

    def focus_window(self, address: str) -> None:
        """Met une fenêtre au premier plan, avec la syntaxe Lua puis l’ancienne.

        Depuis Hyprland 0.56, ``dispatch`` évalue du Lua : l’adresse y est
        insérée, d’où sa vérification stricte avant tout.
        """
        if not re.fullmatch(r"0x[0-9a-fA-F]+", address):
            raise HyprlandError("adresse de fenêtre invalide")
        lua = f'hl.dsp.focus({{ window = "address:{address}" }})'
        if self._run(["hyprctl", "dispatch", lua]).returncode == 0:
            return
        self.dispatch("focuswindow", f"address:{address}")

    def _json_command(self, command: list[str], label: str) -> Any:
        result = self._run(command)
        if result.returncode != 0:
            raise HyprlandError(f"{label} indisponible")
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise HyprlandError(f"réponse JSON invalide pour {label}") from exc

    def _run(self, command: list[str]) -> Any:
        try:
            return self.run(command, timeout=3)
        except CommandError as exc:
            raise HyprlandError("hyprctl indisponible") from exc


def resolve_launch_command(config: BridgeConfig) -> list[str]:
    if config.workspace.launch_command:
        return list(config.workspace.launch_command)
    desktop = list(config.opening.desktop_command)
    if desktop and commands.available(desktop[0]):
        if commands.available("uwsm-app"):
            return ["uwsm-app", "--", *desktop]
        return desktop
    if commands.available("omarchy-launch-webapp"):
        return ["omarchy-launch-webapp", config.account.site, "--class=zulip-hub"]
    raise HyprlandError(
        "aucun client Zulip ni lanceur webapp Omarchy disponible; configurez workspace.launch_command"
    )
