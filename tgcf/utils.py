# nb/utils.py —— 修复 Hidden Media / Spoiler Effect 无法转发的问题

import logging
import asyncio
import re
import os
import sys
import platform
from datetime import datetime
from typing import TYPE_CHECKING, List, Optional, Union

from telethon.client import TelegramClient
from telethon.hints import EntityLike
from telethon.tl.custom.message import Message
from telethon.tl.types import (
    DocumentAttributeVideo,
    DocumentAttributeAnimated,
    DocumentAttributeAudio,
    DocumentAttributeSticker,
    InputMediaUploadedPhoto,
    InputMediaUploadedDocument,
    InputMediaPhoto,
    InputMediaDocument,
    InputPhoto,
    InputDocument,
    MessageMediaPhoto,
    MessageMediaDocument,
)

from nb import __version__
from nb.config import CONFIG
from nb.plugin_models import STYLE_CODES

if TYPE_CHECKING:
    from nb.plugins import TgcfMessage


def _has_spoiler(message: Message) -> bool:
    """检测消息的媒体是否带有 Spoiler（隐藏媒体）效果。

    Telethon 的 Message 对象中，spoiler 信息存储在：
    - message.media.spoiler (Telethon >= 1.28 的某些版本)
    - message.media 底层 TL 对象的 spoiler 字段

    对于 MessageMediaPhoto 和 MessageMediaDocument，
    TL 层的字段名为 `spoiler`（bool）。
    """
    if not message.media:
        return False

    media = message.media

    # MessageMediaPhoto 和 MessageMediaDocument 在 TL layer 160+ 有 spoiler 字段
    if hasattr(media, 'spoiler'):
        return bool(media.spoiler)

    # 兼容：某些 Telethon 版本将其存储在 ttl_seconds（自毁消息，不完全等同但相关）
    if hasattr(media, 'ttl_seconds') and media.ttl_seconds is not None:
        return True

    return False


def _build_spoiler_input_media(message: Message):
    """将带 spoiler 的消息媒体转换为带 spoiler=True 标记的 InputMedia 对象。

    这样在通过 send_file / send_media 发送时能保留 spoiler 效果。
    如果消息不含 spoiler 或无法转换，返回 None（让调用方回退到默认行为）。
    """
    if not _has_spoiler(message):
        return None

    media = message.media

    try:
        if isinstance(media, MessageMediaPhoto) and media.photo:
            photo = media.photo
            return InputMediaPhoto(
                id=InputPhoto(
                    id=photo.id,
                    access_hash=photo.access_hash,
                    file_reference=photo.file_reference,
                ),
                spoiler=True,
            )

        elif isinstance(media, MessageMediaDocument) and media.document:
            doc = media.document
            return InputMediaDocument(
                id=InputDocument(
                    id=doc.id,
                    access_hash=doc.access_hash,
                    file_reference=doc.file_reference,
                ),
                spoiler=True,
            )
    except Exception as e:
        logging.warning(f"⚠️ 构建 spoiler InputMedia 失败: {e}")

    return None


async def _send_album_with_spoiler(
    client: TelegramClient,
    recipient: EntityLike,
    grouped_messages: List[Message],
    caption: Optional[str] = None,
    reply_to: Optional[int] = None,
) -> List[Message]:
    """发送媒体组，正确保留每个媒体的 spoiler 属性。

    策略：
    1. 对每个消息检测是否有 spoiler
    2. 有 spoiler 的 → 构建带 spoiler=True 的 InputMedia
    3. 无 spoiler 的 → 使用原始 message 对象（Telethon 会自动提取媒体）
    4. 用 client.send_file 发送整组，传入混合的文件列表
    """
    files_to_send = []
    has_any_spoiler = False

    for msg in grouped_messages:
        spoiler_media = _build_spoiler_input_media(msg)
        if spoiler_media is not None:
            files_to_send.append(spoiler_media)
            has_any_spoiler = True
        else:
            # 没有 spoiler，使用原始消息对象作为媒体源
            if msg.photo or msg.video or msg.gif or msg.document:
                files_to_send.append(msg)

    if not files_to_send:
        raise ValueError("媒体组中没有可发送的文件")

    # 如果没有任何 spoiler，走普通路径即可
    if not has_any_spoiler:
        return await client.send_file(
            recipient,
            files_to_send,
            caption=caption or None,
            reply_to=reply_to,
            supports_streaming=True,
            force_document=False,
            allow_cache=False,
            parse_mode="md",
        )

    # 有 spoiler 的情况：需要使用 send_file 并传入 InputMedia 对象
    # Telethon 的 send_file 可以接受 InputMedia 对象列表
    try:
        result = await client.send_file(
            recipient,
            files_to_send,
            caption=caption or None,
            reply_to=reply_to,
            supports_streaming=True,
            force_document=False,
            allow_cache=False,
            parse_mode="md",
        )
        logging.info(f"✅ 成功发送带 spoiler 的媒体组 ({len(files_to_send)} 项)")
        return result
    except TypeError:
        # 某些 Telethon 版本的 send_file 不接受 InputMedia 混合列表
        # 回退方案：使用底层 API 直接发送
        logging.warning("⚠️ send_file 不支持混合 InputMedia，尝试底层 API...")
        return await _send_album_via_raw_api(
            client, recipient, grouped_messages, caption, reply_to
        )


