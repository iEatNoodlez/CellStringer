"""
Core read/write/validation engine for Celltron .CDO (Celltron Data Offline)
site-template files. No GUI dependencies -- safe to import from a CLI, a
test suite, or a Tkinter front end.

File format summary (see SPEC.md for the full reverse-engineered spec):

    Signature (50 bytes):
        "File created by Celltron Max", space-padded.

    Header (10 bytes, at file offset 0x32):
        0x00  u8       counter (export-side save/modification counter;
                        import-ignored -- node data always starts at region
                        offset 0x0A regardless of this byte's value)
        0x01  u8       version = 0x02 (required)
        0x02  u32 LE   size (header + all node bytes)
        0x06  u32      reserved = 0

    Node-tree image (at file offset 0x3C == region offset 0x0A):
        A flat, sequentially-packed image of SITE -> PLANT -> STRING -> JAR
        records. Every link field (prev/next/parent/child, and STRING id) is
        an offset relative to the flash region base 0x5C0000, i.e. relative
        to the start of the header (region offset 0x00). Null = 0xFFFFFFFF.

        file_offset = region_offset - 0x0A + 0x3C = region_offset + 0x32

A template (a site defined but not yet tested) has JAR.child = null -- no
MEASUREMENT record. This engine only builds/reads templates: a JAR whose
child pointer is non-null (already has test results) is refused on parse.
MEASUREMENT records are decoded (see SPEC.md Section 9) but out of scope
for template authoring, so this engine deliberately doesn't build or
re-pack them.
"""

from __future__ import annotations

import re
import struct
import uuid
from dataclasses import dataclass, field

NULL = 0xFFFFFFFF

TYPE_SITE, TYPE_PLANT, TYPE_STRING, TYPE_JAR, TYPE_MEAS = 1, 2, 3, 4, 5

SITE_SIZE = 0x6E    # 110
PLANT_SIZE = 0x6E   # 110
STRING_SIZE = 0xF6  # 246
JAR_SIZE = 0x6E     # 110
MEAS_SIZE = 0x3C    # 60

COMMON_HEADER_SIZE = 0x6E  # 110; SITE/PLANT/JAR records are exactly this

MIN_JARS_PER_STRING = 1
MAX_JARS_PER_STRING = 240  # device-enforced maximum; confirmed via hardware import/export round-trip

SIGNATURE_TEXT = b"File created by Celltron Max"
SIGNATURE_SIZE = 0x32  # 50
HEADER_SIZE = 10
NODE_START = 0x0A  # region offset of the first node
FILE_NODE_START = SIGNATURE_SIZE + HEADER_SIZE  # 0x3C

REGION_BASE = 0x5C0000
FLASH_TOP = 0x800000
# device bounds-checks: REGION_BASE + size + SIGNATURE_SIZE < FLASH_TOP
MAX_REGION_SIZE = FLASH_TOP - REGION_BASE - SIGNATURE_SIZE - 1

FLAG_SITE = 0x02
FLAG_PLANT = 0xFF
FLAG_STRING = 0xFF
FLAG_JAR = 0x02

# STRING config block (+0x87, 7 bytes)
TEST_TYPE_VOLTAGE_ONLY = 0
TEST_TYPE_VOLTS_AND_COND = 1
TEST_TYPE_VOLTS_THEN_COND = 2
TEST_TYPES = {
    TEST_TYPE_VOLTAGE_ONLY: "Voltage Only",
    TEST_TYPE_VOLTS_AND_COND: "Volts and Cond",
    TEST_TYPE_VOLTS_THEN_COND: "Volts then Cond",
}
STRAPS_PER_JAR_OPTIONS = {0: "None", 1: "1", 2: "2", 3: "3"}
_CONFIG_FIXED_88 = 0x04
_CONFIG_FIXED_8B = 0x01

# The tester only accepts these nominal jar voltages. Each has a confirmed
# default voltage alarm pair (upper = 1.25x nominal, lower = 1.10x nominal,
# stored as explicit millivolts).
JAR_VOLTAGE_OPTIONS = [2, 4, 6, 8, 10, 12, 16, 18]
JAR_VOLTAGE_DEFAULT_THRESHOLDS_MV = {
    2: (2500, 2200),
    4: (5000, 4400),
    6: (7500, 6600),
    8: (10000, 8800),
    10: (12500, 11000),
    12: (15000, 13200),
    16: (20000, 17600),
    18: (22500, 19800),
}

