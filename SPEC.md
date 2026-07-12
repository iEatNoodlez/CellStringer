# Celltron `.CDO` (Celltron Data Offline) — File Format Specification
> Reverse-engineered from device firmware (import parser at `0x0802A818`) and
> **verified byte-for-byte against multiple real `.CDO` files** (single-site,
> multi-site, differential-config, and multi-plant exports) produced by the
> Celltron Max desktop software. The generator in this repo (`cdo_format.py`)
> produces site/plant/string/jar records **identical** to real files, including
> the multi-plant `count` propagation rule and the deterministic STRING `id`
> (see §6 and §11) — neither of those is an extrapolation anymore.
>
> **Hardware round-trip confirmed:** a comprehensive generated `.CDO` (2 sites, 3
> plants, 5 strings exercising every test-type/tests/straps/voltage/threshold
> combination, including a 240-jar string) was imported into a real tester and
> then exported. The export was **byte-for-byte identical** to the imported file
> except for the single header counter byte at `+0x00` (§4). No field was
> normalized, dropped, or rejected; no size limit was tripped short of the
> 240-jar-per-string cap (§6). The format below is confirmed against live
> device import **and** export, not just a single real-world sample file.

Purpose: enable a developer to build a program that writes custom `.CDO`
**site-template** files (Site → Plant → String → Jar trees) for loading into
the tester so operators don't have to key sites in by hand.

## 1. Overview
A `.CDO` file carries the tester's **results/site database** as a tree of linked
nodes:

```
SITE ── PLANT ── STRING ── JAR ── (MEASUREMENT)
```

- A **site** contains plants; a **plant** contains strings; a **string** contains
  jars (individual batteries) and holds the battery-model assignment + test
  settings; a **jar** optionally contains a measurement.
- For a **template** (a site defined but not yet tested), jars have **no**
  measurement child — that is the normal thing to generate.

The file is a small signature + header followed by a flat image of the node tree.
On import the device erases its database region and copies the node image into
internal flash at base address **`0x5C0000`**. All node cross-references are stored
as **offsets relative to that `0x5C0000` base**.

## 2. File structure

```
+-----------------------------------------------+  file offset
| Signature (50 bytes)                          |  0x00
+-----------------------------------------------+
| Header (10 bytes)                              |  0x32
+-----------------------------------------------+
| Node-tree image (all records, packed)          |  0x3C
+-----------------------------------------------+
```

Total file size = `0x3C + (sum of all node record sizes)`.

## 3. Signature (50 bytes, offset `0x00`)
The exact ASCII string:

```
File created by Celltron Max
```

followed by **space (`0x20`) padding to fill 50 bytes total**. The device seeks
past these 50 bytes on import (it does not parse them), but the desktop software
writes this signature and it should be present.

## 4. Header (10 bytes, offset `0x32`)
| Offset | Size | Field    | Value / meaning                                            |
|-------:|-----:|----------|-------------------------------------------------------------|
| `0x00` | 1    | counter  | export-side value; **import-ignored** (see note below)     |
| `0x01` | 1    | version  | `0x02` — **required**; any other value is rejected         |
| `0x02` | 4    | size     | `u32 LE` — total region bytes used = header(10) + all nodes |
| `0x06` | 4    | reserved | `0x00 00 00 00`                                             |

`size` equals the "next free offset" in the flash region after import, i.e.
`10 + (total node bytes)`. The device bounds-checks `0x5C0000 + size + 0x32 <
0x800000`.

**Byte `+0x00` is import-ignored.** The import parser reads only `+0x01`
(version) and `+0x02` (size); it never reads `+0x00`. Its value in real files
is *not* content-deterministic (observed `0x0A`, `0x12`, `0x22` across
exports with no consistent relationship to node counts) — it is an export-side
save/modification counter. **Confirmed by hardware round-trip:** an imported
file whose `+0x00` was `0x0A` re-exported with `+0x00 = 0x47`, while every
other byte in the file was unchanged. A generator may write any value here
(`0x0A` is a safe default); the device overwrites it on its own export. Node
data always begins at region offset `0x0A` regardless of this byte.

## 5. The region / pointer model (important)
All node link fields (`prev`, `next`, `parent`, `child`, and string `id`) are
**offsets relative to the flash region base `0x5C0000`**, NOT file offsets.

