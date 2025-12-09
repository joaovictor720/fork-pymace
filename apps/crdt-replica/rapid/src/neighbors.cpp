#include "neighbors.h"

void Neighbors::heartbeat_seen(uint64_t id) {
    std::lock_guard<std::mutex> lg(m);
    table[id] = std::chrono::steady_clock::now();
}

int Neighbors::count() {
    std::lock_guard<std::mutex> lg(m);
    return (int)table.size();
}

void Neighbors::cleanup_seconds(int secs) {
    std::lock_guard<std::mutex> lg(m);
    auto now = std::chrono::steady_clock::now();
    for (auto it = table.begin(); it != table.end(); ) {
        auto age = std::chrono::duration_cast<std::chrono::seconds>(now - it->second).count();
        if (age > secs) { it = table.erase(it); } else { ++it; }
    }
}

