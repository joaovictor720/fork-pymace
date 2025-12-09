#pragma once
#include <vector>
#include <cstdint>

void build_data_packet(uint64_t msgid, const std::vector<char>& payload, std::vector<char>& out);
void build_gossip_packet(const std::vector<uint64_t>& headers, std::vector<char>& out);
void build_request_packet(uint64_t msgid, std::vector<char>& out);
void build_heartbeat_packet(uint64_t peerid, std::vector<char>& out);
