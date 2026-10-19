# How the game talks to the 3DS OS (USA v1.3.0)

Which system calls and system services Monster Hunter 4 Ultimate uses, where the session handles live, and what that means for the PC port: frame pacing, input, local play, files and sound. Addresses apply to the USA v1.3.0 code only.

Produced with `tools/find_services.py` (see the end of this page) plus manual reading of the functions it pointed at. Command names come from the public 3DS IPC documentation (3dbrew); every header the game sends was checked against those tables, and the few disagreements are listed under [Caveats](#caveats).

## Summary

- All OS access goes through the statically linked Nintendo SDK, which is **ARM code** (no `svc` exists in the 257 Thumb functions). It inlines the IPC sequence instead of calling a SendSyncRequest wrapper: **350 request sites in 350 small functions**, each one IPC command.
- **692 `svc` sites**: 350 SendSyncRequest, 178 CloseHandle, 50 GetSystemTick, 49 WaitSynchronization1, 14 ClearEvent, 11 SignalEvent and a handful of others. 20 small wrapper functions cover the rest (table below).
- **About 23 services** are used. `srv:` itself comes from ConnectToPort, and 21 names go through one `srv:GetServiceHandle` request function (`err:f`, the APT name and the BOSS sessions take other paths). Almost every session handle lives in a fixed global (table below), so a port can intercept services by address.
- **Frame pacing:** the engine is delta-time based. Its frame limiter (in the `sMain` base class) caps the loop at **60 fps** and measures time in 1/60 s units. The display code counts VBlanks per screen and waits for them at present time. Each screen also has a "present every n+1 frames" setting (`mDisplaySkip`, default 0). We found **no code that locks the game to 30 fps**: no second VBlank wait, no 33 ms sleep, and no write to the limiter's rate fields after construction. The likeliest explanation is that a frame simply costs more than 16.7 ms on the console, but this still needs a runtime measurement (see [the open question](#vblanks-per-frame-the-open-question)).
- **Input:** `hid:USER` shared memory is mapped once and read through the SDK readers, which `sPad::update` polls. The Circle Pad Pro goes through `ir:USER`.
- **Local multiplayer** uses `nwm::UDS` through Nintendo's NEX/NetZ middleware (`UdsNodeBase`, host migration), not directly from the `nNetwork` classes.
- **Files:** `fs:USER` with RomFS (archive 3 and 0x2345678A), save data (4), shared extdata (7), extdata creation and archive 0x567890B4.
- **Sound** talks to `dsp::DSP` directly (component load, pipes, semaphores). There is no `csnd:SND`.

## System calls

Inline sites dominate. The wrappers below are the only functions that exist just to issue one `svc`; the ones with an output store it through their first, pointer argument (confidence: high).

| Address | svc | Callers | Notes |
|---|---|---|---|
| `0x00105de8` | 0x01 ControlMemory | 2 | |
| `0x00105e0c` | 0x38 GetResourceLimit | 1 | |
| `0x00107320` | 0x21 CreateAddressArbiter | 1 | the arbiter goes to `0x010652ec` |
| `0x0010919c` | 0x17 CreateEvent | 16 | |
| `0x001091b4` | 0x08 CreateThread | 1 | priority and core passed on the stack |
| `0x001091d8` | 0x2B GetProcessInfo | 1 | |
| `0x001091f4` | 0x37 GetThreadId | 33 | |
| `0x0010b8c0` | 0x2D ConnectToPort | 2 | used for `"srv:"` |
| `0x0010b8d8` | 0x25 WaitSynchronizationN | 3 | |
| `0x0010d91c` | 0x35 GetProcessId | 3 | |
| `0x0010d934` | 0x0A SleepThread | 91 | entry is a `nop` that falls into the `svc` |
| `0x0010fc54` | 0x22 ArbitrateAddress | 8 | |
| `0x00111864` | 0x1E CreateMemoryBlock | 2 | |
| `0x00111880` | 0x13 CreateMutex | 2 | |
| `0x00111898` | 0x1A CreateTimer | 1 | |
| `0x001118b0` | 0x27 DuplicateHandle | 1 | |
| `0x001118c8` | 0x29 GetHandleInfo | 1 | |
| `0x001118e4` | 0x28 GetSystemTick | many | `nop` + `svc` + `bx lr`; Ghidra splits it at `0x001118e8` |
| `0x001118f0` | 0x0B GetThreadPriority | 5 | |
| `0x00111908` | 0x1B SetTimer | 1 | |

Tick helpers `0x00532174`, `0x0053aa38` and `0x005b261c` also wrap GetSystemTick, but they store the result into an object (Tick constructors). `0x0010c0e8` maps a shared memory block (MapMemoryBlock, size rounded up to whole pages) and is used for the GSP and HID shared memory.

## Sessions and their handle globals

`srv:` is reached with ConnectToPort(`"srv:"`) in `0x0010a110`, which also runs `RegisterClient`. All named services then go through the request function **`0x0010cc1c` (srv:GetServiceHandle, header `0x00050100`)**. It copies the 8-byte name into command words 1-2 through a small copy helper (`0x0010d7cc`) and stores response word 3 (the session) through its first argument. Most callers use `0x0010a234`, which checks that srv is initialised and the name is at most 8 bytes and then tail-calls the request. `0x00300f04` (strlen) computes the length argument.

| Service | Handle global | Opened in | Notes |
|---|---|---|---|
| `srv:` | `0x01057de4` | `0x0010a110` | |
| `fs:USER` | `0x01057648` | `0x0010665c` | copied to `0x01057d98` by `0x00107800`, which also sends InitializeWithSdkVersion (SDK version word `0x090B00C8`). Most FS requests use the copy |
| `gsp::Gpu` | `0x01057dbc`, used as `0x01057dc0` | `0x0056ca84` | name read from the pointer at `0x01057db0`; `0x0010f9ac` returns `&0x01057dc0` |
| `hid:USER` | `0x01064b50` | `0x00107c6c` | name taken from a table at `0x00e3be90` that also holds `"hid:SPVR"` |
| `APT:U` (name chosen at run time) | `0x01057cf0` | `0x00108680` | the name pointer comes from `.data` (`0x01057cc0`, filled from `0x01057cbc` = `"APT:A"`; `"APT:S"`/`"APT:U"` at `0x01057c94`/`0x01057c98`); identified by its commands |
| `cfg:u` (`cfg:s` and `cfg:i` are tried too) | `0x01057d20` | `0x00109d5c`, `0x0010c2c4`, `0x0010c340` | a second cfg session at `0x0107ed10`/`0x0107ed14` |
| `ptm:u` | `0x010652f8` | `0x001069f4` | closed by `0x00106a44` |
| `ndm:u` | `0x01064bb8` | `0x00107e50` | |
| `dsp::DSP` | `0x01057d70`, copy at `0x01057d6c` | `0x00484078` | `0x01057d64` holds the pointer `0x01057d6c`, which the request functions get as `Handle*` |
| `err:f` | `0x01057d7c` | `0x0010c548` | opened by the fatal-error path (reached from the error handlers `0x00109f4c`/`0x00109910`) |
| `ac:u` | `0x010588ac` | `0x00473e14` | |
| `am:app` | `0x01065360` | `0x0047492c` | DLC content queries |
| `nim:aoc` | `0x01065364` | `0x00474cac` | add-on content download |
| `ir:USER` | `0x01081a00` | `0x0047c1ac` | Circle Pad Pro |
| `act:u` | `0x01057c60` | `0x0047f69c` | |
| `cecd:u` | `0x0107ed08` | `0x004828a4` | a second CECD session `0x0107ed0c` uses the same commands (named by header) |
| `frd:u` | `0x01057d84` | `0x005771b4` | |
| `soc:U` | `0x01065314` | `0x005749c4` | plus a pool of per-thread sessions at `[0x01117a08]+8` whose name is copied into a buffer at run time |
| `nwm::UDS` | `0x01065338`, `0x0106533c` | `0x00564998` | |
| `y2r:u` | `0x01065358` | `0x005af780` | |
| `cam:u` | `0x01057cf8` | `0x00693504` | |
| `http:C` | object fields (e.g. `0x010ea094`/`0x010ea098`) | `0x00568024`, `0x00567b88` | per-context sessions |
| BOSS (SpotPass) | passed by pointer | not traced | 2 copies of a 12-command client at `0x00566cbc` and `0x0056783c`, identified by headers (GetNsDataHeaderInfo `0x002700C2`, ReadNsData `0x00280102`, their 0x0416/0x0417 variants…) |

No trace of `csnd:SND`, `mic:u`, `ldr:ro`, `news:*` or `qtm` (not even the name strings). `gsp::Lcd` and `hid:SPVR` exist as strings, but no traced request uses them.

## Request functions that matter for the port

Each address is a whole function that sends one command (handle global in parentheses).

**gsp::Gpu** (`0x01057dc0`): WriteHWRegs `0x0056d8d0`, WriteHWRegsWithMask `0x0056da4c`, ReadHWRegs `0x0056d87c`, FlushDataCache `0x0056d940`, SetLcdForceBlack `0x0056d9a4`, TriggerCmdReqQueue `0x0056d9dc`, SetAxiConfigQoSMode `0x0056da1c`, RegisterInterruptRelayQueue `0x0056dafc`, UnregisterInterruptRelayQueue `0x0056db4c`, AcquireRight `0x0010f908`, ReleaseRight `0x0010f94c`, SaveVramSysArea `0x0056d97c`, RestoreVramSysArea `0x0010f974`, ImportDisplayCaptureInfo `0x0056daac`. There is **no SetBufferSwap**: frames are presented through GSP shared memory, the usual SDK path.

**hid:USER** (`0x01064b50`): only GetIPCHandles `0x0010a010`. All input then comes from shared memory and events.

**fs:USER** (`0x01057648` / `0x01057d98`): InitializeWithSdkVersion `0x001095f0`, SetPriority `0x0010782c`, GetPriority `0x00110784`, IsSdmcDetected `0x001095b8`, OpenArchive `0x0010db0c`, CloseArchive `0x0010db6c`, OpenFileDirectly `0x0010db9c`, ControlArchive `0x0047b9fc`, FormatSaveData `0x0047ba68`, CreateExtSaveData `0x0047bb64`, DeleteExtSaveData `0x0047bbc0`, OpenFile `0x0047bbfc`, and (handle passed in, service inferred) CreateFile `0x0047b8b0`, DeleteFile `0x0047b90c`, GetFreeBytes `0x0047b95c`, OpenDirectory `0x0047b998`, CreateDirectory `0x0047bac0`, DeleteDirectory `0x0047bb14`. File sessions: Read `0x0047bcd8`, Write `0x0047bd5c`, GetSize `0x0047bdbc`, SetSize `0x0047bdf4`, Close `0x0047bd30`, SetPriority `0x0047bc70`, OpenLinkFile `0x0047bca0`. Directory sessions: Read `0x0047be24`, Close `0x0047be78`.

**nwm::UDS** (`0x01065338`): InitializeWithVersion `0x00565180`, Shutdown `0x005653f8`, BeginHostingNetwork `0x00564ebc`, DestroyNetwork `0x00564f10`, ConnectNetwork `0x00564f38`, DisconnectNetwork `0x00565014`, EjectClient `0x00564e84`, RecvBeaconBroadcastData `0x00565420`, ScanOnConnection `0x00564f9c`, DecryptBeaconData `0x0056527c`, SetApplicationData `0x005650ec`, GetApplicationData `0x0056503c`, UpdateNetworkAttribute `0x0056523c`, SetProbeResponseParam `0x005651f4`, GetNodeInformation `0x0056509c`, GetConnectionStatus `0x00565130`, GetChannel `0x00564de4`, Bind `0x005652f0`, Unbind `0x005653b0`, SendTo `0x0056533c`, PullPacket `0x00564e18`.

**dsp::DSP** (`[0x01057d64]`): LoadComponent `0x0010f6d4`, UnloadComponent `0x0010f734`, ForceHeadphoneOut `0x0010f75c`, RecvData `0x0048480c`, RecvDataIsReady `0x00484634`, SetSemaphore `0x004845c0`, SetSemaphoreMask `0x00484678`, GetSemaphoreEventHandle `0x00484760`, RegisterInterruptEvents `0x00484794`, WriteProcessPipe `0x004846b0`, ReadPipeIfPossible `0x004846f8`, ConvertProcessAddressFromDspDram `0x004847d0`, FlushDataCache `0x004845f8`.

**APT** (`0x01057cf0`): 31 commands, including Initialize `0x00108594`, Enable `0x0056f810`, GetLockHandle `0x001085e4`, InquireNotification `0x0010d334`, Send/Receive/Glance/CancelParameter `0x0010e590`/`0x0010fa68`/`0x0010e644`/`0x0010e5e8`, ReplySleepQuery `0x0010e6d0`, ReplySleepNotificationComplete `0x0010e714`, NotifyToWait `0x0010e558`, SleepSystem `0x0010d2fc`, JumpToHomeMenu `0x0056f4d0`, the library/system applet starters `0x0056f648`/`0x0056f5f8`, AppletUtility `0x0010f9ec`, SetAppCpuTimeLimit `0x0010b2a0` and GetTargetPlatform `0x00108640`. The port can turn almost all of these into no-ops. The HOME/sleep flow matters only for pausing.

Everything else is in the tool's output: 36 `cam:u` + 22 `y2r:u` (camera capture and YUV conversion, used by `sLtHID::CameraThread`/`YuvEncodeThread`), 26 `cecd:u` (StreetPass), 24 BOSS (SpotPass), 23 `frd:u`, 21 `http:C`, 18 `soc:U`, 16 `nim:aoc`, 15 `ac:u`, 12 `ir:USER`, 8 `am:app`, 5 `ndm:u`, 3 `act:u`, 3 `cfg`, 2 `ptm:u` (pedometer: GetStepHistory `0x0010cb94`, GetTotalStepCount `0x0010cbe0`), 1 `err:f`.

## Frame pacing

### GSP interrupt plumbing (confidence: high)

- **Init `0x0056ca84`** opens `gsp::Gpu` (above), calls AcquireRight with flags read from `0x00e3be84`, and points `0x01057dc8` at a GSP state block at **`0x00ec6c40`**.
- **`0x0056d24c`** creates the GSP event (block `+0x2c` = `0x00ec6c6c`). It registers that event with **RegisterInterruptRelayQueue `0x0056dafc`**, using flags 1 or 2, plus 4 when `0x0056db94` says so. It maps the returned GSP shared memory (mapped address at block `+0x40`), keeps the thread index at `+0x74`, and computes this thread's slices of shared memory:
  - interrupt queue `shm + index*0x40` (`+0x34`);
  - framebuffer info `shm + 0x200 + index*0x80` for the top screen and `+0x40` for the bottom (`+0x5c`/`+0x60`);
  - command queue `shm + 0x800 + index*0x200` (object at `+0x58`).

  Result code `0x2A07` (first registration) sets `+0x75`. It then starts the interrupt thread (handle at `+0x6c` = `0x00ec6cac`).
- **Interrupt thread `0x0056d43c`** waits forever on the GSP event, clears it, and pops interrupt IDs from the shared-memory queue with LDREX/STREX. For each ID, it calls the callback in a 7-entry table at block `+0x10` under the block's lock, then signals a "processed" light event at `+0x64`. `0x0056cc0c` waits for that light event, so it means "wait for the next processed interrupt". `0x0056d5dc` shuts the thread down (signal the event, wait for the thread).
- **`0x0056d7f4`** sets the callback for interrupt id < 7 and returns the old one. It is called only from the GX init **`0x00d81674`** (the SDK's nngxInitialize equivalent, 0xb20 bytes), which installs:
  - PSC0 `0x00d898e0`, PSC1 `0x00d898e4`;
  - **VBlank top (PDC0) `0x00d89850`**, **VBlank bottom (PDC1) `0x00d89894`**;
  - PPF `0x00d898dc`, P3D `0x00d89810` (advances command-list progress), DMA `0x00d898e8`.

  The PSC, PPF, P3D and DMA callbacks continue into a common handler at `0x00d898ec`.

### VBlank counting and waiting (confidence: high)

- The GX state block is at `0x0110ceb0` (bss). The two VBlank callbacks increment **per-screen VBlank counters at `0x0110d004` (top) and `0x0110d008` (bottom)** (`+0x154`/`+0x158`), modulo 0x7FFFFFFF. Each also calls an optional user callback stored at `+0x15c`/`+0x160` with the display id: 0x400 top, 0x401 bottom.
- **`0x00d82d00` is the VSync wait** (nngxWaitVSync equivalent). It takes 0x400 (top), 0x401 (bottom) or 0x402 (both), snapshots both counters, and blocks on `0x0056cc0c` until the selected counter or counters change. Nothing else reads the counters.
- **`0x00d824ac` sets the VSync callback** (display 0x400, 0x401 or 0x402).

### Present and buffer swap (confidence: high on the flow, medium on flag meanings)

- The render system is **sLtRender** (DTI `0x010e8670`, 0x8ab8 bytes; instance pointer `0x010572cc`; constructor `0x00bfbabc`; vtable around `0x00e6cab8`).
- Its graphics init **`0x00bfa42c`** does the following:
  - calls the GX init with allocator callbacks `0x00633864`/`0x00633824`;
  - creates 2 command lists (`0x00d8132c`, bind `0x00d80a7c`, storage `0x00d80d7c`);
  - sets display mode 0x700 or 0x702 depending on flag 0x40 (presumably mono or stereo 3D) through `0x00d82420`;
  - **installs `0x00bf66f0` as the VSync callback for both screens**;
  - sets an "LCD on" countdown of 3 frames (`+0x8a6c`) and flag bit 28 in `+0x6ba8`;
  - allocates 2 or 3 display buffers per screen (flag bit 1 of `+0x6ba4`), 400x240 for the top screen (left and right eye) and 320x240 for the bottom, through `0x0062e184`.
- **Per frame**, the render virtual **`0x00bfa670`** first flips the previous frame (**`0x00bf5f9c`**, only if byte `+0x8ab0` is set), then builds and runs the next command list in **`0x00bf672c`** (run: `0x00d82704`). `0x00bf672c` increments the frame counter `+0x6b90`, mirrors it to the global `0x010571d4` (read by about 70 functions), and sets `+0x8ab0`. **Display lags the CPU by one frame.**
- **`0x00bf5f9c`** calls a virtual (`+0x2c`) on the object at `[0x010572f0]`, presents, and rotates each screen's buffer index (`+0x6e00`/`+0x6e04`, 2- or 3-way). Then `0x00bf990c` handles display-mode changes and the LCD-on countdown. When the countdown ends, it calls `0x0056d220` and clears bit 28.
- **Present `0x00bf894c`** has three steps:
  1. `0x00bf6f20` takes optional timestamps (flag `0x400000`). If flag bits 1 and 2 are set, it waits for a VBlank on each screen whose last submitted buffer is not yet shown, then waits for the GPU (`0x00d82c64`).
  2. It hands buffers to **`0x00d82804`** (display, left address, right address, width, format; the shape of nngxSwapBuffersByAddress), then calls `0x00d80cec`. The top screen is handed over when byte `+0x8ab1` is set. The bottom screen is handed over when byte `+0x8ab2` is set, or when the scene byte at `[[0x0104dea0]+0x30]` is 4, 5 or 6. Both flags come from the per-screen present intervals below.
  3. It **waits one VBlank on both screens** (`0x00d82d00(0x402)`) when flag bit 0 of `+0x6ba4` or bit 28 of `+0x6ba8` is set.
- **Per-screen present intervals.** The render virtual `0x00bfa6f0` (vtable slot after the per-frame one) sets `+0x8ab1` (top) and `+0x8ab2` (bottom) to `frame_counter % (skip + 1) == 0`, using `+0x6b90` and `0x002ff298` (unsigned divide/modulo). `skip` is the 2-entry array at **`+0x8aa8`/`+0x8aac`**, which sLtRender's property-registration virtual `0x00bf685c` exposes under the name **`mDisplaySkip`**. The constructor sets both to 0, so every frame is presented on both screens. No direct writer was found; MT Framework's property system can still set them by name. A skip of 1 on the bottom screen would give a 30 Hz touch screen, so a reworked second-screen HUD should check this value at run time.
- **Other render properties** registered by `0x00bf685c` describe the knobs that matter for pacing: `mVSyncEdit`, `mTripleBufferRenderWaitEdit`, `mParallelRenderingEdit`, `mParallelExecute`, `mActiveDisplay`, `mULCDEnableEdit`, `mDitherDisplay0/1`, `mProfileRFPS`, `mFSAA_2D`, `mDithering`, `mUpsideToTopEdit`, `mYFlipEdit` (group `Config`). Their setters (pointer-to-member pairs in the table at `0x00e35a7c`) set or clear one bit each of the **render settings word at `sLtRender+0x6ba8`**: `0x1` mVSyncEdit (`0x00bfb8fc`), `0x4` mTripleBufferRenderWaitEdit (`0x00bfa2fc`), `0x8` mUpsideToTopEdit (`0x00bf88a4`), `0x10` mYFlipEdit (`0x00bfb91c`), `0x40` mULCDEnableEdit (`0x00bf66d0`), `0x100` mParallelRenderingEdit (`0x00bf98ec`), `0x400` mFSAA_2D (`0x00bfb93c`), `0x100000` mDitherDisplay0 (`0x00bf9398`), `0x200000` mDitherDisplay1 (`0x00bf93b8`), `0x400000` mProfileRFPS (`0x00bf7290`) (confidence: high for the setters, medium for which name each belongs to; the pairing follows the registration order). This is the word the community's quality cheats write: their USA v1.1 default `0x001F076E` and "Trinity Disabler" `0x000F432E` differ in `0x100000` (dithering, top), `0x400` (FSAA_2D), `0x40` (ULCD/stereo) and `0x4000` (not one of the named properties). The port exposes it as the experimental `render_flags_set` / `render_flags_clear` options. **The dither bits, tried (October 2026):** with `0x300000` cleared every frame the checkered dither pattern disappears from the picture (an expedition scene, software renderer). Azahar's renderers don't emulate PICA dithering at all (no code for it in `video_core`), so the pattern is the game's own drawing and scales up with the internal resolution. The port's `dither = off` option clears these two bits, and `stereo_3d = off` (the default) clears `0x40` (mULCDEnableEdit): the community's "3D off" cheat, which spares the second eye's pass of the top screen that the game does even with the 3D slider at 0 (October 2026: the maintainer saw a doubled, soft picture with the bit on and Azahar's 3D at 0). Clearing the Trinity Disabler's bits as well (`0x104440`) also turns the game's anti-aliasing off: the edges get jagged. The constructor also names a render thread, `sLtRender::ExecuteThread`, so command execution can run in parallel with the main loop.
- **The engine's VSync callback `0x00bf66f0`** only records, per screen, that the last submitted buffer index is now displayed (`+0x6e10`→`+0x6e08`, `+0x6e14`→`+0x6e0c`).
- `0x00bf8d1c` (present + wait) is used by the applet code `0x00ad4bac`/`0x00aedc28` (HOME menu, eShop; the strings `HbmEnable` and `ECommerce::WorkerThread` are nearby). `0x00bfc1b4` is the render shutdown.

### Main loop, frame limiter, delta time (confidence: high on mechanics)

- MT startup `0x001046d0` creates the main system from the **sMhMain** DTI `0x010b5240`. Its create hook is `0x00d045b4`, the constructor `0x00af55a4` (base constructor `0x00644968`), the vtable `0x00e6b814`, and the instance pointer `0x01057194`.
- **Main loop `0x00105acc`** calls vtable slot `+0x1c` (**`0x00af5250`**, one frame) until byte `+0x54` is set.
- **`0x00af5250`** stores the frame delta (`[sMain+0x5c]`, in frames) at `+0x348` and the delta in seconds (`/[sMain+0x2c]`) at `+0x34c`. It then calls **`0x006442f8`** and the update/draw virtuals of about 30 subsystems kept at `+0x288..+0x330`.
- **`0x006442f8` is the frame limiter.** It reads ticks with `0x001118e4`, gets the tick rate from `0x003e6d30`, and keeps:
  - measured fps at `+0x34`, refreshed every second;
  - the frame number at rate `[+0x2c]` at `+0x28`;
  - time accumulated in "rate units" at `+0x38`/`+0x40`.

  It has two modes:
  - **Fixed step** (byte `+0x56` set): sleeps in 1 ms steps (`0x0010d934` with 1,000,000 ns) until one frame at `[+0x2c]` Hz has passed, and sets delta = 1.0.
  - **Variable step** (the default; `+0x56` = 0): delta = elapsed frames at `[+0x2c]` Hz (written to `+0x5c`). While delta < `[+0x2c]/[+0x30]`, it sleeps the remainder in whole milliseconds (if ≥ 2 ms) and then spins.
- Constructor values: `+0x2c` = **60.0**, `+0x30` = 3000.0, which **sMhMain overrides to 60.0**; `+0x5c` = `+0x68` = 1.0; `+0x15c` = `+0x160` = 1. So **the engine caps at 60 fps and measures delta in 1/60 s frames**.
- Game code converts values authored per 30 fps frame with `30.0 / [sMain+0x2c]` (e.g. `0x001c20c0`, `0x001c8bf0`). Gameplay therefore scales with delta instead of counting frames, which is good news for 60 fps.
- An interpreter pass over all 31,409 known functions found **no write to `+0x2c`, `+0x30` or `+0x56` after construction**, whether through the singleton or through a pointer returned by a getter. Virtual calls were not followed.

### VBlanks per frame: the open question

| Candidate | Confidence | Reasoning |
|---|---|---|
| Frame cost alone: CPU and GPU time above 16.7 ms, quantised by the present-time VBlank waits | **medium** | The limiter allows 60. Per frame the game waits for at most "the previous buffer is on screen" plus one VBlank on both screens, so a frame that costs 17-33 ms shows as 30 fps. This fits the absence of any lock |
| `mDisplaySkip` (`sLtRender+0x8aa8/+0x8aac`) set to 1 at run time | low-medium | the mechanism exists and is exactly "present every 2nd frame". Its defaults are 0 and no code writer was found, but the property system can set it by name |
| A runtime change of the cap `[sMain+0x30]`, or fixed-step mode at 30 Hz, made through a virtual call | low | not seen statically; easy to check at run time |
| The present-time waits in `0x00bf6f20`/`0x00bf894c` taking two VBlanks per frame in steady state | low | by the logic above they add up to one VBlank, unless the swap is latched a VBlank later than the counters suggest |

To settle it, log per frame in Azahar (with the recompiled backend or a GDB stub): `[0x0110d004]` (top VBlanks), `[[0x01057194]+0x5c]` (delta), `[[0x01057194]+0x34]` (measured fps), `[0x010571d4]` (frames submitted), and `[[0x010572cc]+0x8aa8]`/`+0x8aac` (mDisplaySkip). If delta sits near 2.0 with no lock, the 60 fps work is about making each frame cheap enough and checking any logic that counts frames.

### Above 60 fps: what the game does at 120 Hz (runtime, October 2026)

With the cap at 120 and Azahar's LCD at 120 Hz (`fps = 120`, `refresh_rate = 120`), from a saved game: menus, loading, the real-time cutscene on the sand ship and gameplay on its deck run at 120 fps (delta 0.5). Walking for 1.5 s covers the same distance as at 60, so gameplay speed is right.

**Movies play at their own rate.** The opening film is a Mobiclip movie (`uMovie`, code `0x00962000..0x0096a000`: `uMovie::Player::AsyncReader::ReaderThread`, `uMovie::Player::Decoder::VideoDecodeThread`, "[uMovie Error] ..."). While one plays, the player paces the game loop to the movie's frame rate: `0x00966aa4` gets the target frame time from `0x009692ac` (33.3 ms for 29.97 fps), subtracts the elapsed time and arms a kernel timer (`svcSetTimer` on its handle at `+0x1c0`), and the frame ends waiting on it (`0x00969da0`, called at `0x00af5584` in `sMhMain::frame`). At 60 Hz every movie frame spans two refreshes and no timer is needed. So the game runs at 30 during a movie at any higher rate; that is intended, not a bug. An earlier version of this note took that film for the sand ship scene.

**A loading race at the start of a quest.** At 120 Hz with Azahar's usual asynchronous file reads, also when paced to real time, most runs from the saved game showed the hunter falling through a dark void after the loading screen, then "Quest Failed" and the opening film again. One run at 60 Hz with a 50% emulated CPU did the same. Each such run logged two reads of address 0 from code reached from the state machine `0x0065bcd0`. The chain, traced with temporary hooks:

1. When the quest is about to start, `0x00b7e240` (the quest flow's state machine, mode 3) calls `0x0069b21c(holder, 0)` and then `0x00b7c010` every frame, once the stage loader at `[0x00fb7190]` is ready (`+0x42c == 4`; that class loads `data\m%02d`, `data\m%02da%02dqr`, `data\gc_bg%02d`, `data\pl_mus%03d`). The holder is `[0x00fbeffc]`.
2. `0x0069b21c` builds a list of quest-file resources if there is none (`0x0069af14`). It asks the resource manager for one file per id of a table (`[holder+0xe604]`; for the caravan quests `0x00efce2c`, 207 ids: 100, 101, 102, 11, 12, …). It keeps those it gets: array `[holder+0xe608]`, count `[holder+0xe610]`. The list always came out one short (206), and which file was missing changed from run to run: 16 or 17 at 60 Hz, often 100 or 101 at 120 Hz.
3. `0x00b7c010` hands the quest id to `0x0069b4d0` (here quest 100, the sand ship, from `[[0x00edbe90]+0x9788+0x8c]`). Through `0x006aa160` it looks the id up in the list (`0x006ae870`: an entry's id is the u16 at `buffer + [buffer] + 0x20`) and copies that file into the holder (`+0xb40c`, size `[resource+0x68]`). Then it releases the list (`0x006bc838`).
4. If the quest's file was the one left out, nothing is copied. State 0 of `0x0065bcd0` then reads an empty monster list and requests monster 0, whose path entry in `0x00efc268` (`enemy\emNNN\data\emNNN`) is null: those are the reads of address 0. The stage is missing as well.

Repeatable runs (reads done in place, "Force deterministic async operations", now the runner's default) never hit it, even at 120 Hz with a 300% CPU or at 60 Hz with 50%.

**The port's fix** (`QuestFlow::startQuest` hook on `0x00b7c010`): when the quest about to start is in the table but not in the list, it releases the list with the game's own `0x006bc838` and skips the call for that frame. On the next frame `0x0069b21c` builds the list again and `0x00b7c010` comes back. In the runs where the race happened, the quest was in the rebuilt list one frame later, and the cutscene and the deck followed as normal. The hold gives up after 600 frames.

### What the port should replace

- **GSP:** replace RegisterInterruptRelayQueue, the interrupt thread `0x0056d43c` and `0x00d82d00` with a host present plus a host VBlank signal. Keep the callback table semantics: `0x00bf66f0` must still see "displayed = submitted".
- **Swap:** `0x00d82804`/`0x00d80cec` become "present this framebuffer". The per-screen gating (`+0x8ab1`/`+0x8ab2` from `mDisplaySkip`) decides which screen is new this frame, and a reworked HUD can simply force the bottom screen on.
- **Timing:** `0x001118e4` (GetSystemTick) must return ticks at the 3DS rate (`0x003e6d30` supplies the frequency). For 60 fps, `[sMain+0x2c]` should stay 60 and the cap `[sMain+0x30]` must be at least 60.

## Input (confidence: high for HID, medium for the engine side)

- **HID init `0x00107c6c`** (called from `0x0010698c`, flag `0x01057dcc`) opens `hid:USER`, calls GetIPCHandles, and maps the shared memory (0x2b0 bytes, read-only) into a reader block at **`0x0110e488`**. The block holds:

  | Device | Event at | Pointer at | Shared memory offset |
  |---|---|---|---|
  | pad (buttons, circle pad) | `+0x00` | `+0x04` | `+0x0` |
  | touch screen | `+0x08` | `+0x0c` | `+0xA8` |
  | accelerometer | `+0x10` | `+0x14` | `+0x108` |
  | gyroscope | `+0x1c` | `+0x20` | `+0x158` |
  | debug pad | `+0x24` | `+0x28` | `+0x238` |

  The memory-block object is at `+0x2c` and the mapped address at `+0x34`. `0x00485be0` and `0x004856d4` return the pad and touch reader blocks.
- **sPad** (DTI `0x010e8730`): constructor `0x005b87b0` (starts `ExtraPadSearchThread` and sets up the pad and touch readers), vtable `0x00e64b48`, instance pointer `0x010572e0`. **sMhPad**: constructor `0x0069522c`, vtable `0x00e658fc`.
- **`sPad::update` = vtable `+0x1c` = `0x005b825c`**. It reads the sMain instance `[0x01057194]`, polls the SDK readers through `0x005b70b0`, and post-processes in `0x005b8438`. Two of the readers it uses:
  - touch reads `0x0048592c` → `0x00572274`; touch points are zeroed when the checks `0x001080f8`/`0x0010d6d8` say so (probably applet or HOME state);
  - circle-pad normalisation `0x00485a08`, using range 0x69 = 105 in one calibration mode.

  sMhPad's update is `0x00694c98`.
- **Circle Pad Pro:** `ir:USER` (InitializeIrNopShared `0x0047e534`, SendIrNop `0x0047e5c8`, events, ReleaseReceivedData `0x0047e4fc`), with the receive loop in `0x0047c3ec` and the thread in `0x0047cd78`.
- For the port, feeding the HID shared-memory layout, or replacing `0x005b70b0` with host input, gives all buttons, sticks and touch. Touch maps to the mouse.

## Local multiplayer (confidence: high for the layers, low for the nNetwork link)

- The `nwm::UDS` request functions sit at `0x00564de4..0x00565420`. The SDK UDS layer above them includes `0x00564998` (initialise: CreateMemoryBlock, then `nwm::UDS`), `0x005632d0` (receive), `0x00564784` (send), `0x0056445c` (scan), `0x005634a4` (create network) and `0x005636f4` (connect).
- Their callers are Nintendo's **NEX/NetZ** middleware (source path strings `...\redgiant\CTR_NEX\include\NetZ\...`). The local transport is `UdsNodeBase` (event thread `0x0052f2e8`), with host migration in `LocalProcessHostMigrationJob` (`0x00532d20..0x00533700`) and more NEX code at `0x005c6000..0x005c8000` and `0x002df000..0x002e2000`.
- The MT `nNetwork::*` classes (Session, Transport, ProtocolStandard, …) sit above NEX. We did not trace them to these functions because the calls are virtual.
- For the port, the cleanest seam is the SDK UDS layer (or NEX's UDS node): replace it with a LAN/UDP transport. Online play (`frd:u`, `ac:u`, `soc:U`, `http:C`, `act:u`) is NEX over sockets and can come later.
- **Tried on the port (October 2026):** Azahar's network rooms carry it unchanged, so no seam is needed for now. Two runners (recompiled code, `--room-host`/`--room-join`) met in a Gathering Hall through *Local Play*: the second found the first's hall, joined it, and both list both hunters, for as long as the runs went on. Details:
  - The node information the SDK layer gives `InitializeWithVersion` (`0x00564998`) carries, as the friend code seed, config block `0x00090000` (the console's unique ID, read by `0x00483bfc`) and, as the user name, the hunter's name. A copy of a user folder with `--new-console-id` therefore counts as another console. Two copies of the same save (the same hunter twice) play together without trouble.
  - Village *Hunter Search* runs in the background: scan, host for about 11.5 s, destroy, repeat. The hall itself is a UDS network on data channel 243. Each packet starts with a 16-byte header of the game's (source and destination node, payload length, a checksum), and the payload of a session packet starts with the bytes `32 AB 98 64`.
  - **A quest together works.** The host posts a quest at the Quest Counter (the Low Questatrix: HR 1+ quests, "Select this Quest?" Yes, "Communicating..."). The guest joins it at the Quest Board: the board shows the quest and its members, then "Join this Quest?" Yes, "Communicating...", "Quest joined. Head to the Quest Gate and stand by for departure." At the gate the guest's A prompt is *Ready to Depart*; A again is *Exit Standby*, so press it once. The host's prompt is *Depart on Quest*, then "Depart on Quest?" Yes. Both load into the quest, a Harvest Tour of the Ancestral Steppe, with the party list showing two hunters and two Palicoes, and each sees the other at the camp. A host who departs before anyone has joined goes alone. Near another hunter the A prompt becomes *View States* (that hunter's equipment and stats), which can swallow a press meant for the board or the gate.
  - **Silence drops a player within about a second.** A runner's emulated clock stands still while the GPU works (Azahar runs command lists synchronously, and the software renderer takes up to a second per frame on a small machine), while the other runner's clock keeps going. A joining console resends its first packet every 500 ms and gives up after about a second. Once in the hall, a 2-second stall of the host made the guest show "*name* left the Gathering Hall" and take over as host (NEX's host migration). Runners on one machine avoid this with `--lockstep`; a player's real PC must not stutter for that long (shader compilation is the usual cause).
  - **The CPU backend doesn't matter to the other side.** A host on recompiled code and a guest on Azahar's own CPU emulation (dynarmic, `--cpu jit`) went through the same quest flow and both loaded into the Ancestral Steppe, each seeing the other's hunter and Palico. Rooms check only Azahar's room protocol (`network_version` 4 in releases 2125.0 and 2126.2 as in the pinned 2127.0 alpha), so a friend can play with a port player on official Azahar.
  - **Three players, and a bug in Azahar's UDS for consoles that join at the same moment.** With two guests joining a hall in the same instant, one of them always got "The connection was lost" (the other went on). Azahar's host gives a joining console its node id in an EAPoL-Logoff that it broadcasts to every member (a stand-in for the real UDS, which sends it to the new console only), and a console that is still connecting takes the first logoff it sees: both guests took the first one's id, and the host later got a deauthentication "from unknown node". The logoff has a field for the console it is meant for (`client_mac_address`), which Azahar left at zero. The port's patch fills it in (`GenerateEAPoLLogoffFrame`) and has a connecting console skip logoffs for another address (zero, from hosts without the fix, still counts as its own). With it, three runners joined the hall in the same instant, all three joined one quest and loaded into the Ancestral Steppe together (party list of three hunters; Palicoes stay home with three or more), and so did four (two runs: in the first the third guest's script ended at another hunter's *View States* instead of the board, a script problem; in the second all four loaded into the quest). Both sides need the fix, so mixed with official Azahar the old behaviour remains: join one at a time there.
  - **An Arena quest together** (net sync, no `--lockstep`). At *Arena Bambina* (next to the Low Questatrix): her talk, *Arena Quests*, "Grudge Match: Yian Kut-Ku", "Select this Quest?" Yes, "Communicating..."; the quest is then on the board ("Players 1/2") and the guest joins it there as usual. After departure each player picks a preset loadout ("Use this equipment?" has *No* selected, so LEFT then A) and both load into the Arena with their great swords; they fought side by side for a minute, each seeing the other, without a drop. The Yian Kut-Ku didn't show up in that minute where they stood (target camera: *Unseen*). Two script pitfalls: pressing B right after the quest is accepted ("Yeah, good luck and stuff.") left it unposted, and a guest standing next to the host at the counter turns the host's A into *View States*.
  - **Net sync (the port's answer to stalls).** Each port console sends the room its emulated clock 20 times a second in a Wi-Fi packet of type `0x4d`, which no 3DS frame uses: room servers relay Wi-Fi packets without looking at the type, and Azahar's UDS service ignores types it doesn't know. A console counts, per peer it plays with (game data from it, addressed to this console, within the last 3 s of its own emulated time: broadcasts reach every console in the room, including other groups', and host time would run out while everyone waits), how far its clock has advanced beyond the peer's since then, and waits at the start of each `RunLoop` while that lead is over 200 ms (`port/src/netsync.cpp`; the run-loop hook is in the Azahar patch). Reports carry whom a console waits for, so two that each think they are ahead stop waiting for each other; a peer silent for 6 s (paused in Azahar, say) is left to the game. In the cloud, two runners without `--lockstep` went through the whole quest flow into the Ancestral Steppe with screenshot stalls on both sides, also with 80 ms (±20) of delay each way between them (a UDP relay in front of the room); and in a hall: a 3 s `--freeze` of the host dropped it without net sync (the guest took over the hall about a second later); with it the guest waited (2.7 s) and both stayed, also through 25 s of second-long screenshot stalls on both sides with slacks of 300 and 50 ms. A first version counted the 3 s in host time: when a stall went past it the guest stopped waiting and the host was dropped.

## File system (confidence: high)

- `OpenArchive` (`0x0010db0c`) is called with archive IDs **3** (RomFS, the title's own content), **4** (save data), **7** (shared extdata) and **0x567890B4**.
- `OpenFileDirectly` (`0x0010db9c`) is called with archives **3** and **0x2345678A** (the usual SDK RomFS mount), archive path types 1/2 and file path type 2 (binary).
- Save data is managed with FormatSaveData `0x0047ba68` (archive 4), Create/DeleteExtSaveData, ControlArchive (commit), and file Read/Write/SetSize.
- The FS client sits in two blocks: low-level `0x0010d9ac..0x0010dc3c` and wrappers `0x0047b8b0..0x0047be8c`. `0x0010dc3c` returns the `fs:USER` session, and `0x0010d9ac` opens a file through OpenFileDirectly.

## Audio and the rest (brief)

- **Audio** uses `dsp::DSP` directly (component load, pipe read/write, semaphores, interrupt events), which is the SDK's own DSP sound library. There is no `csnd`. The port needs a DSP HLE, as in Azahar, or a replacement of the sound layer above it.
- **Camera:** `cam:u` + `y2r:u` drive `sLtHID::CameraThread`/`YuvEncodeThread`. A port can stub them.
- **StreetPass** (`cecd:u`), **SpotPass** (BOSS), **eShop/DLC** (`am:app`, `nim:aoc`), the pedometer (`ptm:u`), `ndm:u` and `err:f` can all be stubbed. The DLC queries matter only if DLC quests should load.

## Caveats

- Handles that come from heap objects can't be traced statically. Those requests get a service by **vote of neighbouring request functions** or from a header that only one known service uses, and are marked `?` in the tool output: APT, the second CECD session, BOSS, part of `http:C`/`soc:U`/FS.
- Headers missing from the built-in tables: `ac:u` `0x00130042`, `cecd` `0x04020002`, `soc:U` `0x00200146`, and all `nim:aoc` commands. Where the game's own headers disagreed with a table entry, the game was taken as right: `frd:u` Login (`0x00030002`), three `cam:u` entries and the `ndm:u` ids from 0x0C on were corrected.
- Only direct calls are followed. Callbacks and virtual calls (the main loop, the systems, the render jobs) were followed by hand above.

## Tool

`tools/find_services.py`: 

```
python3 tools/find_services.py -o build/services.tsv --handles build/service_handles.tsv [--detail]
```

It takes about a minute on the full game and writes:

- `services.tsv`: one row per request site and per `svc` site, with the columns function, mode, service, header, command, callers, site, handle and note. `--detail` adds the command words to the note.
- `service_handles.tsv`: every global that receives a session handle, an svc output (event, thread, memory block) or an IPC response word.

Both files are derived from the dump: keep them under `build/`. How the tool works:

- It runs a small abstract interpreter over each function's CFG (ARM and Thumb-1) with symbolic values.
- Predicated instructions keep two states until the flags change or a branch resolves them.
- What a function stores through its arguments becomes a summary, applied at its call sites.
- Values that depend on arguments are resolved up to 5 levels through the direct call graph, with stack slots and read-only data looked up in the caller.
- Small getters are evaluated on demand.
- Service names are traced from `srv:GetServiceHandle` and ConnectToPort to the globals the handles end up in, following copies between globals.

Tests in `tests/test_find_services.py` use hand-encoded synthetic code only.
