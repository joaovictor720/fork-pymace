#pragma once
#include <vector>
#include <cstdint>
#include <mutex>

struct CRDTOps {
    std::mutex mu;
    void apply_op(const char* data, size_t len);
};
