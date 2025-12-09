cmake_minimum_required(VERSION 3.10)
project(rapid_daemon)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} -O2 -pthread")
add_executable(rapid_daemon
    src/main.cpp
    src/network.cpp
    src/neighbors.cpp
    src/cache.cpp
    src/castqueue.cpp
    src/gossip.cpp
    src/crdt_handler.cpp
    src/stats.cpp
)
include_directories(include)
