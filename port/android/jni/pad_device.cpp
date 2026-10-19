#include "pad_device.h"

#include <array>
#include <memory>

#include "common/param_package.h"
#include "common/settings.h"
#include "core/frontend/input.h"
#include "input_map.h"

namespace Mh4uAndroid::Pad {

namespace {

std::atomic<std::uint32_t> g_buttons{0};
std::array<std::atomic<float>, 4> g_sticks{};

using Port::InputMap::Button;

// the profile's button index (Settings::NativeButton) -> the engine's bit
constexpr std::uint32_t BitFor(int native_button) {
    switch (native_button) {
    case Settings::NativeButton::A: return Button::A;
    case Settings::NativeButton::B: return Button::B;
    case Settings::NativeButton::X: return Button::X;
    case Settings::NativeButton::Y: return Button::Y;
    case Settings::NativeButton::Up: return Button::Up;
    case Settings::NativeButton::Down: return Button::Down;
    case Settings::NativeButton::Left: return Button::Left;
    case Settings::NativeButton::Right: return Button::Right;
    case Settings::NativeButton::L: return Button::L;
    case Settings::NativeButton::R: return Button::R;
    case Settings::NativeButton::Start: return Button::Start;
    case Settings::NativeButton::Select: return Button::Select;
    case Settings::NativeButton::ZL: return Button::ZL;
    case Settings::NativeButton::ZR: return Button::ZR;
    case Settings::NativeButton::Home: return Button::Home;
    default: return 0;
    }
}

class PadButton final : public Input::ButtonDevice {
public:
    explicit PadButton(std::uint32_t bit_) : bit(bit_) {}
    bool GetStatus() const override {
        return (g_buttons.load(std::memory_order_relaxed) & bit) != 0;
    }

private:
    std::uint32_t bit;
};

class PadStick final : public Input::AnalogDevice {
public:
    explicit PadStick(int first_) : first(first_) {}
    std::tuple<float, float> GetStatus() const override {
        return {g_sticks[first].load(std::memory_order_relaxed),
                g_sticks[first + 1].load(std::memory_order_relaxed)};
    }

private:
    int first;
};

class ButtonFactory final : public Input::Factory<Input::ButtonDevice> {
public:
    std::unique_ptr<Input::ButtonDevice> Create(const Common::ParamPackage& params) override {
        return std::make_unique<PadButton>(static_cast<std::uint32_t>(params.Get("bit", 0)));
    }
};

class AnalogFactory final : public Input::Factory<Input::AnalogDevice> {
public:
    std::unique_ptr<Input::AnalogDevice> Create(const Common::ParamPackage& params) override {
        return std::make_unique<PadStick>(params.Get("stick", 0) == 0 ? 0 : 2);
    }
};

} // namespace

void RegisterEngines();
void BuildProfile();

void Init() {
    // the profile is rebuilt on every settings change; the engines are registered once
    static bool registered = false;
    if (!registered) {
        registered = true;
        RegisterEngines();
    }
    BuildProfile();
}

void RegisterEngines() {
    Input::RegisterFactory<Input::ButtonDevice>("mh4u", std::make_shared<ButtonFactory>());
    Input::RegisterFactory<Input::AnalogDevice>("mh4u", std::make_shared<AnalogFactory>());
}

void BuildProfile() {
    // every 3DS button reads its own bit; Power and the debug buttons read nothing
    auto& profile = Settings::values.current_input_profile;
    for (int i = 0; i < Settings::NativeButton::NumButtons; ++i) {
        Common::ParamPackage p{{"engine", "mh4u"}, {"bit", std::to_string(BitFor(i))}};
        profile.buttons[i] = p.Serialize();
    }
    profile.analogs[Settings::NativeAnalog::CirclePad] =
        Common::ParamPackage{{"engine", "mh4u"}, {"stick", "0"}}.Serialize();
    profile.analogs[Settings::NativeAnalog::CStick] =
        Common::ParamPackage{{"engine", "mh4u"}, {"stick", "1"}}.Serialize();
    // no motion sensor, no touch from a pad: the screen's touches go through the window
    profile.motion_device = "engine:motion_emu,update_period:100,sensitivity:0.01,tilt_clamp:90.0";
    profile.touch_device = "engine:emu_window";
}

void Shutdown() {
    Input::UnregisterFactory<Input::ButtonDevice>("mh4u");
    Input::UnregisterFactory<Input::AnalogDevice>("mh4u");
}

void Set(std::uint32_t buttons, const float sticks[4]) {
    g_buttons.store(buttons, std::memory_order_relaxed);
    for (int i = 0; i < 4; ++i) {
        g_sticks[i].store(sticks[i], std::memory_order_relaxed);
    }
}

} // namespace Mh4uAndroid::Pad