- The 10-byte header occupies region offsets `0x00`–`0x09`.
- The first node begins at **region offset `0x0A`**.
- Nodes are packed **sequentially** with no gaps; each node's region offset is the
  previous node's offset plus the previous node's record size.
- **Null pointer = `0xFFFFFFFF`.**

**File ↔ region mapping:** a node at region offset `R` sits at file offset
`R + 0x32` (because file `0x3C` == region `0x0A`). Equivalently: `file = R - 0x0A +
0x3C`.

A generator should: assign region offsets to every node first, then fill in the
pointer fields using those offsets.

## 6. Node record — common header (all types)
| Offset | Size | Field  | Notes                                                       |
|-------:|-----:|--------|-------------------------------------------------------------|
| `0x00` | 1    | type   | `1`=SITE `2`=PLANT `3`=STRING `4`=JAR `5`=MEAS              |
| `0x01` | 4    | prev   | region offset of previous sibling, or `0xFFFFFFFF`          |
| `0x05` | 4    | next   | region offset of next sibling, or `0xFFFFFFFF`             |
| `0x09` | 4    | parent | region offset of parent node, or `0xFFFFFFFF` (SITE=null)  |
| `0x0D` | 4    | child  | region offset of first child, or `0xFFFFFFFF`             |
| `0x11` | 4    | id     | `u32`; on STRING = `region_offset + 0x6E` (deterministic, see below); else `0xFFFFFFFF` |
| `0x15` | 1    | flag   | see per-type values below                                  |
| `0x16` | 50   | name   | ASCII, null-padded (`char[0x32]`)                          |
| `0x6C` | 2    | count  | `u16` — jars-per-string, propagated (see §11)              |

**`count` (`+0x6C`) is a propagation, not a sum** — confirmed against
multi-plant real exports. On a STRING it is that string's own jar count. On a
PLANT it is the jar count of the plant's **first string**. On a SITE it is the
jar count of that site's first plant's first string. It **never** sums or
totals jar counts across multiple strings/plants. See §11 for the worked
example.

**STRING `id` (`+0x11`) is deterministic:** always `region_offset + 0x6E`
(the region address of the STRING's own extension block, right after its
common header) — confirmed across 15/15 real strings and preserved through a
hardware import/export round-trip. A generator must compute this from the
string's actual position; it is not a placeholder and not user-settable.

**Jar-count limit:** a string may hold **1–240 jars** (device-enforced
maximum, confirmed via hardware round-trip — a 240-jar string imported and
re-exported intact). A builder should reject counts outside this range;
`cdo_format.py` does via `validate_jar_count()`.

**Record sizes by type:**
| Type   | Size          |
|--------|---------------|
| SITE   | `0x6E` (110)  |
| PLANT  | `0x6E` (110)  |
| STRING | `0xF6` (246)  |
| JAR    | `0x6E` (110)  |
| MEAS   | `0x3C` (60)   |

**Flag values (`+0x15`) observed in a real template:**
| Type   | flag   |
|--------|--------|
| SITE   | `0x02` |
| PLANT  | `0xFF` |
| STRING | `0xFF` |
| JAR    | `0x02` |

**Padding note:** in real records, bytes `0x48`–`0x6B` (the tail of the 50-byte
name field onward, up to `count`) are filled with `0xFF`, not `0x00`. This
generator matches that.

## 7. SITE / PLANT / JAR records
These use only the common header (§6). Nothing beyond it.

- **SITE**: `parent = null`. `child` → first PLANT. `next` → next SITE (top-level
  sites chain via `next`). flag `0x02`.
- **PLANT**: `parent` → SITE. `child` → first STRING. flag `0xFF`.
- **JAR**: `parent` → STRING. `child` → MEAS or `null` (null for a template). flag
  `0x02`. `count` = 0. Name e.g. `BAT1`.

## 8. STRING record (246 bytes) — the important one
A STRING has the common header (§6) **plus** an extended battery-assignment /
test-settings block. It links a string to a specific battery model and carries
the test parameters.

