  # Auto-import all channel binding modules at discover() time.
# Each module registers itself via cloud_agent_gateway.channel_binding.register()
from . import wechat_binding  # noqa: F401
from . import qq_binding  # noqa: F401
from . import dingtalk_binding  # noqa: F401
from . import telegram_binding  # noqa: F401
from . import discord_binding  # noqa: F401
from . import feishu_binding  # noqa: F401
from . import slack_binding  # noqa: F401
from . import manual_binding  # noqa: F401 — WhatsApp, QQ, WeCom, NapCat, Mochat, MSTeams, Matrix, Signal, Email
