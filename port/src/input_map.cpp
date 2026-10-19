#include "input_map.h"

#include <algorithm>
#include <cmath>
#include <cstdlib>
#include <sstream>

namespace Port::InputMap {

namespace {

constexpr float kPress = 0.5f, kRelease = 0.35f, kDeadzone = 0.12f;

struct Name { const char* text; std::uint32_t bit; };
constexpr Name kNames[] = {
    {"A", A}, {"B", B}, {"X", X}, {"Y", Y}, {"L", L}, {"R", R}, {"ZL", ZL}, {"ZR", ZR},
    {"Start", Start}, {"Select", Select}, {"Up", Up}, {"Down", Down}, {"Left", Left},
    {"Right", Right}, {"Home", Home},
};
constexpr const char* kStickNames[] = {"circle_x", "circle_y", "cstick_x", "cstick_y"};

std::string Trim(const std::string& s) {
    const auto b = s.find_first_not_of(" \t\r");
    if (b == std::string::npos) return {};
    return s.substr(b, s.find_last_not_of(" \t\r") - b + 1);
}

std::vector<std::string> Split(const std::string& s, char sep) {
    std::vector<std::string> out;
    std::string cur;
    std::istringstream in(s);
    while (std::getline(in, cur, sep)) out.push_back(Trim(cur));
    return out;
}

std::string SourceText(const Source& s) {
    const char* kind = s.kind == Source::Kind::Key ? "key" : s.kind == Source::Kind::AxisPos ? "axis+" : "axis-";
    return std::string(kind) + ":" + std::to_string(s.code) + (s.device.empty() ? "" : "@" + s.device);
}

std::optional<Source> ParseSource(const std::string& text) {
    const auto colon = text.find(':');
    if (colon == std::string::npos) return std::nullopt;
    Source s;
    const std::string kind = text.substr(0, colon);
    if (kind == "key") s.kind = Source::Kind::Key;
    else if (kind == "axis+" || kind == "axis") s.kind = Source::Kind::AxisPos;
    else if (kind == "axis-") s.kind = Source::Kind::AxisNeg;
    else return std::nullopt;
    const auto at = text.find('@', colon);
    const std::string num = text.substr(colon + 1, at == std::string::npos ? at : at - colon - 1);
    char* end = nullptr;
    const long v = std::strtol(num.c_str(), &end, 10);
    if (num.empty() || *end) return std::nullopt;
    s.code = static_cast<int>(v);
    if (at != std::string::npos) s.device = text.substr(at + 1);
    return s;
}

bool Contains(const std::vector<Source>& outer, const std::vector<Source>& inner) {
    return std::all_of(inner.begin(), inner.end(), [&](const Source& s) {
        return std::find(outer.begin(), outer.end(), s) != outer.end();
    });
}

} // namespace

// Android constants: KeyEvent.KEYCODE_* and MotionEvent.AXIS_*
namespace Android {
constexpr int DPAD_UP = 19, DPAD_DOWN = 20, DPAD_LEFT = 21, DPAD_RIGHT = 22;
constexpr int BUTTON_A = 96, BUTTON_B = 97, BUTTON_X = 99, BUTTON_Y = 100;
constexpr int BUTTON_L1 = 102, BUTTON_R1 = 103, BUTTON_L2 = 104, BUTTON_R2 = 105;
constexpr int BUTTON_START = 108, BUTTON_SELECT = 109, BUTTON_MODE = 110;
constexpr int AXIS_X = 0, AXIS_Y = 1, AXIS_Z = 11, AXIS_RZ = 14, AXIS_HAT_X = 15, AXIS_HAT_Y = 16,
              AXIS_LTRIGGER = 17, AXIS_RTRIGGER = 18, AXIS_BRAKE = 23, AXIS_GAS = 22;
} // namespace Android

void Engine::LoadDefaults() {
    using namespace Android;
    Clear();
    auto key = [](int code) { return Source{Source::Kind::Key, code, {}}; };
    auto axis = [](int code, bool pos) { return Source{pos ? Source::Kind::AxisPos : Source::Kind::AxisNeg, code, {}}; };
    const std::pair<int, std::uint32_t> keys[] = {
        {BUTTON_B, A}, {BUTTON_A, B}, {BUTTON_Y, X}, {BUTTON_X, Y},
        {BUTTON_L1, L}, {BUTTON_R1, R}, {BUTTON_L2, ZL}, {BUTTON_R2, ZR},
        {BUTTON_START, Start}, {BUTTON_SELECT, Select}, {BUTTON_MODE, Home},
        {DPAD_UP, Up}, {DPAD_DOWN, Down}, {DPAD_LEFT, Left}, {DPAD_RIGHT, Right},
    };
    for (auto [code, bit] : keys) AddBinding({{key(code)}, bit, {}});
    // pads that send the d-pad as a hat and the triggers as axes
    AddBinding({{axis(AXIS_HAT_Y, false)}, Up, {}});
    AddBinding({{axis(AXIS_HAT_Y, true)}, Down, {}});
    AddBinding({{axis(AXIS_HAT_X, false)}, Left, {}});
    AddBinding({{axis(AXIS_HAT_X, true)}, Right, {}});
    AddBinding({{axis(AXIS_LTRIGGER, true)}, ZL, {}});
    AddBinding({{axis(AXIS_RTRIGGER, true)}, ZR, {}});
    AddBinding({{axis(AXIS_BRAKE, true)}, ZL, {}});
    AddBinding({{axis(AXIS_GAS, true)}, ZR, {}});
    AddAxisMap({"", AXIS_X, CircleX, false});
    AddAxisMap({"", AXIS_Y, CircleY, true}); // Android's Y is down-positive
    AddAxisMap({"", AXIS_Z, CStickX, false});
    AddAxisMap({"", AXIS_RZ, CStickY, true});
}

void Engine::Clear() {
    std::lock_guard lock(mutex);
    bindings.clear();
    axis_maps.clear();
    held.clear();
    axes.clear();
}

void Engine::AddBinding(Binding b) {
    std::lock_guard lock(mutex);
    bindings.push_back(std::move(b));
}

void Engine::AddAxisMap(AxisMap m) {
    std::lock_guard lock(mutex);
    axis_maps.push_back(std::move(m));
}

void Engine::RemoveBindingsFor(std::uint32_t buttons) {
    std::lock_guard lock(mutex);
    bindings.erase(std::remove_if(bindings.begin(), bindings.end(),
                                  [&](const Binding& b) { return b.buttons & buttons; }),
                   bindings.end());
}

void Engine::SetHeld(const std::string& device, Source::Kind kind, int code, bool on) {
    auto it = std::find_if(held.begin(), held.end(), [&](const Held& h) {
        return h.device == device && h.kind == kind && h.code == code;
    });
    if (on && it == held.end()) held.push_back({device, kind, code});
    if (!on && it != held.end()) held.erase(it);
}

bool Engine::SourceHeld(const Source& s) const {
    return std::any_of(held.begin(), held.end(), [&](const Held& h) {
        return h.kind == s.kind && h.code == s.code && (s.device.empty() || s.device == h.device);
    });
}

void Engine::LearnTouch() {
    // learn_now: held now; learn_peak: the biggest set held at once, in press order
    learn_now.clear();
    for (const Held& h : held) learn_now.push_back({h.kind, h.code, h.device});
    for (const Source& s : learn_now) {
        if (std::find(learn_peak.begin(), learn_peak.end(), s) == learn_peak.end()) learn_peak.push_back(s);
    }
    if (learn_now.empty() && !learn_peak.empty()) {
        learned = learn_peak;
        learn_peak.clear();
        learning = false;
    }
}

void Engine::OnKey(const std::string& device, int keycode, bool down) {
    std::lock_guard lock(mutex);
    SetHeld(device, Source::Kind::Key, keycode, down);
    if (learning) LearnTouch();
}

void Engine::OnAxis(const std::string& device, int axis, float value) {
    std::lock_guard lock(mutex);
    auto it = std::find_if(axes.begin(), axes.end(), [&](const auto& e) {
        return e.first.first == device && e.first.second == axis;
    });
    if (it == axes.end()) {
        axes.push_back({{device, axis}, value});
    } else {
        it->second = value;
    }
    // a digital view of the axis, with hysteresis so a wobbling trigger doesn't chatter
    for (auto kind : {Source::Kind::AxisPos, Source::Kind::AxisNeg}) {
        const float v = kind == Source::Kind::AxisPos ? value : -value;
        const bool was = std::any_of(held.begin(), held.end(), [&](const Held& h) {
            return h.device == device && h.kind == kind && h.code == axis;
        });
        if (!was && v >= kPress) SetHeld(device, kind, axis, true);
        if (was && v < kRelease) SetHeld(device, kind, axis, false);
    }
    if (learning) LearnTouch();
}

void Engine::OnDeviceRemoved(const std::string& device) {
    std::lock_guard lock(mutex);
    held.erase(std::remove_if(held.begin(), held.end(), [&](const Held& h) { return h.device == device; }), held.end());
    axes.erase(std::remove_if(axes.begin(), axes.end(), [&](const auto& e) { return e.first.first == device; }), axes.end());
    if (learning) LearnTouch();
}

void Engine::ReleaseAll() {
    std::lock_guard lock(mutex);
    held.clear();
    axes.clear();
    learn_peak.clear();
    learn_now.clear();
}

State Engine::Poll() const {
    std::lock_guard lock(mutex);
    State out;
    if (learning) return out;
    std::vector<const Binding*> active;
    for (const Binding& b : bindings) {
        if (!b.chord.empty() && std::all_of(b.chord.begin(), b.chord.end(), [&](const Source& s) { return SourceHeld(s); })) {
            active.push_back(&b);
        }
    }
    for (const Binding* b : active) {
        // a chord that is a strict part of another active chord stays quiet
        const bool shadowed = std::any_of(active.begin(), active.end(), [&](const Binding* o) {
            return o != b && o->chord.size() > b->chord.size() && Contains(o->chord, b->chord);
        });
        if (shadowed) continue;
        out.buttons |= b->buttons;
        for (int i = 0; i < 4; ++i) out.sticks[i] += b->push[i];
    }
    // analog: the device that pushes hardest wins an axis
    float best[4] = {0, 0, 0, 0};
    for (const AxisMap& m : axis_maps) {
        for (const auto& e : axes) {
            if (e.first.second != m.axis || (!m.device.empty() && m.device != e.first.first)) continue;
            float v = m.invert ? -e.second : e.second;
            if (std::fabs(v) < kDeadzone) continue;
            v = (v - std::copysign(kDeadzone, v)) / (1.0f - kDeadzone);
            if (std::fabs(v) > std::fabs(best[m.stick])) best[m.stick] = v;
        }
    }
    for (int i = 0; i < 4; ++i) {
        out.sticks[i] = std::clamp(out.sticks[i] + best[i], -1.0f, 1.0f);
    }
    return out;
}

void Engine::StartLearn() {
    std::lock_guard lock(mutex);
    learning = true;
    learned.reset();
    learn_peak.clear();
    learn_now.clear();
    // what is held at this moment (the button that opened the dialog) isn't part of it
    held.clear();
}

bool Engine::Learning() const {
    std::lock_guard lock(mutex);
    return learning;
}

std::optional<std::vector<Source>> Engine::TakeLearned() {
    std::lock_guard lock(mutex);
    auto out = std::move(learned);
    learned.reset();
    return out;
}

void Engine::CancelLearn() {
    std::lock_guard lock(mutex);
    learning = false;
    learned.reset();
    learn_peak.clear();
}

std::string Engine::ButtonName(std::uint32_t single) {
    for (const Name& n : kNames) if (n.bit == single) return n.text;
    return {};
}

std::uint32_t Engine::ButtonFromName(const std::string& name) {
    std::string lower = name;
    std::transform(lower.begin(), lower.end(), lower.begin(), [](unsigned char c) { return std::tolower(c); });
    for (const Name& n : kNames) {
        std::string t = n.text;
        std::transform(t.begin(), t.end(), t.begin(), [](unsigned char c) { return std::tolower(c); });
        if (t == lower) return n.bit;
    }
    return 0;
}

std::string Engine::Serialize() const {
    std::lock_guard lock(mutex);
    std::ostringstream out;
    out << "# MH4U input bindings. bind = <3DS buttons, comma separated> <= <source> [+ <source> ...]\n"
           "# sources: key:<Android keycode> axis+:<axis> axis-:<axis>, then @<device> for one device\n";
    for (const Binding& b : bindings) {
        std::string buttons;
        for (const Name& n : kNames) {
            if (b.buttons & n.bit) buttons += (buttons.empty() ? "" : ",") + std::string(n.text);
        }
        for (int i = 0; i < 4; ++i) {
            if (b.push[i] != 0) buttons += (buttons.empty() ? "" : ",") + std::string(kStickNames[i]) + (b.push[i] < 0 ? "-" : "+");
        }
        std::string chord;
        for (const Source& s : b.chord) chord += (chord.empty() ? "" : " + ") + SourceText(s);
        out << "bind = " << buttons << " <= " << chord << "\n";
    }
    for (const AxisMap& m : axis_maps) {
        out << "stick = " << kStickNames[m.stick] << " <= axis:" << m.axis
            << (m.device.empty() ? "" : "@" + m.device) << (m.invert ? " invert" : "") << "\n";
    }
    return out.str();
}

bool Engine::Parse(const std::string& text, std::vector<std::string>* errors) {
    std::vector<Binding> new_bindings;
    std::vector<AxisMap> new_maps;
    bool ok = true;
    std::istringstream in(text);
    std::string line;
    int number = 0;
    auto fail = [&](const std::string& why) {
        ok = false;
        if (errors) errors->push_back("line " + std::to_string(number) + ": " + why);
    };
    while (std::getline(in, line)) {
        ++number;
        if (const auto hash = line.find('#'); hash != std::string::npos) line.erase(hash);
        line = Trim(line);
        if (line.empty()) continue;
        const auto eq = line.find('=');
        const auto arrow = line.find("<=");
        if (eq == std::string::npos || arrow == std::string::npos || arrow < eq) { fail("expected key = outputs <= sources"); continue; }
        const std::string key = Trim(line.substr(0, eq));
        const std::string outputs = Trim(line.substr(eq + 1, arrow - eq - 1));
        const std::string sources = Trim(line.substr(arrow + 2));
        if (key == "bind") {
            Binding b;
            bool bad = false;
            for (const std::string& o : Split(outputs, ',')) {
                if (const auto bit = ButtonFromName(o)) { b.buttons |= bit; continue; }
                bool found = false;
                for (int i = 0; i < 4 && !found; ++i) {
                    for (const char sign : {'+', '-'}) {
                        if (o == std::string(kStickNames[i]) + sign) { b.push[i] = sign == '+' ? 1.0f : -1.0f; found = true; break; }
                    }
                }
                if (!found) { fail("unknown output '" + o + "'"); bad = true; break; }
            }
            // split on " + " (with spaces), because "axis+:17" has a plus of its own
            std::vector<std::string> parts;
            std::size_t pos = 0;
            for (;;) {
                const auto next = sources.find(" + ", pos);
                parts.push_back(Trim(sources.substr(pos, next == std::string::npos ? next : next - pos)));
                if (next == std::string::npos) break;
                pos = next + 3;
            }
            for (const std::string& p : parts) {
                if (auto src = ParseSource(p)) b.chord.push_back(*src); else { fail("unreadable source '" + p + "'"); bad = true; }
            }
            if (!bad && (b.buttons || std::any_of(b.push.begin(), b.push.end(), [](float f) { return f != 0; })) && !b.chord.empty()) {
                new_bindings.push_back(std::move(b));
            } else if (!bad) {
                fail("nothing to press or nothing to press it with");
            }
        } else if (key == "stick") {
            AxisMap m;
            int stick = -1;
            for (int i = 0; i < 4; ++i) if (outputs == kStickNames[i]) stick = i;
            const bool invert = sources.size() >= 6 && sources.compare(sources.size() - 6, 6, "invert") == 0;
            const auto src = ParseSource(Trim(invert ? sources.substr(0, sources.size() - 6) : sources));
            if (stick < 0 || !src) { fail("unreadable stick line"); continue; }
            m.stick = static_cast<StickAxis>(stick);
            m.axis = src->code;
            m.device = src->device;
            m.invert = invert;
            new_maps.push_back(std::move(m));
        } else {
            fail("unknown key '" + key + "'");
        }
    }
    std::lock_guard lock(mutex);
    bindings = std::move(new_bindings);
    axis_maps = std::move(new_maps);
    held.clear();
    axes.clear();
    return ok;
}

} // namespace Port::InputMap