| Offset | Size | Field                     | Notes                                        |
|-------:|-----:|---------------------------|------------------------------------------------|
| `0x00`–`0x6D` | — | common header       | type=3, links, name                          |
| `0x6E` | 1    | marker                    | `0x06`                                        |
| `0x6F` | 4    | padding                   | `0xFF FF FF FF`                               |
| `0x73` | 20   | tech-id name              | ASCII, null-padded (`char[0x14]`)            |
| `0x87` | 7    | config block              | see below; default `01 04 01 00 01 00 00`    |
| `0x8E` | 2    | reference conductance     | `u16 LE`                                      |
| `0x90` | 14   | threshold block           | see below; default `0C 00 1E 0A 3C 46 98 3A 90 33 00 00 3C 46` |
| `0x9E` | 20   | manufacturer name         | ASCII, null-padded (`char[0x14]`)            |
| `0xB2` | 32   | model name                | ASCII, null-padded (`char[0x20]`)            |
| `0xD2` | 36   | model GUID                | ASCII UUID `8-4-4-4-12`, uppercase           |

### 8.1 Config block (`+0x87`, 7 bytes)
| Offset | Size | Field           | Notes                                          |
|-------:|-----:|-----------------|-------------------------------------------------|
| `0x87` | 1    | test type       | `0`=Voltage Only, `1`=Volts and Cond, `2`=Volts then Cond |
| `0x88` | 1    | fixed           | always `0x04`                                    |
| `0x89` | 1    | tests per jar   | `1`-`3`                                          |
| `0x8A` | 1    | straps per jar  | `0`=None, `1`-`3`                                |
| `0x8B` | 1    | fixed           | always `0x01`                                    |
| `0x8C` | 1    | fixed           | always `0x00`                                    |
| `0x8D` | 1    | fixed           | always `0x00`                                    |

### 8.2 Threshold block (`+0x90`, 14 bytes, all multi-byte fields little-endian)
| Offset | Size | Field                | Notes                          |
|-------:|-----:|----------------------|---------------------------------|
| `0x90` | 2    | jar voltage          | `u16`, Volts -- **only** `2, 4, 6, 8, 10, 12, 16, 18` are accepted |
| `0x92` | 1    | temp alarm upper     | `u8`, degrees **Celsius**      |
| `0x93` | 1    | temp alarm lower     | `u8`, degrees **Celsius**      |
| `0x94` | 1    | String G alarm       | `u8`, %                         |
| `0x95` | 1    | String G warn        | `u8`, %                         |
| `0x96` | 2    | voltage alarm upper  | `u16`, millivolts               |
| `0x98` | 2    | voltage alarm lower  | `u16`, millivolts               |
| `0x9A` | 2    | reserved             | always `0x0000`                 |
| `0x9C` | 1    | Jar G alarm          | `u8`, %                         |
| `0x9D` | 1    | Jar G warn           | `u8`, %                         |

This tool's GUI collects temperature in Fahrenheit (more familiar to a US-based
technician) and converts to Celsius for storage: `C = round((F - 32) * 5 / 9)`.

**Default voltage alarm thresholds by nominal jar voltage** (upper = 1.25x
nominal, lower = 1.10x nominal; stored as explicit millivolts, so any value is
technically representable, but the GUI only lets you pick one of the eight
nominal voltages below and auto-fills its confirmed defaults):

| Jar Voltage | Upper (V) | Lower (V) | Upper (mV) | Lower (mV) |
|------------:|----------:|----------:|-----------:|-----------:|
| 2 V  | 2.500  | 2.200  | 2500  | 2200  |
| 4 V  | 5.000  | 4.400  | 5000  | 4400  |
| 6 V  | 7.500  | 6.600  | 7500  | 6600  |
| 8 V  | 10.000 | 8.800  | 10000 | 8800  |
| 10 V | 12.500 | 11.000 | 12500 | 11000 |
| 12 V | 15.000 | 13.200 | 15000 | 13200 |
| 16 V | 20.000 | 17.600 | 20000 | 17600 |
| 18 V | 22.500 | 19.800 | 22500 | 19800 |

## 9. MEASUREMENT record (60 bytes, type `5`) — decoded, but not built by this tool
Present only for jars that have been tested (a tested jar's `child` points to
one). A **template** does not emit these — keep jar `child = null`. Decoded
from real measurement records; documented here for *reading* result files,
even though `cdo_format.py` deliberately doesn't build or re-pack them (see
below).

