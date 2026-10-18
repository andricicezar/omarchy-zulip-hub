from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import math
import os
import re
import tomllib
from urllib.parse import urlsplit

from .limits import MAX_BACKOFF_SECONDS, MAX_TIMEOUT_SECONDS


class ConfigError(ValueError):
    """Configuration cannot be loaded safely."""


def _number(section: dict, name: str, default: float) -> float:
    """Nombre de configuration, fini et de type attendu.

    ``float("nan")`` traverse toute comparaison de bornes sans jamais la
    déclencher : la finitude se vérifie donc explicitement.
    """
    value = section.get(name, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{name} doit être un nombre")
    if not math.isfinite(value):
        raise ConfigError(f"{name} doit être un nombre fini")
    return float(value)


def _xdg_path(variable: str, fallback: str) -> Path:
    return Path(os.environ.get(variable, str(Path.home() / fallback))).expanduser()


@dataclass(frozen=True)
class Paths:
    config: Path
    state: Path

    @classmethod
    def defaults(cls) -> "Paths":
        return cls(
            config=_xdg_path("XDG_CONFIG_HOME", ".config") / "zulip-hub/config.toml",
            state=_xdg_path("XDG_STATE_HOME", ".local/state") / "zulip-hub/state.json",
        )


def default_account_name(site: str) -> str:
    """Nom court d’un compte, tiré de son serveur.

    « fstar.zulipchat.com » devient « fstar », « zulip.example.org »
    devient « example.org » : assez pour distinguer les comptes dans la liste.
    """
    host = urlsplit(site).hostname or site
    if host.endswith(".zulipchat.com") and host != "zulipchat.com":
        host = host[: -len(".zulipchat.com")]
    elif host.startswith("zulip.") and host.count(".") >= 2:
        host = host[len("zulip."):]
    return host


@dataclass(frozen=True)
class AccountConfig:
    site: str
    email: str
    name: str = ""

    @property
    def label(self) -> str:
        return self.name or default_account_name(self.site)


@dataclass(frozen=True)
class NotificationConfig:
    enabled: bool = True
    private_messages: bool = True
    mentions: bool = True
    followed_topics: bool = True
    other_messages: bool = False
    hide_content_when_locked: bool = True
    group_window_seconds: int = 10
    muted_channels: tuple[str, ...] = ()
    always_channels: tuple[str, ...] = ()


@dataclass(frozen=True)
class OpenConfig:
    mode: str = "auto"
    desktop_command: tuple[str, ...] = ()
    # Un lanceur Omarchy par compte, créé à la connexion, retiré à la déconnexion.
    launchers: bool = True


@dataclass(frozen=True)
class WorkspaceConfig:
    name: str = "zulip"
    class_pattern: str = r"^(Zulip|zulip|zulip-hub|org\.zulip\.Zulip)$"
    launch_command: tuple[str, ...] = ()


@dataclass(frozen=True)
class DevelopmentConfig:
    """Bouton du panneau qui ouvre un agent de code sur le plugin lui-même."""
    enabled: bool = False
    # Vide : le dossier du plugin installé.
    directory: str = ""
    agent_command: tuple[str, ...] = ("claude",)


@dataclass(frozen=True)
class BridgeConfig:
    accounts: tuple[AccountConfig, ...]
    notifications: NotificationConfig = NotificationConfig()
    opening: OpenConfig = OpenConfig()
    workspace: WorkspaceConfig = WorkspaceConfig()
    recent_message_limit: int = 30
    request_timeout_seconds: int = 90
    initial_backoff_seconds: float = 1.0
    max_backoff_seconds: float = 60.0
    development: DevelopmentConfig = DevelopmentConfig()

    def __post_init__(self) -> None:
        if isinstance(self.accounts, AccountConfig):
            object.__setattr__(self, "accounts", (self.accounts,))
        if not self.accounts:
            raise ConfigError("au moins un compte Zulip est requis")

    @property
    def account(self) -> AccountConfig:
        """Premier compte : celui du workspace et des commandes sans --site."""
        return self.accounts[0]

    def account_for(self, site: str | None) -> AccountConfig:
        """Compte configuré pour *site* ; le premier quand *site* est absent."""
        if site is None or site == "":
            return self.account
        normalized = str(site).strip().rstrip("/")
        for account in self.accounts:
            if account.site == normalized:
                return account
        raise ConfigError("ce serveur Zulip n’est pas configuré")


MAX_ACCOUNTS = 16


def _account(entry: object, prefix: str) -> AccountConfig:
    if not isinstance(entry, dict):
        raise ConfigError(f"{prefix} doit être une table")
    site = str(entry.get("site", "")).strip().rstrip("/")
    email = str(entry.get("email", "")).strip()
    name = entry.get("name", "")
    if not site.startswith("https://"):
        raise ConfigError(f"{prefix}.site doit utiliser https://")
    if not email or "@" not in email:
        raise ConfigError(f"{prefix}.email est manquant ou invalide")
    if "key" in entry or "api_key" in entry:
        raise ConfigError("la clé API ne doit pas être stockée dans config.toml")
    if not isinstance(name, str) or len(name.strip()) > 60:
        raise ConfigError(f"{prefix}.name doit être une chaîne de 60 caractères au plus")
    return AccountConfig(site, email, name.strip())


def _accounts(raw: dict) -> tuple[AccountConfig, ...]:
    """Comptes configurés, au format liste ou au format historique.

    ``[[accounts]]`` porte plusieurs comptes ; ``[account]`` reste lu pour les
    configurations écrites avant la prise en charge de plusieurs serveurs.
    """
    if "accounts" in raw and "account" in raw:
        raise ConfigError("[account] et [[accounts]] ne peuvent pas coexister")
    if "accounts" in raw:
        entries = raw["accounts"]
        if not isinstance(entries, list) or not entries:
            raise ConfigError("[[accounts]] doit contenir au moins un compte")
        if len(entries) > MAX_ACCOUNTS:
            raise ConfigError(f"au plus {MAX_ACCOUNTS} comptes sont pris en charge")
        accounts = tuple(_account(entry, "accounts") for entry in entries)
    else:
        accounts = (_account(raw.get("account", {}), "account"),)
    sites = [account.site for account in accounts]
    if len(set(sites)) != len(sites):
        raise ConfigError("un même serveur Zulip ne peut être configuré qu’une fois")
    return accounts


def load_config(path: Path) -> BridgeConfig:
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"configuration introuvable: {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"configuration TOML invalide: {exc}") from exc

    meta = raw.get("meta", {})
    schema_version = meta.get("schema_version", 1)
    if not isinstance(schema_version, int) or isinstance(schema_version, bool):
        raise ConfigError("meta.schema_version doit être un entier")
    if schema_version != 1:
        raise ConfigError(f"version de configuration non prise en charge: {schema_version}")
    accounts = _accounts(raw)

    bridge = raw.get("bridge", {})
    limit = int(_number(bridge, "recent_message_limit", 30))
    timeout = int(_number(bridge, "request_timeout_seconds", 90))
    initial = _number(bridge, "initial_backoff_seconds", 1)
    maximum = _number(bridge, "max_backoff_seconds", 60)
    if not 1 <= limit <= 200:
        raise ConfigError("recent_message_limit doit être compris entre 1 et 200")
    if not 10 <= timeout <= MAX_TIMEOUT_SECONDS:
        raise ConfigError(
            f"request_timeout_seconds doit être compris entre 10 et {MAX_TIMEOUT_SECONDS}"
        )
    if initial <= 0 or maximum < initial or maximum > MAX_BACKOFF_SECONDS:
        raise ConfigError("configuration du backoff invalide")
    notifications = raw.get("notifications", {})
    group_window = int(notifications.get("group_window_seconds", 10))
    if not 1 <= group_window <= 300:
        raise ConfigError("group_window_seconds doit être compris entre 1 et 300")

    def string_tuple(name: str) -> tuple[str, ...]:
        value = notifications.get(name, [])
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise ConfigError(f"notifications.{name} doit être une liste de chaînes")
        return tuple(item.strip() for item in value if item.strip())

    def boolean(name: str, default: bool) -> bool:
        value = notifications.get(name, default)
        if not isinstance(value, bool):
            raise ConfigError(f"notifications.{name} doit être un booléen")
        return value

    notification_config = NotificationConfig(
        enabled=boolean("enabled", True),
        private_messages=boolean("private_messages", True),
        mentions=boolean("mentions", True),
        followed_topics=boolean("followed_topics", True),
        other_messages=boolean("other_messages", False),
        hide_content_when_locked=boolean("hide_content_when_locked", True),
        group_window_seconds=group_window,
        muted_channels=string_tuple("muted_channels"),
        always_channels=string_tuple("always_channels"),
    )

    opening = raw.get("open", {})
    mode = str(opening.get("mode", "auto")).strip().lower()
    if mode not in {"auto", "desktop", "webapp", "browser"}:
        raise ConfigError("open.mode doit valoir auto, desktop, webapp ou browser")
    desktop_command = opening.get("desktop_command", [])
    if not isinstance(desktop_command, list) or not all(
        isinstance(item, str) and item for item in desktop_command
    ):
        raise ConfigError("open.desktop_command doit être une liste de chaînes non vides")
    launchers = opening.get("launchers", True)
    if not isinstance(launchers, bool):
        raise ConfigError("open.launchers doit être un booléen")

    workspace = raw.get("workspace", {})
    workspace_name = str(workspace.get("name", "zulip")).strip()
    if not re.fullmatch(r"[A-Za-z0-9._-]+", workspace_name):
        raise ConfigError("workspace.name contient des caractères non autorisés")
    class_pattern = str(
        workspace.get("class_pattern", r"^(Zulip|zulip|zulip-hub|org\.zulip\.Zulip)$")
    )
    if not class_pattern or len(class_pattern) > 300:
        raise ConfigError("workspace.class_pattern est vide ou trop long")
    if re.search(r"\(\?[=!<]|\\[1-9]", class_pattern):
        raise ConfigError("workspace.class_pattern utilise une fonction non compatible avec RE2")
    try:
        re.compile(class_pattern)
    except re.error as exc:
        raise ConfigError(f"workspace.class_pattern est invalide: {exc}") from exc
    launch_command = workspace.get("launch_command", [])
    if not isinstance(launch_command, list) or not all(
        isinstance(item, str) and item for item in launch_command
    ):
        raise ConfigError("workspace.launch_command doit être une liste de chaînes non vides")

    development = raw.get("development", {})
    if not isinstance(development, dict):
        raise ConfigError("[development] doit être une table")
    dev_enabled = development.get("enabled", False)
    dev_directory = development.get("directory", "")
    agent_command = development.get("agent_command", ["claude"])
    if not isinstance(dev_enabled, bool):
        raise ConfigError("development.enabled doit être un booléen")
    if not isinstance(dev_directory, str) or len(dev_directory) > 500:
        raise ConfigError("development.directory doit être un chemin")
    if (
        not isinstance(agent_command, list) or not agent_command or len(agent_command) > 20
        or not all(isinstance(item, str) and item and len(item) <= 300 for item in agent_command)
    ):
        raise ConfigError("development.agent_command doit être une liste de chaînes non vides")

    return BridgeConfig(
        accounts,
        notification_config,
        OpenConfig(mode, tuple(desktop_command), launchers),
        WorkspaceConfig(workspace_name, class_pattern, tuple(launch_command)),
        limit,
        timeout,
        initial,
        maximum,
        DevelopmentConfig(dev_enabled, dev_directory.strip(), tuple(agent_command)),
    )
