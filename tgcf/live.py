"""The module responsible for operating tgcf in live mode."""

import logging
import os
import sys
from typing import Union, List

from telethon import TelegramClient, events, functions, types
from telethon.sessions import StringSession
from telethon.tl.custom.message import Message
from telethon.errors import MediaInvalidError

from tgcf import config, const
from tgcf import storage as st
from tgcf.bot import get_events
from tgcf.config import CONFIG, get_SESSION
from tgcf.plugins import apply_plugins, apply_plugins_to_group, load_async_plugins
from tgcf.utils import clean_session_files, send_message


async def _send_grouped_messages(grouped_id: int) -> None:
    """发送缓存中的媒体组到所有目标，对每条消息应用插件"""
    if grouped_id not in st.GROUPED_CACHE:
        return

    chat_messages_map = st.GROUPED_CACHE[grouped_id]  # {chat_id: [messages]}

    for chat_id, messages in chat_messages_map.items():
        if chat_id not in config.from_to:
            continue

        dest = config.from_to.get(chat_id)

        # ✅ Step 1: 对整组消息应用插件
        tms = await apply_plugins_to_group(messages)
        if not tms:
            logging.info(f"All messages in group {grouped_id} filtered out by plugins.")
            continue

        # ✅ Step 2: 提取有效消息（仅 photo/video）
        valid_messages = []
        for tm in tms:
            msg = tm.message
            if getattr(msg, "photo", None) or getattr(msg, "video", None):
                valid_messages.append(msg)
            else:
                logging.debug(f"Message {msg.id} skipped in live group: unsupported media type.")

        if not valid_messages:
            logging.warning(f"No valid media in group {grouped_id} after filtering.")
            for tm in tms:
                tm.clear()
            continue

        for d in dest:
            # 处理回复关系（使用第一条消息的 reply_to）
            reply_to = None
            if messages[0].is_reply:
                r_event = st.DummyEvent(chat_id, messages[0].reply_to_msg_id)
                r_event_uid = st.EventUid(r_event)
                if r_event_uid in st.stored:
                    reply_to = st.stored.get(r_event_uid).get(d)

            try:
                # 使用第一条消息的 TgcfMessage 作为模板（用于 text/reply_to）
                tm_template = tms[0]
                tm_template.reply_to = reply_to

                # 尝试发送媒体组
                fwded_msgs = await send_message(d, tm_template, grouped_messages=valid_messages)
                logging.info(f"Sent media group {grouped_id} with {len(valid_messages)} items to {d}")

                # 存储映射：每条原始消息 <-> 转发后的消息
                for i, original_msg in enumerate(messages):
                    event_uid = st.EventUid(st.DummyEvent(chat_id, original_msg.id))
                    if event_uid not in st.stored:
                        st.stored[event_uid] = {}
                    if isinstance(fwded_msgs, list) and i < len(fwded_msgs):
                        st.stored[event_uid].update({d: fwded_msgs[i]})
                    elif not isinstance(fwded_msgs, list):
                        st.stored[event_uid].update({d: fwded_msgs})

            except MediaInvalidError as mie:
                logging.warning(f"MediaInvalidError in group {grouped_id}: {mie}. Falling back to individual send.")
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
                        st.stored[event_uid].update({d: fwded_msg})
                    except Exception as e:
                        logging.error(f"Failed to send individual message {single_msg.id}: {e}")
                    finally:
                        single_tm.clear()

            except Exception as e:
                logging.error(f"Unexpected error sending group {grouped_id} to {d}: {e}")

        # 清理插件临时文件
        for tm in tms:
            tm.clear()

    # 清除已发送的缓存
    st.GROUPED_CACHE.pop(grouped_id, None)
    st.GROUPED_TIMERS.pop(grouped_id, None)
    st.GROUPED_MAPPING.pop(grouped_id, None)


async def new_message_handler(event: Union[Message, events.NewMessage]) -> None:
    """Process new incoming messages."""
    chat_id = event.chat_id

    if chat_id not in config.from_to:
        return
    logging.info(f"New message received in {chat_id}")
    message = event.message

    # 媒体组处理：若存在 grouped_id，暂存并等待
    if message.grouped_id is not None:
        st.add_to_group_cache(chat_id, message.grouped_id, message)
        return

    event_uid = st.EventUid(event)

    length = len(st.stored)
    exceeding = length - const.KEEP_LAST_MANY

    if exceeding > 0:
        for key in st.stored:
            del st.stored[key]
            break

    dest = config.from_to.get(chat_id)

    tm = await apply_plugins(message)
    if not tm:
        return

    if event.is_reply:
        r_event = st.DummyEvent(chat_id, event.reply_to_msg_id)
        r_event_uid = st.EventUid(r_event)

    st.stored[event_uid] = {}
    for d in dest:
        if event.is_reply and r_event_uid in st.stored:
            tm.reply_to = st.stored.get(r_event_uid).get(d)
        fwded_msg = await send_message(d, tm)
        st.stored[event_uid].update({d: fwded_msg})
    tm.clear()


async def edited_message_handler(event) -> None:
    """Handle message edits."""
    message = event.message
    chat_id = event.chat_id

    if chat_id not in config.from_to:
        return

    logging.info(f"Message edited in {chat_id}")
    event_uid = st.EventUid(event)

    # 检查是否是媒体组中的消息
    grouped_ids = st.get_grouped_messages(chat_id, message.id)
    if grouped_ids:
        # 处理媒体组编辑
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

    # 原有单消息编辑逻辑
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
    """Handle message deletes."""
    chat_id = event.chat_id
    if chat_id not in config.from_to:
        return

    logging.info(f"Message deleted in {chat_id}")

    # 检查是否是媒体组中的消息
    for msg_id in event.deleted_ids:
        grouped_ids = st.get_grouped_messages(chat_id, msg_id)
        if grouped_ids:
            # 删除整个媒体组
            for gid in grouped_ids:
                uid = st.EventUid(st.DummyEvent(chat_id, gid))
                fwded_msgs = st.stored.get(uid)
                if fwded_msgs:
                    for _, msg in fwded_msgs.items():
                        await msg.delete()
                    st.stored.pop(uid, None)
            return

    # 原有单消息删除逻辑
    for msg_id in event.deleted_ids:
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
    # clear past session files
    clean_session_files()

    # load async plugins defined in plugin_models
    await load_async_plugins()

    SESSION = get_SESSION()
    client = TelegramClient(
        SESSION,
        CONFIG.login.API_ID,
        CONFIG.login.API_HASH,
        sequential_updates=CONFIG.live.sequential_updates,
    )
    if CONFIG.login.user_type == 0:
        if CONFIG.login.BOT_TOKEN == "":
            logging.warning("Bot token not found, but login type is set to bot.")
            sys.exit()
        await client.start(bot_token=CONFIG.login.BOT_TOKEN)
    else:
        await client.start()
    config.is_bot = await client.is_bot()
    logging.info(f"config.is_bot={config.is_bot}")
    command_events = get_events()

    await config.load_admins(client)

    ALL_EVENTS.update(command_events)

    for key, val in ALL_EVENTS.items():
        if config.CONFIG.live.delete_sync is False and key == "deleted":
            continue
        client.add_event_handler(*val)
        logging.info(f"Added event handler for {key}")

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
    config.from_to = await config.load_from_to(client, config.CONFIG.forwards)
    await client.run_until_disconnected()
