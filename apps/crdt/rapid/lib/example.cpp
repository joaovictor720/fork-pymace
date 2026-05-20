#include "rapid.hpp"

#include <chrono>
#include <iostream>
#include <string>
#include <vector>

int main() {
    rapid::Config a_cfg;
    a_cfg.node_id = 1;
    rapid::Config b_cfg;
    b_cfg.node_id = 2;
    rapid::Config c_cfg;
    c_cfg.node_id = 3;

    rapid::Node a(a_cfg);
    rapid::Node b(b_cfg);
    rapid::Node c(c_cfg);

    auto deliver = [](const char* name) {
        return [name](const rapid::MessageId& id, const rapid::Bytes& payload) {
            std::string text(payload.begin(), payload.end());
            std::cout << name << " delivered " << id.origin << ":" << id.sequence
                      << " " << text << "\n";
        };
    };

    a.set_deliver_callback(deliver("a"));
    b.set_deliver_callback(deliver("b"));
    c.set_deliver_callback(deliver("c"));

    a.set_send_callback([&](const rapid::Bytes& packet) {
        auto now = rapid::Clock::now();
        b.receive(packet, 1, now);
        c.receive(packet, 1, now);
    });
    b.set_send_callback([&](const rapid::Bytes& packet) {
        auto now = rapid::Clock::now();
        a.receive(packet, 2, now);
        c.receive(packet, 2, now);
    });
    c.set_send_callback([&](const rapid::Bytes& packet) {
        auto now = rapid::Clock::now();
        a.receive(packet, 3, now);
        b.receive(packet, 3, now);
    });

    rapid::Bytes payload{'h', 'e', 'l', 'l', 'o'};
    a.publish(payload);

    auto start = rapid::Clock::now();
    for (int i = 0; i < 200; ++i) {
        auto now = start + std::chrono::milliseconds(i * 10);
        a.tick(now);
        b.tick(now);
        c.tick(now);
    }

    return 0;
}