NAME_FIELD_SIZE = 0x32       # 50, node name
TECH_ID_FIELD_SIZE = 0x14    # 20
MFR_FIELD_SIZE = 0x14        # 20
MODEL_FIELD_SIZE = 0x20      # 32

GUID_RE = re.compile(
    r"^[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}$"
)


class CdoFormatError(ValueError):
    """Raised for anything that would make the device reject the file, or
    that this tool cannot safely round-trip."""


def new_guid() -> str:
    return str(uuid.uuid4()).upper()


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class BatteryAssignment:
    """The battery-model link + test settings carried on a STRING record.
    tech_id/conductance/manufacturer/model and the test/alarm settings below
    are meaningful to a human; guid is device plumbing that this tool
    preserves or defaults but never surfaces in the GUI. The STRING `id`
    field (+0x11) is not stored here at all -- it's deterministic
    (region_offset + 0x6E, confirmed 15/15 real strings) and always
    computed fresh at build time in build_cdo().

    Test/alarm fields map onto the STRING record's config block (+0x87, 7
    bytes) and threshold block (+0x90, 14 bytes) -- see SPEC.md Section 8."""
    tech_id: str
    conductance: int
    manufacturer: str
    model: str
    guid: str = field(default_factory=new_guid)

    # config block (+0x87)
    test_type: int = TEST_TYPE_VOLTS_AND_COND   # +0x87
    tests_per_jar: int = 1                      # +0x89, 1-3
    straps_per_jar: int = 0                     # +0x8A, 0=None, 1-3

    # threshold block (+0x90)
    jar_voltage: int = 12             # +0x90, u16, Volts
    temp_upper_c: int = 30            # +0x92, u8, degrees C
    temp_lower_c: int = 10            # +0x93, u8, degrees C
    string_g_alarm_pct: int = 60      # +0x94, u8, %
    string_g_warn_pct: int = 70       # +0x95, u8, %
    voltage_upper_mv: int = 15000     # +0x96, u16, millivolts
    voltage_lower_mv: int = 13200     # +0x98, u16, millivolts
    jar_g_alarm_pct: int = 60         # +0x9C, u8, %
    jar_g_warn_pct: int = 70          # +0x9D, u8, %


@dataclass
class Jar:
    name: str


@dataclass
class String:
    name: str
    battery: BatteryAssignment
    jars: list[Jar] = field(default_factory=list)


@dataclass
class Plant:
    name: str
    strings: list[String] = field(default_factory=list)


@dataclass
class Site:
    name: str
    plants: list[Plant] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def _pad_ascii(s: str, size: int, field_name: str) -> bytes:
    # Normalized at the format layer -- guaranteed regardless of how data
    # got in (GUI, script, or a loaded file with mixed case).
    s = s.upper()
    try:
        b = s.encode("ascii")
    except UnicodeEncodeError as e:
        raise CdoFormatError(f"{field_name} '{s}' contains non-ASCII characters") from e
    if len(b) > size - 1:
        raise CdoFormatError(
            f"{field_name} '{s}' is too long ({len(b)} chars, max {size - 1})"
        )
    return b.ljust(size, b"\x00")


def _validate_u16(value: int, field_name: str) -> None:
    if not (0 <= value <= 0xFFFF):
        raise CdoFormatError(f"{field_name} {value} out of range (0-65535)")


def _validate_u8(value: int, field_name: str) -> None:
    if not (0 <= value <= 0xFF):
        raise CdoFormatError(f"{field_name} {value} out of range (0-255)")


def validate_name(name: str, field_name: str = "Name") -> None:
    """Public pre-flight check for a node name (site/plant/string/jar), for
    GUI dialogs to call before the user can even try to save."""
    _pad_ascii(name, NAME_FIELD_SIZE, field_name)


def validate_jar_count(count: int, string_name: str) -> None:
    """Public pre-flight check for a string's jar count against the
    device-enforced 1-240 range (confirmed via hardware round-trip)."""
    if not (MIN_JARS_PER_STRING <= count <= MAX_JARS_PER_STRING):
        raise CdoFormatError(
            f"String '{string_name}' has {count} jar(s); the device only accepts "
            f"{MIN_JARS_PER_STRING}-{MAX_JARS_PER_STRING} jars per string"
        )


