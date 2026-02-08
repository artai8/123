# tgcf/live.py —— 已修复 FloodWait & 媒体组问题（支持 live 模式）

"""The module responsible for operating tgcf in live mode."""

import asyncio
import logging
import random
from typing import Union, List

from telethon import TelegramClient, events, functions, types
from telethon.errors import MediaInvalidError, FloodWaitError
from telethon.tl.custom.message import Message

from tgcf import config, const
from tgcf import storage as st
from tgcf.bot import get_events
from tgcf.config import CONFIG, get_SESSION
from tgcf.plugins import apply_plugins, apply_plugins_to_group, load_async_plugins
from tgcf.utils import clean_session_files, send_message


async def _send_grouped_messages(grouped_id: int) -> None:
    """发送缓存中的媒体组到所有目标，带 FloodWait 处理和降级机制"""
    if grouped_id not in st.GROUPED_CACHE:
        return

    chat_messages_map = st.GROUPED_CACHE[grouped_id]  # {chat_id: [messages]}
    success = True

    for chat_id, messages in chat_messages_map.items():
        if chat_id not in config.from_to:
            continue

        dest = config.from_to.get(chat_id)

        # ✅ Step 1: 对整组消息应用插件
        tms = await apply_plugins_to_group(messages)
        if not tms:
            logging.info(f"媒体组 {grouped_id} 被插件过滤，已跳过。")
            continue

        # ✅ Step 2: 提取有效媒体消息（仅 photo/video）
        valid_messages = []
        for tm in tms:
            msg = tm.message
            if getattr(msg, "photo", None) or getattr(msg, "video", None):
                valid_messages.append(msg)
            else:
                logging.debug(f"消息 {msg.id} 不支持作为媒体组内容，已跳过。")

        if not valid_messages:
            logging.warning(f"媒体组 {grouped_id} 中无有效媒体内容，已跳过。")
            for tm in tms:
                tm.clear()
            continue

        # 使用第一条消息作为模板
        tm_template = tms[0]
        reply_to = None
        if messages[0].is_reply:
            r_event = st.DummyEvent(chat_id, messages[0].reply_to_msg_id)
            r_event_uid = st.EventUid(r_event)
            if r_event_uid in st.stored:
                reply_to = st.stored.get(r_event_uid).get(dest[0])
        tm_template.reply_to = reply_to

        for d in dest:
            sent = False
            retries = 0
            max_retries = 3

            while not sent and retries < max_retries:
                try:
                    fwded_msgs = await send_message(d, tm_template, grouped_messages=valid_messages)
                    logging.info(f"✅ 成功将媒体组 {grouped_id} 发送至 {d}")

                    # 存储映射：每条原始消息 ↔ 转发后消息
                    for i, original_msg in enumerate(messages):
                        event_uid = st.EventUid(st.DummyEvent(chat_id, original_msg.id))
                        if event_uid not in st.stored:
                            st.stored[event_uid] = {}
                        if isinstance(fwded_msgs, list) and i < len(fwded_msgs):
                            st.stored[event_uid][d] = fwded_msgs[i]
                        elif not isinstance(fwded_msgs, list):
                            st.stored[event_uid][d] = fwded_msgs
                    sent = True

                except FloodWaitError as fwe:
                    wait_sec = fwe.seconds
                    logging.warning(f"❌ FloodWait 触发！需等待 {wait_sec} 秒...（第 {retries+1}/{max_retries} 次尝试）")
                    await asyncio.sleep(wait_sec)
                    retries += 1

                except MediaInvalidError as mie:
                    logging.warning(f"❌ MediaInvalidError: {mie}，尝试逐条发送...")
                    # 降级：逐条发送
                    for single_msg in valid_messages:
                        single_tm = await apply_plugins(single_msg)
                        if not single_tm:
                            continue
                        try:
                            fwded_msg = await send_message(d, single_tm)
                            event_uid = st.EventUid(st.DummyEvent(chat_id, single_msg.id))
                            if event_uid not in st.stored:
                                st.stored[event_uid] = {}
                            st.stored[event_uid][d] = fwded_msg
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
                        logging.critical(f"❌ 达到最大重试次数，放弃向 {d} 发送媒体组 {grouped_id}")
                        success = False

            if not sent:
                logging.critical(f"❌ 所有重试失败，无法向 {d} 发送媒体组 {grouped_id}")

        # 清理资源
        for tm in tms:
            tm.clear()

    # 清除已发送缓存
    st.GROUPED_CACHE.pop(grouped_id, None)
    st.GROUPED_TIMERS.pop(grouped_id, None)
    st.GROUPED_MAPPING.pop(grouped_id, None)


