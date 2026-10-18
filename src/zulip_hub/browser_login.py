from __future__ import annotations

from configparser import ConfigParser, Error as ConfigParserError
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import secrets
import stat
import time
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit

from . import commands
from .api import ZulipAPIError, ZulipClient, _canonical_site
from .commands import CommandError
from .files import write_atomic


class LoginError(RuntimeError):
    """Une connexion par navigateur, mot de passe ou zuliprc a échoué."""


# Le serveur chiffre la clé API (32 caractères ASCII, soit 64 chiffres
# hexadécimaux) par un masque jetable de même longueur.
OTP_HEX_LENGTH = 64
PENDING_TTL_SECONDS = 600
HANDLER_DESKTOP = "zulip-hub-login.desktop"
SCHEME = "x-scheme-handler/zulip"
MAX_ZULIPRC_BYTES = 4096
API_KEY_PATTERN = re.compile(r"[A-Za-z0-9]{16,64}")


def _xor_hex(left: str, right: str) -> str:
    if len(left) != len(right):
        raise LoginError("La clé chiffrée n’a pas la longueur attendue.")
    return "".join(f"{int(a, 16) ^ int(b, 16):x}" for a, b in zip(left, right))


def decrypt_api_key(encrypted: str, otp: str) -> str:
    """Inverse ``otp_encrypt_api_key`` du serveur Zulip (zerver/lib/utils.py)."""
    if not re.fullmatch(r"[0-9a-fA-F]+", encrypted or "") or not re.fullmatch(r"[0-9a-f]+", otp):
        raise LoginError("La clé chiffrée est invalide.")
    try:
        key = bytes.fromhex(_xor_hex(encrypted.lower(), otp)).decode("ascii")
    except (ValueError, UnicodeDecodeError) as exc:
        raise LoginError("La clé chiffrée est invalide.") from exc
    if not API_KEY_PATTERN.fullmatch(key):
        raise LoginError("La clé déchiffrée est invalide.")
    return key


def _same_server(left: str, right: str) -> bool:
    try:
        return _canonical_site(left).rstrip("/") == _canonical_site(right).rstrip("/")
    except ZulipAPIError:
        return False


def _desktop_quote(value: str) -> str:
    """Argument d’une ligne Exec, selon la spécification Desktop Entry."""
    if any(character in value for character in "\n\r\t%"):
        raise LoginError("Chemin du lanceur inutilisable dans un fichier .desktop.")
    escaped = re.sub(r'(["`$\\])', r"\\\1", value)
    return f'"{escaped}"'


def _data_home() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")


def _config_home() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")


