# tgcf/live.py —— 同步更新 media group 支持

import logging
import os
import sys
from typing import Union, List

from telethon import TelegramClient, events, functions, types
from telethon.sessions import StringSession
from telethon.tl.custom.message import Message
from telethon.errors import MediaInvalidError, FloodWaitError

from tgcf import config, const
from tgcf import storage as st
from tgcf.bot import get_events
from tgcf.config import CONFIG, get_SESSION
from tgcf.plugins import apply_plugins, apply_plugins_to_group, load_async_plugins
from tgcf.utils import clean_session_files, send_message


async def _send_grouped_messages(grouped_id: int, tms: List["TgcfMessage"]) -> None:
    if grouped_id not in st.GROUPED_CACHE:
        return

    chat_messages_map = st.GROUPED_CACHE[grouped_id]
    success = True

    for chat_id, messages in chat_messages_map.items():
        if chat_id not in config.from_to:
            continue

        dest = config.from_to.get(chat_id)
        tm_template = tms[0]

        for d in dest:
            sent = False
            retries = 0
            max_retries = 3

            while not sent and retries < max_retries:
                try:
                    if messages[0].is_reply:
                        r_event = st.DummyEvent(chat_id, messages[0].reply_to_msg_id)
                        r_event_uid = st.EventUid(r_event)
                        if r_event_uid in st.stored:
                            tm_template.reply_to = st.stored.get(r_event_uid).get(d)

                    fwded_msgs = await send_message(
                        d,
                        tm_template,
                        grouped_messages=[tm.message for tm in tms],
                        grouped_tms=tms
                    )

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
                    logging.warning(f"⛔ FloodWait: 等待 {fwe.seconds}s")
                    await asyncio.sleep(fwe.seconds)
                    retries += 1
                except Exception as e:
                    logging.error(f"❌ 发送失败: {e}")
                    retries += 1
                    await asyncio.sleep(2 ** retries + random.uniform(0, 5))

            if not sent:
                logging.critical(f"❌ 放弃向 {d} 发送媒体组 {grouped_id}")

        for tm in tms:
            tm.clear()

    st.GROUPED_CACHE.pop(grouped_id, None)
    st.GROUPED_TIMERS.pop(grouped_id, None)
    st.GROUPED_MAPPING.pop(grouped_id, None)


async def new_message_handler(event: Union[Message, events.NewMessage]) -> None:
    chat_id = event.chat_id
    if chat_id not in config.from_to:
        return

    logging.info(f"📩 新消息来自 {chat_id}")
    message = event.message

    if message.grouped_id is not None:
        st.add_to_group_cache(chat_id, message.grouped_id, message)
        return

    event_uid = st.EventUid(event)
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
                tm.reply_to = st.stored[r_event_uid].get(d)

        try:
            fwded_msg = await send_message(d, tm)
            st.stored[event_uid][d] = fwded_msg
        except FloodWaitError as fwe:
            logging.warning(f"等待 {fwe.seconds}s")
            await asyncio.sleep(fwe.seconds)
            try:
                fwded_msg = await send_message(d, tm)
                st.stored[event_uid][d] = fwded_msg
            except Exception as e:
                logging.error(f"重试失败: {e}")
        except Exception as e:
            logging.error(f"发送失败: {e}")

    tm.clear()


async def edited_message_handler(event) -> None:
    message = event.message
    chat_id = event.chat_id
    if chat_id not in config.from_to:
        return

    event_uid = st.EventUid(event)
    grouped_ids = st.get_grouped_messages(chat_id, message.id)
    if grouped_ids:
        for msg_id in grouped_ids:
            uid = st.EventUid(st.DummyEvent(chat_id, msg_id))
            fwded_msgs = st.stored.get(uid)
            if fwded_msgs:
                tm = await apply_plugins(message)
                if tm:
                    for _, fwded_msg in fwded_msgs.items():
                        if CONFIG.live.delete_on_edit == message.text:
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
            if CONFIG.live.delete_on_edit == message.text:
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
