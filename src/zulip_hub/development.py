from __future__ import annotations

from pathlib import Path

from . import commands
from .commands import CommandError
from .config import DevelopmentConfig


class AgentError(RuntimeError):
    """L’agent de développement n’a pas pu être lancé."""


PLUGIN_ROOT = Path(__file__).resolve().parents[2]
APP_ID = "org.omarchy.zulip-hub-agent"
# Le dossier et la commande arrivent en arguments positionnels : rien de la
# configuration n’est jamais recopié dans le texte du script shell.
LOGIN_SCRIPT = 'cd -- "$1" && shift && exec "$@"'


def agent_directory(config: DevelopmentConfig) -> Path:
    directory = Path(config.directory).expanduser() if config.directory else PLUGIN_ROOT
    if not directory.is_dir():
        raise AgentError(f"dossier de développement introuvable: {directory}")
    return directory


def agent_command(config: DevelopmentConfig) -> list[str]:
    """Terminal Omarchy, shell de connexion (PATH de l’utilisateur), puis l’agent.

    Le pont tourne avec un PATH réduit ; c’est le shell de connexion qui
    retrouve l’agent, installé par exemple par mise dans le dossier personnel.
    """
    return [
        "omarchy-launch-tui", f"--app-id={APP_ID}",
        "bash", "-lc", LOGIN_SCRIPT, "zulip-hub-agent",
        str(agent_directory(config)), *config.agent_command,
    ]


def start_agent(config: DevelopmentConfig) -> Path:
    if not config.enabled:
        raise AgentError("le bouton de développement est désactivé ([development] enabled)")
    command = agent_command(config)
    try:
        commands.spawn(command)
    except CommandError as exc:
        raise AgentError("impossible d’ouvrir le terminal de développement") from exc
    return Path(command[6])
