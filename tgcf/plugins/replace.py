# tgcf/plugins/replace.py —— 已修复版本

import logging
from typing import Any, Dict, List

from pydantic import BaseModel

from tgcf.plugin_models import Replace
from tgcf.plugins import TgcfMessage, TgcfPlugin
from tgcf.utils import replace as utils_replace  # 我们复用增强版 replace 工具


class TgcfReplace(TgcfPlugin):
    id_ = "replace"

    def __init__(self, data):
        self.replace = data
        logging.info(f"加载替换规则: {data}")

    def modify(self, tm: TgcfMessage) -> TgcfMessage:
        msg_text: str = tm.raw_text  # ✅ 关键修复：始终从原始文本开始
        if not msg_text:
            return tm

        for original, new in self.replace.text.items():
            msg_text = utils_replace(original, new, msg_text, self.replace.regex)

        tm.text = msg_text
        return tm

    def modify_group(self, tms: List[TgcfMessage]) -> List[TgcfMessage]:
        for tm in tms:
            if tm.text:
                msg_text = tm.raw_text
                for original, new in self.replace.text.items():
                    msg_text = utils_replace(original, new, msg_text, self.replace.regex)
                tm.text = msg_text
        return tms
