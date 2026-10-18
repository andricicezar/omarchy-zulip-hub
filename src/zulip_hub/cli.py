from __future__ import annotations

import argparse
import getpass
import json
import logging
from pathlib import Path
import sys
import threading

from .api import ZulipClient
from .config import ConfigError, Paths, load_config
from .composer import serve_once as serve_composer_once
from .daemon import BridgeDaemon, MultiBridge
from .files import single_instance
from .links import LinkError, summary_url
from .marketplace import IntegrationError, run_action
from .hyprland import HyprlandController, HyprlandError, resolve_launch_command
from .notifications import NotificationCoordinator
from .browser_login import LoginManager
from .onboarding import OnboardingManager, serve_once
from .opening import AccountOpener, OpenError, UrlOpener
from .reader import serve_once as serve_reader_once
from .secrets import SecretError, SecretToolProvider
from .state import MergedStateStore, StateReducer, StateStore, recent_row


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="zulip-hub", description="Bridge Zulip local pour Omarchy")
    result.add_argument("--config", type=Path, help="chemin vers config.toml")
    result.add_argument("--state", type=Path, help="chemin vers state.json")
    sub = result.add_subparsers(dest="command", required=True)
    sub.add_parser("daemon", help="écouter les événements Zulip")
    sub.add_parser("status", help="afficher l'état local")
    sub.add_parser("check-config", help="valider la configuration")
    login = sub.add_parser("login", help="enregistrer la clé API dans le trousseau")
    login.add_argument("--site", help="serveur du compte (par défaut : le premier)")
    open_url = sub.add_parser("open-url", help="ouvrir une URL Zulip HTTPS")
    open_url.add_argument("url")
    open_message = sub.add_parser("open-message", help="ouvrir un message récent par son identifiant")
    open_message.add_argument("message_id", type=int)
    open_message.add_argument("--site", help="serveur du message")
    open_account = sub.add_parser("open-account", help="afficher ou lancer l’application web d’un compte")
    open_account.add_argument("--site", required=True, help="serveur du compte")
    mark_read = sub.add_parser("mark-read", help="marquer un message comme lu")
    mark_read.add_argument("message_id", type=int)
    mark_read.add_argument("--site", help="serveur du message (par défaut : le premier compte)")
    sub.add_parser("workspace-toggle", help="afficher, masquer ou lancer le workspace Zulip")
    sub.add_parser("workspace-status", help="diagnostiquer la détection de la fenêtre Zulip")
    sub.add_parser("onboarding", help=argparse.SUPPRESS)
    finish_login = sub.add_parser("finish-login", help=argparse.SUPPRESS)
    finish_login.add_argument("url")
    sub.add_parser("compose", help=argparse.SUPPRESS)
    sub.add_parser("read-message", help=argparse.SUPPRESS)
    os_integration = sub.add_parser("os-integration", help=argparse.SUPPRESS)
    os_integration.add_argument("action", choices=("status", "install", "remove"))
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    paths = Paths.defaults()
    config_path = args.config or paths.config
    state_path = args.state or paths.state
    try:
        if args.command == "onboarding":
            return serve_once(args.config)
        if args.command == "finish-login":
            manager = OnboardingManager(config_path, state_path=state_path)
            outcome = LoginManager(manager, state_path.parent).finish(args.url)
            print(outcome.get("message") or outcome.get("error") or "")
            return 0 if outcome.get("ok") else 2
        if args.command == "compose":
            return serve_composer_once(args.config, args.state)
        if args.command == "read-message":
            return serve_reader_once(args.config, args.state)
        if args.command == "os-integration":
            print(json.dumps(
                run_action(Path(__file__).resolve().parents[2], args.action),
                ensure_ascii=False,
            ))
            return 0
        config = load_config(config_path)
        if args.command == "check-config":
            for account in config.accounts:
                print(f"configuration valide: {account.label}: {account.site} ({account.email})")
            return 0
        if args.command == "status":
            try:
                print(json.dumps(StateStore(state_path).read(), ensure_ascii=False, indent=2))
                return 0
            except FileNotFoundError:
                print("aucun état disponible; le daemon a-t-il démarré ?", file=sys.stderr)
                return 3
        if args.command == "open-url":
            if not any(
                args.url.startswith(account.site.rstrip("/") + "/#narrow/")
                for account in config.accounts
            ):
                raise OpenError("l’URL ne correspond à aucun serveur Zulip configuré")
            UrlOpener(config.opening).open(args.url)
            return 0
        if args.command == "open-message":
            state = StateStore(state_path).read()
            row = recent_row(state, args.message_id, args.site)
            if row is None:
                raise OpenError("message absent de l’état récent")
            # Le serveur vient de l’état, et doit encore être un compte configuré.
            account = config.account_for(row.get("site") or args.site)
            UrlOpener(config.opening).open(summary_url(account.site, row))
            return 0
        if args.command == "open-account":
            account = config.account_for(args.site)
            controller = HyprlandController(config.workspace, resolve_launch_command(config))
            print(AccountOpener(controller).open(account.site))
            return 0
        if args.command in {"workspace-toggle", "workspace-status"}:
            controller = HyprlandController(
                config.workspace, resolve_launch_command(config)
            )
            if args.command == "workspace-toggle":
                print(controller.toggle())
            else:
                print(json.dumps(controller.status(), ensure_ascii=False, indent=2))
            return 0
        secrets = SecretToolProvider()
        if args.command == "login":
            account = config.account_for(args.site)
            key = getpass.getpass(f"Clé API Zulip ({account.label}): ")
            secrets.store(account.site, account.email, key)
            print("clé enregistrée dans le trousseau")
            return 0
        logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
        if args.command == "mark-read":
            if args.message_id <= 0:
                raise ConfigError("message_id doit être positif")
            account = config.account_for(args.site)
            key = secrets.get(account.site, account.email)
            ZulipClient(
                account.site, account.email, key, config.request_timeout_seconds
            ).mark_read([args.message_id])
            print("message marqué comme lu")
            return 0
        return run_bridge(config, secrets, state_path)
    except (
        ConfigError, SecretError, OpenError, LinkError, HyprlandError, IntegrationError,
        FileNotFoundError
    ) as exc:
        print(f"erreur: {exc}", file=sys.stderr)
        return 2


