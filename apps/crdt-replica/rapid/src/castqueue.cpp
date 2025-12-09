#include "castqueue.h"

void CastQueue::push(const CastEntry &e) {
    std::lock_guard<std::mutex> lg(mu);
    q.push_back(e);
    cv.notify_one();
}

bool CastQueue::pop_due(CastEntry &out) {
    std::unique_lock<std::mutex> lk(mu);
    if (q.empty()) {
        cv.wait_for(lk, std::chrono::milliseconds(50));
    }
    auto now = std::chrono::steady_clock::now();
    for (auto it = q.begin(); it != q.end(); ++it) {
        if (it->when <= now) {
            out = *it;
            q.erase(it);
            return true;
        }
    }
    return false;
}

void CastQueue::remove_if(uint64_t msgid) {
    std::lock_guard<std::mutex> lg(mu);
    for (auto it = q.begin(); it != q.end(); ) {
        if (it->msgid == msgid) { it = q.erase(it); } else { ++it; }
    }
}