class SchemeHandler:
    """Associe temporairement ``zulip://`` au pont, puis rend la main.

    L’association n’existe que pendant une connexion en cours : le gestionnaire
    précédent, s’il y en avait un (le client Zulip desktop par exemple), est
    rétabli dès la fin, réussie ou non.
    """

    def __init__(self, applications: Path | None = None, mimeapps: Path | None = None):
        self.applications = applications or _data_home() / "applications"
        self.mimeapps = mimeapps or _config_home() / "mimeapps.list"

    @property
    def desktop_path(self) -> Path:
        return self.applications / HANDLER_DESKTOP

    @staticmethod
    def _exec_line() -> str:
        runner = os.environ.get("ZULIP_HUB_RUNNER", "")
        if runner:
            return f"/usr/bin/python3 {_desktop_quote(runner)} finish-login %u"
        return "zulip-hub finish-login %u"

    def current(self) -> str:
        try:
            result = commands.run(["xdg-mime", "query", "default", SCHEME], timeout=10)
        except CommandError:
            return ""
        return result.stdout.strip() if result.returncode == 0 else ""

    def register(self) -> str:
        """Installe le gestionnaire ; renvoie celui qu’il remplace."""
        previous = self.current()
        if previous == HANDLER_DESKTOP:
            previous = ""
        entry = (
            "[Desktop Entry]\n"
            "Type=Application\n"
            "Name=Zulip Hub sign-in\n"
            "NoDisplay=true\n"
            f"Exec={self._exec_line()}\n"
            f"MimeType={SCHEME};\n"
        )
        self.applications.mkdir(parents=True, exist_ok=True)
        write_atomic(self.desktop_path, entry, 0o644)
        try:
            result = commands.run(["xdg-mime", "default", HANDLER_DESKTOP, SCHEME], timeout=10)
        except CommandError as exc:
            raise LoginError("Impossible d’enregistrer le gestionnaire zulip://.") from exc
        if result.returncode != 0:
            raise LoginError("Impossible d’enregistrer le gestionnaire zulip://.")
        return previous

    def restore(self, previous: str) -> None:
        if previous and previous != HANDLER_DESKTOP:
            try:
                commands.run(["xdg-mime", "default", previous, SCHEME], timeout=10)
            except CommandError:
                pass
        else:
            self._forget_association()
        self.desktop_path.unlink(missing_ok=True)

    def _forget_association(self) -> None:
        # Un mimeapps.list lien symbolique appartient à l’utilisateur (dotfiles) :
        # l’association orpheline y reste, inoffensive une fois le .desktop retiré.
        try:
            if stat.S_ISLNK(os.lstat(self.mimeapps).st_mode):
                return
            lines = self.mimeapps.read_text(encoding="utf-8").splitlines()
        except OSError:
            return
        kept = []
        for line in lines:
            if line.startswith(SCHEME + "="):
                handlers = [
                    item for item in line.split("=", 1)[1].split(";")
                    if item and item != HANDLER_DESKTOP
                ]
                if not handlers:
                    continue
                line = f"{SCHEME}={';'.join(handlers)};"
            kept.append(line)
        if kept != lines:
            write_atomic(self.mimeapps, "\n".join(kept) + "\n", 0o644)


def _open_in_browser(url: str) -> None:
    for command in (["uwsm-app", "--", "xdg-open", url], ["xdg-open", url]):
        if commands.available(command[0]):
            try:
                commands.spawn(command)
                return
            except CommandError:
                continue
    raise LoginError("Aucun navigateur n’a pu être ouvert.")


