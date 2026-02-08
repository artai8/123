# tgcf/past.py —— 已修复 FloodWait & 媒体组问题 + 随机延迟 60-300s

"""The module for running tgcf in past mode.

- past mode can only operate with a user account.
- past mode deals with all existing messages.
"""

import asyncio
import logging
import time
import random
from collections import defaultdict
from typing import List, Dict

from telethon import TelegramClient
from telethon.errors.rpcerrorlist import FloodWaitError, MediaInvalidError
from telethon.tl.custom.message import Message

from tgcf import config
from tgcf import storage as st
from tgcf.config import CONFIG, get_SESSION, write_config
from tgcf.plugins import apply_plugins, apply_plugins_to_group, load_async_plugins
from tgcf.utils import clean_session_files, send_message


async def _send_past_grouped(
    client: TelegramClient, src: int, dest: List[int], messages: List[Message]
) -> bool:
    """发送 past 模式下的媒体组，带 FloodWait 自动处理和重试机制"""
    if not messages:
        return True

    grouped_id = messages[0].grouped_id
    logging.info(f"准备发送媒体组 grouped_id={grouped_id}，共 {len(messages)} 条消息")

    # Step 1: 应用插件
    tms = await apply_plugins_to_group(messages)
    if not tms:
        logging.info("所有消息被插件过滤，跳过该媒体组。")
        return True

    # Step 2: 提取支持的媒体类型 (photo/video)
    valid_messages = []
    for tm in tms:
        msg = tm.message
        if getattr(msg, "photo", None) or getattr(msg, "video", None):
            valid_messages.append(msg)
        else:
            logging.debug(f"消息 {msg.id} 不支持作为媒体组发送，已跳过。")

    if not valid_messages:
        logging.warning("媒体组中无有效媒体内容（仅支持图片/视频），已跳过。")
        for tm in tms:
            tm.clear()
        return True

    # 使用第一条消息作为模板
    tm_template = tms[0]

    success = True
    for d in dest:
        sent = False
        retries = 0
        max_retries = 3

        while not sent and retries < max_retries:
            try:
                # 处理回复关系
                if valid_messages[0].is_reply:
                    r_event = st.DummyEvent(valid_messages[0].chat_id, valid_messages[0].reply_to_msg_id)
                    r_event_uid = st.EventUid(r_event)
                    if r_event_uid in st.stored:
                        tm_template.reply_to = st.stored.get(r_event_uid).get(d)

                # 尝试发送媒体组
                fwded_msgs = await send_message(d, tm_template, grouped_messages=valid_messages)
                first_msg_id = valid_messages[0].id
                event_uid = st.EventUid(st.DummyEvent(src, first_msg_id))
                st.stored[event_uid] = {d: fwded_msgs}
                logging.info(f"✅ 成功将媒体组 {grouped_id} 发送至 {d}")
                sent = True

            except FloodWaitError as fwe:
                wait_sec = fwe.seconds
                logging.warning(f"❌ FloodWait 触发！需等待 {wait_sec} 秒... （第 {retries+1} 次尝试）")
                await asyncio.sleep(wait_sec)
                retries += 1

            except MediaInvalidError as mie:
                logging.warning(f"❌ MediaInvalidError: {mie}，尝试逐条发送...")
                # 降级为逐条发送
                for single_msg in valid_messages:
                    single_tm = await apply_plugins(single_msg)
                    if not single_tm:
                        continue
                    try:
                        await send_message(d, single_tm)
                        logging.info(f"✅ 单条发送成功: {single_msg.id}")
                    except Exception as e:
                        logging.error(f"❌ 单条发送失败 {single_msg.id}: {e}")
                    finally:
                        single_tm.clear()
                sent = True  # 标记为已处理

            except Exception as e:
                logging.error(f"❌ 向 {d} 发送媒体组 {grouped_id} 时发生未知错误: {e}")
                retries += 1
                if retries < max_retries:
                    backoff = 2 ** retries + random.uniform(0, 5)
                    logging.info(f"等待 {backoff:.1f}s 后重试...")
                    await asyncio.sleep(backoff)
                else:
                    logging.critical(f"❌ 达到最大重试次数，放弃发送该目标 {d}")
                    success = False

        if not sent:
            logging.critical(f"❌ 所有重试失败，无法向 {d} 发送媒体组 {grouped_id}")

    # 清理资源
    for tm in tms:
        tm.clear()

    return success


