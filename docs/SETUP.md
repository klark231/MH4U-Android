# Setup: getting your own files

You need two files from **your own** copy of the game:

| File | What it is |
|---|---|
| `exheader.bin` | NCCH extended header (0x400 or 0x800 bytes). Describes where each code segment is loaded in memory. |
| `code.bin` | The game's executable code from ExeFS (`.code`). Retail titles usually ship it BLZ-compressed; our tooling decompresses it automatically. |

## 1. Dump your cartridge / installed title (decrypted)

On a 3DS with custom firmware, **GodMode9** can dump a game card or an installed title as a *decrypted* `.3ds` / `.cxi`. Refer to the GodMode9 documentation and the 3DS homebrew guides for the exact steps for your setup. Dump the **base game**; updates (patches) are separate titles and can be handled later.

Only dump games you own.

## 2. Extract the exheader and ExeFS

Use the bundled extractor. It reads decrypted `.3ds`/`.cci`, `.cia` and `.cxi` files, and refuses encrypted ones (it never handles keys):

```bash
# The update CIA carries the complete code of the final version: this is what we decompile.
python3 tools/extract_ncch.py "<update>.cia" -o baserom/update_v130

# The base cartridge is only needed for assets (RomFS), later.
python3 tools/extract_ncch.py "<game>.trim.3ds" -o baserom/base --romfs
```

Each output folder gets `exheader.bin`, `code.bin` (still compressed, which is fine), `exefs.bin` and `info.txt`.

> **Which code to use:** a 3DS update replaces the whole ExeFS, so the update title's `code.bin` is the full executable of the patched game. The project targets **USA v1.3.0** (see `config/versions.json`). Update RomFS files are applied on top of the base RomFS.

`baserom/` is git-ignored. **Never commit it.**

## 3. Record which version you have

Run:

```bash
python3 tools/verify_dump.py baserom/update_v130/exheader.bin baserom/update_v130/code.bin
```

It prints the title ID, region-relevant fields and SHA-1 hashes. If your version isn't listed in `config/versions.json`, open an issue with that output (hashes only — **never upload the files**).

## 4. Load into a disassembler

```bash
python3 tools/codebin2elf.py --exheader baserom/update_v130/exheader.bin --code baserom/update_v130/code.bin -o build/mh4u_v130.elf
```

Open `build/mh4u_v130.elf` in **Ghidra** (processor: `ARM:LE:32:v6`, the 3DS's ARM11 cores are ARMv6K). Segments `.text`, `.rodata`, `.data` and `.bss` will be mapped at their real runtime addresses.
