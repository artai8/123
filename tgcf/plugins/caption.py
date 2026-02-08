# tgcf/plugins/caption.py —— 已修复：防重复 header/footer（简化版）

import logging

from tgcf.plugins import TgcfMessage, TgcfPlugin


class TgcfCaption(TgcfPlugin):
    id_ = "caption"

    def __init__(self, data) -> None:
        self.caption = data
        self._header = data.header.strip() if data.header else ""
        self._footer = data.footer.strip() if data.footer else ""
        logging.info(f"📝 加载标题插件: header='{self._header}', footer='{self._footer}'")

    def modify(self, tm: TgcfMessage) -> TgcfMessage:
        original_text = tm.text or ""

        # ✅ 简单去重：检查开头/结尾是否已存在
        has_content = bool(original_text.strip())
        final_text = original_text

        # 添加 header
        if self._header and not final_text.startswith(self._header):
            sep = "\n\n" if has_content else ""
            final_text = self._header + sep + final_text

        # 添加 footer
        if self._footer and not final_text.endswith(self._footer):
            sep = "\n\n" if has_content else ""
            final_text += sep + self._footer

        tm.text = final_text
        return tm
