// The JavaVM, and a JNIEnv for whichever thread asks (the emulation thread calls into Kotlin
// for the keyboard and for events).
#pragma once

#include <jni.h>

namespace Mh4uAndroid {

void SetVm(JavaVM* vm);
JNIEnv* Env();

} // namespace Mh4uAndroid
