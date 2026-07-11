"""
Core read/write/validation engine for Midtronics/Celltron .lib battery
library files. No GUI dependencies -- safe to import from a CLI, a test
suite, or a Tkinter front end.

Reused as-is from the sibling CellLibrarian project. CellStringer only
reads libraries (parse_lib) to power manufacturer/model autocomplete and
reference-conductance autofill in the String dialog -- it does not write
.lib files itself.

File format summary:

    Header (6 bytes):
        0x00  u8       magic[0] = 0x24 ('$')
        0x01  u8       magic[1] = 0x24 ('$')
        0x02  u16 BE   manufacturer count (A)
        0x04  u16 BE   model count (B)

    Manufacturer record (20 bytes), repeated A times:
        0x00  char[20] name, ASCII, null-padded

    Model record (62 bytes), repeated B times:
        0x00  char[20] name, ASCII, null-padded
        0x14  u16 LE   reference conductance
        0x16  u16 LE   manufacturer index (0-based, into manufacturer table)
        0x18  char[36] GUID, canonical 8-4-4-4-12 uppercase ASCII UUID
        0x3C  u8       terminator, always 0x00
        0x3D  u8       battery type (see BATTERY_TYPES)

    Total file size must equal exactly 6 + 20*A + 62*B.
"""

from __future__ import annotations

import re
import struct
import uuid
from dataclasses import dataclass, field

MAGIC = b"\x24\x24"

MFR_RECORD_SIZE = 20
MODEL_RECORD_SIZE = 62
HEADER_SIZE = 6

BATTERY_TYPES = [
    "DIN",
    "VLA-FLOODED/WET",
    "VLA-LEAD CALCIUM",
    "VLA-LEAD ANTIMONY",
    "VRLA",
    "NICAD",
    "LITHIUM",
]

GUID_RE = re.compile(
    r"^[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}$"
)


class LibFormatError(ValueError):
    """Raised for anything that would make the device reject the file."""


def new_guid() -> str:
    return str(uuid.uuid4()).upper()


@dataclass
class Manufacturer:
    name: str


@dataclass
class Model:
    name: str
    conductance: int
    manufacturer: str  # manufacturer name; resolved to an index at build time
    battery_type: int
    guid: str = field(default_factory=new_guid)


def _pad_ascii(s: str, size: int, field_name: str) -> bytes:
    # The tester only ever displays uppercase, so names are normalized here
    # at the format layer -- guaranteed regardless of how data got in
    # (GUI, script, or a loaded file with mixed case).
    s = s.upper()
    try:
        b = s.encode("ascii")
    except UnicodeEncodeError as e:
        raise LibFormatError(
            f"{field_name} '{s}' contains non-ASCII characters"
        ) from e
    if len(b) > size - 1:
        raise LibFormatError(
            f"{field_name} '{s}' is too long ({len(b)} chars, max {size - 1})"
        )
    return b.ljust(size, b"\x00")


def build_manufacturer_record(name: str) -> bytes:
    return _pad_ascii(name, MFR_RECORD_SIZE, "Manufacturer name")


def build_model_record(model: Model, mfr_index: int) -> bytes:
    if not (0 <= model.conductance <= 0xFFFF):
        raise LibFormatError(
            f"Model '{model.name}': conductance {model.conductance} "
            f"out of range (0-65535)"
        )
    if not (0 <= mfr_index <= 0xFFFF):
        raise LibFormatError(f"Model '{model.name}': manufacturer index out of range")
    if not (0 <= model.battery_type < len(BATTERY_TYPES)):
        raise LibFormatError(
            f"Model '{model.name}': invalid battery type {model.battery_type}"
        )
    guid = model.guid.upper()
    if not GUID_RE.match(guid):
        raise LibFormatError(
            f"Model '{model.name}': GUID '{guid}' is not a valid canonical UUID"
        )

    b = bytearray(MODEL_RECORD_SIZE)
    b[0:20] = _pad_ascii(model.name, 20, "Model name")
    struct.pack_into("<H", b, 0x14, model.conductance)
    struct.pack_into("<H", b, 0x16, mfr_index)
    b[0x18:0x18 + 36] = guid.encode("ascii")
    b[0x3C] = 0x00
    b[0x3D] = model.battery_type
    return bytes(b)