def validate_string_fields(name: str, battery: BatteryAssignment) -> None:
    """Public pre-flight check for everything a String record's fields
    must satisfy, for GUI dialogs to call before the user can even try to
    save."""
    validate_name(name, "String name")
    _pad_ascii(battery.tech_id, TECH_ID_FIELD_SIZE, "Tech ID")
    _pad_ascii(battery.manufacturer, MFR_FIELD_SIZE, "Manufacturer")
    _pad_ascii(battery.model, MODEL_FIELD_SIZE, "Model")
    _validate_battery(battery, name)


def _validate_battery(battery: BatteryAssignment, string_name: str) -> None:
    p = f"String '{string_name}'"
    _validate_u16(battery.conductance, f"{p}: conductance")
    guid = battery.guid.upper()
    if not GUID_RE.match(guid):
        raise CdoFormatError(f"{p}: GUID '{guid}' is not a valid canonical UUID")

    if battery.test_type not in TEST_TYPES:
        raise CdoFormatError(f"{p}: test type {battery.test_type} is not one of {sorted(TEST_TYPES)}")
    if not (1 <= battery.tests_per_jar <= 3):
        raise CdoFormatError(f"{p}: tests per jar must be 1-3")
    if battery.straps_per_jar not in STRAPS_PER_JAR_OPTIONS:
        raise CdoFormatError(f"{p}: straps per jar must be one of {sorted(STRAPS_PER_JAR_OPTIONS)}")

    if battery.jar_voltage not in JAR_VOLTAGE_OPTIONS:
        raise CdoFormatError(f"{p}: jar voltage must be one of {JAR_VOLTAGE_OPTIONS}")
    _validate_u8(battery.temp_upper_c, f"{p}: temp upper")
    _validate_u8(battery.temp_lower_c, f"{p}: temp lower")
    _validate_u8(battery.string_g_alarm_pct, f"{p}: string G alarm %")
    _validate_u8(battery.string_g_warn_pct, f"{p}: string G warn %")
    _validate_u16(battery.voltage_upper_mv, f"{p}: voltage alarm upper")
    _validate_u16(battery.voltage_lower_mv, f"{p}: voltage alarm lower")
    _validate_u8(battery.jar_g_alarm_pct, f"{p}: jar G alarm %")
    _validate_u8(battery.jar_g_warn_pct, f"{p}: jar G warn %")


def _build_config_bytes(battery: BatteryAssignment) -> bytes:
    b = bytearray(7)
    b[0x00] = battery.test_type & 0xFF               # +0x87
    b[0x01] = _CONFIG_FIXED_88                        # +0x88 fixed
    b[0x02] = battery.tests_per_jar & 0xFF            # +0x89
    b[0x03] = battery.straps_per_jar & 0xFF           # +0x8A
    b[0x04] = _CONFIG_FIXED_8B                        # +0x8B fixed
    b[0x05] = 0x00                                    # +0x8C fixed
    b[0x06] = 0x00                                    # +0x8D fixed
    return bytes(b)


def _build_threshold_bytes(battery: BatteryAssignment) -> bytes:
    b = bytearray(14)
    struct.pack_into("<H", b, 0x00, battery.jar_voltage)       # +0x90
    b[0x02] = battery.temp_upper_c & 0xFF                       # +0x92
    b[0x03] = battery.temp_lower_c & 0xFF                       # +0x93
    b[0x04] = battery.string_g_alarm_pct & 0xFF                 # +0x94
    b[0x05] = battery.string_g_warn_pct & 0xFF                  # +0x95
    struct.pack_into("<H", b, 0x06, battery.voltage_upper_mv)   # +0x96
    struct.pack_into("<H", b, 0x08, battery.voltage_lower_mv)   # +0x98
    struct.pack_into("<H", b, 0x0A, 0)                          # +0x9A reserved
    b[0x0C] = battery.jar_g_alarm_pct & 0xFF                    # +0x9C
    b[0x0D] = battery.jar_g_warn_pct & 0xFF                     # +0x9D
    return bytes(b)