async def new_message_handler(event: Union[Message, events.NewMessage]) -> None:
    """Process new incoming messages."""
    chat_id = event.chat_id
    if chat_id not in config.from_to:
        return

    logging.info(f"📩 新消息来自 {chat_id}")
    message = event.message

    # 媒体组处理
    if message.grouped_id is not None:
        st.add_to_group_cache(chat_id, message.grouped_id, message)
        return

    event_uid = st.EventUid(event)

    # 控制内存大小
    length = len(st.stored)
    exceeding = length - const.KEEP_LAST_MANY
    if exceeding > 0:
        for _ in range(exceeding // 2 + 1):
            try:
                del st.stored[next(iter(st.stored))]
            except StopIteration:
                break

    dest = config.from_to.get(chat_id)
    tm = await apply_plugins(message)
    if not tm:
        return

    st.stored[event_uid] = {}
    for d in dest:
        if event.is_reply:
            r_event = st.DummyEvent(chat_id, event.reply_to_msg_id)
            r_event_uid = st.EventUid(r_event)
            if r_event_uid in st.stored:
                tm.reply_to = st.stored.get(r_event_uid).get(d)

        try:
            fwded_msg = await send_message(d, tm)
            st.stored[event_uid][d] = fwded_msg
        except FloodWaitError as fwe:
            logging.warning(f"⚠️ FloodWait: 等待 {fwe.seconds} 秒...")
            await asyncio.sleep(fwe.seconds)
            # 可选：重试一次
            try:
                fwded_msg = await send_message(d, tm)
                st.stored[event_uid][d] = fwded_msg
            except Exception as e:
                logging.error(f"重试失败: {e}")
        except Exception as e:
            logging.error(f"发送消息失败: {e}")

    tm.clear()


# === edited 和 deleted handler 保持不变（已有基础处理）===

async def edited_message_handler(event) -> None:
    message = event.message
    chat_id = event.chat_id
    if chat_id not in config.from_to:
        return

    logging.info(f"📝 消息编辑于 {chat_id}")
    event_uid = st.EventUid(event)

    # 检查是否属于媒体组
    grouped_ids = st.get_grouped_messages(chat_id, message.id)
    if grouped_ids:
        for msg_id in grouped_ids:
            uid = st.EventUid(st.DummyEvent(chat_id, msg_id))
            fwded_msgs = st.stored.get(uid)
            if fwded_msgs:
                tm = await apply_plugins(message)
                if tm:
                    for _, fwded_msg in fwded_msgs.items():
                        if config.CONFIG.live.delete_on_edit == message.text:
                            await fwded_msg.delete()
                        else:
                            await fwded_msg.edit(tm.text)
                    tm.clear()
        return

    tm = await apply_plugins(message)
    if not tm:
        return

    fwded_msgs = st.stored.get(event_uid)
    if fwded_msgs:
        for _, msg in fwded_msgs.items():
            if config.CONFIG.live.delete_on_edit == message.text:
                await msg.delete()
                await message.delete()
            else:
                await msg.edit(tm.text)
        return

    dest = config.from_to.get(chat_id)
    for d in dest:
        await send_message(d, tm)
    tm.clear()


async def deleted_message_handler(event):
    chat_id = event.chat_id
    if chat_id not in config.from_to:
        return

    logging.info(f"🗑️ 消息删除于 {chat_id}")
    for msg_id in event.deleted_ids:
        grouped_ids = st.get_grouped_messages(chat_id, msg_id)
        if grouped_ids:
            for gid in grouped_ids:
                uid = st.EventUid(st.DummyEvent(chat_id, gid))
                fwded_msgs = st.stored.get(uid)
                if fwded_msgs:
                    for _, msg in fwded_msgs.items():
                        await msg.delete()
                    st.stored.pop(uid, None)
            return

        event_uid = st.EventUid(st.DummyEvent(chat_id, msg_id))
        fwded_msgs = st.stored.get(event_uid)
        if fwded_msgs:
            for _, msg in fwded_msgs.items():
                await msg.delete()
            st.stored.pop(event_uid, None)


ALL_EVENTS = {
    "new": (new_message_handler, events.NewMessage()),
    "edited": (edited_message_handler, events.MessageEdited()),
    "deleted": (deleted_message_handler, events.MessageDeleted()),
}


async def start_sync() -> None:
    """Start tgcf live sync."""
    clean_session_files()
    await load_async_plugins()

    SESSION = get_SESSION()
    client = TelegramClient(
        SESSION,
        CONFIG.login.API_ID,
        CONFIG.login.API_HASH,
        sequential_updates=CONFIG.live.sequential_updates,
    )

    if CONFIG.login.user_type == 0:
        if not CONFIG.login.BOT_TOKEN:
            logging.error("❌ Bot token 未设置！")
            return
        await client.start(bot_token=CONFIG.login.BOT_TOKEN)
    else:
        await client.start()

    config.is_bot = await client.is_bot()
    logging.info(f"🤖 is_bot = {config.is_bot}")

    command_events = get_events()
    ALL_EVENTS.update(command_events)

    await config.load_admins(client)
    config.from_to = await config.load_from_to(client, CONFIG.forwards)

    for key, val in ALL_EVENTS.items():
        if not CONFIG.live.delete_sync and key == "deleted":
            continue
        client.add_event_handler(*val)
        logging.info(f"✅ 注册事件处理器: {key}")

    if config.is_bot and const.REGISTER_COMMANDS:
        await client(
            functions.bots.SetBotCommandsRequest(
                scope=types.BotCommandScopeDefault(),
                lang_code="en",
                commands=[
                    types.BotCommand(command=key, description=value)
                    for key, value in const.COMMANDS.items()
                ],
            )
        )

    logging.info("🟢 live 模式启动完成，正在监听消息...")
    await client.run_until_disconnected()
