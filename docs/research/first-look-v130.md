# First look at USA v1.3.0 code

Observations from the decompressed `code.bin` of the v1.3.0 update (hashes in `config/versions.json`). Addresses apply to that version only.

## Layout
| Segment | Address | Size |
|---|---|---|
| text | `0x00100000` | `0x00CEC734` (~13.5 MB of code) |
| rodata | `0x00DED000` | `0x000D2D90` |
| data | `0x00EC0000` | `0x001C1B84` |
| bss | `0x01081B84` | `0x0009B5A4` |

- Exheader application title: `redgiant` (internal codename). Stack size `0x40000`.
- `.code` is BLZ-compressed (`0x8ABDCC` → `0xF82000` bytes); the decompressed size matches the exheader segment layout exactly.

## Entry point (`0x00100000`)
A typical 3DS crt0: a chain of `bl` calls (init, a `.bss` clearing loop at `0x00100024`, one `blx` into Thumb code at `0x001013B8`, …) ending in a branch at `0x00100020`. **Mixed ARM/Thumb code exists**, which Ghidra handles but the difftest harness must account for.

## Engine: MT Framework naming is all over the binary
- ~525 `c`-prefixed and ~363 `u`-prefixed class-name strings, e.g. `cResource`, `cSystem`, `cQuestBoard`, `cWeaponControl`, `uPlChar`, `uEffect2D`, and monster units `uEm001`, `uEm007`, `uEm009`, …
- In MT Framework, classes register themselves with a runtime type-info object (name, parent class, instance size). **Done:** 992 classes recovered with hierarchy, sizes and ids; see [mt-classes.md](mt-classes.md).

## Nintendo SDK
- `nn::` strings found so far mention `nn::fs`, `nn::camera`, `nn::y2r` (error/log messages like `[ROM] nn::fs::FileStream::TryInitialize`). No SDK source paths in the strings.
- SDK code still has to be identified by patterns (svc instructions, IPC command headers) rather than names.
