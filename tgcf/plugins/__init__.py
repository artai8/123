"""Subpackage of tgcf: plugins.  
  
Contains all the first-party tgcf plugins.  
"""  
  
import inspect  
import logging  
from enum import Enum  
from importlib import import_module  
from typing import Any, Dict, List, Union  
  
from telethon.tl.custom.message import Message  
  
from tgcf.config import CONFIG  
from tgcf.plugin_models import FileType, ASYNC_PLUGIN_IDS  
from tgcf.utils import cleanup, stamp  
  
PLUGINS = CONFIG.plugins  
  
  
class TgcfMessage:  
    def __init__(self, message: Message) -> None:  
        self.message = message  
        self.text = self.message.text  
        self.raw_text = self.message.raw_text  
        self.sender_id = self.message.sender_id  
        self.file_type = self.guess_file_type()  
        self.new_file = None  
        self.cleanup = False  
        self.reply_to = None  
        self.client = self.message.client  
  
    async def get_file(self) -> str:  
        """Downloads the file in the message and returns the path where its saved."""  
        if self.file_type == FileType.NOFILE:  
            raise FileNotFoundError("No file exists in this message.")  
        self.file = stamp(await self.message.download_media(""), self.sender_id)  
        return self.file  
  
    def guess_file_type(self) -> FileType:  
        for i in FileType:  
            if i == FileType.NOFILE:  
                return i  
            obj = getattr(self.message, i.value)  
            if obj:  
                return i  
  
    def clear(self) -> None:  
        if self.new_file and self.cleanup:  
            cleanup(self.new_file)  
            self.new_file = None  
  
  
class TgcfPlugin:  
    id_ = "plugin"  
  
    def __init__(self, data: Dict[str, Any]) -> None:  
        self.data = data  
  
    async def __ainit__(self) -> None:  
        """Asynchronous initialization here."""  
  
    def modify(self, tm: TgcfMessage) -> TgcfMessage:  
        """Modify the message here."""  
        return tm  
  
    def modify_group(self, tms: List[TgcfMessage]) -> List[TgcfMessage]:  
        """Modify a group of messages here. Default applies modify to each."""  
        return [self.modify(tm) for tm in tms if tm]  
  
  
def load_plugins() -> Dict[str, TgcfPlugin]:  
    """Load the plugins specified in config."""  
    _plugins = {}  
    for item in PLUGINS:  
        plugin_id = item[0]  
        if item[1].check == False:  
            continue  
  
        plugin_class_name = f"Tgcf{plugin_id.title()}"  
  
        try:  
            plugin_module = import_module("tgcf.plugins." + plugin_id)  
        except ModuleNotFoundError:  
            logging.error(  
                f"{plugin_id} is not a first party plugin. Third party plugins are not supported."  
            )  
        else:  
            logging.info(f"First party plugin {plugin_id} loaded!")  
  
        try:  
            plugin_class = getattr(plugin_module, plugin_class_name)  
            if not issubclass(plugin_class, TgcfPlugin):  
                logging.error(  
                    f"Plugin class {plugin_class_name} does not inherit TgcfPlugin"  
                )  
                continue  
            plugin: TgcfPlugin = plugin_class(item[1])  
            if not plugin.id_ == plugin_id:  
                logging.error(f"Plugin id for {plugin_id} does not match expected id.")  
                continue  
        except AttributeError:  
            logging.error(f"Found plugin {plugin_id}, but plugin class not found.")  
        else:  
            logging.info(f"Loaded plugin {plugin_id}")  
            _plugins.update({plugin.id_: plugin})  
    return _plugins  
  
  
async def load_async_plugins() -> None:  
    """Load async plugins specified plugin_models."""  
    if plugins:  
        for id in ASYNC_PLUGIN_IDS:  
            if id in plugins:  
                await plugins[id].__ainit__()  
                logging.info(f"Plugin {id} asynchronously loaded")  
  
  
async def apply_plugins(message: Message) -> TgcfMessage:  
    """Apply all loaded plugins to a message."""  
    tm = TgcfMessage(message)  
  
    for _id, plugin in plugins.items():  
        try:  
            if inspect.iscoroutinefunction(plugin.modify):  
                ntm = await plugin.modify(tm)  
            else:  
                ntm = plugin.modify(tm)  
        except Exception as err:  
            logging.error(f"Failed to apply plugin {_id}. \n {err} ")  
        else:  
            logging.info(f"Applied plugin {_id}")  
            if not ntm:  
                tm.clear()  
                return None  
    return tm  
  
  
async def apply_plugins_to_group(messages: List[Message]) -> List[TgcfMessage]:  
    """Apply all loaded plugins to a group of messages."""  
    tms = [TgcfMessage(msg) for msg in messages]  
  
    for _id, plugin in plugins.items():  
        try:  
            if hasattr(plugin, 'modify_group'):  
                if inspect.iscoroutinefunction(plugin.modify_group):  
                    tms = await plugin.modify_group(tms)  
                else:  
                    tms = plugin.modify_group(tms)  
            else:  
                # Fallback: apply modify to each message  
                if inspect.iscoroutinefunction(plugin.modify):  
                    tms = [await plugin.modify(tm) for tm in tms]  
                else:  
                    tms = [plugin.modify(tm) for tm in tms]  
        except Exception as err:  
            logging.error(f"Failed to apply plugin {_id} to group. \n {err} ")  
        else:  
            logging.info(f"Applied plugin {_id} to group")  
            # Filter out None messages  
            tms = [tm for tm in tms if tm]  
      
    # Cleanup filtered messages  
    for tm in tms:  
        tm.clear()  
      
    return tms  
  
  
plugins = load_plugins()
