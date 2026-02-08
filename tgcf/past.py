# tgcf/past.py —— 已修复：延迟 + 重复 header/footer + 静默丢失

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
    """发送媒体组，返回是否成功"""
    try:
        tms = await apply_plugins_to_group(messages)
        if not tms:
            # 尝试用第一条消息做 fallback
            fallback_tm = await apply_plugins(messages[0])
            if not fallback_tm:
                logging.warning("⚠️ 媒体组及 fallback 消息均被过滤 → 跳过")
                return True  # 被有意过滤，视为“成功跳过”
            tms = [fallback_tm]

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
                logging.error(f"❌ 组播失败到目标 {d}: {e}")
                return False  # 明确失败

        return True

    except Exception as e:
        logging.error(f"🚨 发送媒体组时发生未预期错误: {e}")
        return False


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

                success = False  # 是否成功处理这条消息
                should_delay = True  # 是否需要额外延迟（避免双重 sleep）

                try:
                    # === 处理媒体组缓存 ===
                    if message.grouped_id is not None:
                        grouped_buffer[message.grouped_id].append(message)
                        continue
                    else:
                        # 先发送所有缓存的 media group
                        all_groups_sent = True
                        for gid, msgs in list(grouped_buffer.items()):
                            if not await _send_past_grouped(client, src, dest, msgs):
                                all_groups_sent = False
                        grouped_buffer.clear()

                        if not all_groups_sent:
                            raise Exception("One or more media groups failed to send")

                    # === 处理单条消息 ===
                    tm = await apply_plugins(message)
                    if not tm:
                        logging.info(f"🟡 消息被插件系统过滤 [chat={src}, msg={message.id}]")
                        success = True  # 视为已处理，避免无限重试
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
                            logging.error(f"❌ 单条转发失败到 {d}: {e}")
                            sent_all = False

                    if sent_all:
                        success = True
                    else:
                        logging.warning(f"🟡 部分目标发送失败 [msg_id={message.id}]")

                    tm.clear()

                except FloodWaitError as fwe:
                    logging.critical(f"⛔ FloodWait 触发！必须等待 {fwe.seconds} 秒...")
                    await asyncio.sleep(fwe.seconds + 10)
                    should_delay = False  # 不再额外延迟
                except Exception as err:
                    logging.exception(f"💥 消息处理失败 [msg_id={message.id}]: {err}")

                finally:
                    # ✅ 无论成败都延迟（除非刚等过 FloodWait）
                    if should_delay:
                        delay_seconds = random.randint(60, 300)
                        logging.info(f"⏸️ 休息 {delay_seconds} 秒")
                        await asyncio.sleep(delay_seconds)

                # ✅ 只有在成功或明确过滤时才更新 offset
                if success:
                    last_id = message.id
                    forward.offset = last_id
                    write_config(CONFIG, persist=False)

            # === 清理最后残留的 media group ===
            for gid, msgs in grouped_buffer.items():
                await _send_past_grouped(client, src, dest, msgs)

            # === 清理全局缓存，防止跨任务污染 ===
            for gid in list(st.GROUPED_CACHE.keys()):
                st.GROUPED_CACHE.pop(gid, None)
                st.GROUPED_TIMERS.pop(gid, None)
                st.GROUPED_MAPPING.pop(gid, None)
