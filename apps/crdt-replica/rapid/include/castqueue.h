#pragma once
#include <queue>
#include <mutex>
#include <condition_variable>
#include <chrono>
#include <cstdint>
#include <vector>

struct CastEntry {
    uint64_t msgid;
    double prob;
    std::chrono::steady_clock::time_point when;
    std::vector<char> payload;
};

struct CastQueue {
    std::mutex mu;
    std::condition_variable cv;
    std::vector<CastEntry> q;
    void push(const CastEntry &e);
    bool pop_due(CastEntry &out);
    void remove_if(uint64_t msgid);
};
