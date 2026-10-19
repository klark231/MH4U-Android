# FAQ

Short answers to the questions people ask most. The full guide is [`docs/PLAYING.md`](docs/PLAYING.md). If your question isn't here, open an issue.

## The project

**So it's just Azahar?**
The hardware side (system services, graphics, audio, network rooms) is Azahar's core, credited and GPL-3.0. The game's code is not emulated: it's recompiled to C and runs natively on your PC. That is what makes source-level fixes, 120 fps and code-cave mods possible, and it lets us replace more pieces over time. It's the same architecture as Zelda64Recomp. Details: [`docs/PORT_ARCHITECTURE.md`](docs/PORT_ARCHITECTURE.md).

**Is this legal?**
No game data is distributed: no ROM, no `code.bin`, no RomFS, no keys. You build everything from your own dump, on your own PC. It's the same stance as Azahar and every recompilation project. See [`docs/LEGAL.md`](docs/LEGAL.md).

**Where do I get the game?**
From your own cartridge or eShop purchase, dumped with GodMode9 on your 3DS. Where to get it otherwise is not discussed here.

## Setting up

**What do I need?**
- The game (USA), dumped as a decrypted `.3ds`, `.cci` or `.cxi`, and its v1.3.0 update as a `.cia`. GodMode9 can make both.
- A PC with Windows 10/11 x64 or Linux x64, or a Mac with Apple silicon (not tried yet).
- Python 3.10 or newer, which the setup uses to build the game's code.

The downloads are on the [releases page](https://github.com/CompaLarios18/MH4UltimateDC/releases). Details: [What you need](docs/PLAYING.md#what-you-need).

**Does it work with the European or Japanese version?**
Not yet. Only the USA version with the v1.3.0 update (title `0004000000126300`) is supported.

**Why does the first start take a while?**
The setup recompiles the game's code from your update and then compiles it for your PC: a couple of minutes to recompile, several more to compile. It happens once, and again only after you add, change or remove a code mod.

**Windows says "Bad Image ... 0xc0e90002"**
That's Smart App Control blocking unsigned files (Azahar's DLLs, the compiler, the game's code library). Turn it off in *Windows Security → App & browser control → Smart App Control settings*. Since the April 2026 update, it can be turned back on later without reinstalling Windows.

**Can I keep my 3DS progress?**
Yes. Back up the save on your console with Checkpoint, then copy its files into the game's save data folder while the game isn't running. Steps: [Your 3DS save](docs/PLAYING.md#your-3ds-save).

## Playing

**My PC shows 75 % speed**
That's measured emulation speed, not a setting: your PC isn't keeping up with the frame rate you asked for. Lower the `fps` target or the CPU clock in *Esc → Game* until it reads 100 %.

**Do save states work?**
No. The game's code runs natively, and its call stacks live on your PC's own stacks, which a save state can't capture. Save in the game. Cheats that change data work; cheats that rewrite code don't.

**Steam Deck?**
There's a Linux build, not tried on a Deck yet. If you try it, open an issue and tell us how it went.

**Mac?**
There's a build for Apple silicon (Vulkan through MoltenVK), not tried on a Mac yet. Reports are welcome in an issue.

## Online

**Can I play with people on regular Azahar?**
Yes. Rooms are compatible with official Azahar (2125 or later), and players don't need the same frame rate. Everyone needs the v1.3.0 update. Join a Gathering Hall one at a time: official Azahar gives two consoles that join in the same instant the same id, and the game drops one of them.

**Do I need to open ports?**
Only to host. Either forward UDP port 24872 on your router to your PC, or put everyone in one virtual network (Tailscale, ZeroTier, Radmin VPN), which needs no forwarding. Joining needs nothing. UPnP is planned. Details: [Multiplayer](docs/PLAYING.md#4-multiplayer).

## Mods

**The HD texture pack download fails**
The pack comes straight from its author's Dropbox, which pauses shared links for 24 hours after heavy traffic. Try again later: the download resumes where it stopped. And thank mhondeck for the pack.

## Bugs

**How do I report a bug?**
Open an issue, say what you did and what happened, and attach `mh4u_log.txt` from Azahar's log folder: `%APPDATA%\Azahar\log` on Windows, the `log` folder inside Azahar's user folder elsewhere. Close the game first: the log is written in a buffer, and it's overwritten the next time the game starts.
