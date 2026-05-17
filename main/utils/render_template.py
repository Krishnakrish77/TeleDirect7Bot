import logging
import urllib.parse
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from main.vars import Var
from main.bot import StreamBot
from main.utils.human_readable import humanbytes
from main.utils.file_properties import get_file_ids
from main.server.exceptions import InvalidHash


TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "template"
_env = Environment(
    loader=FileSystemLoader(str(TEMPLATE_DIR)),
    autoescape=select_autoescape(["html"]),
    enable_async=False,
)
_REQ_TEMPLATE = _env.get_template("req.html")
_DL_TEMPLATE = _env.get_template("dl.html")


async def render_page(message_id, secure_hash):
    file_data = await get_file_ids(StreamBot, int(Var.BIN_CHANNEL), int(message_id))
    if file_data.unique_id[:6] != secure_hash:
        logging.debug(f'link hash: {secure_hash} - {file_data.unique_id[:6]}')
        logging.debug(f"Invalid hash for message with - ID {message_id}")
        raise InvalidHash
    src = urllib.parse.urljoin(Var.URL, f'{secure_hash}{message_id}')
    mime_type = (file_data.mime_type or "").split('/')[0].strip()
    if mime_type in ("video", "audio"):
        heading = ("Watch " if mime_type == "video" else "Listen ") + (file_data.file_name or "")
        return _REQ_TEMPLATE.render(
            tag=mime_type,
            heading=heading,
            src=src,
            file_name=file_data.file_name,
        )
    heading = f"Download {file_data.file_name}"
    return _DL_TEMPLATE.render(
        heading=heading,
        file_name=file_data.file_name,
        file_size=humanbytes(file_data.file_size),
        src=src,
    )
