# Minimal SPIRV-Headers CMake package config.
#
# llama.cpp's Vulkan backend does find_package(SPIRV-Headers CONFIG REQUIRED).
# The Android NDK ships the SPIR-V header *sources* but no CMake package config,
# and installing the full LunarG Vulkan SDK is not necessary just for headers.
# This shim exposes the NDK's bundled headers as the expected imported target.
#
# Usage: -DSPIRV-Headers_DIR=<repo>/tools/cmake/SPIRV-Headers
# Requires ANDROID_NDK (or NDK_ROOT) to be set, which the NDK toolchain file does.

if(NOT DEFINED SPIRV_HEADERS_INCLUDE_DIR)
  if(DEFINED ANDROID_NDK)
    set(_spv_ndk "${ANDROID_NDK}")
  elseif(DEFINED ENV{ANDROID_NDK_HOME})
    set(_spv_ndk "$ENV{ANDROID_NDK_HOME}")
  elseif(DEFINED ENV{NDK_ROOT})
    set(_spv_ndk "$ENV{NDK_ROOT}")
  endif()

  set(SPIRV_HEADERS_INCLUDE_DIR
      "${_spv_ndk}/sources/third_party/shaderc/third_party/spirv-tools/external/spirv-headers/include")
endif()

if(NOT EXISTS "${SPIRV_HEADERS_INCLUDE_DIR}/spirv/unified1/spirv.hpp")
  message(FATAL_ERROR
    "SPIRV-Headers shim: spirv.hpp not found under ${SPIRV_HEADERS_INCLUDE_DIR}. "
    "Pass -DSPIRV_HEADERS_INCLUDE_DIR=<path> explicitly.")
endif()

if(NOT TARGET SPIRV-Headers::SPIRV-Headers)
  add_library(SPIRV-Headers::SPIRV-Headers INTERFACE IMPORTED)
  set_target_properties(SPIRV-Headers::SPIRV-Headers PROPERTIES
    INTERFACE_INCLUDE_DIRECTORIES "${SPIRV_HEADERS_INCLUDE_DIR}")
endif()

set(SPIRV-Headers_FOUND TRUE)
