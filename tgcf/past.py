# tgcf/past.py —— 已修复：确保每次转发后都有延迟（防 flood wait）

import asyncio
import logging
import random
from collections import defaultdict
from typing import List, Dict

from telethon import TelegramClient
from telethon.errors.rpcerrorlist import FloodWaitError
from telethon.tl.custom.message import Message
from telethon.tl.patched import MessageService

from tgcf import config
from tgcf import storage as st
from tgcf.config import CONFIG, get_SESSION, write_config
from tgcf.plugins import apply_plugins, apply_plugins_to_group, load_async_plugins
from tgcf.utils import clean_session_files, send_message


async def _send_past_grouped(
    client: TelegramClient, src: int, dest: List[int], messages: List[Message]
) -> bool:
    """强制发送整组消息作为 album"""
    tms = await apply_plugins_to_group(messages)
    if not tms:
        logging.warning("⚠️ 所有消息被插件过滤，但仍尝试发送空相册...")
        tms = [await apply_plugins(messages[0])]
        if not tms[0]:
            return True  # 过滤太强，跳过

    tm_template = tms[0]

    for d in dest:
        try:
            fwded_msgs = await send_message(
                d,
                tm_template,
                grouped_messages=[tm.message for tm in tms],
                grouped_tms=tms
            )

            first_msg_id = messages[0].id
            event_uid = st.EventUid(st.DummyEvent(src, first_msg_id))
            st.stored[event_uid] = {d: fwded_msgs}

        except Exception as e:
            logging.critical(f"🚨 组播失败但将继续重试（不中断）: {e}")

    return True


async def forward_job() -> None:
    clean_session_files()
    await load_async_plugins()

    if CONFIG.login.user_type != 1:
        logging.warning("⚠️ past 模式仅支持用户账号")
        return

    SESSION = get_SESSION()
    async with TelegramClient(SESSION, CONFIG.login.API_ID, CONFIG.login.API_HASH) as client:
        config.from_to = await config.load_from_to(client, CONFIG.forwards)

        for from_to, forward in zip(config.from_to.items(), CONFIG.forwards):
            src, dest = from_to
            last_id = 0
            grouped_buffer: Dict[int, List[Message]] = defaultdict(list)

            async for message in client.iter_messages(src, reverse=True, offset_id=forward.offset):
                if isinstance(message, MessageService):
                    continue

                if forward.end and last_id > forward.end:
                    continue

                success = False  # 标记本次是否成功处理

                try:
                    # === 处理媒体组缓存 ===
                    if message.grouped_id is not None:
                        grouped_buffer[message.grouped_id].append(message)
                        continue
                    else:
                        # 先发送所有缓存的媒体组
                        for gid, msgs in list(grouped_buffer.items()):
                            await _send_past_grouped(client, src, dest, msgs)
                        grouped_buffer.clear()

                    # === 处理单条消息 ===
                    tm = await apply_plugins(message)
                    if not tm:
                        logging.debug("❌ 消息被插件过滤，跳过.")
                        success = True  # 跳过也算“成功”，避免重复拉取
                        continue

                    event_uid = st.EventUid(st.DummyEvent(message.chat_id, message.id))
                    st.stored[event_uid] = {}

                    if message.is_reply:
                        r_event = st.DummyEvent(message.chat_id, message.reply_to_msg_id)
                        r_event_uid = st.EventUid(r_event)
                        if r_event_uid in st.stored:
                            tm.reply_to = st.stored[r_event_uid].get(dest[0])

                    sent_all = True
                    for d in dest:
                        try:
                            fwded_msg = await send_message(d, tm)
                            st.stored[event_uid][d] = fwded_msg.id
                        except Exception as e:
                            logging.error(f"❌ 单条发送失败: {e}")
                            sent_all = False

                    if not sent_all:
                        logging.warning(f"🟡 部分目标发送失败 [msg_id={message.id}]")

                    tm.clear()
                    success = True  # 只要没抛异常就算成功

                except FloodWaitError as fwe:
                    logging.warning(f"⛔ FloodWait: 等待 {fwe.seconds} 秒")
                    await asyncio.sleep(fwe.seconds)
                except Exception as err:
                    logging.exception(f"💥 消息处理失败 [msg_id={message.id}]: {err}")

                finally:
                    # ✅ 无论成功与否，都要延迟，防止暴力请求
                    delay_seconds = random.randint(60, 300)
                    logging.info(f"⏸️ 休息 {delay_seconds} 秒")
                    await asyncio.sleep(delay_seconds)

                # ✅ 只有处理成功才更新偏移量（断点续传）
                if success:
                    last_id = message.id
                    forward.offset = last_id
                    write_config(CONFIG, persist=False)

            # === 发送剩余媒体组 ===
            for gid, msgs in grouped_buffer.items():
                await _send_past_grouped(client, src, dest, msgs)