def _parse_config_bytes(raw: bytes) -> dict:
    return dict(
        test_type=raw[0x00],
        tests_per_jar=raw[0x02],
        straps_per_jar=raw[0x03],
    )


def _parse_threshold_bytes(raw: bytes) -> dict:
    jar_voltage = struct.unpack_from("<H", raw, 0x00)[0]
    voltage_upper_mv = struct.unpack_from("<H", raw, 0x06)[0]
    voltage_lower_mv = struct.unpack_from("<H", raw, 0x08)[0]
    return dict(
        jar_voltage=jar_voltage,
        temp_upper_c=raw[0x02],
        temp_lower_c=raw[0x03],
        string_g_alarm_pct=raw[0x04],
        string_g_warn_pct=raw[0x05],
        voltage_upper_mv=voltage_upper_mv,
        voltage_lower_mv=voltage_lower_mv,
        jar_g_alarm_pct=raw[0x0C],
        jar_g_warn_pct=raw[0x0D],
    )


# ---------------------------------------------------------------------------
# Building
# ---------------------------------------------------------------------------

def _blank_node(size: int) -> bytearray:
    # Real records fill the tail (0x48..0x6B, between name and count) with
    # 0xFF rather than 0x00 -- match that to be safe.
    b = bytearray(size)
    if size >= 0x6C:
        b[0x48:0x6C] = b"\xFF" * (0x6C - 0x48)
    return b


def _write_common_header(b: bytearray, typ: int, prev: int, nxt: int, parent: int,
                          child: int, node_id: int, flag: int, name: str, count: int) -> None:
    b[0] = typ
    struct.pack_into("<I", b, 0x01, prev)
    struct.pack_into("<I", b, 0x05, nxt)
    struct.pack_into("<I", b, 0x09, parent)
    struct.pack_into("<I", b, 0x0D, child)
    struct.pack_into("<I", b, 0x11, node_id)
    b[0x15] = flag
    b[0x16:0x16 + NAME_FIELD_SIZE] = _pad_ascii(name, NAME_FIELD_SIZE, "Name")
    struct.pack_into("<H", b, 0x6C, count & 0xFFFF)


def _first_string_jar_count(strings: list[String]) -> int:
    # `count` (+0x6C) propagates the *first* string's jar count up through
    # its plant and site -- confirmed against multi-plant real exports.
    # It is never a sum/total: a site with one plant holding a 4-jar string
    # and another plant holding a 6-jar string still writes SITE.count = 4
    # (that first plant's first string's count), not 10.
    count = len(strings[0].jars)
    _validate_u16(count, "Jar count")
    return count


