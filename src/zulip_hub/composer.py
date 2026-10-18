from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import sys
from typing import Any, TextIO

from .api import ZulipAPIError, ZulipClient
from .config import ConfigError, Paths, load_config
from .secrets import SecretError, SecretToolProvider
from .limits import MAX_DIRECTORY, MAX_NAME, bounded_text, encoded_response
from .state import account_entry, recent_row


class ComposerError(RuntimeError):
    """A request from the graphical message composer was invalid."""


@dataclass
class ComposerManager:
    config_path: Path
    state_path: Path | None = None
    secrets: SecretToolProvider | None = None

    def __post_init__(self) -> None:
        if self.state_path is None:
            self.state_path = Paths.defaults().state
        if self.secrets is None:
            self.secrets = SecretToolProvider()

    def _client(self, site: str | None = None) -> tuple[ZulipClient, Any]:
        config = load_config(self.config_path)
        account = config.account_for(site)
        assert self.secrets is not None
        key = self.secrets.get(account.site, account.email)
        return ZulipClient(
            account.site,
            account.email,
            key,
            config.request_timeout_seconds,
        ), account

    @staticmethod
    def _site(request: dict[str, Any]) -> str | None:
        site = request.get("site")
        if site is None or site == "":
            return None
        if not isinstance(site, str):
            raise ComposerError("Le serveur demandé est invalide.")
        return site

    def _local_state(self) -> dict[str, Any]:
        try:
            assert self.state_path is not None
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return {}

    def directory(self, request: dict[str, Any] | None = None) -> dict[str, Any]:
        client, account = self._client(self._site(request or {}))
        identity = client.test_connection()
        self_id = identity.get("user_id")
        users = []
        for member in client.users()[: MAX_DIRECTORY * 4]:
            user_id = member.get("user_id")
            if (
                not isinstance(user_id, int)
                or user_id <= 0
                or user_id == self_id
                or member.get("is_active") is not True
                or member.get("is_bot") is True
                or member.get("is_deleted") is True
                or member.get("is_imported_stub") is True
            ):
                continue
            users.append({
                "id": user_id,
                "full_name": bounded_text(
                    member.get("full_name") or member.get("email") or user_id, MAX_NAME,
                ),
                "email": bounded_text(member.get("email") or "", MAX_NAME),
            })
        users.sort(key=lambda item: (item["full_name"].casefold(), item["id"]))
        del users[MAX_DIRECTORY:]

        state = self._local_state()
        recent_ids: list[int] = []
        for row in state.get("recent", []):
            if not isinstance(row, dict) or row.get("type") != "private":
                continue
            if row.get("site") and row.get("site") != account.site:
                continue
            candidates = [row.get("sender_id"), *row.get("recipient_ids", [])]
            for value in candidates:
                if isinstance(value, int) and value > 0 and value != self_id and value not in recent_ids:
                    recent_ids.append(value)
        return {
            "ok": True,
            "users": users,
            "recent_user_ids": recent_ids,
            "max_message_length": self._max_message_length(account.site),
            "site": account.site,
        }

    def _max_message_length(self, site: str | None = None) -> int:
        state = self._local_state()
        entry = account_entry(state, site) if site else {}
        maximum = entry.get("max_message_length", state.get("max_message_length", 10000))
        if not isinstance(maximum, int) or isinstance(maximum, bool) or maximum <= 0:
            return 10000
        return maximum

    def _validated_content(self, request: dict[str, Any], site: str | None = None) -> str:
        content = request.get("content")
        if not isinstance(content, str) or not content.strip():
            raise ComposerError("Le message est vide.")
        maximum = self._max_message_length(site)
        if len(content) > maximum:
            raise ComposerError(f"Le message dépasse la limite de {maximum} caractères.")
        return content

    def _recent_row(self, request: dict[str, Any]) -> dict[str, Any]:
        """Retrouve la conversation d’origine dans l’état local.

        La destination d’une réponse n’est jamais fournie par l’appelant : seul un
        identifiant circule, et le pont la déduit de son propre état. Une interface
        compromise ne peut donc pas rediriger un message.
        """
        message_id = request.get("message_id")
        if not isinstance(message_id, int) or isinstance(message_id, bool) or message_id <= 0:
            raise ComposerError("Le message auquel répondre est invalide.")
        row = recent_row(self._local_state(), message_id, self._site(request))
        if row is None:
            raise ComposerError("Ce message n’est plus dans les conversations récentes.")
        return row

    @staticmethod
    def _positive_int(value: Any) -> int | None:
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            return value
        return None

    @staticmethod
    def _recipient_ids(request: dict[str, Any]) -> list[int]:
        raw = request.get("recipient_ids")
        if not isinstance(raw, list):
            raise ComposerError("La liste des destinataires est invalide.")
        result: list[int] = []
        for value in raw:
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise ComposerError("Un destinataire est invalide.")
            if value not in result:
                result.append(value)
        if not result:
            raise ComposerError("Sélectionnez au moins un destinataire.")
        if len(result) > 100:
            raise ComposerError("Le groupe de destinataires est trop grand.")
        return result

    def send_direct(self, request: dict[str, Any]) -> dict[str, Any]:
        recipient_ids = self._recipient_ids(request)
        site = self._site(request)
        content = self._validated_content(request, site)
        client, _account = self._client(site)
        return {"ok": True, "message_id": client.send_direct(recipient_ids, content)}

    def _reply_to_channel(self, row: dict[str, Any], content: str, client: ZulipClient) -> int:
        stream_id = self._positive_int(row.get("stream_id"))
        topic = row.get("topic")
        if stream_id is None or not isinstance(topic, str) or not topic:
            raise ComposerError("La conversation d’origine est incomplète.")
        return client.send_stream(stream_id, topic, content)

    def _reply_to_direct(self, row: dict[str, Any], content: str, client: ZulipClient) -> int:
        self_id = client.test_connection().get("user_id")
        recipients: list[int] = []
        for value in [*row.get("recipient_ids", []), row.get("sender_id")]:
            user_id = self._positive_int(value)
            if user_id is None or user_id == self_id or user_id in recipients:
                continue
            recipients.append(user_id)
        if not recipients:
            # Une conversation dont je suis le seul participant : Zulip la range
            # sous « Messages avec vous-même », et y répondre s’adresse à moi.
            me = self._positive_int(self_id)
            participants = [*row.get("recipient_ids", []), row.get("sender_id")]
            if me is not None and me in participants:
                recipients = [me]
        if not recipients:
            raise ComposerError("La conversation d’origine est incomplète.")
        return client.send_direct(recipients, content)

    def reply(self, request: dict[str, Any]) -> dict[str, Any]:
        row = self._recent_row(request)
        # La réponse part vers le serveur de la conversation, lu dans l’état.
        site = row.get("site") or self._site(request)
        content = self._validated_content(request, site)
        client, _account = self._client(site)
        message_id = (
            self._reply_to_channel(row, content, client)
            if row.get("type") == "stream"
            else self._reply_to_direct(row, content, client)
        )
        return {"ok": True, "message_id": message_id}

    def handle(self, request: dict[str, Any]) -> dict[str, Any]:
        action = request.get("action")
        if action == "directory":
            return self.directory(request)
        if action == "send_direct":
            return self.send_direct(request)
        if action == "reply":
            return self.reply(request)
        raise ComposerError("Action de composition inconnue.")


def serve_once(
    config_path: Path | None = None,
    state_path: Path | None = None,
    input_stream: TextIO = sys.stdin,
    output_stream: TextIO = sys.stdout,
) -> int:
    try:
        raw = input_stream.readline(65537)
        if not raw or len(raw) > 65536:
            raise ComposerError("Requête de composition absente ou trop volumineuse.")
        request = json.loads(raw)
        if not isinstance(request, dict):
            raise ComposerError("La requête de composition doit être un objet JSON.")
        response = ComposerManager(
            config_path or Paths.defaults().config,
            state_path=state_path,
        ).handle(request)
    except (ComposerError, ZulipAPIError, SecretError, ConfigError, json.JSONDecodeError) as exc:
        response = {
            "ok": False,
            "error": str(exc),
            "delivery_uncertain": isinstance(exc, ZulipAPIError) and exc.code is None,
        }
    output_stream.write(encoded_response(response))
    output_stream.flush()
    return 0
