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
from telethon.errors.rpcerrorlist import FloodWaitError  
from telethon.tl.custom.message import Message  
from telethon.tl.patched import MessageService  
  
from tgcf import config  
from tgcf import storage as st  
from tgcf.config import CONFIG, get_SESSION, write_config  
from tgcf.plugins import apply_plugins, load_async_plugins  
from tgcf.utils import clean_session_files, send_message  
  
  
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
            # 用于聚合媒体组：grouped_id -> List[Message]  
            grouped_buffer: Dict[int, List[Message]] = defaultdict(list)  
            # 记录已处理的 grouped_id，避免重复发送  
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
                    # 媒体组处理：收集同组消息  
                    if message.grouped_id is not None:  
                        if message.grouped_id not in processed_groups:  
                            grouped_buffer[message.grouped_id].append(message)  
                            # 简单启发式：若下一条消息不是同组，则发送当前组  
                            # 注意：此处在反向遍历中，组内消息会连续出现  
                            continue  
                        else:  
                            # 已处理过该组，跳过  
                            continue  
                    else:  
                        # 非媒体组，先发送之前缓存的组（若有）  
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
  
            # 处理剩余的媒体组  
            for gid, msgs in grouped  
