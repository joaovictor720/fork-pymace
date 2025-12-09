#include "gossip.h"
#include <cstring>

void build_data_packet(uint64_t msgid, const std::vector<char>& payload, std::vector<char>& out) {
    out.clear();
    uint8_t type = 1;
    out.push_back((char)type);
    out.insert(out.end(), (char*)&msgid, (char*)&msgid + sizeof(msgid));
    uint32_t len = (uint32_t)payload.size();
    out.insert(out.end(), (char*)&len, (char*)&len + sizeof(len));
    out.insert(out.end(), payload.begin(), payload.end());
}

void build_gossip_packet(const std::vector<uint64_t>& headers, std::vector<char>& out) {
    out.clear();
    uint8_t type = 2;
    out.push_back((char)type);
    uint16_t n = (uint16_t)headers.size();
    out.insert(out.end(), (char*)&n, (char*)&n + sizeof(n));
    for (auto h : headers) {
        out.insert(out.end(), (char*)&h, (char*)&h + sizeof(h));
    }
}

void build_request_packet(uint64_t msgid, std::vector<char>& out) {
    out.clear();
    uint8_t type = 3;
    out.push_back((char)type);
    out.insert(out.end(), (char*)&msgid, (char*)&msgid + sizeof(msgid));
}

void build_heartbeat_packet(uint64_t peerid, std::vector<char>& out) {
    out.clear();
    uint8_t type = 4;
    out.push_back((char)type);
    out.insert(out.end(), (char*)&peerid, (char*)&peerid + sizeof(peerid));
}