#include <iostream>
#include <thread>
#include <random>
#include <chrono>
#include <cstring>
#include "network.h"
#include "neighbors.h"
#include "cache.h"
#include "castqueue.h"
#include "gossip.h"
#include "crdt_handler.h"
#include "stats.h"

static const int PORT = 9000;
static const double BETA = 2.5;
static const int CACHE_TTL = 60;

uint64_t gen_id() {
    static std::mt19937_64 rng((uint64_t)std::chrono::steady_clock::now().time_since_epoch().count());
    return rng();
}

int main(int argc, char** argv) {
    int sock = make_broadcast_socket(PORT);
    Neighbors neigh;
    Cache cache;
    CastQueue cq;
    CRDTOps crdt;
    stats st;

    std::mt19937 rng((uint32_t)std::chrono::steady_clock::now().time_since_epoch().count());
    std::uniform_real_distribution<double> ud(0.0, 1.0);

    start_recv_loop(sock, [&](const char* buf, ssize_t n, const std::string &from) {
        if (n <= 0) {
            return;
        }
        uint8_t type = (uint8_t)buf[0];
        if (type == 1) {
            if (n < 1 + (int)sizeof(uint64_t) + (int)sizeof(uint32_t)) {
                return;
            }
            uint64_t msgid;
            memcpy(&msgid, buf + 1, sizeof(msgid));
            uint32_t len;
            memcpy(&len, buf + 1 + sizeof(msgid), sizeof(len));
            const char* payload = buf + 1 + sizeof(msgid) + sizeof(len);
            if (!cache.exists(msgid)) {
                cache.insert(msgid, payload, len);
                st.msgs_received++;
                crdt.apply_op(payload, len);
                int ncount = neigh.count();
                double pr = BETA / std::max(1, ncount);
                if (pr > 1.0) { pr = 1.0; }
                CastEntry e;
                e.msgid = msgid;
                e.prob = pr;
                e.when = std::chrono::steady_clock::now() + std::chrono::milliseconds( rand() % 40 + 10 );
                e.payload.assign(payload, payload + len + 0);
                cq.push(e);
            }
        } else if (type == 2) {
            if (n < 1 + (int)sizeof(uint16_t)) { return; }
            uint16_t nn;
            memcpy(&nn, buf + 1, sizeof(nn));
            const char* p = buf + 1 + sizeof(nn);
            for (int i = 0; i < nn; ++i) {
                if (p + (int)sizeof(uint64_t) > buf + n) { break; }
                uint64_t hid;
                memcpy(&hid, p, sizeof(hid));
                p += sizeof(hid);
                if (!cache.exists(hid)) {
                    std::vector<char> reqpkt;
                    build_request_packet(hid, reqpkt);
                    send_broadcast(sock, reqpkt.data(), reqpkt.size());
                    st.req_sent++;
                }
            }
        } else if (type == 3) {
            if (n < 1 + (int)sizeof(uint64_t)) { return; }
            uint64_t hid;
            memcpy(&hid, buf + 1, sizeof(hid));
            if (cache.exists(hid)) {
                // reply with data
                // find entry
                CacheEntry ce;
                {
                    std::lock_guard<std::mutex> lg(cache.mu);
                    ce = cache.m[hid];
                }
                std::vector<char> pkt;
                build_data_packet(hid, ce.payload, pkt);
                send_broadcast(sock, pkt.data(), pkt.size());
                st.req_received++;
            }
        } else if (type == 4) {
            if (n < 1 + (int)sizeof(uint64_t)) { return; }
            uint64_t pid;
            memcpy(&pid, buf + 1, sizeof(pid));
            neigh.heartbeat_seen(pid);
        }
    });

    // heartbeat thread
    std::thread([&]() {
        uint64_t selfid = gen_id();
        while (true) {
            std::vector<char> hb;
            build_heartbeat_packet(selfid, hb);
            send_broadcast(sock, hb.data(), hb.size());
            std::this_thread::sleep_for(std::chrono::seconds(1));
        }
    }).detach();

    // cast queue worker
    std::thread([&]() {
        while (true) {
            CastEntry e;
            if (cq.pop_due(e)) {
                double r = ud(rng);
                if (r <= e.prob) {
                    std::vector<char> pkt;
                    build_data_packet(e.msgid, e.payload, pkt);
                    send_broadcast(sock, pkt.data(), pkt.size());
                    st.msgs_forwarded++;
                } else {
                    e.when = std::chrono::steady_clock::now() + std::chrono::milliseconds(200 + (rand() % 400));
                    cq.push(e);
                }
            }
        }
    }).detach();

    // cache cleaner
    std::thread([&]() {
        while (true) {
            cache.cleanup_seconds(CACHE_TTL);
            neigh.cleanup_seconds(5);
            std::this_thread::sleep_for(std::chrono::seconds(5));
        }
    }).detach();

    // interactive loop: send app msg
    while (true) {
        std::string line;
        if (!std::getline(std::cin, line)) { break; }
        uint64_t id = gen_id();
        std::vector<char> payload(line.begin(), line.end());
        CacheEntry e;
        e.t = std::chrono::steady_clock::now();
        e.payload = payload;
        cache.insert(id, payload.data(), payload.size());
        st.msgs_sent++;
        std::vector<char> pkt;
        build_data_packet(id, payload, pkt);
        send_broadcast(sock, pkt.data(), pkt.size());
    }

    return 0;
}
