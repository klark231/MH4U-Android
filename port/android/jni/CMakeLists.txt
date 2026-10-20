# The Android app's native library, built from inside Azahar's tree (see ../README.md):
#   -DMH4U_PORT_DIR=<repo>/port  and  src/CMakeLists.txt adds ${MH4U_PORT_DIR}/android/jni
# It replaces Azahar's own Android library: nothing of Azahar's app is built or linked, only
# its core libraries.
add_library(mh4u-android SHARED
    emu_window_android.cpp
    emu_window_android.h
    jni_env.cpp
    jni_env.h
    drawing_stub.cpp
    jni_input.cpp
    keyboard_android.cpp
    keyboard_android.h
    native.cpp
    pad_device.cpp
    pad_device.h
    # the PC app's SDL-free parts, shared: the app's settings, rooms and invite codes
    ${MH4U_PORT_DIR}/app/config.cpp
    ${MH4U_PORT_DIR}/app/invite.cpp
    ${MH4U_PORT_DIR}/app/multiplayer.cpp
    ${MH4U_PORT_DIR}/app/launcher/lobby.cpp
)
target_include_directories(mh4u-android PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}
    ${MH4U_PORT_DIR}/app
    ${MH4U_PORT_DIR}/app/launcher
    ${MH4U_PORT_DIR}/src
)
target_link_libraries(mh4u-android PRIVATE
    mh4u_backend audio_core citra_common citra_core input_common network video_core inih
    httplib json-headers)
if (ENABLE_WEB_SERVICE)
    target_link_libraries(mh4u-android PRIVATE web_service)
endif()
target_link_libraries(mh4u-android PRIVATE android log)
# the engine's own copy of input_map is already in mh4u_backend; jni_input.cpp uses it from there
