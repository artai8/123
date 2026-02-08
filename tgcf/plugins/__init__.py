# tgcf/plugins/__init__.py —— 已修复版本

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
from tgcf.plugins import TgcfMessage, TgcfPlugin

PLUGINS = CONFIG.plugins

# ✅ 关键：显式定义插件执行顺序
PLUGIN_EXECUTION_ORDER = [
    "filter",   # 先过滤掉不需要的消息
    "ocr",      # OCR 提取图片文字作为内容
    "replace",  # 替换关键词（应基于原始或OCR后文本）
    "caption",  # 添加头尾说明
    "fmt",      # 最后添加格式（避免格式干扰替换）
    "mark",     # 水印最后加，不影响前面处理
]

_plugins = {}


def load_plugins() -> Dict[str, TgcfPlugin]:
    """Load plugins in defined order."""
    global _plugins
    _plugins = {}

    for plugin_id in PLUGIN_EXECUTION_ORDER:
        data = getattr(PLUGINS, plugin_id, None)
        if not data or not getattr(data, "check", False):
            continue

        try:
            module = __import__(f"tgcf.plugins.{plugin_id}", fromlist=[""])
            cls_name = f"Tgcf{plugin_id.title()}"
            cls = getattr(module, cls_name)

            plugin: TgcfPlugin = cls(data)
            if plugin.id_ != plugin_id:
                logging.error(f"Plugin ID mismatch: expected {plugin_id}, got {plugin.id_}")
                continue

            _plugins[plugin_id] = plugin
            logging.info(f"✅ 插件已加载: {plugin_id}")

        except Exception as e:
            logging.error(f"❌ 加载插件失败 {plugin_id}: {e}")

    return _plugins


async def apply_plugins(message: Message) -> TgcfMessage:
    """Apply all loaded plugins to a message in correct order."""
    tm = TgcfMessage(message)

    for plugin_id in PLUGIN_EXECUTION_ORDER:
        if plugin_id not in _plugins:
            continue

        plugin = _plugins[plugin_id]
        try:
            if inspect.iscoroutinefunction(plugin.modify):
                ntm = await plugin.modify(tm)
            else:
                ntm = plugin.modify(tm)

            if not ntm:
                tm.clear()
                return None
            tm = ntm  # 更新为新对象

        except Exception as err:
            logging.error(f"❌ 插件执行失败 [{plugin_id}]: {err}")
            return None  # 或继续？

    return tm


async def apply_plugins_to_group(messages: List[Message]) -> List[TgcfMessage]:
    """Apply plugins to a group of messages."""
    tms = [TgcfMessage(msg) for msg in messages]

    for plugin_id in PLUGIN_EXECUTION_ORDER:
        if plugin_id not in _plugins:
            continue

        plugin = _plugins[plugin_id]
        try:
            if hasattr(plugin, 'modify_group'):
                if inspect.iscoroutinefunction(plugin.modify_group):
                    tms = await plugin.modify_group(tms)
                else:
                    tms = plugin.modify_group(tms)
            else:
                # fallback
                tms = [await plugin.modify(tm) if inspect.iscoroutinefunction(plugin.modify) else plugin.modify(tm) for tm in tms]
        except Exception as err:
            logging.error(f"❌ 组插件执行失败 [{plugin_id}]: {err}")
        else:
            tms = [tm for tm in tms if tm]  # 过滤被过滤掉的

    for tm in tms:
        tm.clear()

    return tms


# 初始化插件
_plugins = load_plugins()


async def load_async_plugins() -> None:
    """异步初始化插件"""
    for id in ASYNC_PLUGIN_IDS:
        if id in _plugins:
            await _plugins[id].__ainit__()
            logging.info(f"🔌 异步插件已加载: {id}")
