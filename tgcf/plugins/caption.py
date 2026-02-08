# tgcf/plugins/caption.py —— 修复 header/footer 不生效

import logging

from tgcf.plugins import TgcfMessage, TgcfPlugin


class TgcfCaption(TgcfPlugin):
    id_ = "caption"

    def __init__(self, data) -> None:
        self.caption = data
        logging.info(f"📝 加载标题插件: header='{data.header}', footer='{data.footer}'")

    def modify(self, tm: TgcfMessage) -> TgcfMessage:
        original_text = tm.text or ""
        has_content = bool(original_text.strip())
        has_header = bool(self.caption.header.strip())
        has_footer = bool(self.caption.footer.strip())

        if not has_header and not has_footer:
            return tm

        if has_content:
            tm.text = f"{self.caption.header}{original_text}{self.caption.footer}"
        else:
            tm.text = f"{self.caption.header}{self.caption.footer}"

        return tm
