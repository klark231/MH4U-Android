#include "input.h"

#include <atomic>
#include <cmath>
#include <memory>
#include <tuple>
#include <SDL.h>
#include <fmt/format.h>

#include "common/logging/log.h"
#include "common/param_package.h"
#include "common/settings.h"
#include "common/string_util.h"
#include "config.h"
#include "core/frontend/input.h"
#include "input_common/main.h"

namespace App::Input {

namespace {

namespace NativeAnalog = Settings::NativeAnalog;
namespace NativeButton = Settings::NativeButton;

constexpr const char* ANY = "mh4u_any";

/// SetMuted: the in-game menu is up, and the game gets nothing.
std::atomic<bool> muted{false};

/// Pressed while either of two buttons is. Muted, it reads released, and a button held when the
/// mute ends stays released until it is let go (the B that closed the menu doesn't also roll).
class AnyButton final : public ::Input::ButtonDevice {
public:
    AnyButton(std::unique_ptr<::Input::ButtonDevice> a_, std::unique_ptr<::Input::ButtonDevice> b_)
        : a(std::move(a_)), b(std::move(b_)) {}
    bool GetStatus() const override {
        const bool pressed = (a && a->GetStatus()) || (b && b->GetStatus());
        if (muted.load(std::memory_order_relaxed)) {
            held_over = true;
            return false;
        }
        if (held_over) {
            held_over = pressed;
            return false;
        }
        return pressed;
    }

private:
    std::unique_ptr<::Input::ButtonDevice> a, b;
    mutable bool held_over = false;  // the emulation thread is the only reader
};

/// Whichever of two sticks is pushed further. Muted, it reads centred, and a stick pushed when
/// the mute ends reads centred until it has come back.
class AnyAnalog final : public ::Input::AnalogDevice {
public:
    AnyAnalog(std::unique_ptr<::Input::AnalogDevice> a_, std::unique_ptr<::Input::AnalogDevice> b_)
        : a(std::move(a_)), b(std::move(b_)) {}
    std::tuple<float, float> GetStatus() const override {
        const auto sa = a ? a->GetStatus() : std::tuple<float, float>{};
        const auto sb = b ? b->GetStatus() : std::tuple<float, float>{};
        const auto length = [](const std::tuple<float, float>& s) {
            return std::hypot(std::get<0>(s), std::get<1>(s));
        };
        const auto status = length(sa) >= length(sb) ? sa : sb;
        if (muted.load(std::memory_order_relaxed)) {
            held_over = true;
            return {};
        }
        if (held_over) {
            held_over = length(status) > 0.25f;
            return {};
        }
        return status;
    }

private:
    std::unique_ptr<::Input::AnalogDevice> a, b;
    mutable bool held_over = false;
};

/// "engine:mh4u_any,a:<params>,b:<params>": the inner parameters are serialized again, which
/// escapes them (Common::ParamPackage).
template <typename Device, typename Any>
class AnyFactory final : public ::Input::Factory<Device> {
public:
    std::unique_ptr<Device> Create(const Common::ParamPackage& params) override {
        const auto inner = [&](const char* key) -> std::unique_ptr<Device> {
            const std::string p = params.Get(key, "");
            return p.empty() ? nullptr : ::Input::CreateDevice<Device>(p);
        };
        return std::make_unique<Any>(inner("a"), inner("b"));
    }
};

/// Every binding goes through "mh4u_any", even a single one, so that SetMuted reaches it.
std::string Join(const std::string& a, const std::string& b) {
    if (a.empty() && b.empty()) {
        return "";
    }
    Common::ParamPackage p{{"engine", ANY}};
    if (!a.empty()) {
        p.Set("a", a);
    }
    if (!b.empty()) {
        p.Set("b", b);
    }
    return p.Serialize();
}

struct Controller {
    std::string guid;
    int port = 0;
    std::string name;
};

/// The first game controller SDL knows, and its port among controllers of the same kind (as
/// Azahar's SDL engine numbers them).
bool FirstController(Controller& out) {
    for (int i = 0; i < SDL_NumJoysticks(); ++i) {
        if (!SDL_IsGameController(i)) {
            continue;
        }
        char guid[33];
        SDL_JoystickGetGUIDString(SDL_JoystickGetDeviceGUID(i), guid, sizeof(guid));
        out.guid = guid;
        out.port = 0;
        for (int j = 0; j < i; ++j) {
            char other[33];
            SDL_JoystickGetGUIDString(SDL_JoystickGetDeviceGUID(j), other, sizeof(other));
            out.port += out.guid == other ? 1 : 0;
        }
        const char* name = SDL_GameControllerNameForIndex(i);
        out.name = name ? name : "controller";
        return true;
    }
    return false;
}

std::string controller_description = "no controller";

std::string KeyParam(const std::string& name) {
    const int code = KeyCode(name);
    return code ? InputCommon::GenerateKeyboardParam(code) : "";
}

std::string StickKeysParam(const std::string& spec) {
    const auto parts = Common::SplitString(spec, ',');
    if (parts.size() < 4) {
        return "";
    }
    int codes[5] = {};
    for (std::size_t i = 0; i < 5 && i < parts.size(); ++i) {
        codes[i] = KeyCode(Common::StripSpaces(parts[i]));
    }
    if (!codes[0] && !codes[1] && !codes[2] && !codes[3]) {
        return "";
    }
    return InputCommon::GenerateAnalogParamFromKeys(codes[0], codes[1], codes[2], codes[3],
                                                    codes[4], 0.5f);
}

} // namespace

void Register() {
    ::Input::RegisterFactory<::Input::ButtonDevice>(
        ANY, std::make_shared<AnyFactory<::Input::ButtonDevice, AnyButton>>());
    ::Input::RegisterFactory<::Input::AnalogDevice>(
        ANY, std::make_shared<AnyFactory<::Input::AnalogDevice, AnyAnalog>>());
}

int KeyCode(const std::string& name) {
    if (name.empty()) {
        return 0;
    }
    const SDL_Scancode code = SDL_GetScancodeFromName(name.c_str());
    if (code == SDL_SCANCODE_UNKNOWN) {
        LOG_WARNING(Frontend, "no key called \"{}\"", name);
        return 0;
    }
    return static_cast<int>(code);
}

void BuildProfile(const Config& config) {
    auto& profile = Settings::values.current_input_profile;
    Controller pad;
    const bool have_pad = config.controller_auto && FirstController(pad);
    Common::ParamPackage pad_base;
    if (have_pad) {
        pad_base = Common::ParamPackage{{"engine", "sdl"}, {"guid", pad.guid},
                                        {"port", std::to_string(pad.port)}, {"api", "controller"}};
        controller_description = fmt::format("controller: {} (port {})", pad.name, pad.port);
    } else {
        controller_description = config.controller_auto ? "no controller found" : "controller off";
    }
    for (int i = 0; i < NativeButton::NumButtons; ++i) {
        if (!config.button_params[i].empty()) {
            profile.buttons[i] = Join(config.button_params[i], "");
            continue;
        }
        std::string from_pad;
        // the Guide button opens the app's menu (ui/): it isn't the 3DS's HOME button here
        if (have_pad && i != NativeButton::Home) {
            const auto bind = InputCommon::GetControllerButtonBinds(pad_base, i);
            if (bind.Has("engine")) {
                from_pad = bind.Serialize();
            }
        }
        profile.buttons[i] = Join(KeyParam(config.keys[i]), from_pad);
    }
    for (int i = 0; i < NativeAnalog::NumAnalogs; ++i) {
        if (!config.stick_params[i].empty()) {
            profile.analogs[i] = Join(config.stick_params[i], "");
            continue;
        }
        std::string from_pad;
        if (have_pad) {
            const auto bind = InputCommon::GetControllerAnalogBinds(pad_base, i);
            if (bind.Has("engine")) {
                from_pad = bind.Serialize();
            }
        }
        profile.analogs[i] = Join(StickKeysParam(config.stick_keys[i]), from_pad);
    }
    // the mouse is the stylus (the window sends it as touches); no motion sensor
    profile.touch_device = "engine:emu_window";
    profile.motion_device = "engine:motion_emu,update_period:100,sensitivity:0.01,tilt_clamp:90.0";
}

std::string ControllerDescription() {
    return controller_description;
}

void SetMuted(bool on) {
    muted.store(on, std::memory_order_relaxed);
}

} // namespace App::Input