def run_bridge(config, secrets: SecretToolProvider, state_path: Path) -> int:
    """Un daemon par compte, tous fusionnés dans un seul fichier d’état.

    Un compte sans clé n’empêche pas les autres de tourner : son erreur est
    inscrite dans l’état. Sans aucune clé, l’échec reste celui d’avant.
    """
    merged = MergedStateStore(
        StateStore(state_path),
        [(account.site, account.label) for account in config.accounts],
        config.recent_message_limit,
    )
    multiple = len(config.accounts) > 1
    stop_event = threading.Event()
    daemons = []
    first_error: SecretError | None = None
    for index, account in enumerate(config.accounts):
        try:
            key = secrets.get(account.site, account.email)
        except SecretError as exc:
            first_error = first_error or exc
            merged.record_error(index, str(exc))
            continue
        client = ZulipClient(
            account.site, account.email, key, config.request_timeout_seconds
        )
        notifier = NotificationCoordinator(
            account.site, account.email, config.notifications,
            label=account.label if multiple else "",
        )
        daemons.append(BridgeDaemon(
            client,
            merged.slot(index),
            StateReducer(config.recent_message_limit),
            config.initial_backoff_seconds,
            config.max_backoff_seconds,
            sleep=stop_event.wait,
            notifier=notifier,
            install_signals=False,
        ))
    if not daemons:
        assert first_error is not None
        raise first_error
    with single_instance(state_path.parent / "bridge.lock"):
        merged.write()
        MultiBridge(daemons, stop_event, on_stop=merged.mark_stopped).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
