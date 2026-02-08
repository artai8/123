# tgcf/plugins/caption.py —— 已修复版本

import logging

from tgcf.plugins import TgcfMessage, TgcfPlugin


class TgcfCaption(TgcfPlugin):
    id_ = "caption"

    def __init__(self, data) -> None:
        self.caption = data
        logging.info(f"📝 加载标题插件: '{data.header}' + '{data.footer}'")

    def modify(self, tm: TgcfMessage) -> TgcfMessage:
        current_text = tm.text or ""

        has_content = bool(current_text.strip())
        has_header = bool(self.caption.header.strip())
        has_footer = bool(self.caption.footer.strip())

        if has_header or has_footer:
            if has_content:
                tm.text = f"{self.caption.header}{current_text}{self.caption.footer}"
            else:
                tm.text = f"{self.caption.header}{self.caption.footer}"

        return tm
