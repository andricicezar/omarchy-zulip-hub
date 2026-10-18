from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import copy
import json
import os
import tempfile
import threading

from .limits import (
    MAX_MESSAGE_LENGTH,
    MAX_PAYLOAD_BYTES,
    bounded_list,
    bounded_text,
    clamped_number,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def recent_row(
    state: dict[str, Any], message_id: int, site: str | None = None,
) -> dict[str, Any] | None:
    """Retrouve une conversation récente par identifiant de message.

    Deux serveurs peuvent attribuer le même identifiant : avec plusieurs
    comptes, le serveur fait partie de la clé. Sans *site*, la première ligne
    portant cet identifiant est retenue, comme avant les comptes multiples.
    """
    wanted = site.rstrip("/") if isinstance(site, str) and site else None
    for row in state.get("recent", []):
        if not isinstance(row, dict) or row.get("id") != message_id:
            continue
        if wanted is None or str(row.get("site") or "").rstrip("/") == wanted:
            return row
    return None


@dataclass
class HubState:
    schema_version: int = 1
    connected: bool = False
    last_sync: str | None = None
    unread: dict[str, int] = field(default_factory=lambda: {"total": 0, "mentions": 0, "private": 0})
    recent: list[dict[str, Any]] = field(default_factory=list)
    server_url: str = ""
    error: str | None = None
    max_message_length: int = 10000
    accounts: list[dict[str, Any]] = field(default_factory=list)


class StateStore:
    def __init__(self, path: Path):
        self.path = path

    @staticmethod
    def _within_budget(state: HubState) -> str:
        """Sérialisation de l’état, ramenée sous le budget d’écriture.

        Les bornes par champ ne s’additionnent pas : c’est ici que la taille
        totale est tenue, en abandonnant les conversations les plus anciennes.
        """
        payload = asdict(state)
        recent = list(payload.get("recent", []))
        while True:
            data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
            if len(data.encode("utf-8")) <= MAX_PAYLOAD_BYTES or not recent:
                return data
            recent = recent[: max(1, len(recent) // 2)] if len(recent) > 1 else []
            payload["recent"] = recent

    def write(self, state: HubState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        data = self._within_budget(state)
        fd, temporary = tempfile.mkstemp(prefix=".state-", dir=self.path.parent, text=True)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def read(self) -> dict[str, Any]:
        return json.loads(self.path.read_text(encoding="utf-8"))


class MergedStateStore:
    """Fusionne l’état de plusieurs comptes dans un seul fichier.

    Chaque compte a son daemon et son réducteur ; tous écrivent ici par leur
    façade. L’interface lit un état unique : compteurs additionnés,
    conversations récentes de tous les serveurs triées par date, chacune
    marquée de son serveur, et un résumé par compte.
    """

    def __init__(self, store: StateStore, accounts: list[tuple[str, str]], limit: int = 30):
        self.store = store
        self.accounts = list(accounts)
        self.limit = limit
        self._lock = threading.Lock()
        self._slots: list[dict[str, Any]] = [
            asdict(HubState(server_url=site)) for site, _name in self.accounts
        ]

    def slot(self, index: int) -> "AccountSlotStore":
        return AccountSlotStore(self, index)

    def update(self, index: int, state: HubState) -> None:
        # Instantané pris dans le fil du compte : les autres fils ne lisent
        # jamais un réducteur qu’ils ne possèdent pas.
        snapshot = copy.deepcopy(asdict(state))
        with self._lock:
            self._slots[index] = snapshot
            self.store.write(self.merged())

    def record_error(self, index: int, message: str) -> None:
        with self._lock:
            slot = self._slots[index]
            slot["connected"] = False
            slot["error"] = message
            slot["last_sync"] = utc_now()

    def write(self) -> None:
        with self._lock:
            self.store.write(self.merged())

    def mark_stopped(self) -> None:
        """Arrêt du bridge : plus aucun compte n’est connecté."""
        with self._lock:
            for slot in self._slots:
                slot["connected"] = False
                slot["error"] = None
            self.store.write(self.merged())

    def merged(self) -> HubState:
        result = HubState()
        result.server_url = self.accounts[0][0] if self.accounts else ""
        rows: list[dict[str, Any]] = []
        errors: list[str] = []
        syncs: list[str] = []
        lengths: list[int] = []
        totals = {"total": 0, "mentions": 0, "private": 0}
        multiple = len(self.accounts) > 1
        for (site, name), slot in zip(self.accounts, self._slots):
            unread = slot.get("unread") or {}
            for key in totals:
                value = unread.get(key, 0)
                totals[key] += value if isinstance(value, int) and value > 0 else 0
            for row in slot.get("recent", []):
                if isinstance(row, dict):
                    rows.append({**row, "site": site, "account": name})
            error = slot.get("error")
            if error:
                errors.append(f"{name} : {error}" if multiple else str(error))
            if slot.get("last_sync"):
                syncs.append(str(slot["last_sync"]))
            if isinstance(slot.get("max_message_length"), int):
                lengths.append(slot["max_message_length"])
            result.accounts.append({
                "site": site,
                "name": name,
                "connected": slot.get("connected") is True,
                "error": error or None,
                "last_sync": slot.get("last_sync"),
                "unread": {key: unread.get(key, 0) for key in totals},
                "max_message_length": slot.get("max_message_length", 10000),
            })
        rows.sort(
            key=lambda row: row.get("timestamp") if isinstance(row.get("timestamp"), int) else 0,
            reverse=True,
        )
        result.recent = rows[: self.limit]
        result.unread = totals
        result.connected = any(account["connected"] for account in result.accounts)
        result.error = " · ".join(errors) or None
        result.last_sync = max(syncs) if syncs else None
        result.max_message_length = lengths[0] if lengths else 10000
        return result


class AccountSlotStore:
    """Façade de StateStore remise au daemon d’un seul compte."""

    def __init__(self, merged: MergedStateStore, index: int):
        self.merged = merged
        self.index = index

    def write(self, state: HubState) -> None:
        self.merged.update(self.index, state)


def account_entry(state: dict[str, Any], site: str | None) -> dict[str, Any]:
    """Résumé d’un compte dans l’état fusionné ; vide s’il est inconnu."""
    for entry in state.get("accounts", []) or []:
        if isinstance(entry, dict) and (site is None or entry.get("site") == site):
            return entry
    return {}


class StateReducer:
    def __init__(self, limit: int = 30):
        self.state = HubState()
        self.limit = limit
        self._unread_ids: set[int] = set()
        self._mention_ids: set[int] = set()
        self._private_ids: set[int] = set()

    def initialized(self, registration: dict[str, Any]) -> None:
        unread = registration.get("unread_msgs", {})
        if not isinstance(unread, dict):
            unread = {}
        mention_ids = self._ids(unread.get("mentions", []))
        private_ids = self._ids(unread.get("pms", [])) | self._ids(unread.get("huddles", []))
        stream_ids = self._ids(unread.get("streams", []))
        self._mention_ids = mention_ids
        self._private_ids = private_ids
        self._unread_ids = mention_ids | private_ids | stream_ids
        self._sync_counts()
        self.state.connected = True
        self.state.error = None
        self.state.last_sync = utc_now()
        self.state.max_message_length = int(clamped_number(
            registration.get("max_message_length", 10000),
            default=10000, minimum=1, maximum=MAX_MESSAGE_LENGTH,
        ))

    def apply(self, event: dict[str, Any]) -> None:
        event_type = event.get("type")
        if event_type == "message":
            message = event.get("message", {})
            flags = set(message.get("flags", []))
            if "read" not in flags:
                message_id = message.get("id")
                if not isinstance(message_id, int):
                    return
                self._unread_ids.add(message_id)
                if message.get("type") == "private":
                    self._private_ids.add(message_id)
                if flags & {
                    "mentioned", "wildcard_mentioned",
                    "stream_wildcard_mentioned", "topic_wildcard_mentioned",
                }:
                    self._mention_ids.add(message_id)
                self._sync_counts()
                self.state.recent.insert(0, self._summary(message))
                del self.state.recent[self.limit :]
        elif event_type == "update_message_flags":
            flag = event.get("flag")
            operation = event.get("op", event.get("operation"))
            if flag == "read" and operation == "add":
                if event.get("all") is True:
                    self._unread_ids.clear()
                    self._mention_ids.clear()
                    self._private_ids.clear()
                else:
                    message_ids = set(event.get("messages", []))
                    self._unread_ids.difference_update(message_ids)
                    self._mention_ids.difference_update(message_ids)
                    self._private_ids.difference_update(message_ids)
                self._sync_counts()
        self.state.connected = True
        self.state.error = None
        self.state.last_sync = utc_now()

    @staticmethod
    def _ids(entries: Any) -> set[int]:
        """Identifiants des messages non lus, quelle que soit la forme reçue.

        Zulip envoie des objets portant ``unread_message_ids`` pour les
        messages directs, les groupes et les canaux, et des identifiants nus
        pour les mentions ; les paires ``[clé, [ids]]`` restent acceptées.
        """
        result: set[int] = set()
        for entry in bounded_list(entries):
            if isinstance(entry, dict):
                values = bounded_list(entry.get("unread_message_ids", []))
            elif isinstance(entry, list) and len(entry) == 2 and isinstance(entry[1], list):
                values = bounded_list(entry[1])
            else:
                values = [entry]
            result.update(
                value for value in values if isinstance(value, int) and not isinstance(value, bool)
            )
        return result

    def _sync_counts(self) -> None:
        self.state.unread = {
            "total": len(self._unread_ids),
            "mentions": len(self._mention_ids),
            "private": len(self._private_ids),
        }

    @staticmethod
    def _summary(message: dict[str, Any]) -> dict[str, Any]:
        """Résumé borné d’un message, sans son corps.

        Tout ce qui vient du serveur est tronqué avant d’entrer dans l’état :
        ce fichier est relu tel quel par l’interface.
        """
        is_stream = message.get("type") == "stream"
        recipients = bounded_list(message.get("display_recipient", []))
        return {
            "id": message.get("id"),
            "type": message.get("type"),
            "sender": bounded_text(message.get("sender_full_name", "")),
            "sender_id": message.get("sender_id"),
            "channel": bounded_text(message.get("display_recipient")) if is_stream else None,
            "topic": bounded_text(message.get("subject")) if is_stream else None,
            "stream_id": message.get("stream_id") if is_stream else None,
            "recipient_ids": [
                recipient.get("id") for recipient in recipients
                if isinstance(recipient, dict) and isinstance(recipient.get("id"), int)
            ] if message.get("type") == "private" else [],
            "timestamp": message.get("timestamp"),
            "flags": [bounded_text(flag, 64) for flag in bounded_list(message.get("flags", []))],
        }
