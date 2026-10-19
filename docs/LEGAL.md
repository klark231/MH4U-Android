# Legal

Monster Hunter 4 Ultimate is © CAPCOM. Nintendo 3DS is © Nintendo. This project is not affiliated with, endorsed by, or sponsored by either company.

## Ground rules

1. **No copyrighted material in the repository.** That means no ROM images (`.3ds`, `.cci`, `.cia`, `.cxi`), no `code.bin`, no exheader dumps, no RomFS files (models, textures, audio, text, `.arc` archives), no system firmware, and no encryption keys — not even small excerpts.
2. **Bring your own dump.** Every contributor must dump a game they legally own using their own hardware. We do not link to, host, or help locate pirated copies. Issues asking for ROMs will be closed.
3. **Clean-room friendly contributions.** Do not paste code from leaked SDKs or proprietary sources. Use publicly available documentation (e.g. 3dbrew) and your own reverse-engineering.
4. **The future PC port will require the user's own dump at runtime** to supply assets. The port's binaries will contain only original project code.

## Why the repository is structured this way

Decompilation projects that stay sustainable (and avoid takedowns) follow the same pattern: original source code written by contributors, plus tooling that extracts everything else from a user-supplied copy at build time. `.gitignore` is configured to keep dumps and build output out of git.

*This is not legal advice. If you are unsure whether something may be committed, ask in an issue before committing it.*
