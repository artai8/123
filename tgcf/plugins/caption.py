# tgcf/plugins/caption.py —— 已修复版本

import logging

from tgcf.plugins import TgcfMessage, TgcfPlugin


class TgcfCaption(TgcfPlugin):
    id_ = "caption"

    def __init__(self, data) -> None:
        self.caption = data
        logging.info(f"加载标题插件: header='{data.header}', footer='{data.footer}'")

    def modify(self, tm: TgcfMessage) -> TgcfMessage:
        # 只有当消息有可见文本时才加头尾
        if tm.text and (self.caption.header or self.caption.footer):
            tm.text = f"{self.caption.header}{tm.text}{self.caption.footer}"
        elif not tm.text and (self.caption.header or self.caption.footer):
            # 即使原消息无文本，也可仅发送 header/footer
            tm.text = f"{self.caption.header}{self.caption.footer}"
        return tm
