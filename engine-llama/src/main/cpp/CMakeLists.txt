cmake_minimum_required(VERSION 3.22.1)

project(mobilespec_llama LANGUAGES C CXX)

set(CMAKE_C_STANDARD 11)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

set(LLAMA_SOURCE_DIR "${CMAKE_CURRENT_LIST_DIR}/../../../../third_party/llama.cpp")
set(MOBILESPEC_ROOT_DIR "${CMAKE_CURRENT_LIST_DIR}/../../../..")

option(MOBILESPEC_ENABLE_KLEIDIAI "Enable experimental Arm KleidiAI CPU kernels" OFF)
option(MOBILESPEC_ENABLE_VULKAN "Enable Vulkan GPU and hybrid offload" ON)

set(BUILD_SHARED_LIBS OFF CACHE BOOL "" FORCE)
set(GGML_NATIVE OFF CACHE BOOL "" FORCE)
set(GGML_OPENMP OFF CACHE BOOL "" FORCE)
set(GGML_LLAMAFILE OFF CACHE BOOL "" FORCE)
set(GGML_CPU_ARM_ARCH "armv8.2-a+dotprod+fp16" CACHE STRING "" FORCE)
set(GGML_CPU_KLEIDIAI ${MOBILESPEC_ENABLE_KLEIDIAI} CACHE BOOL "" FORCE)
set(GGML_VULKAN ${MOBILESPEC_ENABLE_VULKAN} CACHE BOOL "" FORCE)
set(LLAMA_CURL OFF CACHE BOOL "" FORCE)
set(LLAMA_BUILD_COMMON OFF CACHE BOOL "" FORCE)
set(LLAMA_BUILD_EXAMPLES OFF CACHE BOOL "" FORCE)
set(LLAMA_BUILD_TESTS OFF CACHE BOOL "" FORCE)
set(LLAMA_BUILD_TOOLS OFF CACHE BOOL "" FORCE)
set(LLAMA_BUILD_SERVER OFF CACHE BOOL "" FORCE)

if(MOBILESPEC_ENABLE_VULKAN)
    set(Vulkan_INCLUDE_DIR
        "${MOBILESPEC_ROOT_DIR}/third_party/Vulkan-Headers/include"
        CACHE PATH "" FORCE)
    set(SPIRV-Headers_DIR
        "${MOBILESPEC_ROOT_DIR}/tools/cmake/SPIRV-Headers"
        CACHE PATH "" FORCE)
    set(SPIRV_HEADERS_INCLUDE_DIR
        "${CMAKE_ANDROID_NDK}/sources/third_party/shaderc/third_party/spirv-tools/external/spirv-headers/include"
        CACHE PATH "" FORCE)

    if(CMAKE_HOST_SYSTEM_NAME STREQUAL "Windows")
        set(_mobilespec_shader_tools "windows-x86_64")
        set(_mobilespec_glslc "glslc.exe")
    elseif(CMAKE_HOST_SYSTEM_NAME STREQUAL "Darwin")
        set(_mobilespec_shader_tools "darwin-x86_64")
        set(_mobilespec_glslc "glslc")
    else()
        set(_mobilespec_shader_tools "linux-x86_64")
        set(_mobilespec_glslc "glslc")
    endif()
    set(Vulkan_GLSLC_EXECUTABLE
        "${CMAKE_ANDROID_NDK}/shader-tools/${_mobilespec_shader_tools}/${_mobilespec_glslc}"
        CACHE FILEPATH "" FORCE)

    if(NOT EXISTS "${Vulkan_GLSLC_EXECUTABLE}")
        message(FATAL_ERROR "Android NDK glslc not found: ${Vulkan_GLSLC_EXECUTABLE}")
    endif()
    if(NOT EXISTS "${SPIRV_HEADERS_INCLUDE_DIR}/spirv/unified1/spirv.hpp")
        message(FATAL_ERROR "Android NDK SPIR-V headers not found: ${SPIRV_HEADERS_INCLUDE_DIR}")
    endif()

    # Create Vulkan::Vulkan in this directory scope as well; llama.cpp finds it
    # inside its backend subdirectory, whose imported target is not visible here.
    find_package(Vulkan REQUIRED)

    # The pinned ggml-vulkan target does not propagate SPIRV-Headers' imported
    # include directory, so make the NDK copy visible to its C++ compilation.
    include_directories(SYSTEM "${SPIRV_HEADERS_INCLUDE_DIR}")
endif()

add_subdirectory("${LLAMA_SOURCE_DIR}" llama-build EXCLUDE_FROM_ALL)

add_library(mobilespec_llama SHARED mobilespec_jni.cpp)

target_include_directories(mobilespec_llama PRIVATE
    "${LLAMA_SOURCE_DIR}/include"
    "${LLAMA_SOURCE_DIR}/ggml/include"
)

target_compile_options(mobilespec_llama PRIVATE
    -Wall
    -Wextra
    -Werror=return-type
    -march=armv8.2-a+dotprod+fp16
)

target_compile_definitions(mobilespec_llama PRIVATE
    MOBILESPEC_ENABLE_KLEIDIAI=$<BOOL:${MOBILESPEC_ENABLE_KLEIDIAI}>
    MOBILESPEC_ENABLE_VULKAN=$<BOOL:${MOBILESPEC_ENABLE_VULKAN}>
)

target_link_libraries(mobilespec_llama
    PRIVATE
    llama
    android
    log
)

if(MOBILESPEC_ENABLE_VULKAN)
    target_link_libraries(mobilespec_llama PRIVATE Vulkan::Vulkan)
endif()
