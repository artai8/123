# tgcf/utils.py —— 新增功能：支持 .replace(text, new='bold')

import logging
import os
import platform
import re
import sys
from datetime import datetime
from typing import TYPE_CHECKING, List, Optional, Union

from telethon.client import TelegramClient
from telethon.hints import EntityLike
from telethon.tl.custom.message import Message

from tgcf import __version__
from tgcf.config import CONFIG
from tgcf.plugin_models import STYLE_CODES

if TYPE_CHECKING:
    from tgcf.plugins import TgcfMessage


def platform_info():
    nl = "\n"
    return f"""Running tgcf {__version__}\
    \nPython {sys.version.replace(nl,"")}\
    \nOS {os.name}\
    \nPlatform {platform.system()} {platform.release()}\
    \n{platform.architecture()} {platform.processor()}"""


async def send_message(recipient: EntityLike, tm: "TgcfMessage", grouped_messages: Optional[List[Message]] = None) -> Union[Message, List[Message]]:
    client: TelegramClient = tm.client

    if CONFIG.show_forwarded_from:
        if grouped_messages:
            return await client.forward_messages(recipient, grouped_messages)
        return await client.forward_messages(recipient, tm.message)

    if grouped_messages:
        for msg in grouped_messages:
            if not (getattr(msg, "photo", None) or getattr(msg, "video", None)):
                raise ValueError("Only photo/video messages are allowed in grouped send")

        caption = None
        for msg in grouped_messages:
            if msg.text and msg.text.strip():
                caption = msg.text
                break

        return await client.send_file(recipient, grouped_messages, caption=caption, reply_to=tm.reply_to)

    if tm.new_file:
        message = await client.send_file(
            recipient, tm.new_file, caption=tm.text, reply_to=tm.reply_to
        )
        return message

    tm.message.text = tm.text
    return await client.send_message(recipient, tm.message, reply_to=tm.reply_to)


def cleanup(*files: str) -> None:
    for file in files:
        try:
            os.remove(file)
        except FileNotFoundError:
            logging.info(f"File {file} does not exist.")


def stamp(file: str, user: str) -> str:
    now = str(datetime.now())
    outf = safe_name(f"{user} {now} {file}")
    try:
        os.rename(file, outf)
        return outf
    except Exception as err:
        logging.warning(f"重命名失败 {file} → {outf}: {err}")


def safe_name(string: str) -> str:
    return re.sub(pattern=r"[-!@#$%^&*()\s]", repl="_", string=string)


def match(pattern: str, string: str, regex: bool) -> bool:
    if regex:
        return bool(re.findall(pattern, string))
    return pattern in string


def replace(pattern: str, new: str, string: str, regex: bool) -> str:
    """
    Replace text with support for style keywords like 'bold', 'italics'
    Example: replace('hello', 'bold', 'hello world') → '**hello** world'
    """

    def fmt_repl(matched):
        style = new
        code = STYLE_CODES.get(style)
        if code:
            return f"{code}{matched.group(0)}{code}"
        return style  # literal replacement

    if regex:
        if new in STYLE_CODES:
            compiled_pattern = re.compile(pattern)
            return compiled_pattern.sub(repl=fmt_repl, string=string)
        return re.sub(pattern, new, string)
    else:
        if new in STYLE_CODES:
            code = STYLE_CODES[new]
            return string.replace(pattern, f"{code}{pattern}{code}")
        return string.replace(pattern, new)


def clean_session_files():
    for item in os.listdir():
        if item.endswith(".session") or item.endswith(".session-journal"):
            os.remove(item)