@dataclass
class LoginManager:
    """Connexions sans saisie de clé API.

    * Navigateur : le flux « mobile » de Zulip. Le serveur renvoie la clé
      chiffrée par un masque jetable vers ``zulip://login`` ; seul ce
      processus connaît le masque, conservé le temps de la connexion.
    * Mot de passe : ``fetch_api_key``, le mot de passe n’est jamais conservé.
    * zuliprc : les fichiers téléchargés depuis Zulip, supprimés une fois lus.

    Chaque voie aboutit à ``setup`` de l’onboarding, qui teste la clé avant
    de l’écrire dans le trousseau.
    """

    onboarding: Any
    state_dir: Path
    handler: SchemeHandler = field(default_factory=SchemeHandler)
    open_url: Callable[[str], None] = _open_in_browser
    clock: Callable[[], float] = time.time

    @property
    def pending_path(self) -> Path:
        return self.state_dir / "login-pending.json"

    @property
    def result_path(self) -> Path:
        return self.state_dir / "login-result.json"

    @staticmethod
    def _site(request: dict[str, Any]) -> str:
        site = str(request.get("site", "")).strip().rstrip("/")
        if not site:
            raise LoginError("Saisissez l’adresse du serveur Zulip.")
        if "://" not in site:
            site = "https://" + site
        try:
            return _canonical_site(site)
        except ZulipAPIError as exc:
            raise LoginError("L’URL Zulip doit être une adresse HTTPS valide.") from exc

    @staticmethod
    def _settings(site: str) -> dict[str, Any]:
        try:
            return ZulipClient.anonymous(site).server_settings()
        except ZulipAPIError as exc:
            raise LoginError(f"Serveur Zulip injoignable : {exc}") from exc

    @staticmethod
    def _external_methods(settings: dict[str, Any]) -> list[dict[str, str]]:
        methods = []
        for item in settings.get("external_authentication_methods", []) or []:
            if not isinstance(item, dict):
                continue
            login_url = str(item.get("login_url") or "")
            # Chemin relatif au serveur et rien d’autre : « //hôte » sortirait.
            if not login_url.startswith("/") or login_url.startswith("//"):
                continue
            methods.append({
                "name": str(item.get("name") or "")[:40],
                "display_name": str(item.get("display_name") or item.get("name") or "")[:60],
                "login_url": login_url[:200],
            })
        return methods

    def methods(self, request: dict[str, Any]) -> dict[str, Any]:
        site = self._site(request)
        settings = self._settings(site)
        return {
            "ok": True,
            "site": site,
            "realm_name": str(settings.get("realm_name") or "")[:60],
            "password": settings.get("email_auth_enabled") is True,
            "methods": [
                {"name": item["name"], "display_name": item["display_name"]}
                for item in self._external_methods(settings)
            ],
        }

    def _name(self, request: dict[str, Any], settings: dict[str, Any]) -> str:
        name = " ".join(str(request.get("name") or "").split())
        return (name or str(settings.get("realm_name") or ""))[:60]

    def _read_pending(self) -> dict[str, Any] | None:
        try:
            value = json.loads(self.pending_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None

    def _write_result(self, result: dict[str, Any]) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        write_atomic(self.result_path, json.dumps(result, ensure_ascii=False), 0o600)

    def _end_pending(self, pending: dict[str, Any] | None) -> None:
        self.pending_path.unlink(missing_ok=True)
        if pending is not None:
            self.handler.restore(str(pending.get("previous_handler") or ""))

    def start(self, request: dict[str, Any]) -> dict[str, Any]:
        site = self._site(request)
        settings = self._settings(site)
        wanted = str(request.get("method") or "")
        method = next(
            (item for item in self._external_methods(settings) if item["name"] == wanted), None,
        )
        if method is None:
            raise LoginError("Cette méthode de connexion n’est pas proposée par le serveur.")
        earlier = self._read_pending()
        registered = self.handler.register()
        # Une connexion déjà en cours garde le gestionnaire d’origine à rétablir.
        previous = str(earlier.get("previous_handler") or "") if earlier else registered
        otp = secrets.token_hex(OTP_HEX_LENGTH // 2)
        pending = {
            "site": site,
            "otp": otp,
            "name": self._name(request, settings),
            "original_site": str(request.get("original_site") or ""),
            "created": self.clock(),
            "previous_handler": previous,
        }
        self.state_dir.mkdir(parents=True, exist_ok=True)
        write_atomic(self.pending_path, json.dumps(pending), 0o600)
        self.result_path.unlink(missing_ok=True)
        separator = "&" if "?" in method["login_url"] else "?"
        try:
            self.open_url(f"{site}{method['login_url']}{separator}mobile_flow_otp={otp}")
        except LoginError:
            self._end_pending(pending)
            raise
        return {
            "ok": True,
            "pending": True,
            "site": site,
            "message": f"Terminez la connexion « {method['display_name']} » dans le navigateur.",
        }

    def finish(self, url: str) -> dict[str, Any]:
        """Appelé par le navigateur avec l’adresse ``zulip://login?…``."""
        pending = self._read_pending()
        try:
            if pending is None:
                raise LoginError("Aucune connexion Zulip Hub n’est en cours.")
            if self.clock() - float(pending.get("created", 0)) > PENDING_TTL_SECONDS:
                raise LoginError("La connexion a expiré ; recommencez depuis le panneau.")
            parts = urlsplit(url)
            if parts.scheme != "zulip" or parts.netloc != "login":
                raise LoginError("Adresse de retour Zulip inattendue.")
            query = parse_qs(parts.query)

            def single(name: str) -> str:
                values = query.get(name, [])
                if len(values) != 1:
                    raise LoginError("Réponse de connexion Zulip incomplète.")
                return values[0]

            if not _same_server(single("realm"), str(pending["site"])):
                raise LoginError("La réponse provient d’un autre serveur que celui demandé.")
            api_key = decrypt_api_key(single("otp_encrypted_api_key"), str(pending["otp"]))
            result = self.onboarding.setup({
                "site": pending["site"],
                "email": single("email"),
                "api_key": api_key,
                "name": pending.get("name", ""),
                "original_site": pending.get("original_site", ""),
            })
            outcome = {
                "ok": True,
                "site": pending["site"],
                "message": f"Compte {pending.get('name') or pending['site']} ajouté.",
            }
        except Exception as exc:
            outcome = {"ok": False, "error": str(exc)}
            result = None
        finally:
            self._end_pending(pending)
        self._write_result(outcome)
        self.notify(outcome)
        return result or outcome

    @staticmethod
    def notify(outcome: dict[str, Any]) -> None:
        """Le panneau peut être fermé : le résultat s’affiche aussi en notification."""
        text = str(outcome.get("message") or outcome.get("error") or "")
        if not text or not commands.available("notify-send"):
            return
        try:
            commands.run(["notify-send", "--app-name=zulip-hub", "Zulip Hub", text], timeout=5)
        except CommandError:
            pass

    def status(self, _request: dict[str, Any] | None = None) -> dict[str, Any]:
        pending = self._read_pending()
        if pending is not None and self.clock() - float(pending.get("created", 0)) > PENDING_TTL_SECONDS:
            self._end_pending(pending)
            self._write_result({"ok": False, "error": "La connexion a expiré."})
            pending = None
        try:
            outcome = json.loads(self.result_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            outcome = None
        if outcome is not None:
            self.result_path.unlink(missing_ok=True)
        return {"ok": True, "pending": pending is not None, "result": outcome}

    def cancel(self, _request: dict[str, Any] | None = None) -> dict[str, Any]:
        self._end_pending(self._read_pending())
        self.result_path.unlink(missing_ok=True)
        return {"ok": True, "pending": False, "message": "Connexion annulée."}

    def password(self, request: dict[str, Any]) -> dict[str, Any]:
        site = self._site(request)
        email = str(request.get("email") or "").strip()
        password = str(request.get("password") or "")
        if not email or not password:
            raise LoginError("Saisissez l’adresse e-mail et le mot de passe.")
        settings = self._settings(site)
        try:
            fetched = ZulipClient.anonymous(site).fetch_api_key(email, password)
        except ZulipAPIError as exc:
            raise LoginError(f"Connexion refusée : {exc}") from exc
        api_key = str(fetched.get("api_key") or "")
        return self.onboarding.setup({
            "site": site,
            "email": str(fetched.get("email") or email),
            "api_key": api_key,
            "name": self._name(request, settings),
            "original_site": str(request.get("original_site") or ""),
        })

    @staticmethod
    def _read_zuliprc(path: Path) -> tuple[str, str, str]:
        if not path.is_file() or path.is_symlink() or path.stat().st_size > MAX_ZULIPRC_BYTES:
            raise LoginError(f"{path.name} : fichier ignoré.")
        parser = ConfigParser(interpolation=None)
        try:
            parser.read_string(path.read_text(encoding="utf-8"))
            section = parser["api"]
            return section["site"], section["email"], section["key"]
        except (ConfigParserError, KeyError, UnicodeDecodeError, OSError) as exc:
            raise LoginError(f"{path.name} : ce n’est pas un zuliprc valide.") from exc

    def import_zuliprc(self, request: dict[str, Any]) -> dict[str, Any]:
        directory = Path(str(request.get("directory") or Path.home() / "Downloads")).expanduser()
        candidates = sorted(
            path for path in directory.glob("zuliprc*") if not path.name.endswith(".crdownload")
        )
        if not candidates:
            raise LoginError(f"Aucun fichier zuliprc dans {directory}.")
        imported: list[str] = []
        errors: list[str] = []
        result: dict[str, Any] = {}
        for path in candidates:
            try:
                site, email, key = self._read_zuliprc(path)
                site = self._site({"site": site})
                name = ""
                try:
                    name = str(self._settings(site).get("realm_name") or "")[:60]
                except LoginError:
                    pass
                result = self.onboarding.setup({
                    "site": site, "email": email, "api_key": key, "name": name,
                })
                # Le fichier contient la clé en clair : il disparaît une fois importé.
                path.unlink()
                imported.append(name or site)
            except Exception as exc:  # un fichier défectueux n’arrête pas les autres
                errors.append(str(exc))
        if not imported:
            raise LoginError(" ; ".join(errors))
        response = dict(result) if result else self.onboarding.status()
        response["ok"] = True
        response["message"] = "Importés : " + ", ".join(imported) + (
            " — " + " ; ".join(errors) if errors else ""
        )
        return response
