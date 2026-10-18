from __future__ import annotations

from dataclasses import dataclass
import html
import logging
import os
import re
import time
from typing import Any, Callable

from . import commands
from .commands import CommandError
from .config import NotificationConfig
from .links import LinkError, message_url


LOGGER = logging.getLogger("zulip_hub.notifications")
MENTION_FLAGS = {
    "mentioned", "wildcard_mentioned", "stream_wildcard_mentioned", "topic_wildcard_mentioned"
}


class LockDetector:
    def is_locked(self) -> bool:
        try:
            result = commands.run(["omarchy-shell", "lock", "isLocked"], timeout=2)
        except CommandError:
            return True
        # Fail closed: if the lock service cannot answer, do not expose text.
        return result.returncode != 0 or result.stdout.strip() != "false"


class OmarchyNotificationSender:
    def send(
        self,
        summary: str,
        body: str,
        url: str,
        *,
        urgency: str = "low",
        replace_id: int | None = None,
    ) -> int | None:
        command = [
            "omarchy", "notification", "send",
            "--app-name", "zulip-hub",
            "--urgency", urgency,
            "--icon", "zulip",
            "--print-id",
        ]
        if replace_id is not None:
            command.extend(["--replace-id", str(replace_id)])
        runner = os.environ.get("ZULIP_HUB_RUNNER", "")
        click_command = ["/usr/bin/python3", runner] if runner else ["zulip-hub"]
        command.extend([summary, body, "--exec", *click_command, "open-url", url])
        try:
            result = commands.run(command, timeout=5)
        except CommandError as exc:
            LOGGER.warning("notification Omarchy indisponible: %s", type(exc).__name__)
            return None
        if result.returncode != 0:
            LOGGER.warning("échec de notification Omarchy (code %d)", result.returncode)
            return None
        try:
            return int(result.stdout.strip())
        except ValueError:
            return None


@dataclass
class NotificationGroup:
    notification_id: int | None
    count: int
    updated_at: float


class NotificationCoordinator:
    def __init__(
        self,
        site: str,
        user_email: str,
        config: NotificationConfig,
        sender: OmarchyNotificationSender | None = None,
        lock_detector: LockDetector | None = None,
        clock: Callable[[], float] = time.monotonic,
        label: str = "",
    ):
        self.site = site
        # Avec plusieurs comptes, le titre dit de quel serveur vient le message.
        self.label = label
        self.user_email = user_email.casefold()
        self.config = config
        self.sender = sender or OmarchyNotificationSender()
        self.lock_detector = lock_detector or LockDetector()
        self.clock = clock
        self.groups: dict[str, NotificationGroup] = {}
        self.followed_topics: set[tuple[int, str]] = set()

    def initialize(self, registration: dict[str, Any]) -> None:
        self.followed_topics = {
            (int(item["stream_id"]), str(item["topic_name"]))
            for item in registration.get("user_topics", [])
            if isinstance(item, dict) and item.get("visibility_policy") == 3
            and isinstance(item.get("stream_id"), int)
        }

    def handle_event(self, event: dict[str, Any]) -> None:
        if event.get("type") == "message":
            self.handle_message(event.get("message", {}))
            return
        if event.get("type") != "user_topic":
            return
        stream_id = event.get("stream_id")
        if not isinstance(stream_id, int):
            return
        key = (stream_id, str(event.get("topic_name", "")))
        if event.get("visibility_policy") == 3:
            self.followed_topics.add(key)
        else:
            self.followed_topics.discard(key)

    def handle_message(self, message: dict[str, Any]) -> None:
        if not self.should_notify(message):
            return
        try:
            url = message_url(self.site, message)
        except LinkError as exc:
            LOGGER.warning("notification ignorée, lien invalide: %s", exc)
            return

        now = self.clock()
        key = self.conversation_key(message)
        group = self.groups.get(key)
        within_window = group is not None and now - group.updated_at <= self.config.group_window_seconds
        count = group.count + 1 if within_window else 1
        replace_id = group.notification_id if within_window else None
        locked = self.config.hide_content_when_locked and self.lock_detector.is_locked()
        summary, body = self.notification_text(message, count, locked)
        if self.label:
            summary = f"[{self.label}] {summary}"
        flags = set(message.get("flags", []))
        urgency = "normal" if flags & MENTION_FLAGS else "low"
        notification_id = self.sender.send(
            summary, body, url, urgency=urgency, replace_id=replace_id
        )
        self.groups[key] = NotificationGroup(notification_id, count, now)
        self._prune(now)

    def should_notify(self, message: dict[str, Any]) -> bool:
        if not self.config.enabled or "read" in set(message.get("flags", [])):
            return False
        if str(message.get("sender_email", "")).casefold() == self.user_email:
            return False
        if message.get("type") == "private":
            return self.config.private_messages
        channel = str(message.get("display_recipient", ""))
        if channel in self.config.muted_channels:
            return False
        if channel in self.config.always_channels:
            return True
        flags = set(message.get("flags", []))
        if flags & MENTION_FLAGS:
            return self.config.mentions
        topic_key = (message.get("stream_id"), str(message.get("subject", "")))
        if self.config.followed_topics and topic_key in self.followed_topics:
            return True
        return self.config.other_messages

    @staticmethod
    def conversation_key(message: dict[str, Any]) -> str:
        if message.get("type") == "stream":
            return f"stream:{message.get('stream_id')}:{message.get('subject', '')}"
        recipients = message.get("display_recipient", [])
        ids = sorted(
            str(item.get("id")) for item in recipients
            if isinstance(item, dict) and item.get("id") is not None
        )
        return "dm:" + ",".join(ids)

    @staticmethod
    def notification_text(message: dict[str, Any], count: int, locked: bool) -> tuple[str, str]:
        if locked:
            return "Zulip", f"{count} nouveau message" + ("s" if count > 1 else "")
        sender = str(message.get("sender_full_name", "Zulip"))
        if message.get("type") == "stream":
            conversation = f"#{message.get('display_recipient', '')} › {message.get('subject', '')}"
        else:
            conversation = "message direct"
        prefix = f"{count} nouveaux messages · " if count > 1 else ""
        body = prefix + _plain_preview(str(message.get("content", "")))
        return f"{sender} — {conversation}", body

    def _prune(self, now: float) -> None:
        horizon = self.config.group_window_seconds * 2
        self.groups = {
            key: value for key, value in self.groups.items()
            if now - value.updated_at <= horizon
        }


def _plain_preview(content: str, limit: int = 180) -> str:
    value = html.unescape(content)
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"[`*_~]", "", value)
    value = re.sub(r"\s+", " ", value).strip()
    if not value:
        return "New Zulip message"
    # omarchy notification send reserves dash-leading words in the optional
    # body position. Prefix them so untrusted message text remains positional.
    if value.startswith("-"):
        value = "Message: " + value
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"
