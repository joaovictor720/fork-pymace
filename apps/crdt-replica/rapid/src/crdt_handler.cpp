#include "crdt_handler.h"
#include <cstring>

void CRDTOps::apply_op(const char* data, size_t len) {
    std::lock_guard<std::mutex> lg(mu);
    (void)data;
    (void)len;
}
