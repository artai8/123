# tgcf/plugins/replace.py —— 已修复版本

import logging
from typing import Any, Dict, List

from tgcf.plugins import TgcfMessage, TgcfPlugin


class TgcfReplace(TgcfPlugin):
    id_ = "replace"

    def __init__(self, data):
        self.replace = data
        logging.info(f"🔧 加载替换规则: {data.text}")

    def modify(self, tm: TgcfMessage) -> TgcfMessage:
        raw_text = tm.raw_text  # ✅ 关键：始终从原始文本开始
        if not raw_text:
            return tm

        for original, new in self.replace.text.items():
            raw_text = replace(original, new, raw_text, self.replace.regex)  # 使用增强版 replace

        tm.text = raw_text
        return tm

    def modify_group(self, tms: List[TgcfMessage]) -> List[TgcfMessage]:
        for tm in tms:
            if tm.raw_text:
                text = tm.raw_text
                for original, new in self.replace.text.items():
                    text = replace(original, new, text, self.replace.regex)
                tm.text = text
        return tms
