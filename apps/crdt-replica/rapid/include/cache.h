#pragma once
#include <unordered_map>
#include <mutex>
#include <chrono>
#include <vector>

struct CacheEntry { std::chrono::steady_clock::time_point t; std::vector<char> payload; };

struct Cache {
    std::unordered_map<uint64_t, CacheEntry> m;
    std::mutex mu;
    void insert(uint64_t id, const char* data, size_t len);
    bool exists(uint64_t id);
    void cleanup_seconds(int secs);
};