async def _send_album_via_raw_api(
    client: TelegramClient,
    recipient: EntityLike,
    grouped_messages: List[Message],
    caption: Optional[str] = None,
    reply_to: Optional[int] = None,
) -> List[Message]:
    """通过 Telethon 底层 TL 请求发送带 spoiler 的媒体组。

    使用 messages.SendMultiMedia 请求，手动构建每个 InputSingleMedia。
    """
    from telethon.tl.functions.messages import SendMultiMediaRequest
    from telethon.tl.types import (
        InputSingleMedia,
        InputPeerEmpty,
    )
    import random

    peer = await client.get_input_entity(recipient)
    multi_media = []

    for i, msg in enumerate(grouped_messages):
        media = msg.media
        is_spoiler = _has_spoiler(msg)

        # 取该消息的文本（仅第一条带 caption，或合并后的 caption）
        if i == 0 and caption:
            msg_text = caption
        else:
            msg_text = ""

        input_media = None

        if isinstance(media, MessageMediaPhoto) and media.photo:
            photo = media.photo
            input_media = InputMediaPhoto(
                id=InputPhoto(
                    id=photo.id,
                    access_hash=photo.access_hash,
                    file_reference=photo.file_reference,
                ),
                spoiler=is_spoiler,
            )

        elif isinstance(media, MessageMediaDocument) and media.document:
            doc = media.document
            input_media = InputMediaDocument(
                id=InputDocument(
                    id=doc.id,
                    access_hash=doc.access_hash,
                    file_reference=doc.file_reference,
                ),
                spoiler=is_spoiler,
            )

        if input_media is None:
            logging.warning(f"⚠️ 跳过无法识别的媒体类型: {type(media)}")
            continue

        single = InputSingleMedia(
            media=input_media,
            random_id=random.randrange(-2**63, 2**63),
            message=msg_text,
        )
        multi_media.append(single)

    if not multi_media:
        raise ValueError("没有有效的媒体可发送")

    # 构建请求参数
    kwargs = {
        'peer': peer,
        'multi_media': multi_media,
    }
    if reply_to is not None:
        kwargs['reply_to_msg_id'] = reply_to

    result = await client(SendMultiMediaRequest(**kwargs))

    # 解析返回的 Updates 获取发送后的消息
    sent_messages = []
    if hasattr(result, 'updates'):
        for update in result.updates:
            if hasattr(update, 'message'):
                sent_messages.append(update.message)

    logging.info(f"✅ 底层 API 成功发送带 spoiler 的媒体组 ({len(multi_media)} 项)")
    return sent_messages if sent_messages else result


def platform_info():
    nl = "\n"
    return f"""Running nb {__version__}\
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
    强制将一组消息作为 album 发送，正确保留 spoiler 效果。
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
            delay = min(delay * 2, 300)
            await asyncio.sleep(delay)

    # === 情况 2: 复制模式发送（apply_plugins 后）—— 修复 spoiler ===
    if grouped_messages and grouped_tms:
        combined_caption = "\n\n".join([
            gtm.text.strip() for gtm in grouped_tms
            if gtm.text and gtm.text.strip()
        ])

        # 检测是否有任何消息带 spoiler
        any_spoiler = any(_has_spoiler(msg) for msg in grouped_messages)

        if any_spoiler:
            logging.info("🔒 检测到 Hidden Media / Spoiler，使用 spoiler 保留模式发送")

        # 开始重试循环
        attempt = 0
        delay = 5
        while True:
            try:
                if any_spoiler:
                    # 使用专门的 spoiler 发送函数
                    result = await _send_album_with_spoiler(
                        client,
                        recipient,
                        grouped_messages,
                        caption=combined_caption or None,
                        reply_to=tm.reply_to,
                    )
                else:
                    # 无 spoiler，走原来的路径
                    files_to_send = []
                    for msg in grouped_messages:
                        if msg.photo or msg.video or msg.gif or msg.document:
                            files_to_send.append(msg)

                    if not files_to_send:
                        return await client.send_message(
                            recipient,
                            combined_caption or "空相册",
                            reply_to=tm.reply_to,
                        )

                    result = await client.send_file(
                        recipient,
                        files_to_send,
                        caption=combined_caption or None,
                        reply_to=tm.reply_to,
                        supports_streaming=True,
                        force_document=False,
                        allow_cache=False,
                        parse_mode="md",
                    )

                logging.info(
                    f"✅ 成功复制发送媒体组"
                    f"{'（含 spoiler）' if any_spoiler else ''}"
                    f" → 第 {attempt+1} 次尝试"
                )
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

    # === 情况 3: 单条消息处理（非 grouped）—— 也处理 spoiler ===
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

    # 单条带 spoiler 的媒体消息
    if _has_spoiler(tm.message):
        spoiler_media = _build_spoiler_input_media(tm.message)
        if spoiler_media is not None:
            try:
                result = await client.send_file(
                    recipient,
                    spoiler_media,
                    caption=tm.text,
                    reply_to=tm.reply_to,
                    parse_mode="md",
                )
                logging.info("✅ 成功发送带 spoiler 的单条消息")
                return result
            except Exception as e:
                logging.warning(f"⚠️ spoiler 单条发送失败，回退普通模式: {e}")

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
        return file


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
    """Delete .session and .session-journal files."""
    for item in os.listdir():
        if item.endswith(".session") or item.endswith(".session-journal"):
            os.remove(item)
            logging.info(f"🧹 删除会话文件: {item}")
