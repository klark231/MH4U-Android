#include "keyboard_android.h"

#include <string>

#include "jni_env.h"

namespace Mh4uAndroid {

namespace {

jclass g_class = nullptr;
jmethodID g_ask = nullptr;    // static String[] ask(String hint, int maxLength, boolean multiline,
                              //                     String[] buttons)
jmethodID g_error = nullptr;  // static void showError(String)

std::string ToString(JNIEnv* env, jstring s) {
    if (!s) {
        return {};
    }
    const char* chars = env->GetStringUTFChars(s, nullptr);
    std::string out = chars ? chars : "";
    if (chars) {
        env->ReleaseStringUTFChars(s, chars);
    }
    return out;
}

} // namespace

void InitKeyboardJni() {
    JNIEnv* env = Env();
    g_class = reinterpret_cast<jclass>(env->NewGlobalRef(env->FindClass("org/mh4u/port/Keyboard")));
    g_ask = env->GetStaticMethodID(
        g_class, "ask", "(Ljava/lang/String;IZ[Ljava/lang/String;)[Ljava/lang/String;");
    g_error = env->GetStaticMethodID(g_class, "showError", "(Ljava/lang/String;)V");
}

void Keyboard::Execute(const Frontend::KeyboardConfig& config) {
    Frontend::SoftwareKeyboard::Execute(config);
    JNIEnv* env = Env();

    jclass string_class = env->FindClass("java/lang/String");
    // ok / cancel / forgot, as the game named them
    std::vector<std::string> names = config.button_text;
    switch (config.button_config) {
    case Frontend::ButtonConfig::Single:
        names.resize(1);
        break;
    case Frontend::ButtonConfig::Dual:
        names.resize(2);
        break;
    case Frontend::ButtonConfig::Triple:
        names.resize(3);
        break;
    default:
        names.clear();
    }
    jobjectArray buttons = env->NewObjectArray(static_cast<jsize>(names.size()), string_class, nullptr);
    for (std::size_t i = 0; i < names.size(); ++i) {
        const std::string& label =
            names[i].empty() ? std::string(i == 0 ? Frontend::SWKBD_BUTTON_OKAY
                                                   : Frontend::SWKBD_BUTTON_CANCEL)
                             : names[i];
        env->SetObjectArrayElement(buttons, static_cast<jsize>(i), env->NewStringUTF(label.c_str()));
    }

    // Kotlin shows the dialog and blocks this (the emulation) thread until it is answered,
    // as the console's own applet does; the game keeps its timers in emulated time.
    auto* result = reinterpret_cast<jobjectArray>(env->CallStaticObjectMethod(
        g_class, g_ask, env->NewStringUTF(config.hint_text.c_str()),
        static_cast<jint>(config.max_text_length), static_cast<jboolean>(config.multiline_mode),
        buttons));

    std::string text;
    u8 button = 0;
    if (result && env->GetArrayLength(result) >= 2) {
        text = ToString(env, reinterpret_cast<jstring>(env->GetObjectArrayElement(result, 0)));
        button = static_cast<u8>(
            std::atoi(ToString(env, reinterpret_cast<jstring>(env->GetObjectArrayElement(result, 1)))
                          .c_str()));
    }
    Finalize(text, button);
}

void Keyboard::ShowError(const std::string& error) {
    JNIEnv* env = Env();
    env->CallStaticVoidMethod(g_class, g_error, env->NewStringUTF(error.c_str()));
}

} // namespace Mh4uAndroid
