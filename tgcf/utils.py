# tgcf/utils.py —— 已修复：强制完整转发媒体组

import logging
import asyncio
import re
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


async def send_message(
    recipient: EntityLike,
    tm: "TgcfMessage",
    grouped_messages: Optional[List[Message]] = None,
    grouped_tms: Optional[List["TgcfMessage"]] = None,
) -> Union[Message, List[Message]]:
    """
    强制将一组消息作为 album 发送。
    - 成功则返回结果
    - 失败则指数退避 + 无限重试
    - 不降级为单条发送
    """
    client: TelegramClient = tm.client

    # === 情况 1: 尝试直接转发原始 album ===
    if CONFIG.show_forwarded_from and grouped_messages:
        attempt = 0
        delay = 5
        while True:
            try:
                result = await client.forward_messages(recipient, grouped_messages)
                logging.info(f"✅ 成功直接转发媒体组 → 第 {attempt+1} 次尝试")
                return result
            except TimeoutError as te:
                logging.warning(f"⏳ 转发超时 (attempt={attempt+1}): {te}")
            except ConnectionError as ce:
                logging.warning(f"🔌 连接中断 (attempt={attempt+1}): {ce}")
            except Exception as e:
                if "FLOOD_WAIT" in str(e).upper():
                    wait_sec = int(re.search(r'\d+', str(e)).group())
                    logging.critical(f"⛔ FloodWait 触发！必须等待 {wait_sec} 秒...")
                    await asyncio.sleep(wait_sec + 10)
                    delay = 60
                else:
                    logging.error(f"❌ 直接转发失败 (attempt={attempt+1}): {e}")

            attempt += 1
            delay = min(delay * 2, 300)  # 最长 5 分钟
            await asyncio.sleep(delay)

    # === 情况 2: 复制模式发送（apply_plugins 后）===
    if grouped_messages and grouped_tms:
        # 合并所有文本
        combined_caption = "\n\n".join([
            gtm.text.strip() for gtm in grouped_tms
            if gtm.text and gtm.text.strip()
        ])

        files_to_send = []
        for msg in grouped_messages:
            if msg.photo or msg.video or msg.gif or msg.document:
                files_to_send.append(msg)

        if not files_to_send:
            # 至少发一条空消息
            try:
                return await client.send_message(recipient, combined_caption or "空相册", reply_to=tm.reply_to)
            except Exception as e:
                logging.error(f"❌ 空消息发送失败: {e}")
                raise RuntimeError("无法发送空相册")

        # 开始重试循环
        attempt = 0
        delay = 5
        while True:
            try:
                result = await client.send_file(
                    recipient,
                    files_to_send,
                    caption=combined_caption or None,
                    reply_to=tm.reply_to,
                    supports_streaming=True,
                    force_document=False,
                    allow_cache=False,
                    parse_mode="md"
                )
                logging.info(f"✅ 成功复制发送媒体组（{len(files_to_send)} 项）→ 第 {attempt+1} 次尝试")
                return result

            except TimeoutError as te:
                logging.warning(f"⏳ 网络超时 (attempt={attempt+1}): {te}")
            except ConnectionError as ce:
                logging.warning(f"🔌 连接中断 (attempt={attempt+1}): {ce}")
            except Exception as e:
                if "FLOOD_WAIT" in str(e).upper():
                    wait_sec = int(re.search(r'\d+', str(e)).group())
                    logging.critical(f"⛔ FloodWait 触发！等待 {wait_sec} 秒...")
                    await asyncio.sleep(wait_sec + 10)
                    delay = 60
                else:
                    logging.error(f"❌ 发送失败 (attempt={attempt+1}): {e}")

            attempt += 1
            delay = min(delay * 2, 300)
            await asyncio.sleep(delay)

    # === 情况 3: 单条消息处理（非 grouped）===
    if tm.new_file:
        try:
            return await client.send_file(
                recipient,
                tm.new_file,
                caption=tm.text,
                reply_to=tm.reply_to,
                supports_streaming=True,
            )
        except Exception as e:
            logging.error(f"❌ 新文件发送失败: {e}")

    try:
        tm.message.text = tm.text
        return await client.send_message(recipient, tm.message, reply_to=tm.reply_to)
    except Exception as e:
        logging.error(f"❌ 文本消息发送失败: {e}")
        return None


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
    def fmt_repl(matched):
        style = new
        code = STYLE_CODES.get(style)
        return f"{code}{matched.group(0)}{code}" if code else new

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
