# tgcf/plugins/__init__.py —— 修复循环导入后完整版

"""Subpackage of tgcf: plugins.

Contains all the first-party tgcf plugins.
"""

import inspect
import logging
from typing import Any, Dict, List, Union

from telethon.tl.custom.message import Message

from tgcf.config import CONFIG
from tgcf.plugin_models import ASYNC_PLUGIN_IDS
from tgcf.utils import cleanup, stamp


# === Step 1: 先定义核心类，不要做任何跨插件导入 ===

class TgcfMessage:
    def __init__(self, message: Message) -> None:
        self.message = message
        self.text = self.message.text or ""
        self.raw_text = self.message.raw_text or ""
        self.sender_id = self.message.sender_id
        self.file_type = self.guess_file_type()
        self.new_file = None
        self.cleanup = False
        self.reply_to = None
        self.client = self.message.client

    async def get_file(self) -> str:
        if self.file_type == "nofile":
            raise FileNotFoundError("No file exists in this message.")
        self.file = stamp(await self.message.download_media(""), self.sender_id)
        return self.file

    def guess_file_type(self) -> str:
        for ft in ["photo", "video", "gif", "audio", "document", "sticker", "contact", "voice"]:
            if getattr(self.message, ft, None):
                return ft
        return "nofile"

    def clear(self) -> None:
        if self.new_file and self.cleanup:
            cleanup(self.new_file)
            self.new_file = None


class TgcfPlugin:
    id_ = "plugin"

    def __init__(self, data: Dict[str, Any]) -> None:
        self.data = data

    async def __ainit__(self) -> None:
        """异步初始化钩子"""
        pass

    def modify(self, tm: TgcfMessage) -> TgcfMessage:
        """修改单条消息"""
        return tm

    def modify_group(self, tms: List[TgcfMessage]) -> List[TgcfMessage]:
        """修改一组消息"""
        return [self.modify(tm) for tm in tms if tm]


# === Step 2: 定义插件执行顺序（关键）===

PLUGIN_EXECUTION_ORDER = [
    "filter",
    "ocr",
    "replace",
    "caption",
    "fmt",
    "mark",
]

PLUGINS = CONFIG.plugins
_plugins = {}


# === Step 3: 插件加载函数（不再依赖 from tgcf.plugins 导入）===

def load_plugins() -> Dict[str, TgcfPlugin]:
    global _plugins
    _plugins = {}

    for plugin_id in PLUGIN_EXECUTION_ORDER:
        plugin_cfg = getattr(PLUGINS, plugin_id, None)
        if not plugin_cfg or not getattr(plugin_cfg, "check", False):
            continue

        try:
            # 动态导入模块
            module = __import__(f"tgcf.plugins.{plugin_id}", fromlist=[""])
            cls_name = f"Tgcf{plugin_id.title()}"
            plugin_class = getattr(module, cls_name)

            plugin: TgcfPlugin = plugin_class(plugin_cfg)
            if plugin.id_ != plugin_id:
                logging.error(f"Plugin ID mismatch: got {plugin.id_}, expected {plugin_id}")
                continue

            _plugins[plugin_id] = plugin
            logging.info(f"✅ 插件已加载: {plugin_id}")

        except Exception as e:
            logging.error(f"❌ 加载插件失败 {plugin_id}: {e}")

    return _plugins


# === Step 4: 消息处理入口函数 ===

async def apply_plugins(message: Message) -> TgcfMessage:
    tm = TgcfMessage(message)

    for pid in PLUGIN_EXECUTION_ORDER:
        if pid not in _plugins:
            continue
        plugin = _plugins[pid]
        try:
            if inspect.iscoroutinefunction(plugin.modify):
                result = await plugin.modify(tm)
            else:
                result = plugin.modify(tm)

            if not result:
                tm.clear()
                return None
            tm = result

        except Exception as err:
            logging.error(f"❌ 插件 [{pid}] 执行失败: {err}")
            return None

    return tm


async def apply_plugins_to_group(messages: List[Message]) -> List[TgcfMessage]:
    tms = [TgcfMessage(msg) for msg in messages]

    for pid in PLUGIN_EXECUTION_ORDER:
        if pid not in _plugins:
            continue
        plugin = _plugins[pid]
        try:
            if hasattr(plugin, "modify_group"):
                if inspect.iscoroutinefunction(plugin.modify_group):
                    tms = await plugin.modify_group(tms)
                else:
                    tms = plugin.modify_group(tms)
            else:
                # fallback
                tms = [
                    await plugin.modify(tm) if inspect.iscoroutinefunction(plugin.modify) else plugin.modify(tm)
                    for tm in tms
                ]
        except Exception as err:
            logging.error(f"❌ 组插件 [{pid}] 执行失败: {err}")
        else:
            tms = [tm for tm in tms if tm]

    for tm in tms:
        tm.clear()

    return tms


async def load_async_plugins() -> None:
    for pid in ASYNC_PLUGIN_IDS:
        if pid in _plugins:
            await _plugins[pid].__ainit__()
            logging.info(f"🔌 异步插件已加载: {pid}")


# === 最终初始化 ===
_plugins = load_plugins()
