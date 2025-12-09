#pragma once

#include <string>
#include <functional>
#include <cstdint>

using RecvCallback = std::function<void(const char*, ssize_t, const std::string&)>;

int make_broadcast_socket(int port);
void send_broadcast(int sock, const char* buf, size_t len);
void start_recv_loop(int sock, RecvCallback cb);
