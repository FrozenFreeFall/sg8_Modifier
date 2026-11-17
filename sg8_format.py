"""Binary framing for Kingdom Heroes 8 campaign saves."""

from __future__ import annotations

from dataclasses import dataclass


class SaveFormatError(ValueError):
    """A save cannot be decoded or represented by this codec."""


@dataclass(frozen=True)
class Float32:
    """Original IEEE 754 bytes, including any NaN payload."""

    raw: bytes

    def __post_init__(self) -> None:
        if type(self.raw) is not bytes or len(self.raw) != 4:
            raise SaveFormatError("Float32 requires exactly four bytes")


Token = int | bytes | bool | Float32

_BYTES = 0x01
_FALSE = 0x10
_TRUE = 0x11
_INT32 = 0x22
_FLOAT32 = 0x30
_END = 0xFF


class _Reader:
    def __init__(self, data: bytes, offset: int = 0):
        self.data = data
        self.offset = 0
        self.origin = offset

    @property
    def position(self) -> int:
        return self.origin + self.offset

    def finished(self) -> bool:
        return self.offset == len(self.data)

    def read(self, size: int) -> bytes:
        end = self.offset + size
        if end > len(self.data):
            raise SaveFormatError(f"Truncated value at byte {self.position}")
        result = self.data[self.offset:end]
        self.offset = end
        return result

    def expect(self, tag: int) -> None:
        position = self.position
        if self.read(1)[0] != tag:
            raise SaveFormatError(f"Expected tag 0x{tag:02x} at byte {position}")

    def read_token(self) -> Token:
        position = self.position
        tag = self.read(1)[0]
        if tag == _BYTES:
            size = int.from_bytes(self.read(4), "little")
            token = self.read(size)
        elif tag == _INT32:
            token = int.from_bytes(self.read(4), "little", signed=True)
        elif tag == _FLOAT32:
            token = Float32(self.read(4))
        elif tag in (_FALSE, _TRUE):
            token = tag == _TRUE
        else:
            raise SaveFormatError(f"Unknown tag 0x{tag:02x} at byte {position}")
        self.expect(_END)
        return token


def _encode_int(value: int, *, signed: bool) -> bytes:
    try:
        return value.to_bytes(4, "little", signed=signed)
    except OverflowError as error:
        raise SaveFormatError(f"Value {value} does not fit in four bytes") from error


def _encode_token(token: Token) -> bytes:
    if type(token) is bytes:
        tag, body = _BYTES, _encode_int(len(token), signed=False) + token
    elif type(token) is int:
        tag, body = _INT32, _encode_int(token, signed=True)
    elif type(token) is bool:
        tag, body = (_TRUE if token else _FALSE), b""
    elif isinstance(token, Float32):
        tag, body = _FLOAT32, token.raw
    else:
        raise SaveFormatError(f"Unsupported token type {type(token).__name__}")
    return bytes((tag,)) + body + bytes((_END,))


def _section_name(token: Token) -> str:
    if type(token) is not bytes or not token:
        raise SaveFormatError("Section must begin with its name")
    try:
        return token.decode("ascii")
    except UnicodeDecodeError as error:
        raise SaveFormatError("Section name must be ASCII") from error


@dataclass
class SaveFile:
    # Dict insertion order is the original section order. Token 0 is the section name.
    sections: dict[str, list[Token]]

    @classmethod
    def parse(cls, data: bytes) -> SaveFile:
        if not data:
            raise SaveFormatError("Save is empty")

        reader = _Reader(data)
        sections = {}
        while not reader.finished():
            reader.expect(_BYTES)
            size = int.from_bytes(reader.read(4), "little")
            start = reader.position
            payload = _Reader(reader.read(size), start)
            reader.expect(_END)

            first_token = payload.read_token()
            name = _section_name(first_token)
            if name in sections:
                raise SaveFormatError(f"Duplicate section {name!r}")
            tokens = [first_token]
            while not payload.finished():
                tokens.append(payload.read_token())
            sections[name] = tokens
        return cls(sections)

    def to_bytes(self) -> bytes:
        if not self.sections:
            raise SaveFormatError("Save has no sections")

        return b"".join(self._encode_section(name, tokens) for name, tokens in self.sections.items())

    @staticmethod
    def _encode_section(name: str, tokens: list[Token]) -> bytes:
        if not tokens or _section_name(tokens[0]) != name:
            raise SaveFormatError(f"Section {name!r} must begin with its name")
        payload = b"".join(_encode_token(token) for token in tokens)
        return _encode_token(payload)