def build_cdo(sites: list[Site]) -> bytes:
    """Build the raw .CDO file bytes for a forest of site trees. Raises
    CdoFormatError on any violation of the on-device validation rules."""
    if not sites:
        raise CdoFormatError("At least one site is required")

    # Pass 1: walk the tree in pre-order (site, plant, string, jars...),
    # assigning each node a region offset. Nodes are packed back-to-back
    # with no gaps, so offsets must be assigned before any pointer field
    # can be filled in.
    offsets: dict[int, int] = {}
    plan: list[tuple[int, object, int]] = []  # (type, obj, size)
    cursor = NODE_START

    for site in sites:
        if not site.plants:
            raise CdoFormatError(f"Site '{site.name}' has no plants")
        offsets[id(site)] = cursor
        plan.append((TYPE_SITE, site, SITE_SIZE))
        cursor += SITE_SIZE
        for plant in site.plants:
            if not plant.strings:
                raise CdoFormatError(f"Plant '{plant.name}' has no strings")
            offsets[id(plant)] = cursor
            plan.append((TYPE_PLANT, plant, PLANT_SIZE))
            cursor += PLANT_SIZE
            for string in plant.strings:
                validate_jar_count(len(string.jars), string.name)
                _validate_battery(string.battery, string.name)
                offsets[id(string)] = cursor
                plan.append((TYPE_STRING, string, STRING_SIZE))
                cursor += STRING_SIZE
                for jar in string.jars:
                    offsets[id(jar)] = cursor
                    plan.append((TYPE_JAR, jar, JAR_SIZE))
                    cursor += JAR_SIZE

    total_region_size = HEADER_SIZE + (cursor - NODE_START)
    if total_region_size > MAX_REGION_SIZE:
        raise CdoFormatError(
            f"Site tree is too large ({total_region_size} bytes; device limit is "
            f"{MAX_REGION_SIZE} bytes)"
        )

    # Pass 2: emit bytes, now that every node's offset is known.
    out = bytearray()
    for site_idx, site in enumerate(sites):
        prev_site = offsets[id(sites[site_idx - 1])] if site_idx > 0 else NULL
        next_site = offsets[id(sites[site_idx + 1])] if site_idx + 1 < len(sites) else NULL
        b = _blank_node(SITE_SIZE)
        _write_common_header(
            b, TYPE_SITE, prev_site, next_site, NULL, offsets[id(site.plants[0])],
            NULL, FLAG_SITE, site.name, _first_string_jar_count(site.plants[0].strings),
        )
        out += b

        for plant_idx, plant in enumerate(site.plants):
            prev_plant = offsets[id(site.plants[plant_idx - 1])] if plant_idx > 0 else NULL
            next_plant = offsets[id(site.plants[plant_idx + 1])] if plant_idx + 1 < len(site.plants) else NULL
            b = _blank_node(PLANT_SIZE)
            _write_common_header(
                b, TYPE_PLANT, prev_plant, next_plant, offsets[id(site)],
                offsets[id(plant.strings[0])], NULL, FLAG_PLANT, plant.name,
                _first_string_jar_count(plant.strings),
            )
            out += b

            for string_idx, string in enumerate(plant.strings):
                prev_string = offsets[id(plant.strings[string_idx - 1])] if string_idx > 0 else NULL
                next_string = offsets[id(plant.strings[string_idx + 1])] if string_idx + 1 < len(plant.strings) else NULL
                string_offset = offsets[id(string)]
                # STRING id (+0x11) = its own region offset + the common
                # header size (the region address of the STRING's own
                # extension block, right after its common header) --
                # confirmed deterministic across 15/15 real strings.
                node_id = string_offset + COMMON_HEADER_SIZE
                b = _blank_node(STRING_SIZE)
                _write_common_header(
                    b, TYPE_STRING, prev_string, next_string, offsets[id(plant)],
                    offsets[id(string.jars[0])], node_id, FLAG_STRING, string.name,
                    len(string.jars),
                )
                b[0x6E] = 0x06
                b[0x6F:0x73] = b"\xFF" * 4
                b[0x73:0x73 + TECH_ID_FIELD_SIZE] = _pad_ascii(
                    string.battery.tech_id, TECH_ID_FIELD_SIZE, f"String '{string.name}': tech ID"
                )
                b[0x87:0x8E] = _build_config_bytes(string.battery)
                struct.pack_into("<H", b, 0x8E, string.battery.conductance)
                b[0x90:0x9E] = _build_threshold_bytes(string.battery)
                b[0x9E:0x9E + MFR_FIELD_SIZE] = _pad_ascii(
                    string.battery.manufacturer, MFR_FIELD_SIZE, f"String '{string.name}': manufacturer"
                )
                b[0xB2:0xB2 + MODEL_FIELD_SIZE] = _pad_ascii(
                    string.battery.model, MODEL_FIELD_SIZE, f"String '{string.name}': model"
                )
                guid = string.battery.guid.upper().encode("ascii")
                b[0xD2:0xD2 + len(guid)] = guid
                out += b

                for jar_idx, jar in enumerate(string.jars):
                    prev_jar = offsets[id(string.jars[jar_idx - 1])] if jar_idx > 0 else NULL
                    next_jar = offsets[id(string.jars[jar_idx + 1])] if jar_idx + 1 < len(string.jars) else NULL
                    b = _blank_node(JAR_SIZE)
                    _write_common_header(
                        b, TYPE_JAR, prev_jar, next_jar, string_offset, NULL, NULL,
                        FLAG_JAR, jar.name, 0,
                    )
                    out += b

    header = bytearray(HEADER_SIZE)
    header[0] = NODE_START  # import-ignored counter byte; 0x0A is a safe default
    header[1] = 0x02
    struct.pack_into("<I", header, 0x02, total_region_size)

    signature = SIGNATURE_TEXT + b" " * (SIGNATURE_SIZE - len(SIGNATURE_TEXT))
    result = bytes(signature) + bytes(header) + bytes(out)
    expected_len = FILE_NODE_START + (cursor - NODE_START)
    assert len(result) == expected_len, "internal size mismatch while building .CDO"
    return result


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _region_to_file(region_offset: int) -> int:
    return region_offset - NODE_START + FILE_NODE_START


