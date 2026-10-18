from __future__ import annotations

import base64
import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .limits import MAX_RESPONSE_BYTES, exceeds_depth


class ZulipAPIError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        retryable: bool = True,
        retry_after: float | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        # Delai reclame par le serveur, en secondes. Le respecter evite de
        # consommer le quota du compte, partage avec le client Zulip.
        self.retry_after = retry_after


def _origin(url: str) -> tuple[str, str | None, int | None] | None:
    """Origine d’une adresse, ou None si elle ne peut pas porter la clé API."""
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
        return None
    return parts.scheme, parts.hostname.lower(), parts.port or 443


class SameOriginRedirect(HTTPRedirectHandler):
    """Refuse toute redirection qui changerait d’origine.

    urllib recopie les en-têtes de la requête initiale vers la cible d’une
    redirection, en n’écartant que ``content-length`` et ``content-type``. La
    clé API partirait donc vers l’hôte choisi par le serveur, y compris un
    autre domaine. Seule une redirection vers exactement la même origine
    HTTPS est suivie.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        current = _origin(req.full_url)
        target = _origin(newurl)
        if current is None or target is None or current != target:
            raise HTTPError(
                req.full_url, code,
                "redirection hors origine refusée par Zulip Hub",
                headers, fp,
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _canonical_site(site: str) -> str:
    """Adresse normalisée du serveur, refusée si elle ne peut pas être sûre.

    La canonicalisation précède la construction de la clé d’authentification :
    une adresse ambiguë ne doit jamais servir à fabriquer un en-tête.
    """
    parts = urlsplit(str(site).strip())
    origin = _origin(site)
    if origin is None:
        raise ZulipAPIError("adresse de serveur Zulip invalide", retryable=False)
    _scheme, host, port = origin
    authority = host if port == 443 else f"{host}:{port}"
    return f"https://{authority}{parts.path.rstrip('/')}"


def _decoded(raw: bytes) -> Any:
    """JSON d’une réponse déjà bornée en octets."""
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ZulipAPIError("réponse Zulip trop volumineuse", retryable=False)
    if exceeds_depth(raw):
        raise ZulipAPIError("réponse Zulip trop imbriquée", retryable=False)
    try:
        return json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ZulipAPIError("réponse Zulip invalide", retryable=True) from exc


def _retry_after(body: object) -> float | None:
    if not isinstance(body, dict):
        return None
    value = body.get("retry-after")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        return None
    return float(value)


class ZulipClient:
    def __init__(self, site: str, email: str, api_key: str, timeout: int = 90):
        self.site = _canonical_site(site)
        self.headers = {"User-Agent": "omarchy-zulip-hub/2.0"}
        # Sans identité, le client ne sert qu’aux points d’accès publics.
        if email or api_key:
            token = base64.b64encode(f"{email}:{api_key}".encode()).decode()
            self.headers["Authorization"] = f"Basic {token}"
        self.timeout = timeout
        # Ouvreur privé : l’ouvreur global du processus suivrait les
        # redirections sans regarder l’origine.
        self._opener = build_opener(SameOriginRedirect())

    def _request(self, method: str, path: str, params: dict[str, Any]) -> dict[str, Any]:
        encoded = urlencode({
            key: json.dumps(value) if isinstance(value, (list, dict, bool)) else value
            for key, value in params.items()
        })
        url = f"{self.site}/api/v1/{path}"
        data = None
        if method == "GET":
            url = f"{url}?{encoded}"
        else:
            data = encoded.encode()
        request = Request(url, data=data, headers=self.headers, method=method)
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                payload = _decoded(response.read(MAX_RESPONSE_BYTES + 1))
        except HTTPError as exc:
            code = None
            delay = None
            message = f"erreur HTTP Zulip {exc.code}"
            try:
                body = _decoded(exc.read(MAX_RESPONSE_BYTES + 1))
                if isinstance(body, dict):
                    code = body.get("code")
                    message = body.get("msg", message)
                    delay = _retry_after(body)
            except (ValueError, UnicodeDecodeError, ZulipAPIError):
                pass
            raise ZulipAPIError(
                message,
                code=code,
                retryable=exc.code >= 500 or exc.code == 429,
                retry_after=delay,
            ) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise ZulipAPIError("serveur Zulip inaccessible", retryable=True) from exc
        except (ValueError, UnicodeDecodeError) as exc:
            raise ZulipAPIError("réponse Zulip invalide", retryable=True) from exc
        if payload.get("result") == "error":
            raise ZulipAPIError(
                payload.get("msg", "erreur API Zulip"),
                code=payload.get("code"),
                retry_after=_retry_after(payload),
            )
        return payload

    def register(self) -> dict[str, Any]:
        return self._request(
            "POST", "register",
            {
                "event_types": ["message", "update_message_flags", "user_topic"],
                # unread_msgs n’est renvoyé que si message et
                # update_message_flags sont tous deux demandés.
                "fetch_event_types": ["message", "update_message_flags", "user_topic"],
                "apply_markdown": False,
            },
        )

    @classmethod
    def anonymous(cls, site: str, timeout: int = 15) -> "ZulipClient":
        return cls(site, "", "", timeout)

    def server_settings(self) -> dict[str, Any]:
        """Réglages publics du serveur : nom, méthodes de connexion."""
        return self._request("GET", "server_settings", {})

    def fetch_api_key(self, username: str, password: str) -> dict[str, Any]:
        """Échange identifiant et mot de passe contre la clé API du compte."""
        return self._request("POST", "fetch_api_key", {"username": username, "password": password})

    def test_connection(self) -> dict[str, Any]:
        return self._request("GET", "users/me", {})

    def events(self, queue_id: str, last_event_id: int) -> list[dict[str, Any]]:
        payload = self._request(
            "GET", "events", {"queue_id": queue_id, "last_event_id": last_event_id},
        )
        return list(payload.get("events", []))

    def mark_read(self, message_ids: list[int]) -> None:
        if not message_ids:
            return
        self._request(
            "POST", "messages/flags",
            {"messages": message_ids, "op": "add", "flag": "read"},
        )

    def message(self, message_id: int) -> dict[str, Any]:
        """Récupère un message unique, contenu non rendu.

        Les serveurs récents renvoient l’objet complet sous « message » ; les
        plus anciens ne renvoient que « raw_content ». Les deux formes sont
        acceptées plutôt que d’imposer une version de serveur.
        """
        payload = self._request("GET", f"messages/{message_id}", {"apply_markdown": False})
        found = payload.get("message")
        if isinstance(found, dict) and isinstance(found.get("content"), str):
            return found
        legacy = payload.get("raw_content")
        if isinstance(legacy, str):
            return {"id": message_id, "content": legacy}
        raise ZulipAPIError("réponse Zulip sans contenu de message", retryable=False)

    def users(self) -> list[dict[str, Any]]:
        payload = self._request("GET", "users", {"client_gravatar": True})
        members = payload.get("members", [])
        return [member for member in members if isinstance(member, dict)]

    @staticmethod
    def _sent_message_id(payload: dict[str, Any]) -> int:
        message_id = payload.get("id")
        if not isinstance(message_id, int) or message_id <= 0:
            raise ZulipAPIError("réponse d’envoi Zulip invalide", retryable=False)
        return message_id

    def send_direct(self, recipient_ids: list[int], content: str) -> int:
        params: dict[str, Any] = {"type": "direct", "to": recipient_ids, "content": content}
        try:
            payload = self._request("POST", "messages", params)
        except ZulipAPIError as exc:
            detail = str(exc).casefold()
            legacy_type_rejected = exc.code == "BAD_REQUEST" and (
                "direct" in detail or "message type" in detail or "invalid type" in detail
            )
            if not legacy_type_rejected:
                raise
            params["type"] = "private"
            payload = self._request("POST", "messages", params)
        return self._sent_message_id(payload)

    def send_stream(self, stream_id: int, topic: str, content: str) -> int:
        params: dict[str, Any] = {
            "type": "stream", "to": stream_id, "topic": topic, "content": content,
        }
        try:
            payload = self._request("POST", "messages", params)
        except ZulipAPIError as exc:
            detail = str(exc).casefold()
            # Zulip nommait ce champ « subject » avant la 2.0. Un rejet portant sur
            # le canal ou le contenu ne doit pas déclencher ce repli.
            legacy_topic_rejected = exc.code == "BAD_REQUEST" and (
                "topic" in detail or "subject" in detail
            )
            if not legacy_topic_rejected:
                raise
            del params["topic"]
            params["subject"] = topic
            payload = self._request("POST", "messages", params)
        return self._sent_message_id(payload)
