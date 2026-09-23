cmake_minimum_required(VERSION 3.21)
project(zview_gpu_media_bridge LANGUAGES CXX)

if(NOT WIN32)
    message(FATAL_ERROR "zview_gpu_media_bridge is Windows-only")
endif()

set(ZVIEW_NVENC_SDK_DIR "" CACHE PATH "NVIDIA video-sdk-samples checkout")
if(NOT ZVIEW_NVENC_SDK_DIR)
    message(FATAL_ERROR "Set ZVIEW_NVENC_SDK_DIR to the NVIDIA video-sdk-samples checkout")
endif()

set(NVENC_ROOT "${ZVIEW_NVENC_SDK_DIR}/Samples/NvCodec")
foreach(required_file
    "${NVENC_ROOT}/NvEncoder/NvEncoder.cpp"
    "${NVENC_ROOT}/NvEncoder/NvEncoderD3D11.cpp"
    "${NVENC_ROOT}/NvEncoder/nvEncodeAPI.h")
    if(NOT EXISTS "${required_file}")
        message(FATAL_ERROR "Missing NVIDIA Video Codec SDK source: ${required_file}")
    endif()
endforeach()

add_library(zview_gpu_media_bridge SHARED
    src/zview_gpu_media_bridge.cpp
    "${NVENC_ROOT}/NvEncoder/NvEncoder.cpp"
    "${NVENC_ROOT}/NvEncoder/NvEncoderD3D11.cpp")

target_compile_features(zview_gpu_media_bridge PRIVATE cxx_std_17)
target_compile_definitions(zview_gpu_media_bridge PRIVATE WIN32_LEAN_AND_MEAN NOMINMAX)
target_include_directories(zview_gpu_media_bridge PRIVATE
    "${CMAKE_CURRENT_SOURCE_DIR}/include"
    "${NVENC_ROOT}")
target_link_libraries(zview_gpu_media_bridge PRIVATE d3d11 dxgi)
set_target_properties(zview_gpu_media_bridge PROPERTIES
    OUTPUT_NAME "zview_gpu_media_bridge"
    RUNTIME_OUTPUT_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}/bin"
    RUNTIME_OUTPUT_DIRECTORY_DEBUG "${CMAKE_CURRENT_SOURCE_DIR}/bin"
    RUNTIME_OUTPUT_DIRECTORY_RELEASE "${CMAKE_CURRENT_SOURCE_DIR}/bin"
    RUNTIME_OUTPUT_DIRECTORY_RELWITHDEBINFO "${CMAKE_CURRENT_SOURCE_DIR}/bin")
