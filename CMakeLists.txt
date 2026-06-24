cmake_minimum_required(VERSION 3.24)

# ========================================
# Project Configuration
# ========================================
project(
    ProxyLauncher
    VERSION 1.0.0
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

# fmt - string formatting (required by CommonLibSSE-NG)
FetchContent_Declare(fmt
    GIT_REPOSITORY https://github.com/fmtlib/fmt.git
    GIT_TAG 10.2.1
    GIT_SHALLOW TRUE
    FIND_PACKAGE_ARGS CONFIG
)
FetchContent_MakeAvailable(fmt)

# spdlog - logging (required by CommonLibSSE-NG), link against external fmt
set(SPDLOG_FMT_EXTERNAL ON CACHE BOOL "" FORCE)
FetchContent_Declare(spdlog
    GIT_REPOSITORY https://github.com/gabime/spdlog.git
    GIT_TAG v1.13.0
    GIT_SHALLOW TRUE
    FIND_PACKAGE_ARGS CONFIG
)
FetchContent_MakeAvailable(spdlog)

# rapidcsv - header-only CSV parser (required by CommonLibSSE-NG)
FetchContent_Declare(rapidcsv
    GIT_REPOSITORY https://github.com/d99kris/rapidcsv.git
    GIT_TAG v8.83
    GIT_SHALLOW TRUE
)
FetchContent_MakeAvailable(rapidcsv)
# CommonLibSSE uses find_path(RAPIDCSV_INCLUDE_DIRS "rapidcsv.h").
# Pre-set the cache variable so find_path returns immediately with our path,
# and add to CMAKE_INCLUDE_PATH as a belt-and-suspenders fallback.
set(RAPIDCSV_INCLUDE_DIRS "${rapidcsv_SOURCE_DIR}/src" CACHE PATH "" FORCE)
list(APPEND CMAKE_INCLUDE_PATH "${rapidcsv_SOURCE_DIR}/src")

# CommonLibSSE-NG - core SKSE library
set(BUILD_TESTS OFF CACHE BOOL "" FORCE)
message(STATUS "Fetching CommonLibSSE-NG (first build takes a few minutes)...")
FetchContent_Declare(CommonLibSSE
    GIT_REPOSITORY https://github.com/CharmedBaryon/CommonLibSSE-NG.git
    GIT_TAG v3.7.0
    GIT_SHALLOW TRUE
)
FetchContent_MakeAvailable(CommonLibSSE)

# CommonLibSSE v3.7.0 uses std::aligned_storage_t which is deprecated in C++23.
# MSVC's /WX turns this deprecation warning into an error — suppress it on the library target.
if(TARGET CommonLibSSE)
    # Suppress C++23 compatibility warnings that CommonLibSSE v3.7.0 doesn't handle
    # (std::aligned_storage_t deprecated, va_start second arg change)
    target_compile_options(CommonLibSSE PRIVATE /wd4996 /wd5082)
endif()

# ========================================
# Source Files
# ========================================
set(SOURCES
    src/main.cpp
    src/proxy_launcher.cpp
    src/proxy_launcher.h
    src/PCH.h
)

# ========================================
# Build Target
# ========================================
add_library(${PROJECT_NAME} SHARED ${SOURCES})

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
)

# ========================================
# Precompiled Headers
# ========================================
target_precompile_headers(${PROJECT_NAME}
    PRIVATE
        src/PCH.h
)

# proxy_launcher.cpp uses raw Win32 headers (winsock2.h) which conflict with
# CommonLibSSE's SKSE::WinAPI wrappers — exclude it from the PCH entirely.
set_source_files_properties(src/proxy_launcher.cpp
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
# Post-Build: Copy to Skyrim Data/SKSE/Plugins
# ========================================
set(SKYRIM_PATH "E:/SteamLibrary/steamapps/common/Skyrim Special Edition")
add_custom_command(
    TARGET ${PROJECT_NAME} POST_BUILD
    COMMAND ${CMAKE_COMMAND} -E copy $<TARGET_FILE:${PROJECT_NAME}> "${SKYRIM_PATH}/Data/SKSE/Plugins/"
    COMMAND ${CMAKE_COMMAND} -E echo "Deployed to: ${SKYRIM_PATH}/Data/SKSE/Plugins/"
)
