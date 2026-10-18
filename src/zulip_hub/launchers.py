from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import re
from urllib.parse import urljoin

from . import commands
from .api import ZulipAPIError, ZulipClient
from .commands import CommandError
from .files import write_atomic


LOGGER = logging.getLogger("zulip_hub.launchers")
INSTALL = "omarchy-webapp-install"
REMOVE = "omarchy-webapp-remove"


def launcher_name(label: str) -> str:
    """Nom du lanceur : « FStar Zulip ». Le nom devient un nom de fichier."""
    name = " ".join(re.sub(r"[/\\\x00-\x1f]", " ", label).split()) or "Zulip"
    return name if name.casefold().endswith("zulip") else f"{name} Zulip"


def _applications() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local/share")
    return Path(base) / "applications"


class WebappLaunchers:
    """Un lanceur Omarchy (Super+Espace) par compte connecté.

    Les lanceurs créés ici sont notés dans ``launchers.json`` : seuls ceux-là
    sont retirés à la déconnexion. Un lanceur web déjà présent pour le même
    serveur, créé à la main, est remplacé plutôt que doublé.
    """

    def __init__(self, state_dir: Path, applications: Path | None = None):
        self.registry_path = state_dir / "launchers.json"
        self.applications = applications or _applications()

    def _registry(self) -> dict[str, str]:
        try:
            value = json.loads(self.registry_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return {}
        if not isinstance(value, dict):
            return {}
        return {str(site): str(name) for site, name in value.items() if isinstance(name, str)}

    def _save(self, registry: dict[str, str]) -> None:
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)
        write_atomic(self.registry_path, json.dumps(registry, ensure_ascii=False, indent=2), 0o600)

    @staticmethod
    def active() -> bool:
        """Seul le plugin installé touche aux lanceurs de l’utilisateur.

        ``run-plugin.py`` pose ZULIP_HUB_EMBEDDED ; une suite de tests ou un
        appel manuel de la bibliothèque ne crée ni ne supprime rien.
        """
        return os.environ.get("ZULIP_HUB_EMBEDDED") == "1"

    def _run(self, argv: list[str]) -> bool:
        if not self.active() or not commands.available(argv[0]):
            return False
        try:
            result = commands.run(argv, timeout=45, env_extra={"OMARCHY_REMOVE_NOTIFY": "false"})
        except CommandError as exc:
            LOGGER.warning("%s a échoué: %s", argv[0], type(exc).__name__)
            return False
        return result.returncode == 0

    @staticmethod
    def _icon(site: str) -> str:
        """Icône de l’organisation, telle que la publie le serveur."""
        try:
            icon = str(ZulipClient.anonymous(site, timeout=10).server_settings().get("realm_icon") or "")
        except ZulipAPIError:
            return ""
        url = urljoin(site.rstrip("/") + "/", icon)
        return url if url.startswith("https://") else ""

    def _same_site_launchers(self, site: str) -> list[str]:
        """Lanceurs web existants qui ouvrent déjà ce serveur."""
        wanted = site.rstrip("/")
        found = []
        for path in sorted(self.applications.glob("*.desktop")):
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for line in text.splitlines():
                match = re.fullmatch(r'Exec=omarchy-launch-webapp "([^"]+)"', line.strip())
                if match and match.group(1).rstrip("/") == wanted:
                    found.append(path.stem)
        return found

    def install(self, site: str, label: str) -> bool:
        if not self.active():
            return False
        registry = self._registry()
        name = launcher_name(label)
        stale = [registry.get(site)] + self._same_site_launchers(site)
        for old in dict.fromkeys(item for item in stale if item and item != name):
            self._run([REMOVE, old])
        icon = self._icon(site)
        installed = (icon and self._run([INSTALL, name, site, icon])) or self._run(
            [INSTALL, name, site, ""]
        )
        if installed:
            registry[site] = name
            self._save(registry)
        return bool(installed)

    def remove(self, site: str) -> bool:
        if not self.active():
            return False
        registry = self._registry()
        name = registry.pop(site, None)
        if name is None:
            return False
        self._run([REMOVE, name])
        self._save(registry)
        return True
