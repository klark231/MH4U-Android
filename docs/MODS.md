# Mods

Azahar loads a game's mods from one folder, `load/mods/0004000000126300/` in its user folder (right-click the game → *Open Mods Location*; the v1.3.0 update uses the base game's folder). Many mods at once means merging them by hand into that one folder, and two mods that replace the same file silently overwrite each other there.

`tools/mods.py` does that merging. It keeps a **mod library**, one folder per mod, in an order in which later mods win, shows which files two mods both change, and puts the enabled mods' files into Azahar's folder for you. It never touches files it didn't put there unless you tell it to. It uses only Python's standard library, like the other tools, and works the same on Windows, Linux and macOS.

The port's launcher drives it from its Mods screen (each command has a `--json` output for that), which also has a [catalog](#the-catalog-recommended-mods-and-texture-packs) of recommended mods and texture packs; it is a command too:

```bash
# Linux, macOS (from the repository, or the package's mh4u-tools folder)
python3 tools/mods.py list

# Windows
py tools\mods.py list
```

Folders: the library is `mh4u-mods/` in Azahar's user folder (`%APPDATA%\Azahar` on Windows, `~/.local/share/azahar-emu` on Linux, `~/Library/Application Support/Azahar` on macOS). `--user-dir DIR` points at another Azahar user folder (a portable setup), `--library DIR` at another library.

## Installing mods

```bash
python3 tools/mods.py add "Some mod 1.0.zip"     # a .zip, unpacked into the library
python3 tools/mods.py add path/to/some-mod        # or a folder, copied into it
python3 tools/mods.py list                        # the mods, on or off, in order
python3 tools/mods.py deploy                      # put the enabled ones in Azahar's mods folder
```

Then start the game. Nothing changes in Azahar's folder until `deploy`; `list` and `status` say when it is out of date.

