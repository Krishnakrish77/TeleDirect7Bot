from os import environ
from typing import Dict


class TokenParser:
    def __init__(self):
        self.tokens: Dict[int, str] = {}

    def parse_from_env(self) -> Dict[int, str]:
        self.tokens = {
            c + 1: t
            for c, (_, t) in enumerate(
                sorted((k, v) for k, v in environ.items() if k.startswith("MULTI_TOKEN"))
            )
        }
        return self.tokens
