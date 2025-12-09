#include "network.h"
#include <arpa/inet.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <unistd.h>
#include <fcntl.h>
#include <thread>
#include <cstring>
#include <iostream>

int make_broadcast_socket(int port) {
    int s = socket(AF_INET, SOCK_DGRAM, 0);
    int reuse = 1;
    setsockopt(s, SOL_SOCKET, SO_REUSEADDR, &reuse, sizeof(reuse));
    int bc = 1;
    setsockopt(s, SOL_SOCKET, SO_BROADCAST, &bc, sizeof(bc));
    sockaddr_in addr;
    addr.sin_family = AF_INET;
    addr.sin_port = htons(port);
    addr.sin_addr.s_addr = INADDR_ANY;
    bind(s, (sockaddr*)&addr, sizeof(addr));
    int flags = fcntl(s, F_GETFL, 0);
    fcntl(s, F_SETFL, flags | O_NONBLOCK);
    return s;
}

void send_broadcast(int sock, const char* buf, size_t len) {
    sockaddr_in out;
    out.sin_family = AF_INET;
    out.sin_port = ((sockaddr_in*)0)->sin_port; // placeholder
    out.sin_addr.s_addr = inet_addr("255.255.255.255");
    out.sin_port = htons(9000);
    sendto(sock, buf, len, 0, (sockaddr*)&out, sizeof(out));
}

void start_recv_loop(int sock, RecvCallback cb) {
    std::thread([sock, cb]() {
        while (true) {
            char buf[2048];
            sockaddr_in src;
            socklen_t srclen = sizeof(src);
            ssize_t n = recvfrom(sock, buf, sizeof(buf), 0, (sockaddr*)&src, &srclen);
            if (n > 0) {
                std::string from = inet_ntoa(src.sin_addr);
                cb(buf, n, from);
            } else {
                std::this_thread::sleep_for(std::chrono::milliseconds(5));
            }
        }
    }).detach();
}