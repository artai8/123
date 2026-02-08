# tgcf/past.py —— 完整修复版

"""The module for running tgcf in past mode.

- past mode can only operate with a user account.
- past mode deals with all existing messages.
"""

import asyncio
import logging
import time
from collections import defaultdict
from typing import List, Dict

from telethon import TelegramClient
from telethon.errors.rpcerrorlist import FloodWaitError, MediaInvalidError
from telethon.tl.custom.message import Message
from telethon.tl.patched import MessageService

from tgcf import config
from tgcf import storage as st
from tgcf.config import CONFIG, get_SESSION, write_config
from tgcf.plugins import apply_plugins, apply_plugins_to_group, load_async_plugins
from tgcf.utils import clean_session_files, send_message


async def _send_past_grouped(client: TelegramClient, src: int, dest: List[int], messages: List[Message]) -> None:
    """发送 past 模式下聚合的媒体组"""
    if not messages:
        return

    # ✅ Step 1: 对整个媒体组应用插件
    tms = await apply_plugins_to_group(messages)
    if not tms:
        logging.info("All messages filtered out by plugins. Skipping group.")
        return

    # ✅ Step 2: 提取处理后的消息，并过滤仅支持的媒体类型 (photo/video)
    valid_messages = []
    for tm in tms:
        msg = tm.message
        if getattr(msg, "photo", None) or getattr(msg, "video", None):
            valid_messages.append(msg)
        else:
            logging.debug(f"Message {msg.id} skipped in group: unsupported media type.")

    if not valid_messages:
        logging.warning("No valid media (photo/video) after filtering. Skipping group.")
        for tm in tms:
            tm.clear()
        return

    # 使用第一条消息作为代表
    tm = tms[0]

    for d in dest:
        # 处理回复关系
        if valid_messages[0].is_reply:
            r_event = st.DummyEvent(valid_messages[0].chat_id, valid_messages[0].reply_to_msg_id)
            r_event_uid = st.EventUid(r_event)
            if r_event_uid in st.stored:
                tm.reply_to = st.stored.get(r_event_uid).get(d)

        try:
            # 尝试作为媒体组发送
            fwded_msgs = await send_message(d, tm, grouped_messages=valid_messages)
            first_event_uid = st.EventUid(st.DummyEvent(valid_messages[0].chat_id, valid_messages[0].id))
            st.stored[first_event_uid] = {d: fwded_msgs}
            logging.info(f"Successfully sent media group with {len(valid_messages)} items to {d}")

        except MediaInvalidError as mie:
            logging.warning(f"MediaInvalidError: {mie}. Falling back to sending individually.")
            # 降级：逐条发送
            for single_msg in valid_messages:
                single_tm = await apply_plugins(single_msg)
                if not single_tm:
                    continue
                try:
                    await send_message(d, single_tm)
                except Exception as e:
                    logging.error(f"Failed to send individual message {single_msg.id}: {e}")
                finally:
                    single_tm.clear()

        except Exception as e:
            logging.error(f"Unexpected error sending media group: {e}")

    # 清理资源
    for tm in tms:
        tm.clear()


async def forward_job() -> None:
    """Forward all existing messages in the concerned chats."""
    clean_session_files()

    # load async plugins defined in plugin_models
    await load_async_plugins()

    if CONFIG.login.user_type != 1:
        logging.warning(
            "You cannot use bot account for tgcf past mode. Telegram does not allow bots to access chat history."
        )
        return

    SESSION = get_SESSION()
    async with TelegramClient(
        SESSION, CONFIG.login.API_ID, CONFIG.login.API_HASH
    ) as client:
        config.from_to = await config.load_from_to(client, config.CONFIG.forwards)
        client: TelegramClient
        for from_to, forward in zip(config.from_to.items(), config.CONFIG.forwards):
            src, dest = from_to
            last_id = 0
            forward: config.Forward
            logging.info(f"Forwarding messages from {src} to {dest}")

            # 用于聚合媒体组
            grouped_buffer: Dict[int, List[Message]] = defaultdict(list)
            processed_groups = set()

            async for message in client.iter_messages(
                src, reverse=True, offset_id=forward.offset
            ):
                message: Message
                event = st.DummyEvent(message.chat_id, message.id)
                event_uid = st.EventUid(event)

                if forward.end and last_id > forward.end:
                    continue
                if isinstance(message, MessageService):
                    continue

                try:
                    # 媒体组处理
                    if message.grouped_id is not None:
                        if message.grouped_id not in processed_groups:
                            grouped_buffer[message.grouped_id].append(message)
                            continue
                        else:
                            continue
                    else:
                        # 发送之前缓存的媒体组
                        for gid, msgs in grouped_buffer.items():
                            await _send_past_grouped(client, src, dest, msgs)
                            processed_groups.add(gid)
                        grouped_buffer.clear()

                    tm = await apply_plugins(message)
                    if not tm:
                        continue

                    st.stored[event_uid] = {}

                    if message.is_reply:
                        r_event = st.DummyEvent(
                            message.chat_id, message.reply_to_msg_id
                        )
                        r_event_uid = st.EventUid(r_event)

                    for d in dest:
                        if message.is_reply and r_event_uid in st.stored:
                            tm.reply_to = st.stored.get(r_event_uid).get(d)
                        fwded_msg = await send_message(d, tm)
                        st.stored[event_uid].update({d: fwded_msg.id})

                    tm.clear()
                    last_id = message.id
                    logging.info(f"forwarding message with id = {last_id}")
                    forward.offset = last_id
                    write_config(CONFIG, persist=False)
                    time.sleep(CONFIG.past.delay)
                    logging.info(f"slept for {CONFIG.past.delay} seconds")

                except FloodWaitError as fwe:
                    logging.info(f"Sleeping for {fwe}")
                    await asyncio.sleep(delay=fwe.seconds)
                except Exception as err:
                    logging.exception(err)

            # 处理剩余媒体组
            for gid, msgs in grouped_buffer.items():
                await _send_past_grouped(client, src, dest, msgs)
                processed_groups.add(gid)
            grouped_buffer.clear()
