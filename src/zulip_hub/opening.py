from __future__ import annotations

from typing import Sequence
from urllib.parse import urlsplit

from . import commands
from .commands import CommandError
from .config import OpenConfig


class OpenError(RuntimeError):
    """The message URL could not be opened."""


class UrlOpener:
    def __init__(self, config: OpenConfig):
        self.config = config

    def command(self, url: str) -> list[str]:
        desktop = list(self.config.desktop_command)
        if self.config.mode in {"auto", "desktop"} and desktop:
            if commands.available(desktop[0]):
                return [*desktop, url]
            if self.config.mode == "desktop":
                raise OpenError(f"application Zulip introuvable: {desktop[0]}")
        if self.config.mode == "desktop":
            raise OpenError("open.desktop_command doit être configuré en mode desktop")
        # La fenêtre d’application du compte, comme son lanceur Super+Espace.
        if self.config.mode in {"auto", "webapp"} and commands.available("omarchy-launch-webapp"):
            return ["omarchy-launch-webapp", url]
        if self.config.mode == "webapp":
            raise OpenError("omarchy-launch-webapp est introuvable")
        if commands.available("uwsm-app"):
            return ["uwsm-app", "--", "xdg-open", url]
        if commands.available("xdg-open"):
            return ["xdg-open", url]
        raise OpenError("aucun ouvreur d’URL disponible")

    def open(self, url: str) -> None:
        try:
            commands.spawn(self.command(url))
        except CommandError as exc:
            raise OpenError("impossible d’ouvrir le message Zulip") from exc



def app_window(clients: list[dict], site: str) -> dict | None:
    """Fenêtre d’application web ouverte sur *site*, s’il y en a une.

    Chromium nomme ces fenêtres d’après l’hôte : « chrome-fstar.zulipchat.com__-Default ».
    Un hôte ne contient jamais « _ » : le suffixe marque la fin du nom exact.
    """
    host = (urlsplit(site).hostname or "").lower()
    if not host:
        return None
    for client in clients:
        window_class = str(client.get("class") or "").lower()
        _browser, _sep, rest = window_class.partition("-")
        if rest.startswith(host + "_"):
            return client
    return None


class AccountOpener:
    """Met au premier plan l’application web d’un compte, ou la lance."""

    def __init__(self, hyprland):
        self.hyprland = hyprland

    def open(self, site: str) -> str:
        from .hyprland import HyprlandError
        try:
            window = app_window(self.hyprland.clients(), site)
        except HyprlandError:
            window = None
        if window is not None and window.get("address"):
            try:
                self.hyprland.focus_window(str(window["address"]))
                return "focused"
            except HyprlandError:
                pass
        if not commands.available("omarchy-launch-webapp"):
            raise OpenError("omarchy-launch-webapp est introuvable")
        try:
            commands.spawn(["omarchy-launch-webapp", site])
        except CommandError as exc:
            raise OpenError("impossible de lancer l’application Zulip") from exc
        return "launched"
