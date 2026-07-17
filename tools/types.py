from dataclasses import dataclass
from pathlib import Path

@dataclass
class Image:
    data: bytes
    offset: int


def hex_integer(value: str) -> int:
    return int(value, 16)