cmake_minimum_required(VERSION 3.24)

# ========================================
# Project Configuration
# ========================================
project(
    SkyrimNetMultiProxy
    VERSION 4.0.0
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
# Dependencies - Auto-downloaded via FetchContent
# ========================================
include(FetchContent)

# fmt, spdlog and rapidcsv now come from vcpkg (vcpkg.json), not FetchContent: CommonLibSSE-NG v13.1.0
# needs fmt 12 and spdlog 1.16 or newer (plus DirectXTK, simpleini, toml11 and others it finds itself).
# Configure with -DCMAKE_TOOLCHAIN_FILE=<vcpkg>/scripts/buildsystems/vcpkg.cmake and
# -DVCPKG_TARGET_TRIPLET=x64-windows-static-md (see the handoff for the exact command).
find_package(fmt CONFIG REQUIRED)
find_package(spdlog CONFIG REQUIRED)
find_path(RAPIDCSV_INCLUDE_DIRS "rapidcsv.h")

# CommonLibSSE-NG - core SKSE library
set(BUILD_TESTS OFF CACHE BOOL "" FORCE)
message(STATUS "Fetching CommonLibSSE-NG (first build takes a few minutes)...")
FetchContent_Declare(CommonLibSSE
    GIT_REPOSITORY https://github.com/alandtse/CommonLibSSE-NG.git
    GIT_TAG v13.1.0
    GIT_SHALLOW TRUE
)
FetchContent_MakeAvailable(CommonLibSSE)

# CommonLibSSE-NG has C++23 deprecation warnings (std::aligned_storage_t, va_start) that /WX would turn
# into errors; suppress them on the library target only.
if(TARGET CommonLibSSE)
    target_compile_options(CommonLibSSE PRIVATE /wd4996 /wd5082)
endif()

# ========================================
# Source Files
# ========================================
set(SOURCES
    src/main.cpp
    src/proxy_launcher.cpp
    src/proxy_launcher.h
    src/multiproxy_channel.cpp
    src/multiproxy_channel.h
    src/address_library_check.h
    src/PCH.h
)

# ========================================
# Build Target
# ========================================
add_library(${PROJECT_NAME} SHARED ${SOURCES})

# Feeds the CMake project version (the one source of truth for it) into main.cpp's own startup log
# line, so that string can't silently drift out of sync with the real build version again -- it
# used to hardcode "v2.0.0" long after CMakeLists.txt's own VERSION had moved on. The MAJOR/MINOR/
# PATCH components (auto-populated by project()'s own VERSION above) also drive SKSEPluginInfo's
# .Version field in main.cpp -- that field is a *different* hardcoded literal from the log string
# and three straight version bumps (2.1.0-2.3.0) left it stuck at 2.0.0.0 because nothing tied it
# to PROJECT_VERSION. One bump here now moves both.
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
        # CommonLibSSE marks rapidcsv PRIVATE so it doesn't propagate to dependents,
        # but its PUBLIC headers include rapidcsv.h — add it explicitly.
        ${RAPIDCSV_INCLUDE_DIRS}
)

target_link_libraries(${PROJECT_NAME}
    PRIVATE
        CommonLibSSE::CommonLibSSE
        ws2_32
        Shell32
        ole32
        oleaut32
        uuid
)

# ========================================
# Precompiled Headers
# ========================================
target_precompile_headers(${PROJECT_NAME}
    PRIVATE
        src/PCH.h
)

# proxy_launcher.cpp and multiproxy_channel.cpp use raw Win32 headers (winsock2.h) which conflict with
# CommonLibSSE's SKSE::WinAPI wrappers — exclude it from the PCH entirely.
set_source_files_properties(src/proxy_launcher.cpp src/multiproxy_channel.cpp
    PROPERTIES SKIP_PRECOMPILE_HEADERS ON
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
