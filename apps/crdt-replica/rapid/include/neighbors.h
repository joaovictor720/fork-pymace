#pragma once
#include <unordered_map>
#include <mutex>
#include <chrono>
#include <string>

struct Neighbors {
    std::unordered_map<uint64_t, std::chrono::steady_clock::time_point> table;
    std::mutex m;
    void heartbeat_seen(uint64_t id);
    int count();
    void cleanup_seconds(int secs);
};
