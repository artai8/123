# tgcf/plugins/caption.py —— 已修复：防重复 header/footer

import logging

from tgcf.plugins import TgcfMessage, TgcfPlugin


class TgcfCaption(TgcfPlugin):
    id_ = "caption"

    def __init__(self, data) -> None:
        self.caption = data
        self._header = data.header.strip()
        self._footer = data.footer.strip()
        logging.info(f"📝 加载标题插件: header='{self._header}', footer='{self._footer}'")

        # ✅ 创建唯一标签用于识别是否已处理
        self._tag = f"__CAPTION_ADDED_{hash(self._header + self._footer)}__"

    def modify(self, tm: TgcfMessage) -> TgcfMessage:
        original_text = tm.text or ""

        # ✅ 安全防护：防止重复处理
        if hasattr(tm, "_processed_tags") and self._tag in tm._processed_tags:
            logging.debug("⚠️ 检测到重复处理，跳过 caption 插件")
            return tm

        # ✅ 初始化标签集
        if not hasattr(tm, "_processed_tags"):
            tm._processed_tags = set()

        has_content = bool(original_text.strip())
        has_header = bool(self._header)
        has_footer = bool(self._footer)

        if not has_header and not has_footer:
            return tm

        final_text = original_text

        # ✅ 添加 header（仅当开头不是该 header）
        if has_header:
            stripped_final = final_text.lstrip()
            if not stripped_final.startswith(self._header):
                sep = "\n\n" if has_content else ""
                final_text = self._header + sep + final_text

        # ✅ 添加 footer（仅当结尾不是该 footer）
        if has_footer:
            stripped_final = final_text.rstrip()
            if not stripped_final.endswith(self._footer):
                sep = "\n\n" if has_content else ""
                final_text += sep + self._footer

        tm.text = final_text
        tm._processed_tags.add(self._tag)  # ✅ 标记为已处理
        return tm
