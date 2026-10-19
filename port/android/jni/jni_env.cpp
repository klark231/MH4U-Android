#include "jni_env.h"

namespace Mh4uAndroid {

namespace {
JavaVM* g_vm = nullptr;
}

void SetVm(JavaVM* vm) {
    g_vm = vm;
}

JNIEnv* Env() {
    JNIEnv* env = nullptr;
    if (g_vm && g_vm->GetEnv(reinterpret_cast<void**>(&env), JNI_VERSION_1_6) == JNI_EDETACHED) {
        g_vm->AttachCurrentThread(&env, nullptr);
    }
    return env;
}

} // namespace Mh4uAndroid
