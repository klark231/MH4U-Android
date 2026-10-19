// JNI side of org.mh4u.port.MH4UInput: the combo / chord / per-device binding engine
// (src/input_map.*) fed with raw Android key and motion events, and merged with the on-screen
// controls into the one pad state the game reads (pad_device.cpp). Nothing is sent through
// Kotlin and back: an event changes the pad here.
#include <jni.h>

#include <algorithm>
#include <cmath>
#include <mutex>
#include <string>
#include <vector>

#include "input_map.h"
#include "pad_device.h"

namespace {

Port::InputMap::Engine engine;
std::mutex overlay_mutex;
std::uint32_t overlay_buttons = 0;
float overlay_sticks[4] = {0, 0, 0, 0};

std::string Str(JNIEnv* env, jstring s) {
    if (!s) return {};
    const char* c = env->GetStringUTFChars(s, nullptr);
    std::string out = c ? c : "";
    if (c) env->ReleaseStringUTFChars(s, c);
    return out;
}

/// Devices and on-screen controls together: buttons add up, a stick takes whichever is pushed
/// further.
void Push() {
    const auto device = engine.Poll();
    std::uint32_t buttons = device.buttons;
    float sticks[4];
    {
        std::scoped_lock lock(overlay_mutex);
        buttons |= overlay_buttons;
        for (int i = 0; i < 4; ++i) {
            sticks[i] = std::fabs(overlay_sticks[i]) > std::fabs(device.sticks[i])
                            ? overlay_sticks[i]
                            : device.sticks[i];
        }
    }
    Mh4uAndroid::Pad::Set(buttons, sticks);
}

} // namespace

extern "C" {

#define FN(name) JNIEXPORT auto JNICALL Java_org_mh4u_port_MH4UInput_##name

FN(nativeLoadDefaults)(JNIEnv*, jclass) -> void { engine.LoadDefaults(); Push(); }

FN(nativeOnKey)(JNIEnv* env, jclass, jstring device, jint code, jboolean down) -> void {
    engine.OnKey(Str(env, device), code, down);
    Push();
}

FN(nativeOnAxis)(JNIEnv* env, jclass, jstring device, jint axis, jfloat value) -> void {
    engine.OnAxis(Str(env, device), axis, value);
    Push();
}

FN(nativeOnDeviceRemoved)(JNIEnv* env, jclass, jstring device) -> void {
    engine.OnDeviceRemoved(Str(env, device));
    Push();
}

FN(nativeReleaseAll)(JNIEnv*, jclass) -> void {
    engine.ReleaseAll();
    {
        std::scoped_lock lock(overlay_mutex);
        overlay_buttons = 0;
        std::fill(std::begin(overlay_sticks), std::end(overlay_sticks), 0.0f);
    }
    Push();
}

/// The on-screen controls: the 3DS buttons they hold (a combo button holds several) and the
/// sticks (-1..1, up and right positive).
FN(nativeSetOverlay)(JNIEnv*, jclass, jint buttons, jfloat cx, jfloat cy, jfloat sx, jfloat sy) -> void {
    {
        std::scoped_lock lock(overlay_mutex);
        overlay_buttons = static_cast<std::uint32_t>(buttons);
        overlay_sticks[0] = cx; overlay_sticks[1] = cy;
        overlay_sticks[2] = sx; overlay_sticks[3] = sy;
    }
    Push();
}

// Learning a binding: hold a key or a combination, let go: that is the chord.
FN(nativeStartLearn)(JNIEnv*, jclass) -> void { engine.StartLearn(); }
FN(nativeCancelLearn)(JNIEnv*, jclass) -> void { engine.CancelLearn(); }
FN(nativeLearning)(JNIEnv*, jclass) -> jboolean { return engine.Learning(); }

// The chord learned, as text ("key:96@desc + axis+:18"), or null while nothing is complete.
FN(nativeTakeLearned)(JNIEnv* env, jclass) -> jstring {
    const auto learned = engine.TakeLearned();
    if (!learned) return nullptr;
    Port::InputMap::Engine tmp;
    tmp.AddBinding({*learned, Port::InputMap::A, {}});
    const std::string text = tmp.Serialize();
    const auto at = text.rfind("<= ");
    std::string chord = text.substr(at + 3);
    while (!chord.empty() && (chord.back() == '\n' || chord.back() == ' ')) chord.pop_back();
    return env->NewStringUTF(chord.c_str());
}

// Binds `chord` to the 3DS buttons in `mask`; call nativeRemoveFor first to rebind.
FN(nativeAddBinding)(JNIEnv* env, jclass, jint mask, jstring chord) -> jboolean {
    Port::InputMap::Engine tmp;
    std::vector<std::string> errors;
    if (!tmp.Parse("bind = A <= " + Str(env, chord) + "\n", &errors) || tmp.Bindings().empty()) {
        return JNI_FALSE;
    }
    auto b = tmp.Bindings().front();
    b.buttons = static_cast<std::uint32_t>(mask);
    engine.AddBinding(std::move(b));
    return JNI_TRUE;
}

FN(nativeRemoveFor)(JNIEnv*, jclass, jint mask) -> void {
    engine.RemoveBindingsFor(static_cast<std::uint32_t>(mask));
}

FN(nativeLoad)(JNIEnv* env, jclass, jstring text) -> jboolean {
    return engine.Parse(Str(env, text));
}

FN(nativeSave)(JNIEnv* env, jclass) -> jstring {
    return env->NewStringUTF(engine.Serialize().c_str());
}

} // extern "C"
