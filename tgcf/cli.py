"""The module for running tgcf from the command line."""  
  
import argparse  
import asyncio  
import logging  
import sys  
from enum import Enum  
from pathlib import Path  
from typing import Dict  
  
from pydantic import BaseModel  
  
from tgcf.config import CONFIG, read_config  
from tgcf.utils import platform_info  
  
  
class Mode(Enum):  
    LIVE = "live"  
    PAST = "past"  
  
  
class Login(BaseModel):  
    """Blueprint for the login object."""  
  
    # pylint: disable=too-few-public-methods  
    API_ID: int  
    API_HASH: str  
    user_type: int = 1  # 0: bot, 1: user  
    BOT_TOKEN: str = ""  
    SESSION_STRING: str = ""  
  
  
def get_args() -> argparse.Namespace:  
    """Parse the command line arguments."""  
    parser = argparse.ArgumentParser(  
        prog="tgcf",  
        description="Telegram Chat Forwarder\nA powerful tool to forward telegram messages",  
        formatter_class=argparse.RawDescriptionHelpFormatter,  
        epilog=platform_info(),  
    )  
    parser.add_argument(  
        "-c",  
        "--config",  
        help="path to the config file",  
        type=Path,  
        default=Path("tgcf.config.json"),  
    )  
    parser.add_argument(  
        "-v",  
        "--verbose",  
        help="increase output verbosity",  
        action="store_true",  
    )  
    parser.add_argument(  
        "-V",  
        "--version",  
        action="version",  
        version="%(prog)s 1.1.8",  
    )  
    return parser.parse_args()  
  
  
def main() -> None:  
    """The main function."""  
    args = get_args()  
    CONFIG = read_config(args.config)  
      
    if args.verbose:  
        logging.basicConfig(level=logging.INFO)  
      
    mode = args.mode if hasattr(args, 'mode') else Mode.LIVE  
      
    if mode == Mode.PAST:  
        from tgcf.past import forward_job  # pylint: disable=import-outside-toplevel  
        asyncio.run(forward_job())  
    else:  
        from tgcf.live import start_sync  # pylint: disable=import-outside-toplevel  
        asyncio.run(start_sync())  
  
  
if __name__ == "__main__":  
    main()
