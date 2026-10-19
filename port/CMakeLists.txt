# MH4U PC port: Azahar's 3DS core with the game's recompiled code as the CPU.
#
# This directory is built from inside Azahar's source tree (see azahar/README.md):
#   cmake <azahar> -DMH4U_PORT_DIR=<repo>/port [-DMH4U_RECOMP_DIR=<repo>/build/recomp] ...
#
# mh4u_runner contains no game code. The recompiled code is a separate library, loaded at
# run time (--code): it is generated on the user's machine from their own dump
# (tools/recompile.py) and never distributed.

set(MH4U_RECOMP_DIR "" CACHE PATH "tools/recompile.py output for the game (contains src/)")
set(MH4U_TEST_RECOMP_DIR "" CACHE PATH "tools/recompile.py output for the port's test program")
set(MH4U_RECOMP_OPT "-O1" CACHE STRING "Optimisation level for recompiled code")

get_filename_component(MH4U_TOOLS_DIR "${CMAKE_CURRENT_LIST_DIR}/../tools" ABSOLUTE)
set(MH4U_RUNTIME_DIR "${MH4U_TOOLS_DIR}/recomp/runtime")

# The CPU backend, and the hook frontends call before loading a game (src/frontend.h).
# Azahar's Qt frontend links it when this directory is part of the build (see azahar/).
add_library(mh4u_backend STATIC
    src/arm_recomp.cpp
    src/arm_recomp.h
    src/fiber.cpp
    src/fiber.h
    src/frontend.cpp
    src/frontend.h
    src/hooks.cpp
    src/hooks.h
    src/input_map.cpp
    src/input_map.h
    src/netsync.cpp
    src/netsync.h
)
target_include_directories(mh4u_backend PUBLIC src PRIVATE "${MH4U_RUNTIME_DIR}")
target_link_libraries(mh4u_backend PUBLIC citra_core citra_common network)

# Android: the app's native library (android/jni): Azahar's core with the recompiled code as
# the CPU, on a Surface, with the app's settings, rooms and controls. The Android project
# (android/) loads it as libmh4u-android.so.
if (ANDROID)
    add_subdirectory(android/jni)
    return()  # no runner or SDL app on a phone
endif()

add_executable(mh4u_runner src/runner.cpp)
target_include_directories(mh4u_runner PRIVATE "${MH4U_RUNTIME_DIR}")
target_link_libraries(mh4u_runner PRIVATE mh4u_backend network)

# The port's own app (app/): a window of its own (SDL2) instead of Azahar's Qt interface.
option(MH4U_APP "Build the port's own app (needs SDL2)" ON)
if (MH4U_APP AND ENABLE_SDL2)
    add_subdirectory(app)
endif()

# Recompiled code from `dir` as a shared library for --code.
function(mh4u_add_code_library name dir)
    file(GLOB code CONFIGURE_DEPENDS "${dir}/src/*.c")
    if (NOT code)
        message(FATAL_ERROR "no recompiled code in ${dir}/src (run tools/recompile.py)")
    endif()
    add_library(${name} SHARED ${code} "${MH4U_RUNTIME_DIR}/rc_dll.c")
    target_include_directories(${name} PRIVATE "${MH4U_RUNTIME_DIR}" "${dir}/src")
    target_compile_definitions(${name} PRIVATE RC_PAGED)
    set_target_properties(${name} PROPERTIES PREFIX "" C_VISIBILITY_PRESET hidden)
    if (MSVC)
        target_compile_options(${name} PRIVATE /fp:strict /w)
    else()
        target_compile_options(${name} PRIVATE ${MH4U_RECOMP_OPT} -ffp-contract=off -frounding-math
                               -fno-strict-aliasing -fwrapv -w)
    endif()
endfunction()

if (MH4U_RECOMP_DIR)
    mh4u_add_code_library(mh4u_code "${MH4U_RECOMP_DIR}")
endif()
if (MH4U_TEST_RECOMP_DIR)
    mh4u_add_code_library(mh4u_test_code "${MH4U_TEST_RECOMP_DIR}")
endif()
