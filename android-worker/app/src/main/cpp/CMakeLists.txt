cmake_minimum_required(VERSION 3.22.1)
project(swarm_vulkan LANGUAGES CXX)

add_library(swarm_vulkan SHARED vulkan_compute.cpp)
target_compile_features(swarm_vulkan PRIVATE cxx_std_17)
target_compile_options(swarm_vulkan PRIVATE -Wall -Wextra -Werror=return-type)

find_library(VULKAN_LIB vulkan REQUIRED)
find_library(ANDROID_LIB android REQUIRED)
find_library(LOG_LIB log REQUIRED)

target_link_libraries(swarm_vulkan PRIVATE ${VULKAN_LIB} ${ANDROID_LIB} ${LOG_LIB})