async def forward_job() -> None:
    """Forward all existing messages in the concerned chats."""
    clean_session_files()

    # 加载异步插件（如 sender）
    await load_async_plugins()

    if CONFIG.login.user_type != 1:
        logging.warning(
            "⚠️ 你不能使用 Bot 账号运行 past 模式。Telegram 不允许 Bot 访问聊天历史。"
        )
        return

    SESSION = get_SESSION()
    async with TelegramClient(
        SESSION, CONFIG.login.API_ID, CONFIG.login.API_HASH
    ) as client:
        config.from_to = await config.load_from_to(client, CONFIG.forwards)

        for from_to, forward in zip(config.from_to.items(), CONFIG.forwards):
            src, dest = from_to
            last_id = 0
            forward: config.Forward
            logging.info(f"📌 开始迁移消息：从 {src} 到 {dest}")

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
                if message.action:
                    continue  # 忽略系统消息

                try:
                    # === 媒体组处理逻辑 ===
                    if message.grouped_id is not None:
                        if message.grouped_id not in processed_groups:
                            grouped_buffer[message.grouped_id].append(message)
                            continue
                        else:
                            continue
                    else:
                        # 先发送缓存中的媒体组
                        for gid, msgs in list(grouped_buffer.items()):
                            if gid not in processed_groups:
                                success = await _send_past_grouped(client, src, dest, msgs)
                                if success:
                                    processed_groups.add(gid)
                        grouped_buffer.clear()

                    # === 单条消息处理 ===
                    tm = await apply_plugins(message)
                    if not tm:
                        continue

                    st.stored[event_uid] = {}

                    if message.is_reply:
                        r_event = st.DummyEvent(message.chat_id, message.reply_to_msg_id)
                        r_event_uid = st.EventUid(r_event)
                        if r_event_uid in st.stored:
                            tm.reply_to = st.stored.get(r_event_uid).get(dest[0])

                    for d in dest:
                        try:
                            fwded_msg = await send_message(d, tm)
                            st.stored[event_uid][d] = fwded_msg.id
                        except FloodWaitError as fwe:
                            logging.warning(f"⚠️ FloodWait: 等待 {fwe.seconds} 秒...")
                            await asyncio.sleep(fwe.seconds)
                            # 可选重试一次
                            try:
                                fwded_msg = await send_message(d, tm)
                                st.stored[event_uid][d] = fwded_msg.id
                            except Exception as e:
                                logging.error(f"重试失败: {e}")
                        except Exception as e:
                            logging.error(f"发送单条消息 {message.id} 失败: {e}")

                    tm.clear()
                    last_id = message.id
                    logging.info(f"📩 已转发消息 id={last_id}")

                    # 更新 offset 并持久化（每 10 条保存一次）
                    forward.offset = last_id
                    if last_id % 10 == 0:
                        write_config(CONFIG, persist=False)

                    # 🌟 关键修复：使用 60~300 秒之间的随机延迟
                    delay = random.randint(60, 300)
                    logging.info(f"⏸️ 休息 {delay} 秒以避免触发 FloodWait...")
                    await asyncio.sleep(delay)

                except FloodWaitError as fwe:
                    logging.warning(f"全局 FloodWait: 等待 {fwe.seconds} 秒...")
                    await asyncio.sleep(fwe.seconds)
                except Exception as err:
                    logging.exception(f"处理消息 {message.id} 时出错: {err}")

            # 处理剩余媒体组
            for gid, msgs in list(grouped_buffer.items()):
                if gid not in processed_groups:
                    await _send_past_grouped(client, src, dest, msgs)
                    processed_groups.add(gid)