def _read_common_header(data: bytes, file_off: int, node_label: str) -> dict:
    if file_off + 0x6E > len(data):
        raise CdoFormatError(f"{node_label}: truncated file (node header runs past EOF)")
    typ = data[file_off]
    prev, nxt, parent, child, node_id = struct.unpack_from("<IIIII", data, file_off + 0x01)
    flag = data[file_off + 0x15]
    raw_name = data[file_off + 0x16:file_off + 0x16 + NAME_FIELD_SIZE]
    name = raw_name.split(b"\x00", 1)[0].decode("ascii", errors="replace")
    count = struct.unpack_from("<H", data, file_off + 0x6C)[0]
    return dict(type=typ, prev=prev, next=nxt, parent=parent, child=child,
                id=node_id, flag=flag, name=name, count=count)


def _read_jar(data: bytes, region_off: int) -> Jar:
    file_off = _region_to_file(region_off)
    if file_off + JAR_SIZE > len(data):
        raise CdoFormatError(f"Jar at region offset 0x{region_off:X}: truncated file")
    hdr = _read_common_header(data, file_off, f"Jar at region offset 0x{region_off:X}")
    if hdr["type"] != TYPE_JAR:
        raise CdoFormatError(f"Expected JAR at region offset 0x{region_off:X}, found type {hdr['type']}")
    if hdr["child"] != NULL:
        raise CdoFormatError(
            f"Jar '{hdr['name']}' has a measurement (child record present). This tool only "
            f"edits un-tested site templates, not databases with test results."
        )
    return Jar(name=hdr["name"])


def _read_string(data: bytes, region_off: int) -> String:
    file_off = _region_to_file(region_off)
    if file_off + STRING_SIZE > len(data):
        raise CdoFormatError(f"String at region offset 0x{region_off:X}: truncated file")
    hdr = _read_common_header(data, file_off, f"String at region offset 0x{region_off:X}")
    if hdr["type"] != TYPE_STRING:
        raise CdoFormatError(f"Expected STRING at region offset 0x{region_off:X}, found type {hdr['type']}")

    tech_id = data[file_off + 0x73:file_off + 0x73 + TECH_ID_FIELD_SIZE].split(b"\x00", 1)[0].decode(
        "ascii", errors="replace")
    config_kwargs = _parse_config_bytes(data[file_off + 0x87:file_off + 0x8E])
    conductance = struct.unpack_from("<H", data, file_off + 0x8E)[0]
    threshold_kwargs = _parse_threshold_bytes(data[file_off + 0x90:file_off + 0x9E])
    mfr = data[file_off + 0x9E:file_off + 0x9E + MFR_FIELD_SIZE].split(b"\x00", 1)[0].decode(
        "ascii", errors="replace")
    model = data[file_off + 0xB2:file_off + 0xB2 + MODEL_FIELD_SIZE].split(b"\x00", 1)[0].decode(
        "ascii", errors="replace")
    guid = data[file_off + 0xD2:file_off + 0xD2 + 36].decode("ascii", errors="replace")

    # hdr["id"] is intentionally discarded: STRING id is deterministic
    # (region_offset + COMMON_HEADER_SIZE) and build_cdo() always recomputes
    # it fresh from each string's actual position, so there's nothing to
    # preserve here even if a source file's stored value were stale.
    battery = BatteryAssignment(
        tech_id=tech_id, conductance=conductance, manufacturer=mfr, model=model,
        guid=guid, **config_kwargs, **threshold_kwargs,
    )

    jars: list[Jar] = []
    child = hdr["child"]
    seen: set[int] = set()
    while child != NULL:
        if child in seen:
            raise CdoFormatError(f"String '{hdr['name']}': circular jar chain detected")
        seen.add(child)
        jar_off = _region_to_file(child)
        jar_hdr = _read_common_header(data, jar_off, f"Jar at region offset 0x{child:X}")
        jars.append(_read_jar(data, child))
        child = jar_hdr["next"]

    return String(name=hdr["name"], battery=battery, jars=jars)


