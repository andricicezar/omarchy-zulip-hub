from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
import shlex
import sys
from typing import Any, TextIO
from urllib.parse import urlsplit

from .api import ZulipAPIError, ZulipClient
from .browser_login import LoginError, LoginManager
from .config import MAX_ACCOUNTS, AccountConfig, ConfigError, Paths, load_config
from .files import write_atomic
from .launchers import WebappLaunchers, launcher_name
from .limits import encoded_response
from .secrets import SecretError, SecretToolProvider


class OnboardingError(RuntimeError):
    """A graphical onboarding request was invalid or failed."""


# Actions de connexion sans clé, traitées par browser_login.LoginManager.
LOGIN_ACTIONS = {
    "login_methods": "methods",
    "login_start": "start",
    "login_status": "status",
    "login_cancel": "cancel",
    "login_password": "password",
    "import_zuliprc": "import_zuliprc",
}


@dataclass
class OnboardingManager:
    config_path: Path
    secrets: SecretToolProvider | None = None
    state_path: Path | None = None

    def __post_init__(self) -> None:
        if self.secrets is None:
            self.secrets = SecretToolProvider()
        if self.state_path is None:
            self.state_path = Paths.defaults().state

    @staticmethod
    def _settings(config: Any) -> dict[str, Any]:
        notifications = config.notifications
        return {
            "notifications_enabled": notifications.enabled,
            "private_messages": notifications.private_messages,
            "mentions": notifications.mentions,
            "followed_topics": notifications.followed_topics,
            "other_messages": notifications.other_messages,
            "hide_content_when_locked": notifications.hide_content_when_locked,
            "group_window_seconds": notifications.group_window_seconds,
            "muted_channels": list(notifications.muted_channels),
            "always_channels": list(notifications.always_channels),
            "open_mode": config.opening.mode,
            "desktop_command": list(config.opening.desktop_command),
            "launchers": config.opening.launchers,
            "workspace_launch_command": list(config.workspace.launch_command),
            "desktop_command_text": shlex.join(config.opening.desktop_command),
            "workspace_launch_command_text": shlex.join(config.workspace.launch_command),
        }

    def _service_state(self) -> str:
        """Le bridge suit le cycle de vie du shell, pas celui d’un service.

        Sa mise en pause tient dans un marqueur que le lanceur consulte au
        démarrage ; le plugin ne pilote aucun service utilisateur.
        """
        return "inactive" if self._pause_path().exists() else "active"

    def _pause_path(self) -> Path:
        assert self.state_path is not None
        return self.state_path.parent / "paused"

    def _request_restart(self) -> None:
        assert self.state_path is not None
        marker = self.state_path.parent / "restart"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch()

    @staticmethod
    def _is_example(site: str, email: str) -> bool:
        return (
            urlsplit(site).hostname in {"zulip.example.com", "example.com"}
            or email.endswith("@example.com")
        )

    def _real_accounts(self, config: Any) -> list[AccountConfig]:
        """Comptes saisis par l’utilisateur, sans l’exemple livré par défaut."""
        return [
            account for account in config.accounts
            if not self._is_example(account.site, account.email)
        ]

    @property
    def launchers(self) -> WebappLaunchers:
        assert self.state_path is not None
        return WebappLaunchers(self.state_path.parent)

    def _launchers_enabled(self) -> bool:
        try:
            return load_config(self.config_path).opening.launchers
        except ConfigError:
            return True

    def _sync_launchers(self, enabled: bool) -> None:
        """Le réglage change : lanceurs créés pour tous les comptes, ou retirés."""
        for account in self._current_accounts():
            if not enabled:
                self.launchers.remove(account.site)
            elif self._has_secret(account):
                self.launchers.install(account.site, account.label)

    def _has_secret(self, account: AccountConfig) -> bool:
        try:
            assert self.secrets is not None
            self.secrets.get(account.site, account.email)
            return True
        except SecretError:
            return False

    def status(self) -> dict[str, Any]:
        config = None
        accounts: list[dict[str, Any]] = []
        try:
            config = load_config(self.config_path)
            for account in self._real_accounts(config):
                accounts.append({
                    "site": account.site,
                    "email": account.email,
                    "name": account.name,
                    "label": account.label,
                    "has_secret": self._has_secret(account),
                })
        except ConfigError:
            pass
        first = accounts[0] if accounts else {}
        has_secret = any(account["has_secret"] for account in accounts)
        service = self._service_state()
        return {
            "ok": True,
            "configured": has_secret,
            "site": first.get("site", ""),
            "email": first.get("email", ""),
            "has_secret": has_secret,
            "accounts": accounts,
            "max_accounts": MAX_ACCOUNTS,
            "service": service,
            "active": service == "active",
            "settings": self._settings(config) if config is not None else {},
        }

    @staticmethod
    def _replace_section(text: str, name: str, values: list[tuple[str, Any]]) -> str:
        lines = text.splitlines()
        header = f"[{name}]"
        start = next((i for i, line in enumerate(lines) if line.strip() == header), None)
        rendered = []
        for key, value in values:
            if isinstance(value, bool):
                encoded = "true" if value else "false"
            else:
                encoded = json.dumps(value, ensure_ascii=False)
            rendered.append(f"{key} = {encoded}")
        if start is None:
            return "\n".join(lines).rstrip() + f"\n\n{header}\n" + "\n".join(rendered) + "\n"
        end = next(
            (i for i in range(start + 1, len(lines)) if lines[i].strip().startswith("[")),
            len(lines),
        )
        keys = {key for key, _ in values}
        body = [
            line for line in lines[start + 1:end]
            if not any(re.match(rf"^\s*{re.escape(key)}\s*=", line) for key in keys)
        ]
        updated = lines[:start + 1] + rendered + body + lines[end:]
        return "\n".join(updated).rstrip() + "\n"

    @staticmethod
    def _bool(request: dict[str, Any], name: str) -> bool:
        value = request.get(name)
        if not isinstance(value, bool):
            raise OnboardingError(f"Le réglage {name} doit être un booléen.")
        return value

    @staticmethod
    def _channels(request: dict[str, Any], name: str) -> list[str]:
        value = request.get(name, [])
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise OnboardingError(f"Le réglage {name} doit être une liste.")
        result = []
        for item in value:
            normalized = item.strip()
            if normalized and normalized not in result:
                if len(normalized) > 100:
                    raise OnboardingError("Un nom de canal est trop long.")
                result.append(normalized)
        if len(result) > 100:
            raise OnboardingError("La liste de canaux est trop longue.")
        return result

    @staticmethod
    def _command(request: dict[str, Any], name: str) -> list[str]:
        raw = str(request.get(name, "")).strip()
        if len(raw) > 1000:
            raise OnboardingError("La commande de lancement est trop longue.")
        try:
            result = shlex.split(raw)
        except ValueError as exc:
            raise OnboardingError("La commande de lancement contient des guillemets invalides.") from exc
        if len(result) > 20 or any(not item or len(item) > 300 for item in result):
            raise OnboardingError("La commande de lancement est invalide.")
        return result

    def save_settings(self, request: dict[str, Any]) -> dict[str, Any]:
        group_window = request.get("group_window_seconds")
        if not isinstance(group_window, int) or isinstance(group_window, bool) or not 1 <= group_window <= 300:
            raise OnboardingError("Le regroupement doit être compris entre 1 et 300 secondes.")
        open_mode = str(request.get("open_mode", "auto"))
        if open_mode not in {"auto", "desktop", "browser"}:
            raise OnboardingError("Le mode d’ouverture est invalide.")
        desktop_command = self._command(request, "desktop_command")
        launchers = request.get("launchers", True)
        if not isinstance(launchers, bool):
            raise OnboardingError("Le réglage launchers doit être un booléen.")
        previous_launchers = self._launchers_enabled()
        workspace_command = self._command(request, "workspace_launch_command")
        original = self.config_path.read_text(encoding="utf-8")
        updated = self._replace_section(original, "notifications", [
            ("enabled", self._bool(request, "notifications_enabled")),
            ("private_messages", self._bool(request, "private_messages")),
            ("mentions", self._bool(request, "mentions")),
            ("followed_topics", self._bool(request, "followed_topics")),
            ("other_messages", self._bool(request, "other_messages")),
            ("hide_content_when_locked", self._bool(request, "hide_content_when_locked")),
            ("group_window_seconds", group_window),
            ("muted_channels", self._channels(request, "muted_channels")),
            ("always_channels", self._channels(request, "always_channels")),
        ])
        updated = self._replace_section(updated, "open", [
            ("mode", open_mode), ("desktop_command", desktop_command), ("launchers", launchers),
        ])
        updated = self._replace_section(updated, "workspace", [
            ("launch_command", workspace_command),
        ])
        try:
            write_atomic(self.config_path, updated, 0o600)
            load_config(self.config_path)
        except Exception:
            write_atomic(self.config_path, original, 0o600)
            raise
        self._request_restart()
        if launchers != previous_launchers:
            self._sync_launchers(launchers)
        result = self.status()
        result["message"] = "Réglages enregistrés."
        return result

    def diagnostics(self) -> dict[str, Any]:
        state: dict[str, Any] = {}
        try:
            assert self.state_path is not None
            loaded = json.loads(self.state_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                state = loaded
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            pass
        return {
            "ok": True,
            "service": self._service_state(),
            "connected": state.get("connected") is True,
            "last_sync": state.get("last_sync"),
            "bridge_error": str(state.get("error") or ""),
            "state_available": bool(state),
            "state_schema": state.get("schema_version"),
            "accounts": [
                {
                    "name": str(entry.get("name") or entry.get("site") or ""),
                    "site": str(entry.get("site") or ""),
                    "connected": entry.get("connected") is True,
                    "error": str(entry.get("error") or ""),
                    "last_sync": entry.get("last_sync"),
                }
                for entry in state.get("accounts", []) or []
                if isinstance(entry, dict)
            ],
        }

    def reconnect(self) -> dict[str, Any]:
        status = self.status()
        if not status["configured"]:
            raise OnboardingError("Configurez d’abord le compte Zulip.")
        self._pause_path().unlink(missing_ok=True)
        self._request_restart()
        result = self.diagnostics()
        result["message"] = "Reconnexion du bridge demandée."
        return result

    @staticmethod
    def _credentials(request: dict[str, Any]) -> tuple[str, str, str]:
        site = str(request.get("site", "")).strip().rstrip("/")
        email = str(request.get("email", "")).strip()
        api_key = str(request.get("api_key", "")).strip()
        parsed = urlsplit(site)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise OnboardingError("L’URL Zulip doit être une adresse HTTPS valide.")
        if parsed.query or parsed.fragment:
            raise OnboardingError("L’URL Zulip ne doit contenir ni paramètres ni fragment.")
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
            raise OnboardingError("L’adresse e-mail est invalide.")
        if not api_key or len(api_key) > 500:
            raise OnboardingError("La clé API est vide ou invalide.")
        return site, email, api_key

    @staticmethod
    def _name(request: dict[str, Any]) -> str:
        name = request.get("name", "")
        if not isinstance(name, str):
            raise OnboardingError("Le nom du compte est invalide.")
        name = " ".join(name.split())
        if len(name) > 60:
            raise OnboardingError("Le nom du compte dépasse 60 caractères.")
        return name

    @staticmethod
    def _without_account_sections(lines: list[str]) -> list[str]:
        result: list[str] = []
        skipping = False
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("["):
                skipping = stripped in {"[account]", "[[accounts]]"}
            if not skipping:
                result.append(line)
        return result

    def _write_accounts(self, accounts: list[AccountConfig]) -> None:
        """Réécrit les comptes au format ``[[accounts]]``, le reste intact.

        L’ancienne section ``[account]`` disparaît au passage : la
        configuration n’a plus qu’une seule forme dès qu’elle est réécrite.
        """
        try:
            text = self.config_path.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise OnboardingError("Le fichier de configuration n’est pas installé.") from exc
        if not accounts:
            raise OnboardingError("Au moins un compte doit rester configuré.")
        rest = self._without_account_sections(text.splitlines())
        while rest and not rest[0].strip():
            rest.pop(0)
        blocks: list[str] = []
        for account in accounts:
            blocks.append("[[accounts]]")
            if account.name:
                blocks.append(f"name = {json.dumps(account.name, ensure_ascii=False)}")
            blocks.append(f"site = {json.dumps(account.site, ensure_ascii=False)}")
            blocks.append(f"email = {json.dumps(account.email, ensure_ascii=False)}")
            blocks.append("")
        original = text
        write_atomic(self.config_path, "\n".join(blocks + rest).rstrip() + "\n", 0o600)
        try:
            load_config(self.config_path)
        except Exception:
            write_atomic(self.config_path, original, 0o600)
            raise

    def _current_accounts(self) -> list[AccountConfig]:
        try:
            return self._real_accounts(load_config(self.config_path))
        except ConfigError:
            return []

    def setup(self, request: dict[str, Any]) -> dict[str, Any]:
        """Ajoute un compte, ou remplace celui désigné par ``original_site``."""
        site, email, api_key = self._credentials(request)
        name = self._name(request)
        original_site = str(request.get("original_site") or "").strip().rstrip("/")
        accounts = self._current_accounts()
        replaced = next(
            (account for account in accounts if account.site in {original_site, site}),
            None,
        )
        if replaced is None and len(accounts) >= MAX_ACCOUNTS:
            raise OnboardingError(f"Au plus {MAX_ACCOUNTS} comptes sont pris en charge.")
        if original_site and original_site != site and any(
            account.site == site for account in accounts
        ):
            raise OnboardingError("Ce serveur Zulip est déjà configuré.")
        identity = ZulipClient(site, email, api_key, timeout=15).test_connection()
        assert self.secrets is not None
        self.secrets.store(site, email, api_key)
        updated = AccountConfig(site, email, name)
        if replaced is None:
            accounts.append(updated)
        else:
            accounts = [updated if account is replaced else account for account in accounts]
        self._write_accounts(accounts)
        if replaced is not None and (replaced.site, replaced.email) != (site, email):
            try:
                self.secrets.delete(replaced.site, replaced.email)
            except SecretError:
                pass
        launcher_added = False
        if self._launchers_enabled():
            if replaced is not None and replaced.site != site:
                self.launchers.remove(replaced.site)
            launcher_added = self.launchers.install(site, updated.label)
        self._pause_path().unlink(missing_ok=True)
        self._request_restart()
        result = self.status()
        result["identity"] = str(identity.get("full_name") or identity.get("email") or email)
        result["message"] = "Connexion réussie. Zulip Hub est actif." + (
            f" Lanceur « {launcher_name(updated.label)} » ajouté." if launcher_added else ""
        )
        return result

    def remove_account(self, request: dict[str, Any]) -> dict[str, Any]:
        """Retire un compte et sa clé ; le dernier est seulement déconnecté."""
        site = str(request.get("site") or "").strip().rstrip("/")
        accounts = self._current_accounts()
        target = next((account for account in accounts if account.site == site), None)
        if target is None:
            raise OnboardingError("Ce compte n’est pas configuré.")
        if len(accounts) == 1:
            return self.disconnect()
        self._write_accounts([account for account in accounts if account is not target])
        assert self.secrets is not None
        self.secrets.delete(target.site, target.email)
        self.launchers.remove(target.site)
        self._request_restart()
        result = self.status()
        result["message"] = f"Compte {target.label} retiré."
        return result

    def deactivate(self) -> dict[str, Any]:
        pause = self._pause_path()
        pause.parent.mkdir(parents=True, exist_ok=True)
        pause.touch()
        self._request_restart()
        result = self.status()
        result["message"] = "Zulip Hub est en pause."
        return result

    def activate(self) -> dict[str, Any]:
        try:
            config = load_config(self.config_path)
        except ConfigError as exc:
            raise OnboardingError("Le compte doit être reconnecté avant activation.") from exc
        if not any(self._has_secret(account) for account in config.accounts):
            raise OnboardingError("Le compte doit être reconnecté avant activation.")
        self._pause_path().unlink(missing_ok=True)
        self._request_restart()
        result = self.status()
        result["message"] = "Zulip Hub est actif."
        return result

    def disconnect(self) -> dict[str, Any]:
        try:
            config = load_config(self.config_path)
            self.deactivate()
            assert self.secrets is not None
            self.secrets.delete(config.account.site, config.account.email)
            self.launchers.remove(config.account.site)
        except ConfigError as exc:
            raise OnboardingError(str(exc)) from exc
        result = self.status()
        result["message"] = "Clé supprimée du trousseau."
        return result

    def handle(self, request: dict[str, Any]) -> dict[str, Any]:
        action = str(request.get("action", "status"))
        if action == "status":
            return self.status()
        if action == "setup":
            return self.setup(request)
        if action == "deactivate":
            return self.deactivate()
        if action == "activate":
            return self.activate()
        if action == "disconnect":
            return self.disconnect()
        if action == "remove_account":
            return self.remove_account(request)
        if action in LOGIN_ACTIONS:
            assert self.state_path is not None
            login = LoginManager(self, self.state_path.parent)
            return getattr(login, LOGIN_ACTIONS[action])(request)
        if action == "save_settings":
            return self.save_settings(request)
        if action == "diagnostics":
            return self.diagnostics()
        if action == "reconnect":
            return self.reconnect()
        raise OnboardingError("Action d’onboarding inconnue.")


def serve_once(
    config_path: Path | None = None,
    input_stream: TextIO = sys.stdin,
    output_stream: TextIO = sys.stdout,
) -> int:
    try:
        raw = input_stream.readline(65537)
        if not raw or len(raw) > 65536:
            raise OnboardingError("Requête d’onboarding absente ou trop volumineuse.")
        request = json.loads(raw)
        if not isinstance(request, dict):
            raise OnboardingError("La requête d’onboarding doit être un objet JSON.")
        response = OnboardingManager(config_path or Paths.defaults().config).handle(request)
    except (
        OnboardingError, LoginError, ZulipAPIError, SecretError, ConfigError, json.JSONDecodeError,
    ) as exc:
        response = {"ok": False, "error": str(exc)}
    output_stream.write(encoded_response(response))
    output_stream.flush()
    return 0
