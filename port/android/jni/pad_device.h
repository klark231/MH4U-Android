// The console's controls as Azahar's input system sees them. One state, written by the
// frontend (the combo engine's result plus the on-screen controls) and read by the buttons and
// sticks the 3DS profile points at ("engine:mh4u"). No per-key plumbing: Azahar polls it.
#pragma once

#include <atomic>
#include <cstdint>

namespace Mh4uAndroid::Pad {

/// Registers the "mh4u" button and analog engines, and points the input profile at them.
void Init();
void Shutdown();

/// Replaces everything the pad says: `buttons` is a Port::InputMap::Button mask, `sticks` is
/// circle x, circle y, c-stick x, c-stick y (up and right positive, -1..1).
void Set(std::uint32_t buttons, const float sticks[4]);

} // namespace Mh4uAndroid::Pad
