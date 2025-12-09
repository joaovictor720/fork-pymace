#include "cache.h"
#include <cstring>

void Cache::insert(uint64_t id, const char* data, size_t len) {
    std::lock_guard<std::mutex> lg(mu);
    CacheEntry e;
    e.t = std::chrono::steady_clock::now();
    e.payload.assign(data, data + len);
    m[id] = std::move(e);
}

bool Cache::exists(uint64_t id) {
    std::lock_guard<std::mutex> lg(mu);
    return m.find(id) != m.end();
}

void Cache::cleanup_seconds(int secs) {
    std::lock_guard<std::mutex> lg(mu);
    auto now = std::chrono::steady_clock::now();
    for (auto it = m.begin(); it != m.end(); ) {
        auto age = std::chrono::duration_cast<std::chrono::seconds>(now - it->second.t).count();
        if (age > secs) { it = m.erase(it); } else { ++it; }
    }
}