| Offset | Size | Field | Notes |
|-------:|-----:|-------|-------|
| `0x00` | 1  | type        | `5` |
| `0x01` | 4  | prev        | region offset or `0xFFFFFFFF` |
| `0x05` | 4  | next        | region offset or `0xFFFFFFFF` |
| `0x09` | 4  | parent      | region offset of the JAR this measurement belongs to |
| `0x0D` | 5  | timestamp   | packed date/time; encoding not fully decoded (only remaining unknown — irrelevant to authoring templates) |
| `0x14` | 2  | conductance | `u16 LE` — measured conductance |
| `0x16` | 2  | voltage     | `u16 LE` — measured voltage, millivolts |
| `0x18` | 1  | temperature | `u8` — degrees Celsius |
| `0x19` | 1  | (unknown)   | typically `0x00` |
| `0x1A`–`0x3B` | 22 | padding | `0xFF` |

**Note:** unlike SITE/PLANT/STRING/JAR, a MEASUREMENT record does **not** use
the common header layout (§6) — it's only 60 bytes total, smaller than the
110-byte common header. Offset `+0x0D` onward is measurement data, not a
`child`/`id`/`flag`/`name` block; only `prev`/`next`/`parent` are links.

`cdo_format.py` refuses to parse a JAR whose `child` pointer is non-null —
this tool is for authoring un-tested site templates only. Editing an existing
tested database is out of scope, so it never needs to re-pack MEASUREMENT
bytes.

## 10. Confirmed vs. unconfirmed
**Confirmed (matches real `.CDO` files byte-for-byte, verified against
single-site, multi-site, differential-config, and multi-plant exports, plus a
full hardware import→export round-trip):** signature, header, the region
pointer model, the SITE/PLANT/STRING/JAR record layouts and sizes, flags, the
`0xFF` tail padding, the `count` (jar-count) propagation rule (§11), the
STRING `id` formula (§6), the STRING battery block (tech-id, conductance,
manufacturer, model, GUID), the config/threshold block field layouts
(§8.1/§8.2), header byte `+0x00` being import-ignored, and the MEASUREMENT
record's conductance/voltage/temperature fields (§9).

**Additionally confirmed by the hardware round-trip specifically** (device
import followed by device export, byte-diffed against the original):
- **STRING `id`** survives round-trip unchanged.
- **Header `+0x00`** is genuinely ignored and rewritten by the device on its
  own export (`0x0A` in → `0x47` out), confirming it's never load-bearing.
- **Jar count up to the device maximum of 240 jars per string** imports and
  re-exports intact, with every other byte unchanged.
- **Fahrenheit↔Celsius** temperature storage survives round-trip (values are
  kept in °C on-device and in the file; only the GUI's display unit differs).
- Every test-type / tests-per-jar / straps-per-jar / voltage / threshold
  combination exercised round-tripped with no normalization, drop, or
  rejection.

**Remaining minor unknown (does not affect authoring or import):**
- The exact encoding of the 5-byte MEASUREMENT **timestamp** (`+0x0D`, §9).
  Only relevant if generating synthetic test results, which template
  authoring never does.

## 11. `count` field for multi-plant / multi-string trees
`count` (jar count, `+0x6C`) **propagates the first string's jar count up the
tree — it is never a sum or total.** Confirmed against real multi-plant
exports.

- On a **STRING**: its own jar count (e.g. a 6-jar string → `6`).
- On a **PLANT**: the jar count of the plant's **first string**.
- On a **SITE**: the jar count of that site's first plant's first string.
- On a **JAR**: `0`.

Worked example — a site with plant A (strings hold 4 jars) and plant B (a
6-jar string): `SITE = 4`, `PLANT A = 4`, `PLANT B = 6`, each STRING = its own
count. For the common single-string-per-plant case this degenerates to "same
count everywhere," which is what simple templates produce — so existing
single-string templates are unaffected by this rule.

## 12. Multiple sites
Top-level sites chain via `next`. To emit several sites, lay out every node
sequentially, then set each SITE's `next` to the region offset of the following
SITE (last SITE `next = null`). The same packing/pointer rules apply throughout.
This tool's traversal order for offset assignment is pre-order depth-first:
site, then each of its plants in order, then each plant's strings in order,
then each string's jars in order — repeated for each top-level site in list
order.

## 13. Operational notes
- Import is **destructive** — it erases the existing on-device database. Back up
  (export) first.
- The battery referenced by a STRING should be consistent with what the
  device's own battery library expects (manufacturer/model/GUID).
