// Input mapping with combos and per-device bindings, for frontends that get raw key and
// axis events from many devices (Android: any gamepad, keyboard or mouse the system lists).
// It knows nothing of Azahar or of Android: a frontend feeds it events and polls the 3DS
// state it makes. Used by the Android app (android/), testable on any host.
//
// A binding is "when ALL of these sources are held (a chord), press ALL of these 3DS buttons
// (a combo)". One key to several buttons, several keys to one button and several keys to
// several buttons are all the same thing. Where two active bindings share a source and one's
// chord contains the other's (Select, and Select+Start), only the longer chord acts, so a
// chord never fires its parts as well.
#pragma once

#include <array>
#include <cstdint>
#include <mutex>
#include <optional>
#include <string>
#include <utility>
#include <vector>

namespace Port::InputMap {

enum Button : std::uint32_t {
    A = 1u << 0, B = 1u << 1, X = 1u << 2, Y = 1u << 3,
    L = 1u << 4, R = 1u << 5, ZL = 1u << 6, ZR = 1u << 7,
    Start = 1u << 8, Select = 1u << 9,
    Up = 1u << 10, Down = 1u << 11, Left = 1u << 12, Right = 1u << 13,
    Home = 1u << 14,
};

enum StickAxis { CircleX = 0, CircleY = 1, CStickX = 2, CStickY = 3 };

struct Source {
    enum class Kind { Key, AxisPos, AxisNeg };
    Kind kind = Kind::Key;
    int code = 0;        // Android KeyEvent keycode, or MotionEvent axis
    std::string device;  // InputDevice descriptor; empty: from any device
    bool operator==(const Source& o) const {
        return kind == o.kind && code == o.code && device == o.device;
    }
};

struct Binding {
    std::vector<Source> chord;                 // all held
    std::uint32_t buttons = 0;                 // all pressed
    std::array<float, 4> push{};               // and the sticks pushed this far (-1..1)
};

struct AxisMap { // an analog axis that moves a stick
    std::string device; // empty: any
    int axis = 0;
    StickAxis stick = CircleX;
    bool invert = false;
};

struct State {
    std::uint32_t buttons = 0;
    std::array<float, 4> sticks{}; // CircleX, CircleY, CStickX, CStickY; up and right positive
    bool operator==(const State& o) const { return buttons == o.buttons && sticks == o.sticks; }
};

class Engine {
public:
    /// Android's pad layout mapped the way a 3DS lies in the hand: the pad's right face
    /// button is the 3DS A, and so on. Left stick: circle pad; right stick: C-stick.
    void LoadDefaults();

    void Clear();
    void AddBinding(Binding b);
    void AddAxisMap(AxisMap m);
    const std::vector<Binding>& Bindings() const { return bindings; }
    /// Drops the bindings that use `button` alone or in a combo, e.g. before binding it anew.
    void RemoveBindingsFor(std::uint32_t buttons);

    // Events. `device` is the Android InputDevice descriptor (any stable id works); a frontend's
    // own on-screen combo buttons use the device "virtual" and a code of their own.
    void OnKey(const std::string& device, int keycode, bool down);
    void OnAxis(const std::string& device, int axis, float value);
    /// A device that went away: everything it held is released.
    void OnDeviceRemoved(const std::string& device);
    void ReleaseAll();

    State Poll() const;

    // Learning a binding: after StartLearn() events make no output; everything pressed
    // until all of it is released again is the chord (so a combo is learned by holding it).
    void StartLearn();
    bool Learning() const;
    std::optional<std::vector<Source>> TakeLearned();
    void CancelLearn();

    // Text form, one binding per line:   bind = A,B <= key:97@descriptor + axis+:18
    // and one line per stick axis:       stick = circle_x <= axis:0@descriptor invert
    // "#" starts a comment; "@device" is left out for any device. Parse skips lines it can't
    // read and says which in `errors`.
    std::string Serialize() const;
    bool Parse(const std::string& text, std::vector<std::string>* errors = nullptr);

    static std::string ButtonName(std::uint32_t single);
    static std::uint32_t ButtonFromName(const std::string& name); // 0 if unknown

private:
    struct Held { std::string device; Source::Kind kind; int code; };
    bool SourceHeld(const Source& s) const;
    void SetHeld(const std::string& device, Source::Kind kind, int code, bool on);
    void LearnTouch();

    mutable std::mutex mutex;
    std::vector<Binding> bindings;
    std::vector<AxisMap> axis_maps;
    std::vector<Held> held;                                   // digital sources now pressed
    std::vector<std::pair<std::pair<std::string, int>, float>> axes; // last value per device+axis
    bool learning = false;
    std::vector<Source> learn_peak, learn_now;
    std::optional<std::vector<Source>> learned;
};

} // namespace Port::InputMap
