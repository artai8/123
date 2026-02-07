import logging  
from typing import Any, Dict, List  
  
from pydantic import BaseModel  
  
from tgcf.plugin_models import Replace  
from tgcf.plugins import TgcfMessage, TgcfPlugin  
from tgcf.utils import replace  
  
  
class TgcfReplace(TgcfPlugin):  
    id_ = "replace"  
  
    def __init__(self, data):  
        self.replace = data  
        logging.info(self.replace)  
  
    def modify(self, tm: TgcfMessage) -> TgcfMessage:  
        msg_text: str = tm.text  
        if not msg_text:  
            return tm  
        for original, new in self.replace.text.items():  
            msg_text = replace(original, new, msg_text, self.replace.regex)  
        tm.text = msg_text  
        return tm  
  
    def modify_group(self, tms: List[TgcfMessage]) -> List[TgcfMessage]:  
        """Apply replace to each message in the group."""  
        for tm in tms:  
            msg_text: str = tm.text  
            if msg_text:  
                for original, new in self.replace.text.items():  
                    msg_text = replace(original, new, msg_text, self.replace.regex)  
                tm.text = msg_text  
        return tms
