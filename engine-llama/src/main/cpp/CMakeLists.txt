cmake_minimum_required(VERSION 3.22.1)

project(mobilespec_llama LANGUAGES C CXX)

set(CMAKE_C_STANDARD 11)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

set(LLAMA_SOURCE_DIR "${CMAKE_CURRENT_LIST_DIR}/../../../../third_party/llama.cpp")

set(BUILD_SHARED_LIBS OFF CACHE BOOL "" FORCE)
set(GGML_NATIVE OFF CACHE BOOL "" FORCE)
set(GGML_OPENMP OFF CACHE BOOL "" FORCE)
set(GGML_LLAMAFILE OFF CACHE BOOL "" FORCE)
set(GGML_CPU_ARM_ARCH "armv8.2-a+dotprod+fp16" CACHE STRING "" FORCE)
set(LLAMA_CURL OFF CACHE BOOL "" FORCE)
set(LLAMA_BUILD_COMMON OFF CACHE BOOL "" FORCE)
set(LLAMA_BUILD_EXAMPLES OFF CACHE BOOL "" FORCE)
set(LLAMA_BUILD_TESTS OFF CACHE BOOL "" FORCE)
set(LLAMA_BUILD_TOOLS OFF CACHE BOOL "" FORCE)
set(LLAMA_BUILD_SERVER OFF CACHE BOOL "" FORCE)

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

target_link_libraries(mobilespec_llama
    PRIVATE
    llama
    android
    log
)
