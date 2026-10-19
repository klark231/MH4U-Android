# Metal on macOS: MoltenVK or a native backend

How the port should get Metal on Apple-silicon Macs: what running Azahar's Vulkan renderer on MoltenVK (Vulkan over Metal) costs MH4U, what a native Metal renderer for Azahar's `video_core` would take, and a staged recommendation with the measurements that decide between them.

Read in Azahar at the port's pinned commit (`5f3306d`, `port/azahar/COMMIT`), in Panda3DS's Metal renderer (HEAD `92e53d2`, October 2026), in the port's app (`port/app/`, branch `native-app`) and in the pages under [Sources](#sources) (October 2026). **Nothing here was run on a Mac.** The macOS package hasn't been tried on one yet, so every number about speed on the M2 is unknown until [the checklist](#34-measurement-checklist-m2) has been followed.

Each point says where it comes from: **code** (read at the commits above), **source** (a cited page; I did not check it) or **unknown** (needs a measurement). "My reading" marks an inference.

## Summary

- **On a Mac, Azahar has a Vulkan renderer and a software renderer, and nothing else.** Its CMake turns OpenGL off on Apple (Azahar needs OpenGL 4.3, and macOS stops at 4.1). Vulkan runs on MoltenVK 1.4.1 (November 2025), which Azahar's build downloads. **code, source**
- **What MoltenVK can't give the Vulkan renderer, and what Azahar does instead:**
  - No geometry shaders and no usable fragment barycentrics, so lit draws skip the per-triangle quaternion fix-up that the 3DS does.
  - No logic ops in the default MoltenVK build. The shader emulation draws 4 of the 16 ops correctly.
  - No triangle fans. Those draws fall back to vertex shading on the CPU.
  - No 24-bit depth. Azahar falls back to 32-bit float depth.
  - No custom border colours. Azahar emulates them in the shader.

  **code, source**
- **Costs that aren't missing features:**
  - MoltenVK compiles shaders with Metal's fast math by default, which may defeat Azahar's accurate-multiplication emulation (it relies on `isnan()`).
  - Azahar gives every RGBA8 surface storage usage. Apple GPUs then skip lossless compression for it, which costs bandwidth at high resolution.
  - Commands are recorded twice: Azahar's worker thread replays them into Vulkan, then MoltenVK encodes Metal at submit.
  - The first use of each pipeline pays for SPIR-V→MSL conversion and the Metal compile.

  **code, source**
- **For MH4U**, the lighting fix-up and the bandwidth cost are the likely ones: the engine uses per-fragment lighting, normal maps, self-shadowing and full-screen filters. Whether MH4U uses logic ops or triangle fans is unknown. A census of the PICA state with the runner answers that without a Mac.
- **Gas (the cutscene smoke) is not a Metal question.** No Azahar renderer draws it, so a Metal backend changes nothing there.
- **Shadows should work** (fragment-shader interlock maps to Metal raster order groups), but they need a look.
- **Speed: nobody has measured this.** I found no measurement for Azahar or MH4U on Apple silicon. Other emulators:
  - Dolphin's native Metal backend was 23% and 30% faster than MoltenVK in two tests (2022).
  - Cemu users report up to about twice the frame rate with Cemu's Metal backend (2025). Cemu's Vulkan path has its own MoltenVK problems, so this doesn't carry over (my reading).
  - LunarG says layering Vulkan on Metal adds "very little" overhead for most uses.
  - KosmicKrisp, LunarG's Mesa driver for Vulkan on Metal, became Vulkan 1.4 conformant in October 2026 and ships in the Vulkan SDK. Trying it needs no code.
- **A native backend means** implementing these: `RendererBase`, `RasterizerAccelerated`, the rasterizer cache's runtime (`TextureRuntime`, `Surface`, `Framebuffer`, `Sampler`), a blit helper and presentation.
- **Shaders can stay as they are.** The existing SPIR-V can go through SPIRV-Cross, which is what MoltenVK does with them today.
- **Size:** about 6,000-9,000 lines. The Vulkan backend has 11,400 non-blank lines, the OpenGL one 6,200.
- **Panda3DS's Metal renderer is a reference, not a base.** It is 2,500 lines plus a 760-line ubershader, shades vertices on the CPU, has no upscaling, no shadows and no shader generation. Its architecture differs from Azahar's.
- **Licences are compatible** under the port's GPL-3.0-or-later.
- **Recommendation: MoltenVK now, a native Metal backend later, and only on a trigger.**
  - Now: run the game on the M2 and measure. Fix the cheap things in the shared Vulkan renderer, and try KosmicKrisp.
  - Later: start the native backend when measurements show the MoltenVK path is what limits a busy hunt at the resolution played, or when a visible error traces to a limit of Vulkan on Metal that the shared renderer can't fix ([3.3](#33-the-trigger-for-later)).
  - Build the validation harness (hardware-renderer screenshots in the runner) first either way.

## 1. What MoltenVK costs MH4U

### 1.1 The path on a Mac today (code)

- `CMakeLists.txt`: `CMAKE_DEPENDENT_OPTION(ENABLE_OPENGL ... "NOT APPLE" OFF)`. The OpenGL driver wants GL 4.3 or GLES 3.2 (`gl_driver.cpp`), and macOS stops at 4.1 ([Citra][citra-vk]). Post-processing shaders and video dumping exist only in the OpenGL renderer: there is no such code in `renderer_vulkan/`. So the app's `shader` option does nothing on a Mac.
- `CMakeModules/DownloadExternals.cmake` fetches `MoltenVK/releases/download/v1.4.1/MoltenVK-all.tar`. The app links the dylib (`PLATFORM_LIBRARIES`) and finds it next to the executable (`port/app/CMakeLists.txt` on `native-app`). MoltenVK 1.4.2 came out in July 2026, so the pin is one release behind ([releases][mvk-rel], [rc1 report][mvk-142]).
- `vk_platform.cpp` `OpenLibrary` first tries `dlopen("libvulkan.dylib")`, a Vulkan loader, and only then the bundled `libMoltenVK.dylib`. With a loader installed system-wide (the LunarG SDK, say), the app runs whatever driver that loader picks, not the bundled MoltenVK. Azahar [#1036][az-1036] is a case of system packages getting in the way of the bundled ones. When MoltenVK is the driver, the log has `Extension VK_EXT_tooling_info has been blacklisted because function pointer is not exposed by MoltenVK`.
- At instance creation Azahar sets three MoltenVK options through `VK_EXT_layer_settings`:
  - synchronous queue submits, which follow `async_presentation` (default on)
  - resume lost device
  - maximize concurrent compilation

  Settings made this way override MoltenVK's environment variables. Every other MoltenVK option can still be set from the environment ([parameters][mvk-params]).
- `port/app/window_vk.cpp` makes the Metal layer (`SDL_Metal_CreateView`) that MoltenVK draws into.
- **Command path.** The emulation thread (the app's main thread) records commands into chunks. The `VulkanWorker` thread replays them into a `VkCommandBuffer` and calls `vkQueueSubmit` (`vk_scheduler.cpp`). With MoltenVK's default `MVK_CONFIG_PREFILL_METAL_COMMAND_BUFFERS=0`, the Metal command buffer is encoded at that submit, on that thread ([parameters][mvk-params]; source).

### 1.2 Feature by feature

| Feature | What Azahar's Vulkan renderer does with it | On MoltenVK (Apple GPU) | What happens | Basis |
|---|---|---|---|---|
| Geometry shaders | A fixed geometry shader flips opposite normal quaternions per triangle when per-fragment lighting is on and barycentrics are missing (`vk_pipeline_cache.cpp` `CalcExtraConfig`, `glsl_shader_gen.cpp`). PICA geometry shaders are never accelerated, on any GPU (`AccelerateDrawBatch`). | Metal has none ([KosmicKrisp slides][kk-xdc25-pdf] list them as future work; [Cemu #1287][cemu-1287]) | `UseFixedGeometryShader` returns no shader, so lit draws go without the fix-up. This is the trade-off Azahar makes on purpose on Android: "Geometry shaders are extremely expensive on tilers ... even if it hurts accuracy somewhat" (`vk_instance.h`) | code, source |
| Fragment barycentrics | The other way to do the fix-up, in the fragment shader with `pervertexEXT` | MoltenVK lists the extension but can't translate `PerVertexKHR` to MSL. Metal gives a fragment only its `primitive_id` ([MoltenVK #2059][mvk-2059]) | Azahar blacklists it on MoltenVK ("the PerVertexKHR attribute is not supported by MoltenVK", `vk_instance.cpp`) | code, source |
| Logic ops | A hardware logic op when blending is off | `logicOp` is offered only by MoltenVK builds with Metal's private API ([v1.2.8][mvk-128]). That build setting is off by default, because it keeps an app out of the App Store ([`MVKCommonEnvironment.h`][mvk-env]). Not checked for the release tarball | Emulated in the shader. The SPIR-V generator (Vulkan's default, `spirv_shader_gen = true`) handles Clear, Set, Copy and NoOp (through the colour mask). Its CopyInverted line is commented out, so it draws as Copy. The other 11 log `Unhandled logic_op` and draw as Copy (`spv_fs_shader_gen.cpp`) | code, source |
| Triangle fans | Native topology | Metal has none (Panda3DS: "Triangle fans are not supported on Metal"). Azahar reads `triangleFans` from the portability subset | The accelerated draw is skipped, and that draw's vertices are shaded on the CPU | code, source |
| 24-bit depth (D24, D24S8) | Native formats | Apple GPUs have none (Panda3DS: "Apple sillicon doesn't support 24-bit depth buffers") | Falls back to D32F or D32F_S8 (`CreateFormatTable`), with conversions on upload and download, and a compute pass when depth is read as colour | code, source |
| 16-bit colour (RGB5A1, RGB565, RGBA4) | Native packed formats | Not checked whether MoltenVK offers them with attachment and blit support | If it doesn't, the log says `Format ... unsupported, falling back unconditionally` and RGBA8 is used | code, unknown |
| Custom border colours | `VK_EXT_custom_border_color` | Not in MoltenVK's extension list ([user guide][mvk-guide]) | Emulated in the fragment shader (`TextureConfig::ApplyProfile`): correct, with slightly longer shaders | code, source |
| Shadow rendering | `imageLoad`/`imageStore` on an R32Uint view of the RGBA8 target, inside `beginInvocationInterlock`. Without interlock it uses a compare-and-swap loop | `VK_EXT_fragment_shader_interlock` is supported through Metal raster order groups ([user guide][mvk-guide]) | Expected to work, not seen. MH4U uses shadow textures ([#2531][az-2531]) | code, source, unknown |
| Storage usage on every RGBA8 surface | Every RGBA8 surface gets storage usage and a mutable format "in case it is found out later that this is a shadow-source texture" (`vk_texture_runtime.cpp`). That is nearly every surface: decoded texture formats (ETC1, I8, ...) are stored as RGBA8. Azahar's own comment: "Keeping it disables can boost performance on mobile drivers" (`vk_instance.cpp`) | Apple GPUs don't apply lossless compression to textures with `shaderWrite` or `pixelFormatView` usage ([Apple][apple-tex]). Storage has to become `shaderWrite`, and a mutable format becomes a pixel-format view (my reading of MoltenVK; its source not checked) | More memory bandwidth for render targets and textures, growing with the internal resolution. How much: unknown | code, source, unknown |
| Vertex strides | Any stride | Metal wants vertex strides in multiples of 4 (my understanding). Azahar reads the alignment from the portability subset (`minVertexInputBindingStrideAlignment`) | A PICA loader with another stride is copied vertex by vertex on the CPU (`SetupVertexArray`) | code |
| 8-bit indices | `VK_EXT_index_type_uint8` | Blacklisted ("uint8 index conversion causes memory leaks in MoltenVK"). Metal has no 8-bit indices anyway | Widened to 16 bits on the CPU (`SetupIndexArray`) | code |
| Primitive restart | Off in every pipeline | Metal restarts strips at the all-ones index (my understanding, not checked) | Matters only for a strip that uses index 0xFFFF; not expected (not checked) | code, unknown |
| Push descriptors | Not used | - | - | code |
| Pipeline cache | SPIR-V and configurations in transferable files per title (`shaders/vulkan/transferable/<title>_{vs,fs,gs,pl}.vkch`), plus the driver's `VkPipelineCache` blob per GPU (`shaders/vulkan/pipeline/`) | MoltenVK's blob keeps the converted MSL, so the next run skips SPIR-V→MSL, "the slowest step" ([user guide][mvk-guide]). Metal still compiles MSL to GPU code, and the system shader cache speeds that up on later runs ([WWDC20][wwdc20]) | The first sight of each pipeline pays for conversion and compile. Azahar waits for it unless asynchronous shader compilation is on (off by default) | code, source |
| Fast math | `shaders_accurate_mul` (on by default) wraps PICA multiplies in `sanitize_mul`, which tests `isnan()` to give 0 × ∞ = 0 | MoltenVK compiles with Metal fast math by default (`MVK_CONFIG_FAST_MATH_ENABLED=1`), which trades IEEE 754 behaviour for speed ([parameters][mvk-params]) | The compiler may assume no NaNs and drop the check. Test with `MVK_CONFIG_FAST_MATH_ENABLED=0` | code, source, unknown |

### 1.3 What that means for what MH4U draws

The engine's drawing is described in [screen-filters.md](screen-filters.md), [widescreen.md](widescreen.md) and [community-enhancements.md](community-enhancements.md).

- **Lighting.** The engine's feature list has HDR lighting, normal mapping and self-shadowing ([community-enhancements.md](community-enhancements.md), from a tech report). On the PICA, per-pixel lighting and normal maps go through fragment lighting, where a quaternion per vertex gives the surface's frame (my reading of how MH4U does it).
  - On MoltenVK every lit draw runs without the flip. Where a triangle's vertices carry quaternions of opposite sign, the interpolated frame swings the long way, which shows as wrong shading across that triangle.
  - How often MH4U's models do this is unknown.
  - The software renderer applies the flip (`sw_clipper.cpp` `FlipQuaternionIfOpposite`), so the runner's screenshot of the same frame is the reference.
  - This is the most likely visible difference between the Mac and the PC, because the RX 6700 XT has geometry shaders.
- **Shadows.** MH4U samples PICA shadow textures. Azahar [#2531][az-2531] was a Vulkan assert on the equipment set screen where a shadow-texture slot received an RGBA4 surface; 2126.1.1 lists the fix. On MoltenVK the shadow pass uses interlock through raster order groups. Look at the equipment screen and at shadows in the field.
- **Screen filters** (bloom's quarter, eighth and sixteenth targets, depth of field, the blurs, colour correction) are ordinary render-to-texture passes. MoltenVK lacks nothing they need. Their cost grows with resolution and with the bandwidth cost above. The port's switches (`bloom`, `depth_of_field`, ...) make a GPU-bound check easy.
- **Gas** (cutscene smoke, Azahar [#765][az-765]) is drawn by no Azahar renderer:
  - Both shader generators write transparent black (`WriteGas`), and the software renderer draws it as ordinary fragments.
  - A Metal backend would inherit the same generators.
  - Implementing gas is work in the shader generators and helps every renderer.
- **Fog** is a lookup table in a texel buffer, which Metal has.
- **Logic ops and fans:** unknown for MH4U. If it uses And, Or, Xor and the like without blending, the Mac draws them as Copy.
- **Accurate multiplication:** unknown whether MH4U needs it. If it does, fast math may undo it.

### 1.4 Speed: what is known

- **No measurement exists.** I found no frame-rate report for Azahar or for MH4U on Apple silicon.
- **Where MoltenVK's costs land:**
  - CPU, on `VulkanWorker`: the replay, MoltenVK's encoding and the submit.
  - CPU, on the emulation thread: PICA processing, state sync, vertex repacking, and CPU vertex shading for fans.
  - GPU: bandwidth (the storage-usage row above), and render-pass breaks. `CopyImageView` ends rendering to copy a target that is sampled while drawn to; on a tile-based GPU each break stores and reloads tiles. A native backend has the same cost unless it reads the framebuffer in the shader instead.
  - Stutter: compiles on first use.
- **The renderer's share of CPU time is bigger in the port** than in Azahar. The game's code runs recompiled and native, so the guest costs less than under a JIT (my reading).
- **Other emulators** (sources, not checked):
  - **Dolphin, 2022** ([progress report][dolphin-2022]). Native Metal was faster than MoltenVK: +23% in Skyward Sword and +30% in Rogue Leader. Writing it exposed a MoltenVK regression of up to 45%, fixed upstream. MoltenVK stays as "the benchmark that we compare our native Metal backend against".
  - **Cemu, 2025** ([PR #1287][cemu-1287]). Users report Breath of the Wild at 35-50 fps with Metal against about 20 with Vulkan on an M4, and Mario Kart 8 at 60 fps at 2K against a "stuttery 57fps at Full HD".
  - **LunarG, 2024** ([white paper][lunarg-2024]): layering adds "very little additional overhead (for most use cases)". MoltenVK's own slides speak of "some unavoidable memory and performance overhead" ([Vulkanised 2023][mvk-vulkanised]).
  - **KosmicKrisp**:
    - Conformant to Vulkan 1.3 in October 2025 ([LunarG][kk-13]), and to 1.4 in October 2026 ([Khronos][kk-14]).
    - The Vulkan SDK 1.4.341 installs it but doesn't configure it ([Blender][kk-blender]).
    - Speed "in good shape" against MoltenVK and "not too far behind native Metal", relayed by [Phoronix][kk-xdc26]; tripled since the 1.3 milestone ([Khronos][kk-14]).
    - As of the [XDC 2025 slides][kk-xdc25-pdf]: it emulates triangle fans, custom border colours are not exposed yet, and geometry shaders are on its wish list.
- **So** a native backend should save CPU time per draw, by an amount nobody has measured for this renderer. Whether that matters depends on whether MH4U on the M2 is limited by that path at all.

### 1.5 Known Azahar issues on macOS

- **macOS 26:** MoltenVK 1.2.9 crashed in surface queries. [PR #2149][az-2149] proposed bumping it to 1.4.1 and blacklisting tooling info on MoltenVK. The pinned commit has both (code), whichever change brought them.
- **Hang at launch on macOS 26** ([#1637][az-1637]): traced to start-up code in the Qt frontend. The port's SDL app doesn't run that code (my reading).
- **System libraries:** Homebrew's SPIRV-Tools and Vulkan packages got in the way of the bundled ones ([#1036][az-1036]; one contributor's diagnosis, which others couldn't reproduce).
- **Older reports** ([#1194][az-1194] Mario Kart 7 on an M1; [#703][az-703] home menu glitches) were closed after OS or Azahar updates without a diagnosis.
- **No report specific to Monster Hunter on macOS was found.** The MH4U renderer fixes in Azahar's release notes (#2407, texture lifetime at quest start; #2533, the shadow-texture assert) are in the shared Vulkan code ([2126.1-alpha1 notes][az-2126.1a1], [community-enhancements.md](community-enhancements.md)).

## 2. What a native Metal backend would take

### 2.1 Interfaces (code)

- **`VideoCore::RendererBase`** (`renderer_base.h`): `Rasterizer()`, `SwapBuffers()`, `TryPresent()`, `NotifySurfaceChanged()` and screenshots through `RendererSettings`. It also needs the port's additions that the patch makes to the OpenGL and Vulkan renderers: the touch-overlay crop in `DrawBottomScreen` and the layouts.
- **`VideoCore::RasterizerAccelerated`**, over `RasterizerInterface`:
  - `DrawTriangles` (CPU-shaded vertices) and `AccelerateDrawBatch` (PICA vertex shaders on the GPU)
  - `FlushRegion`, `InvalidateRegion` and `ClearAll`
  - `AccelerateDisplayTransfer`, `AccelerateTextureCopy` and `AccelerateFill`, plus `AccelerateDisplay`
  - the disk-resource loading
- **The rasterizer cache** (`RasterizerCache<Traits>` in `rasterizer_cache.h`, about 1,500 lines, shared by all backends) takes five backend types:
  - **`Runtime`**: `GetResourceTick`/`GetResourceFreeTick`, `Finish`, `FindStaging`, `Reinterpret`, `ClearTexture`, `CopyTextures`, `BlitTextures`, `GenerateMipmaps` and `NeedsConversion`.
  - **`Surface`**: `Upload`, `UploadCustom`, `Download`, `ScaleUp` and `GetInternalBytesPerPixel`, holding base, scaled, custom and copy images with their views.
  - **`Framebuffer`**, **`Sampler`** and **`DebugScope`**.
- **Blit helper:**
  - depth-stencil blits
  - two compute passes: D24S8 to RGBA8 (depth read as colour) and depth to a buffer (downloads)
  - the texture filters (bicubic, xBRZ, ScaleForce, MMPX, Anime4K)
- **Presentation:** a `CAMetalLayer` (the app already makes one), the screen layouts, stereo modes and the cursor.
- **Integration:**
  - a `GraphicsAPI::Metal` value and its keys in `common/settings.h`
  - the factory in `video_core.cpp`, a CMake option, and Objective-C++ or metal-cpp glue
  - `window_mtl.cpp` in the app
  - the Qt frontend's renderer list, if that app keeps shipping

### 2.2 Shaders: SPIRV-Cross, not a new generator

- **Everything the Vulkan renderer runs already ends as SPIR-V (code):**
  - most fragment shaders come straight from the SPIR-V generator (sirit)
  - vertex shaders (PICA programs decompiled to GLSL), the fix-up geometry shader, and the fragment configurations the SPIR-V generator doesn't handle (shadow rendering, shadow cubes, min/max blending; `UsesSpirvIncompatibleConfig`) go through glslang
  - host shaders are GLSL strings compiled at run time (`host_shaders/CMakeLists.txt`)
- **SPIRV-Cross** (Apache-2.0) can turn the same SPIR-V into MSL at run time, which `newLibraryWithSource` then compiles asynchronously.
  - This is MoltenVK's own path, so these shaders already go through it every time Azahar runs on a Mac.
  - The backend controls the conversion options and the resource bindings: 3 dynamic uniform buffers, 3 texel buffers, 5 sampled textures and 1 storage image (`vk_pipeline_cache.cpp`), all well inside Metal's limits.
  - No offline Metal toolchain is needed. Xcode 26 ships it as a separate download, which CI would have to fetch ([CircleCI][xcode26-metal]).
  - Keep Azahar's transferable SPIR-V cache, add an MSL cache, and keep the compiled pipelines in an `MTLBinaryArchive`. In Apple's Fortnite example, pipeline building went from 1 min 26 s to 3 s ([WWDC20][wwdc20]).
- **An MSL generator** would be a fourth generator, next to GLSL, GLSL ES and SPIR-V, kept in step with Azahar's.
  - It pays only for Metal features that Azahar's Vulkan renderer doesn't use: reading the framebuffer in the fragment shader (`[[color(0)]]`) for all 16 logic ops and for min/max blending, and raster order groups for shadow rendering.
  - Try these first as SPIRV-Cross options or small passes over its output.
- **Panda3DS generates no MSL.**
  - Its Metal renderer uses one hand-written ubershader (`src/host_shaders/metal_shaders.metal`, compiled at build time with `xcrun metal`), specialised with function constants.
  - Vertices are shaded on the CPU: `RendererMTL` doesn't implement `prepareForDraw`, the hardware-shader hook.

### 2.3 Size by component

Non-blank, non-comment lines in Azahar's Vulkan backend, and my estimate for the same job in Metal. Metal has no image layouts, barriers, descriptor sets, swapchain or semaphores to manage. A quarter of `vk_texture_runtime.cpp` is barrier and layout code.

| Component | Vulkan files | Vulkan | Metal (estimate) |
|---|---|---:|---:|
| Device, capabilities, format table | `vk_instance`, `vk_platform`, `vk_common`, `vk_memory_util` | 1,288 | 300-500 |
| Submission and sync | `vk_scheduler`, `vk_master_semaphore`, `vk_resource_pool`, `vk_descriptor_update_queue` | 872 | 400-600 |
| Stream buffers | `vk_stream_buffer` | 271 | 150-250 |
| Render passes | `vk_render_manager` | 249 | 200-300 |
| Pipelines and state | `vk_graphics_pipeline`, `vk_pipeline_cache`, `pica_to_vk` | 1,337 | 900-1,300 |
| Shaders and disk cache | `vk_shader_util`, `vk_shader_disk_cache` | 1,627 | 900-1,500 |
| Texture runtime | `vk_texture_runtime` | 1,676 | 1,000-1,400 |
| Blit helper | `vk_blit_helper` | 882 | 500-800 |
| Rasterizer | `vk_rasterizer` | 946 | 800-1,000 |
| Renderer and presentation | `renderer_vulkan`, `vk_present_window`, `vk_swapchain` | 2,265 | 900-1,400 |
| **Total** | | **11,413** | **about 6,000-9,000** |

Two dependencies come on top: SPIRV-Cross and metal-cpp, or Objective-C++. So do 300-500 lines of integration. For scale:

| Code | Size |
|---|---:|
| Azahar's OpenGL backend | 6,185 lines |
| The port's whole app | about 2,000 lines |
| The port's Azahar patch | 1,021 lines |

- **The slow part is matching, not writing.** Matching the Vulkan renderer frame for frame takes longer: format conversions, reinterpretation, scaled surfaces, custom textures. That is why the harness in 2.6 comes first.
- **Upkeep is the larger cost.** The backend would depend on `video_core` internals (the rasterizer cache, the shader generators, the PICA registers), which change in most Azahar releases.
- **Upstreaming is uncertain.** In 2023 Citra chose MoltenVK over a Metal backend, which would be "only relevant for macOS, and no other platform" ([Citra][citra-vk]).
- **If the port carries it,** keep it in the port's tree like the CPU backend, with only the settings value, factory and CMake hooks in the patch.

### 2.4 What Panda3DS's renderer covers (code)

**Has:**
- metal-cpp setup and a `CAMetalLayer` present
- colour and depth targets in a surface cache (the OpenGL renderer's: "HACK: use the OpenGL cache"), with pending clears
- display transfers and texture copies drawn as quads
- lighting and fog lookup tables in texture arrays, refreshed by slice
- pipeline and depth-stencil state caches keyed by PICA state
- all 16 logic ops by reading the framebuffer
- `Depth32Float_Stencil8` for D24S8
- RGBA4 as `ABGR4Unorm` on macOS; RGB5A1 and RGB565 as RGBA8

**Lacks:**
- vertex shaders on the GPU (CPU only)
- resolution scaling
- shadow rendering and shadow textures, procedural textures, gas
- bump mapping (ignored) and parts of lighting (flagged `error_unimpl`)
- TEV sources 6-12 (flagged unimplemented), and the undocumented operand and combiner values (left as TODO)
- screenshots ("not implemented")

**Also:**
- Textures are decoded on the CPU into shared-storage textures.
- It waits for the GPU after every command buffer (`waitUntilCompleted`, "HACK").
- Fans are drawn as triangle lists, with a warning that it does so.
- Panda3DS defaults to OpenGL on macOS and to Metal only on iOS (`config.hpp`).

Its value to the port is as a worked example of Metal for the PICA: formats, framebuffer reads for logic ops, lookup-table textures, depth handling. Its structure doesn't fit Azahar's rasterizer cache or shader cache.

### 2.5 Licences

- **Azahar.** In `src/`, 1,276 files say "GPLv2 or any later version" and 65 say "GPLv3 or any later version". The 65 include `vk_rasterizer.cpp`, `vk_pipeline_cache.cpp`, `vk_graphics_pipeline.*`, `glsl_fs_shader_gen.cpp` and `pica_fs_config.*`. Its `LICENSE.txt` is the GPLv3. A backend derived from the Vulkan files keeps each file's terms. The whole is distributed under GPLv3, which the port's GPL-3.0-or-later already is.
- **Panda3DS.** `LICENSE.md` is the GPLv3 text. It has no per-file headers, and I found no "or later" statement. Treat it as GPL-3.0-only.
  - Code from it can go into the port, under GPLv3 terms.
  - A file built from it can't carry Azahar's GPLv2-or-later header.
  - The port's "or later" would not extend to that file.
  - Little of it would carry over anyway.
- **metal-cpp** is Apache-2.0 ([Apple][metal-cpp], [mirror][metal-cpp-lic]). **SPIRV-Cross** is Apache-2.0, with some MIT ([attribution][spirv-cross-lic]). **MoltenVK**, already shipped, is Apache-2.0.
  - All are compatible with GPLv3, but not with GPLv2 alone.
  - That is fine for the port.
  - It would matter only for code meant to stay GPLv2-only, which nothing here is.

### 2.6 Validation

- **Same frames.** The runner already makes a run repeat exactly: it reads files in place and plays input scripts at fixed emulated times ([port/README.md](../../port/README.md)). It draws only with the software renderer today (`graphics_api = Software` in `runner.cpp`).
- **Hardware renderers in the runner.** Add `--renderer vulkan|metal` with a hidden SDL window. Take shots through `RendererBase::RequestScreenshot`, which every hardware renderer implements; the Metal backend must too.
- **What to compare:**
  - **Metal against MoltenVK on the same Mac**, at 1x and 3x. Same GPU and, with SPIRV-Cross, nearly the same MSL, so a real difference is a backend bug.
  - **Both against the software renderer at 1x**, for accuracy. Known differences: gas, and the quaternion fix-up on MoltenVK.
  - **Optionally against Vulkan on the PC.**
- **Scenes:**
  - the title screen
  - Val Habar outdoors (bloom)
  - a village cutscene (depth of field, image plane)
  - an expedition with the HUD
  - a hunt with a roar (radial blur), fire and smoke
  - the equipment set screen (shadow texture, [#2531][az-2531])
  - a quest start ([#2407][az-2126.1a1])
- **Metric.** Per screen: the share of pixels with a channel difference above a threshold, the mean difference, and a diff image. This goes in a `tools/` script with tests on synthetic images, per the repository's rules.
- **Speed gate:** the checklist in 3.4, on both backends.

## 3. Recommendation

### 3.1 MoltenVK now, native Metal later

- **It runs today.** The macOS build exists and uses MoltenVK, but nobody has played MH4U on it yet. The first questions are whether it runs and how fast; only a Mac answers them.
- **Most gaps belong to Metal, not to MoltenVK.** A native backend still has no geometry shaders, triangle fans, 24-bit depth or custom border colours. It gains framebuffer reads (all logic ops), control over texture usage (compression), one less recording layer, binary archives, and maybe a route to the quaternion fix-up (2.2, 3.2).
- **Several gaps have cheaper fixes in the shared Vulkan renderer, or in another driver** (3.2, stage 2).
- **A native backend is a second large renderer to keep up with Azahar.** That cost is certain. Its speed benefit is only measured elsewhere, and its correctness benefit depends on MH4U's PICA usage, which is unknown.

### 3.2 Staged plan

0. **Without a Mac.**
   1. **A PICA census with the runner.** Count per frame: draws, fans, logic ops without blending (by op), lit draws, shadow rendering and shadow textures, gas, fog, procedural textures, PICA geometry shaders, vertex strides that aren't multiples of 4, 8-bit indices, and texture formats. Use a small hook at `PicaCore` draw time, through the scenes in 2.6. This says which rows of 1.2 touch MH4U.
   2. **Hardware renderers and screenshots in the runner** (2.6). OpenGL under Xvfb in the cloud first, as the README's checks did. Vulkan in the cloud would need a software Vulkan driver such as Mesa's lavapipe (not tried). Vulkan and Metal run on the Mac.
1. **On the M2:** the [checklist](#34-measurement-checklist-m2) with MoltenVK, and once more with KosmicKrisp (no code; it needs macOS 15).
2. **Cheap fixes in the shared Vulkan renderer**, as patches, offered upstream. Only those the census and the checklist call for:
   - Turn fans into indexed triangle lists, so those draws keep their vertex shaders on the GPU.
   - Give storage usage and a mutable format only to surfaces that become shadow sources, so the rest can be compressed. Measure GPU time at 5x before and after.
   - Choose MoltenVK's fast-math setting: 0 if MH4U needs accurate multiplication.
   - Emulate the logic ops MH4U uses. On Apple GPUs, MoltenVK can turn input attachments into framebuffer reads (my understanding); a bigger change.
3. **Native Metal, only on a trigger (3.3).** Each step is gated by screenshot diffs against MoltenVK and the software renderer, and by the checklist.
   1. Device, submission with an `MTLSharedEvent` per tick, stream buffers, the texture runtime and the rasterizer cache, presentation with the port's layouts, and screenshots.
   2. The rasterizer and pipeline caches, with shaders through SPIRV-Cross: first frames (the title screen), then the scenes in 2.6.
   3. The rest of the Vulkan renderer's paths: shadows, depth reinterpretation, custom textures, filters, upscaling, stereo, the cursor.
   4. Shader and pipeline caching: the SPIR-V cache, MSL, `MTLBinaryArchive`, and asynchronous compiles.
   5. What only Metal allows:
      - framebuffer reads for all logic ops and min/max blending
      - compression-friendly texture usage
      - the quaternion fix-up, by having the vertex shader store its outputs in a buffer that the fragment shader reads by `primitive_id`, or with mesh shaders (untried ideas)

   Target the Metal 3 API, which every Apple-silicon Mac on macOS 13 or later has. Metal 4 needs macOS 26 ([Metal by Example][metal4]).

### 3.3 The trigger for "later"

Start the native backend after stage 2 and a KosmicKrisp try, if one of these holds.

- **Speed.**
  - In the busy hunt at the resolution you play (3x, say), the app can't hold 60 fps, or more than 1% of frames take longer than 25 ms with warm caches.
  - The Metal HUD's GPU time is below about 70% of the frame interval (not GPU-bound).
  - The sample shows `VulkanWorker` near a full core, or the main thread waiting on it.
- **Picture.** A difference you notice in play is traced to a limit of Vulkan on Metal (most likely the lighting fix-up), and no fix fits in the shared Vulkan renderer.
- **Platform.** MoltenVK and KosmicKrisp both break on a macOS release and stay broken.

If none holds, MoltenVK is enough. Re-run the checklist when Azahar, MoltenVK or macOS change.

### 3.4 Measurement checklist (M2)

**Before you start**

1. **Build `mh4u` from `native-app` on the Mac**, or package it in CI; the macOS workflow ships only the Qt app.
   - Plug the Mac in and turn Low Power Mode off. Azahar treats Low Power Mode as a low-refresh display.
   - Note the macOS version, the M2's GPU core count and the display's refresh rate.
   - A fanless MacBook Air slows down after a few minutes, so keep every run the same length.
2. **Settings:**
   - `mh4u-app.ini`: `renderer = vulkan`, `fullscreen = on`, `vsync = off`.
   - `mh4u.ini`: `fps = 60`.
   - Pick one save and one busy route: 30 s in the area to settle, then 60 s of fighting a large monster with fire, smoke and a roar. Add 20 s outdoors in Val Habar, one cutscene and the equipment set screen.
3. **Launch from Terminal:**
   ```
   MTL_HUD_ENABLED=1 MTL_HUD_LOG_ENABLED=1 MTL_HUD_LOG_SHADER_ENABLED=1 ./mh4u
   ```
   - In a second Terminal, record shader compiles with their times and `cached: 0/1`:
     ```
     log stream --predicate 'subsystem == "com.apple.metal.hud"' > shaders.txt
     ```
     The once-a-second `metal-HUD:` frame lines go to the console ([Apple][apple-hud]).
   - Check `mh4u_log.txt` for the MoltenVK tooling-info line in 1.1. If it isn't there, a system Vulkan loader picked another driver.

**For each `resolution` = 1, 3, 5 (warm caches)**

4. **Play the route** and write down the HUD's average fps, GPU time per frame and the worst frame intervals. Do it again with `fps = 120` to see the headroom.
5. **During the fight, sample the CPU:**
   ```
   sample mh4u 10 -file ~/Desktop/mh4u-3x.txt
   ```
   Compare the samples of the main thread (game, PICA, recording), `VulkanWorker` (Vulkan replay and MoltenVK's encoding) and the shader and pipeline workers.
6. **Fight again with `bloom = off` and `depth_of_field = off`.** A big gain at 5x but not at 1x means the GPU limits there.

**Stutter**

7. **Cold.** Quit, move `~/Library/Application Support/Azahar/shaders/vulkan` aside, then play title → village → quest start → the first minute of the hunt. Count hitches over 50 ms and note the longest. Sum the compile times in `shaders.txt`. macOS keeps its own shader cache, so `cached: 1` lines are compiles it saved you.
8. **Warm.** Play the same route again. Hitches that remain are not shader compiles.
9. **Optional.** Put these in front of the launch command in step 3 to log MoltenVK's own conversion, compile and pipeline times every 600 frames:
   ```
   MVK_CONFIG_PERFORMANCE_TRACKING=1 MVK_CONFIG_PERFORMANCE_LOGGING_FRAME_COUNT=600
   ```

**Picture**

10. **Take screenshots (⇧⌘4)** of a lit monster and a hunter close-up in the field, shadows on the ground, the equipment set screen and a cutscene. Compare them with the same scenes on the PC, or with the runner's software screenshots. Look for streaks in the lighting and for missing or blocky shadows. Repeat once with `MVK_CONFIG_FAST_MATH_ENABLED=0`.
11. **Search `mh4u_log.txt`** for `falling back unconditionally`, `Unhandled logic_op`, `Unimplemented gas mode` and `blacklisted`.

**KosmicKrisp**

12. **Install the Vulkan SDK system-wide** (1.4.341 or later), so the app finds its `libvulkan.dylib`. Point `VK_DRIVER_FILES` at KosmicKrisp's driver file from the SDK, then repeat steps 4-8. The MoltenVK line should then be gone from the log. Not tried.

**Report**

13. **Fill one row per resolution:** average fps, worst 1% frame interval, GPU ms per frame, CPU share of the main thread and of `VulkanWorker`, and hitches cold and warm. Then read it against 3.3:
    - Holds 60 at 3x with room to spare: stay on MoltenVK.
    - GPU time close to the frame interval: GPU-bound. Lower the resolution or the filters, and try the storage-usage fix.
    - `VulkanWorker` saturated: the MoltenVK path is the limit. Try KosmicKrisp, then consider the native backend.

## Unknowns

- MH4U's PICA usage: logic ops, fans, lit draws, accurate multiplication (the census answers it).
- Every speed number on the M2: frame rate at 1x, 3x and 5x, GPU against CPU limits, stutter (the checklist answers it).
- How MoltenVK maps Azahar's usage flags to Metal (pixel-format views), whether its release binaries enable the private API, and which 16-bit formats it reports as usable. The log and Xcode's GPU capture can show these.
- KosmicKrisp's support for logic ops and geometry shaders, and its speed on this renderer.
- How large the lighting difference from the missing fix-up is in MH4U's models.

## Sources

Code: Azahar `5f3306d` (`src/video_core/renderer_vulkan/`, `rasterizer_cache/`, `shader/generator/`, `renderer_software/sw_clipper.cpp`, `CMakeLists.txt`, `CMakeModules/DownloadExternals.cmake`); Panda3DS `92e53d2` (`src/core/renderer_mtl/`, `include/renderer_mtl/`, `src/host_shaders/metal_shaders.metal`, `include/config.hpp`, `CMakeLists.txt`); this repository's `port/` and `port/app/` (branch `native-app`).

[mvk-rel]: https://github.com/KhronosGroup/MoltenVK/releases
[mvk-142]: https://zaikei.co.jp/article/20260721/862123.html
[mvk-128]: https://github.com/KhronosGroup/MoltenVK/releases/tag/v1.2.8
[mvk-env]: https://skia.googlesource.com/external/github.com/KhronosGroup/MoltenVK/+/403ae9cb2fa47d852fb7a2e7eb0105ec8f9fcba2/Common/MVKCommonEnvironment.h
[mvk-guide]: https://github.com/SRSaunders/MoltenVK/blob/main/Docs/MoltenVK_Runtime_UserGuide.md
[mvk-params]: https://skia.googlesource.com/external/github.com/KhronosGroup/MoltenVK/+/d20d13fe2735adb845636a81522df1b9d89c0fba/Docs/MoltenVK_Configuration_Parameters.md
[mvk-2059]: https://github.com/KhronosGroup/MoltenVK/issues/2059
[mvk-vulkanised]: https://dev.vulkan.org/user/pages/09.events/vulkanised-2023/vulkanised_2023_moltenvk_application_portability_with_vulkan_on_metal.pdf
[lunarg-2024]: https://www.lunarg.com/wp-content/uploads/2024/01/The-State-of-Vulkan-on-Apple-January-2024.pdf
[kk-13]: https://www.lunarg.com/lunarg-achieves-vulkan-1-3-conformance-with-kosmickrisp-on-apple-silicon/
[kk-xdc25-pdf]: https://www.lunarg.com/wp-content/uploads/2025/10/XDC-2025-KosmicKrisp-Overview.pdf
[kk-14]: https://www.khronos.org/news/permalink/kosmickrisp-achieves-vulkan-1.4-conformance-on-apple-silicon
[kk-xdc26]: https://www.phoronix.com/news/KosmicKrisp-Vulkan-1.4-2026
[kk-blender]: https://developer.blender.org/docs/features/gpu/vulkan/kosmic_krisp/
[dolphin-2022]: https://dolphin-emu.org/blog/2022/09/13/dolphin-progress-report-july-and-august-2022/
[cemu-1287]: https://github.com/cemu-project/Cemu/pull/1287
[citra-vk]: https://citra.azahar-emu.org/entry/citra-vulkan-announcement/
[apple-tex]: https://developer.apple.com/documentation/metal/optimizing-texture-data
[apple-hud]: https://developer.apple.com/documentation/xcode/monitoring-your-metal-apps-graphics-performance
[wwdc20]: https://developer.apple.com/videos/play/wwdc2020/10615/
[xcode26-metal]: https://support.circleci.com/hc/en-us/articles/40865586354971-Resolving-Metal-Toolchain-Execution-Missing-Error-in-Xcode-26
[metal-cpp]: https://developer.apple.com/metal/cpp/
[metal-cpp-lic]: https://gitee.com/git_sync/metal-cpp
[metal4]: https://metalbyexample.com/metal-4/
[spirv-cross-lic]: https://doc.qt.io/qt-6.5/qtshadertools-attribution-spirvcross.html
[az-765]: https://github.com/azahar-emu/azahar/issues/765
[az-703]: https://github.com/azahar-emu/azahar/issues/703
[az-1036]: https://github.com/azahar-emu/azahar/issues/1036
[az-1194]: https://github.com/azahar-emu/azahar/issues/1194
[az-1637]: https://github.com/azahar-emu/azahar/issues/1637
[az-2149]: https://github.com/azahar-emu/azahar/pull/2149
[az-2531]: https://github.com/azahar-emu/azahar/issues/2531
[az-2126.1a1]: https://newreleases.io/project/github/azahar-emu/azahar/release/2126.1-alpha1
