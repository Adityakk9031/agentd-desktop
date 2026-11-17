"""Channel integrations (Telegram / Discord / Slack) + the shared manager.

`build_channel_manager` reads env vars and registers only the channels that
are actually configured, so the app runs unchanged with zero channels.
Adapters are imported lazily so optional SDKs stay optional.
"""

import importlib
import logging
import os

from agentd.channels.base import (  # noqa: F401
    ChannelManager,
    MessagingChannel,
    format_approval_card,
    format_score_badge,
    format_task_reply,
)

logger = logging.getLogger("channels")

# env var that must be set -> adapter module path
_CHANNELS = (
    ("TELEGRAM_BOT_TOKEN", "agentd.channels.telegram", "TelegramChannel"),
    ("DISCORD_BOT_TOKEN", "agentd.channels.discord", "DiscordChannel"),
    ("SLACK_APP_TOKEN", "agentd.channels.slack", "SlackChannel"),
)


def build_channel_manager(workflow_service, approval_queue) -> ChannelManager:
    """Register every configured channel; skip the rest silently."""
    manager = ChannelManager(workflow_service, approval_queue)
    for env_var, module_name, class_name in _CHANNELS:
        if not os.getenv(env_var):
            continue
        try:
            adapter_cls = getattr(importlib.import_module(module_name), class_name)
            manager.register(adapter_cls())
        except Exception as exc:
            logger.warning(f"Failed to initialize {class_name} ({env_var} set): {exc}")
    return manager