- `add PATH|ZIP` puts a mod in the library, turned on and last in the order (so it wins over the others). Its name is the `name` in its `mod.json`, else the folder's or the zip's name; `--name` chooses another. `--disabled` adds it turned off. Adding a mod of the same name again is refused (capital letters don't count: on Windows and macOS `Mod` and `mod` are one folder); `--replace` updates it in place instead, keeping its place in the order and whether it is on.
- `enable NAME`, `disable NAME` turn a mod on and off; `remove NAME` deletes it from the library. NAME is its folder in the library or its `mod.json` name, in any capitals.
- `order NAME POSITION` moves a mod: `1` (or `first`) is first, the number of mods (or `last`) is last. **Later mods win.**
- `deploy` puts the enabled mods into Azahar's mods folder, `undeploy` takes them out again (for a game without mods; turning nothing off).
- A folder you put into the library yourself also counts: it shows in `list` as new and off, and `enable` turns it on.

`.zip`, `.7z` and `.tar` (`.tar.gz`, ...) archives are unpacked (`.7z` with the tools' own reader, [`tools/sevenzip.py`](../tools/sevenzip.py): LZMA2, LZMA, BCJ/ARM filters, Deflate, BZip2; not encrypted ones, BCJ2 or PPMd); unpack a `.rar` yourself and add the folder. A zip is refused when one of its paths would land outside the mod's folder (`..`, an absolute path, a drive letter), when a name isn't allowed on Windows (`aux.arc`, `a:b`), when two names differ only in capitals, or when it unpacks to more than 8 GB (4 GB per file). Symbolic links in it are skipped. Password-protected zips aren't supported.

## The catalog: recommended mods and texture packs

The launcher's Mods screen has a **Catalog**: mods and texture packs picked for the port, each installed with one click from its author's own download. It is [`tools/mods_catalog.json`](../tools/mods_catalog.json), which comes with the tools; the launcher also fetches the newest one from this repository when it starts (mh4u-app.ini's `[Mods] catalog_url`; empty: never) and uses whichever is newer. The catalog holds only descriptions and links: nothing in it is the game or comes from it.

| | Author | What | Install |
|---|---|---|---|
| **Monster Hunter 4 Ultimate HD** (recommended) | mhondeck and the MH classic HD Project | The whole game in HD: environments, armor, weapons, monsters, interface. v3.0 and its latest roll-up (3.03), USA build. About 12 GB. | direct (two `.7z` from the author's Dropbox) |
| MH4HDUI | xl3lackout | The interface redrawn at 4x (HUD, icons, fonts); CC0. The HD pack already has an updated version: one or the other. | direct (GitHub) |
| 100% crafting success | anco | Every item combination succeeds. A code patch that changes only data. LGPL-3.0. | direct (Codeberg) |
| Charge Blade: Fast Morph Guard Point | JackUnderTheHood | A Guard Point on the Fast Morph Slash. | direct (GameBanana) |
| Weapon Honing: Life rework | JackUnderTheHood | Life heals like MHW's Health Regen augment; includes both Charge Blade Guard Point mods. | direct (GameBanana) |

Texture packs need **custom textures** on (Settings, or `custom_textures = on` in mh4u-app.ini); the launcher offers to turn them on. The HD pack's author notes that the monsters' HD textures show only with Azahar's New 3DS mode off. The app turns New 3DS mode off by itself while custom textures are on (`new_3ds = auto` in mh4u-app.ini); in the port's Azahar, untick New 3DS mode in *Emulation → Configure → System*. The two Guard Point mods change the game's code and add new code after it (a "code cave", see [below](#code-mods-and-the-code-library)): with one of them on, build the code library again (the launcher offers to); until then the game runs on Azahar's own CPU emulation.

```bash
python3 tools/mods.py catalog                        # the catalog, and what of it is installed
python3 tools/mods.py catalog --refresh              # with the newest one from the web
python3 tools/mods.py install-from-catalog mh4u-hd   # download, check, unpack, install
python3 tools/mods.py remove mh4u-hd                 # and take it out again
```

`install-from-catalog ID` downloads into the library's `.downloads/` folder (a download that stopped goes on from there the next time), checks the size and SHA-256 when the catalog gives them, unpacks it with the same checks as a mod's zip (nothing outside its folder, names Windows can't have left out, the free space checked first), and then:

- a **texture pack** goes into Azahar's texture folder for the game, `load/textures/0004000000126300/` in its user folder, with its `pack.json` at the top as Azahar wants it. The library's `textures.json` remembers its files, so `remove` takes out exactly those. Files of another texture pack, or files you put there yourself, are not replaced unless you say so (`--force`); two packs the catalog says don't go together are refused.
- a **mod** joins the mod library like `add` (on, last in the order, or in place of the version installed before); its `mod.json` is the catalog's description, with the catalog's id.

The downloaded archives are deleted once installed (`--keep-download` keeps them). A `.rar`, or a `.7z` this can't read, is kept, and the command says where it is and where its files go (error `manual_extract`; the launcher opens both folders).

`install URL|ARCHIVE|FOLDER` does the same for anything else: a mod (`--name`, `--replace`, `--disabled` as for `add`), or a texture pack with `--textures`. `add` takes `.7z` and `.tar` archives too.

### Suggesting a mod for the catalog

Open an issue or a pull request that adds an entry to `tools/mods_catalog.json` (and raise its `updated` date). A mod belongs in it when it works with the port (USA, title 0004000000126300, with the update the port's code is built from; texture packs through Azahar's custom textures), when its author shares it publicly with a direct download (GitHub releases, GameBanana, Codeberg, a Dropbox or Drive link of the author's own), and when it neither contains the game nor needs anything that isn't yours. A download behind a sign-in or a click-through (Nexus Mods, Patreon) can still be listed, with no `downloads`: the launcher then shows only *Open its page*. `python3 -m pytest tests/test_mods_catalog.py` checks the file. The launcher fetches the catalog from `main` on every player's PC, so entries are reviewed by the maintainer: link the author's own download at a fixed version (a release or a commit, never a branch), with its `sha256`.

An entry's fields (`id`, `kind`, `name` and `page_url` are needed; the rest is optional):

| Field | Meaning |
|---|---|
| `id` | lower case letters, digits, `.`, `_`, `-`; never changes (installs are remembered by it) |
| `kind` | `textures` (into the texture folder) or `mod` (into the mod library) |
| `name`, `author`, `version`, `category` | as shown; a newer `version` than the installed one shows *Update* |
| `description`, `details`, `note` | a sentence or two; more; a warning worth reading first |
| `region`, `region_note` | `USA`, and which version or update it is made for |
| `license` | its licence, or what its author allows |
| `page_url` | its own page (https) |
| `recommended` | `true` shows the *Recommended* badge (the HD texture pack) |
| `needs` | `custom_textures` (the launcher offers to turn them on), `cpu_emulation` (runs only on Azahar's own CPU emulation) |
| `conflicts` | ids of entries it doesn't go with: installing it while one of them is installed is refused |
| `size`, `installed_size` | bytes to download, and once installed (shown, and checked against the free space) |
| `downloads` | the files, in order: `url` (https), `file` (its name in `.downloads/`), `size` and `sha256` (checked when given; **required for every download of a `mod`**, since a code patch becomes native code on the player's PC: the tools leave out a `mod` entry without them; a texture pack may leave it out for a file its author replaces in place, but should point at a fixed version when it can), `archive` (`auto`, `zip`, `7z`, `tar`, or `file` for a single file), `root` (the folder in the archive to install, also found inside one wrapping folder), `into` (a folder to put it in), `as` (for a single file: where it goes in the mod, e.g. `exefs/code.ips`), `delete_first` (paths to remove from what the earlier downloads unpacked, e.g. a roll-up's `UI`) |

Without `root`, a texture pack is looked through a `load/textures/<title id>/` or `<title id>/` folder, or a folder that wraps its `pack.json`; one for another title (the EU `0004000000126100`) is refused. A mod is found as `add` finds it.

## What a mod can look like

Mods come in a few layouts, and none of them needs a `mod.json`:

- **Azahar's own layout**, what you would put in *Open Mods Location*: `romfs/` with files that replace or add game files, `romfs_ext/` with `<file>.stub` (deletes that file) or `<file>.ips`/`.bps` (patches it), `exefs/` (or the top of the mod) with `code.bin`, `code.ips`, `code.bps`, `banner.bnr`, `icon.bin`, `logo.bcma.lz`, and `exheader.bin`.
- **A title folder**: the same inside `0004000000126300/` (or the update's `0004000E00126300/`), or inside `luma/titles/0004000000126300/`, the layout of mods made for a 3DS with Luma3DS. A mod with folders only for other games or regions is refused: the port plays MH4U USA.
- **The game's own folders**, without `romfs/` around them: `data/...`, or a language folder such as `eng/data/...`. They go into `romfs/`.
- Any of these inside one wrapping folder (`Some mod v1.0/romfs/...`), as zips often have, perhaps with a readme next to it.

Readmes, pictures and the like are left out. Names are matched without regard to capitals (`RomFS/` is `romfs/`), and the game's folders are given their own spelling (`Data/` becomes `data/`), since the game finds its files only by their exact names. Where Azahar would take only one file of a group, the mod manager deploys only that one and says so: `exefs/code.ips` before `exefs/code.bps`, `code.ips` and `code.bps`; `exefs/banner.bnr` before `banner.bnr`.

Patches and `.stub` files can sit in folders like the game's own (`romfs_ext/data/x.arc.ips` patches `data/x.arc`): upstream Azahar reads only the top of `romfs_ext/`, and the port's Azahar reads its sub-folders too, which MH4U's archives need (they are in `data/` and `eng/data/`). With an Azahar without that fix, such a file does nothing.

## Order and conflicts

Two enabled mods conflict when they change the same game file. The later one in the order wins, and its file is the only one deployed. A replacement in `romfs/` and a patch or `.stub` for the same file in `romfs_ext/` count as the same file, as do `exefs/banner.bnr` and `banner.bnr`.

```bash
python3 tools/mods.py conflicts          # each file changed by more than one enabled mod, and the winner
python3 tools/mods.py conflicts --all    # the same among all the mods, on or off
python3 tools/mods.py order "English menus" last
```

**Capitals.** `data/example.arc` and `data/EXAMPLE.arc` are one file on Windows and macOS, and the mod manager treats them as one everywhere, so the result is the same on every system: one conflict, the later mod's file, with its spelling. When mods spell a folder differently, the later mod's spelling is used and `list` warns, because the game's names are case-sensitive and only its own spelling works.

## Deploying

`deploy` makes Azahar's mods folder hold exactly the enabled mods' files:

- It **hard-links** each file to the library (no copy, no extra disk space) when it can, and copies it otherwise (another drive, a FAT or exFAT drive; `--copy` always copies). A hard link is the same file in two places: editing a deployed file edits the library's.
- It remembers what it put there in `mh4u-mods-deployed.json` in that folder. Later deploys remove or replace only those files, and only while they are as deployed; a file you changed since is left alone and from then on counts as yours.
- **Files you put there yourself are kept.** When one of them is in the way (a mod would replace it, or Azahar would use it instead of a mod's: a `.stub` for the same file, another code patch), `deploy` changes nothing and lists them: move them into a mod, or `deploy --force` to replace them.
- It is quick when nothing changed, and running it twice changes nothing the second time. `deploy --dry-run` only says what it would do; `status` says the same, and more.

To keep mods you already installed in Azahar's folder by hand, make them a mod: `add` that folder (`--name "My old mods"`), then `deploy --force`, which replaces the hand-made files with the library's.

Deploy while the game isn't running: it reads the mods' files as it plays, and on Windows a file can't be replaced while it is open.

## Code mods and the code library

Code mods (`code.bin`, `code.ips`, `code.bps`) change the game's code. Azahar applies only one code patch, on top of at most one `code.bin`, so **only one code mod can be on at a time**: `enable` refuses a second one and names the first, and `add` adds a second one turned off.

The port runs the game's code recompiled for your PC (the code library, [PLAYING.md](PLAYING.md) step 1), and it runs it only when it is built from the same code that Azahar loads. So:

- A code mod that only changes the game's **data** (such as the crafting patch of [anco's MH4U quality-of-life project](https://codeberg.org/anco/mh4u_qol)) works as it is.
- One that changes the game's **code** needs the code library built again with it in place: deploy, then build (`tools/build_game.py --install`, or the package's build script, which apply the code mods in Azahar's folder). Build again after adding, changing or removing such a mod. Without that, the port runs the game on Azahar's own CPU emulation and says why when the game starts.
- One that **adds new code** after the game's own, in the padding between the end of the code and the read-only data (the rest of the code's last page, a "code cave" that the code it patches branches to, like JackUnderTheHood's Guard Point mods), is the same: `build_game.py` then recompiles that page to its end too (it says `recompiling 0x... bytes (to the end of the code's last page)`), and the library checks and runs the new code with the rest. The game's own padding is zeros, so without such a mod nothing changes.

`list` shows for each code mod whether it changes the code or only data, read from the patch itself (the offsets it writes, compared with the size of the code segment in `config/versions.json`); for a whole `code.bin` that can't be told without the game's code. `status` (and `deploy`) says whether the code library needs building again:

| `status` says | Meaning |
|---|---|
| `ok` | built with the code mods now in Azahar's folder, or both those and the ones it was built with change only data |
| `rebuild` | the code mods changed since it was built (or the code mod adds new code after the game's own and the library was built by a `build_game.py` that left that out): build it again |
| `unknown` | it was built without a record of its code mods (by an older `build_game.py`, or the record is from another build) |
| `missing` | no code library: [PLAYING.md](PLAYING.md) step 1 |

The record is `recomp/0004000000126300.build.json` next to the library, written by `build_game.py --install` (with `code_size`, how much of the code segment it recompiled). `status` also notices Luma's code patch (`sdmc/luma/titles/0004000E00126300/code.ips`), which Azahar applies when the mods folder has none but `build_game.py` doesn't: move it into a mod instead.

## For mod authors

The simplest mod to install is a zip of Azahar's layout with a `mod.json` next to it:

```
My Mod 1.0.zip
  mod.json
  romfs/data/example.arc
  readme.txt
```

`mod.json` says what the mod is; every field is optional:

```json
{
  "name": "My Mod",
  "version": "1.0",
  "author": "you",
  "description": "What it changes, in a sentence or two.",
  "url": "https://example.org/my-mod",
  "contains": ["romfs"]
}
```

- `name` is how players see it and name it in commands; the library folder is named after it.
- `contains` lists what the mod has, out of `romfs` (files that replace or add game files), `romfs_ext` (patches or deletions of game files), `code` (`code.bin`, `code.ips`, `code.bps`), `exefs` (`banner.bnr`, `icon.bin`, `logo.bcma.lz`) and `exheader`. The mod manager reads the files themselves and warns when they don't match it.
- `root` names the folder inside the mod that holds its files, for a layout it doesn't recognise by itself (`"root": "files"`).

Make code patches for the USA v1.3.0 update's `code.bin` (the port's code), and say in the description whether they change code or only data.

## For the launcher (`--json`)

With `--json` (before or after the command) each command prints one JSON object: `"ok": true` and `"command"` with the result, or `"ok": false` and `"error": {"code", "message", ...}` (exit status 1; 2 for a usage error). `list`, `add`, `remove`, `enable`, `disable` and `order` return the library: `mods` (in order; each with `id`, `name`, `version`, `author`, `description`, `url`, `position`, `enabled`, `new`, `layout`, `contains`, `files`, `code`, `wins`, `loses`, `path`, `warnings`, `errors`), `conflict_count`, `deploy_state` (`up_to_date`, `out_of_date` or `blocked`), `problems`, `warnings` and, for the commands that change it, `messages`. `conflicts` returns `conflicts` (`path`, `mods`, `winner`, `files`); `deploy`, `undeploy` and `status` return `deploy` (`state`, `add`, `update`, `remove`, `in_the_way`, `changed`, `foreign`, `problems`) and `code_library` (`state`, `reason`, `code_files`, `changes_code`, `adds_code`). `catalog` returns `catalog` (`source`: `bundled`, `cache`, `remote` or `file`; `updated`), `entries` (the fields above, plus `direct`, `installed` (`as`, `id`, `version`, `enabled`, `files`, or null), `update` and `downloaded`), `textures` (the texture packs installed), `textures_folder` and `downloads_folder`. `install-from-catalog` and `install` return a texture pack's `pack`, `files`, `replaced`, or a mod's library as `add` does, with `messages` and the entry's `needs`. Before their result, these two (and `add`) print **progress lines**, each `@` and one JSON object: `{"event": "step"|"download"|"unpack"|"install", "message": ...}` when a stage starts, then `{"event", "done", "total", "what"}` a few times a second (`total` null when not known); the launcher reads the last one for its progress bar and leaves them out of the result. Error codes include `not_found`, `exists`, `layout`, `zip`, `archive`, `code_mod_conflict`, `foreign_files`, `conflict`, `download`, `checksum`, `space`, `manual_extract` (with `download`, `target`, `page_url`), `page_only`, `other_title` and `io`.
