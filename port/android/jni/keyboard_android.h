// The 3DS software keyboard (chat, names, quest search) as an Android dialog: Kotlin's
// Keyboard.ask() shows it and returns the text and the button pressed.
#pragma once

#include "core/frontend/applets/swkbd.h"

namespace Mh4uAndroid {

class Keyboard final : public Frontend::SoftwareKeyboard {
public:
    void Execute(const Frontend::KeyboardConfig& config) override;
    void ShowError(const std::string& error) override;
};

/// Looks up the Kotlin class; call from JNI_OnLoad's thread or any thread with a JNIEnv.
void InitKeyboardJni();

} // namespace Mh4uAndroid
