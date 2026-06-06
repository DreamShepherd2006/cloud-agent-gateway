 # Auto-import all channel binding modules at discover() time.
# Each module registers itself via cloud_agent_gateway.channel_binding.register()
from . import wechat_binding  # noqa: F401
from . import dingtalk_binding  # noqa: F401