def _read_plant(data: bytes, region_off: int) -> Plant:
    file_off = _region_to_file(region_off)
    if file_off + PLANT_SIZE > len(data):
        raise CdoFormatError(f"Plant at region offset 0x{region_off:X}: truncated file")
    hdr = _read_common_header(data, file_off, f"Plant at region offset 0x{region_off:X}")
    if hdr["type"] != TYPE_PLANT:
        raise CdoFormatError(f"Expected PLANT at region offset 0x{region_off:X}, found type {hdr['type']}")

    strings: list[String] = []
    child = hdr["child"]
    seen: set[int] = set()
    while child != NULL:
        if child in seen:
            raise CdoFormatError(f"Plant '{hdr['name']}': circular string chain detected")
        seen.add(child)
        string_off_file = _region_to_file(child)
        string_hdr = _read_common_header(data, string_off_file, f"String at region offset 0x{child:X}")
        strings.append(_read_string(data, child))
        child = string_hdr["next"]

    return Plant(name=hdr["name"], strings=strings)


def _read_site(data: bytes, region_off: int) -> Site:
    file_off = _region_to_file(region_off)
    if file_off + SITE_SIZE > len(data):
        raise CdoFormatError(f"Site at region offset 0x{region_off:X}: truncated file")
    hdr = _read_common_header(data, file_off, f"Site at region offset 0x{region_off:X}")
    if hdr["type"] != TYPE_SITE:
        raise CdoFormatError(f"Expected SITE at region offset 0x{region_off:X}, found type {hdr['type']}")

    plants: list[Plant] = []
    child = hdr["child"]
    seen: set[int] = set()
    while child != NULL:
        if child in seen:
            raise CdoFormatError(f"Site '{hdr['name']}': circular plant chain detected")
        seen.add(child)
        plant_off_file = _region_to_file(child)
        plant_hdr = _read_common_header(data, plant_off_file, f"Plant at region offset 0x{child:X}")
        plants.append(_read_plant(data, child))
        child = plant_hdr["next"]

    return Site(name=hdr["name"], plants=plants)


def parse_cdo(data: bytes) -> list[Site]:
    """Parse raw .CDO file bytes into a forest of Site trees. Raises
    CdoFormatError if the file fails validation, or if it contains
    already-tested jars (measurements) this tool doesn't support editing."""
    if len(data) < FILE_NODE_START:
        raise CdoFormatError("File is too small to contain a signature and header")

    header = data[SIGNATURE_SIZE:SIGNATURE_SIZE + HEADER_SIZE]
    # header[0] is an export-side save/modification counter -- the import
    # parser never reads it, and real files carry varying values (0x0A,
    # 0x12, 0x22, ...). Node data always starts at region offset NODE_START
    # regardless of this byte, so it is intentionally not validated here.
    version = header[1]
    size = struct.unpack_from("<I", header, 0x02)[0]
    if version != 0x02:
        raise CdoFormatError(f"Header version is 0x{version:02X}; only version 0x02 is supported")
    expected_file_size = FILE_NODE_START + (size - HEADER_SIZE)
    if len(data) < expected_file_size:
        raise CdoFormatError(
            f"File is truncated: header declares {size} region bytes but the file only "
            f"has room for {len(data) - FILE_NODE_START + HEADER_SIZE}"
        )

    sites: list[Site] = []
    region_off = NODE_START
    seen: set[int] = set()
    while region_off != NULL:
        if region_off in seen:
            raise CdoFormatError("Circular site chain detected")
        seen.add(region_off)
        file_off = _region_to_file(region_off)
        hdr = _read_common_header(data, file_off, f"Site at region offset 0x{region_off:X}")
        sites.append(_read_site(data, region_off))
        region_off = hdr["next"]

    if not sites:
        raise CdoFormatError("File contains no sites")
    return sites


def region_size_for(sites: list[Site]) -> int:
    """Total region byte count (header + all nodes) a build of these sites
    would produce, without actually building -- used for live status-bar
    size estimates."""
    total = HEADER_SIZE
    for site in sites:
        total += SITE_SIZE
        for plant in site.plants:
            total += PLANT_SIZE
            for string in plant.strings:
                total += STRING_SIZE
                total += JAR_SIZE * len(string.jars)
    return total
