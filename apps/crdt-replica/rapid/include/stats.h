#pragma once
#include <cstdint>
#include <mutex>
#include <fstream>
#include <string>

struct stats {
    uint64_t msgs_sent = 0;
    uint64_t msgs_forwarded = 0;
    uint64_t msgs_received = 0;
    uint64_t req_sent = 0;
    uint64_t req_received = 0;
    std::mutex mu;
    void dump_csv(const std::string &fname);
};