def build_lib(manufacturers: list[Manufacturer], models: list[Model]) -> bytes:
    """Build the raw .lib file bytes. Raises LibFormatError on any
    violation of the on-device validation rules."""
    if len(manufacturers) > 0xFFFF:
        raise LibFormatError("Too many manufacturers (max 65535)")
    if len(models) > 0xFFFF:
        raise LibFormatError("Too many models (max 65535)")

    name_to_index: dict[str, int] = {}
    for i, m in enumerate(manufacturers):
        if m.name in name_to_index:
            raise LibFormatError(f"Duplicate manufacturer name '{m.name}'")
        name_to_index[m.name] = i

    guids_seen: set[str] = set()
    model_bytes_list = []
    for model in models:
        if model.manufacturer not in name_to_index:
            raise LibFormatError(
                f"Model '{model.name}' references unknown manufacturer "
                f"'{model.manufacturer}'"
            )
        guid_u = model.guid.upper()
        if guid_u in guids_seen:
            raise LibFormatError(f"Duplicate GUID '{guid_u}' (model '{model.name}')")
        guids_seen.add(guid_u)
        model_bytes_list.append(build_model_record(model, name_to_index[model.manufacturer]))

    hdr = bytearray(HEADER_SIZE)
    hdr[0:2] = MAGIC
    struct.pack_into(">H", hdr, 2, len(manufacturers))
    struct.pack_into(">H", hdr, 4, len(models))

    mfr_bytes = b"".join(build_manufacturer_record(m.name) for m in manufacturers)
    out = bytes(hdr) + mfr_bytes + b"".join(model_bytes_list)

    expected = HEADER_SIZE + MFR_RECORD_SIZE * len(manufacturers) + MODEL_RECORD_SIZE * len(models)
    assert len(out) == expected, "internal size mismatch while building .lib"
    return out


def parse_lib(data: bytes) -> tuple[list[Manufacturer], list[Model]]:
    """Parse raw .lib file bytes. Raises LibFormatError if the file fails
    any of the device's own validation rules."""
    if len(data) < HEADER_SIZE:
        raise LibFormatError("File is too small to contain a header")
    if data[0] != 0x24 or data[1] != 0x24:
        raise LibFormatError("Bad magic bytes ('$$' expected) - not a valid .lib file")

    a = struct.unpack_from(">H", data, 2)[0]
    b = struct.unpack_from(">H", data, 4)[0]
    expected = HEADER_SIZE + MFR_RECORD_SIZE * a + MODEL_RECORD_SIZE * b
    if len(data) != expected:
        raise LibFormatError(
            f"File size {len(data)} does not match expected {expected} "
            f"for {a} manufacturer(s) / {b} model(s)"
        )

    offset = HEADER_SIZE
    manufacturers: list[Manufacturer] = []
    for _ in range(a):
        raw = data[offset:offset + MFR_RECORD_SIZE]
        name = raw.split(b"\x00", 1)[0].decode("ascii", errors="replace")
        manufacturers.append(Manufacturer(name=name))
        offset += MFR_RECORD_SIZE

    models: list[Model] = []
    for _ in range(b):
        raw = data[offset:offset + MODEL_RECORD_SIZE]
        name = raw[0:20].split(b"\x00", 1)[0].decode("ascii", errors="replace")
        conductance = struct.unpack_from("<H", raw, 0x14)[0]
        mfr_index = struct.unpack_from("<H", raw, 0x16)[0]
        guid = raw[0x18:0x18 + 36].decode("ascii", errors="replace")
        battery_type = raw[0x3D]
        if 0 <= mfr_index < len(manufacturers):
            mfr_name = manufacturers[mfr_index].name
        else:
            mfr_name = f"<invalid index {mfr_index}>"
        models.append(
            Model(
                name=name,
                conductance=conductance,
                manufacturer=mfr_name,
                battery_type=battery_type,
                guid=guid,
            )
        )
        offset += MODEL_RECORD_SIZE

    return manufacturers, models
