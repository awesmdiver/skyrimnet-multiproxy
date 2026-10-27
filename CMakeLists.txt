cmake_minimum_required(VERSION 3.24)

# ========================================
# Project Configuration
# ========================================
project(
    SkyrimNetMultiProxy
    VERSION 4.1.0
    LANGUAGES CXX
)

# ========================================
# Build Options
# ========================================
set(CMAKE_CXX_STANDARD 23)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set(CMAKE_INTERPROCEDURAL_OPTIMIZATION ON)
set(CMAKE_INTERPROCEDURAL_OPTIMIZATION_DEBUG OFF)

# MSVC-specific flags
if(MSVC)
    add_compile_options(
        /MP     # Multi-processor compilation
        /W4     # Warning level 4
        /WX     # Treat warnings as errors
        /permissive-    # Standards conformance
        /Zc:preprocessor    # Use conforming preprocessor
        /EHsc   # Exception handling
        /utf-8  # UTF-8 source files

        # Disable specific warnings
        /wd4068 # unknown pragma 'clang'
        /wd4189 # local variable is initialized but not referenced
        /wd4200 # nonstandard extension used : zero-sized array in struct/union
        /wd4201 # nonstandard extension used : nameless struct/union
        /wd4324 # structure was padded due to alignment specifier
    )
endif()

# ========================================
# Dependencies
# ========================================
# None. The mod is plain Windows code: no CommonLibSSE-NG, no Address Library, no downloads at
# configure time. The SKSE interface structs it needs are in src/skse_plugin_api.h.

# ========================================
# Source Files
# ========================================
set(SOURCES
    src/main.cpp
    src/proxy_launcher.cpp
    src/proxy_launcher.h
    src/multiproxy_channel.cpp
    src/multiproxy_channel.h
    src/plugin_support.h
    src/skse_plugin_api.h
)

# ========================================
# Build Target
# ========================================
add_library(${PROJECT_NAME} SHARED ${SOURCES})

# The CMake project version is the one source of truth for the version: it feeds the startup log
# line and the packed version number in the SKSEPlugin_Version export (see main.cpp).
target_compile_definitions(${PROJECT_NAME}
    PRIVATE
        SKYRIMNET_MULTIPROXY_VERSION_STRING="${PROJECT_VERSION}"
        SKYRIMNET_MULTIPROXY_VERSION_LABEL=""
        SKYRIMNET_MULTIPROXY_VERSION_MAJOR=${PROJECT_VERSION_MAJOR}
        SKYRIMNET_MULTIPROXY_VERSION_MINOR=${PROJECT_VERSION_MINOR}
        SKYRIMNET_MULTIPROXY_VERSION_PATCH=${PROJECT_VERSION_PATCH}
)

target_include_directories(${PROJECT_NAME}
    PRIVATE
        ${CMAKE_CURRENT_SOURCE_DIR}/src
)

target_link_libraries(${PROJECT_NAME}
    PRIVATE
        ws2_32
        mswsock
        Shell32
        ole32
        oleaut32
        uuid
        version
)

# ========================================
# Output Configuration
# ========================================
set_target_properties(${PROJECT_NAME}
    PROPERTIES
        OUTPUT_NAME "${PROJECT_NAME}"
        RUNTIME_OUTPUT_DIRECTORY "${CMAKE_CURRENT_BINARY_DIR}"
)

# ========================================
# Post-Build: Copy to Skyrim Data/SKSE/Plugins (opt-in only)
# ========================================
# Off by default so a normal build never silently writes into a live game
# install -- test with a real Vortex/MO2-style package install instead.
# Enable explicitly with:
#   cmake -B build -S . -DDEPLOY_TO_GAME=ON -DSKYRIM_PATH="C:/path/to/Skyrim Special Edition"
# SKYRIM_PATH is a personal, machine-specific path -- deliberately NOT hardcoded here so it never
# ends up committed; it must be supplied on the command line (or via cmake-gui/CMakeCache.txt,
# which is gitignored).
option(DEPLOY_TO_GAME "Copy the built DLL directly into SKYRIM_PATH/Data/SKSE/Plugins/ after building" OFF)
if(DEPLOY_TO_GAME)
    set(SKYRIM_PATH "" CACHE PATH "Path to your Skyrim Special Edition install (required if DEPLOY_TO_GAME is ON)")
    if(SKYRIM_PATH STREQUAL "")
        message(FATAL_ERROR "DEPLOY_TO_GAME is ON but SKYRIM_PATH was not set. Pass -DSKYRIM_PATH=\"C:/path/to/Skyrim Special Edition\".")
    endif()
    add_custom_command(
        TARGET ${PROJECT_NAME} POST_BUILD
        COMMAND ${CMAKE_COMMAND} -E copy $<TARGET_FILE:${PROJECT_NAME}> "${SKYRIM_PATH}/Data/SKSE/Plugins/"
        COMMAND ${CMAKE_COMMAND} -E echo "Deployed to: ${SKYRIM_PATH}/Data/SKSE/Plugins/"
    )
endif()
