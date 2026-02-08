# tgcf/past.py —— 已修复视频丢失 + 延迟无效问题

import asyncio
import logging
import random
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


async def _send_past_grouped(
    client: TelegramClient, src: int, dest: List[int], messages: List[Message]
) -> bool:
    if not messages:
        return True

    tms = await apply_plugins_to_group(messages)
    if not tms:
        logging.info("所有消息被插件过滤，跳过该组。")
        return True

    valid_tms = []
    for tm in tms:
        msg = tm.message
        if getattr(msg, "photo", None) or getattr(msg, "video", None) or getattr(msg, "document", None):
            valid_tms.append(tm)
        else:
            logging.debug(f"消息 {msg.id} 类型不受支持，已跳过。")

    if not valid_tms:
        logging.warning("无有效媒体内容。")
        for tm in tms:
            tm.clear()
        return True

    tm_template = valid_tms[0]

    for d in dest:
        sent = False
        retries = 0
        max_retries = 3

        while not sent and retries < max_retries:
            try:
                if valid_tms[0].message.is_reply:
                    r_event = st.DummyEvent(valid_tms[0].message.chat_id, valid_tms[0].message.reply_to_msg_id)
                    r_event_uid = st.EventUid(r_event)
                    if r_event_uid in st.stored:
                        tm_template.reply_to = st.stored.get(r_event_uid).get(d)

                # ✅ 使用 grouped_tms 参数传递处理后的文本
                fwded_msgs = await send_message(
                    d,
                    tm_template,
                    grouped_messages=[tm.message for tm in valid_tms],
                    grouped_tms=valid_tms
                )

                first_msg_id = valid_tms[0].message.id
                event_uid = st.EventUid(st.DummyEvent(src, first_msg_id))
                st.stored[event_uid] = {d: fwded_msgs}
                logging.info(f"✅ 成功发送媒体组至 {d}")

                sent = True

            except FloodWaitError as fwe:
                wait_sec = fwe.seconds
                logging.warning(f"⛔ FloodWait: 等待 {wait_sec} 秒...")
                await asyncio.sleep(wait_sec)
                retries += 1

            except MediaInvalidError as mie:
                logging.warning(f"⚠️ MediaInvalidError: {mie}，尝试逐条发送...")
                for single_tm in valid_tms:
                    try:
                        await send_message(d, single_tm)
                    except Exception as e:
                        logging.error(f"❌ 单条发送失败: {e}")
                    finally:
                        single_tm.clear()
                sent = True

            except Exception as e:
                logging.error(f"❌ 发送媒体组失败: {e}")
                retries += 1
                if retries < max_retries:
                    backoff = 2 ** retries + random.uniform(0, 5)
                    await asyncio.sleep(backoff)

        if not sent:
            logging.critical(f"❌ 达到最大重试次数，放弃向 {d} 发送媒体组")

    for tm in tms:
        tm.clear()

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
            processed_groups = set()

            async for message in client.iter_messages(src, reverse=True, offset_id=forward.offset):
                if isinstance(message, MessageService):
                    continue

                if forward.end and last_id > forward.end:
                    continue

                try:
                    if message.grouped_id is not None:
                        if message.grouped_id not in processed_groups:
                            grouped_buffer[message.grouped_id].append(message)
                            continue
                        else:
                            continue
                    else:
                        for gid, msgs in list(grouped_buffer.items()):
                            if gid not in processed_groups:
                                await _send_past_grouped(client, src, dest, msgs)
                                processed_groups.add(gid)
                        grouped_buffer.clear()

                    tm = await apply_plugins(message)
                    if not tm:
                        continue

                    event_uid = st.EventUid(st.DummyEvent(message.chat_id, message.id))
                    st.stored[event_uid] = {}

                    if message.is_reply:
                        r_event = st.DummyEvent(message.chat_id, message.reply_to_msg_id)
                        r_event_uid = st.EventUid(r_event)
                        if r_event_uid in st.stored:
                            tm.reply_to = st.stored[r_event_uid].get(dest[0])

                    for d in dest:
                        try:
                            fwded_msg = await send_message(d, tm)
                            st.stored[event_uid][d] = fwded_msg.id
                        except FloodWaitError as fwe:
                            logging.warning(f"等待 {fwe.seconds}s")
                            await asyncio.sleep(fwe.seconds)
                            try:
                                fwded_msg = await send_message(d, tm)
                                st.stored[event_uid][d] = fwded_msg.id
                            except Exception as e:
                                logging.error(f"重试失败: {e}")
                        except Exception as e:
                            logging.error(f"发送失败: {e}")

                    tm.clear()
                    last_id = message.id
                    forward.offset = last_id
                    write_config(CONFIG, persist=False)

                    # ✅ 正确的随机延迟：60~300 秒
                    delay_seconds = random.randint(60, 300)
                    logging.info(f"⏸️ 休息 {delay_seconds} 秒以避免触发限流...")
                    await asyncio.sleep(delay_seconds)

                except FloodWaitError as fwe:
                    logging.warning(f"全局等待 {fwe.seconds}s")
                    await asyncio.sleep(fwe.seconds)
                except Exception as err:
                    logging.exception(err)

            for gid, msgs in grouped_buffer.items():
                if gid not in processed_groups:
                    await _send_past_grouped(client, src, dest, msgs)
