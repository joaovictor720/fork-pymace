#include "rapid.hpp"

#include <chrono>
#include <future>
#include <iostream>
#include <string>

using gossip::rapid::Config;
using gossip::rapid::Rapid;

int main() {
    Config first_config;
    first_config.local_peer_id = 1;
    first_config.port = 39001;
    first_config.broadcast_address = "127.255.255.255";

    Config second_config = first_config;
    second_config.local_peer_id = 2;

    Rapid first(first_config);
    Rapid second(second_config);

    if (!first.start()) {
        std::cerr << "first.start(): " << first.last_error() << "\n";
        return 1;
    }
    if (!second.start()) {
        std::cerr << "second.start(): " << second.last_error() << "\n";
        first.stop();
        return 1;
    }

    auto received = std::async(std::launch::async, [&] {
        return second.receive();
    });

    const std::string text = "hello";
    gossip::rapid::Bytes payload(text.begin(), text.end());
    const auto message_handle = first.disseminate(std::move(payload));
    if (!message_handle) {
        std::cerr << "disseminate(): " << first.last_error() << "\n";
        first.stop();
        second.stop();
        return 1;
    }

    if (received.wait_for(std::chrono::seconds(2)) != std::future_status::ready) {
        std::cerr << "message was not received in time\n";
        first.stop();
        second.stop();
        return 1;
    }

    const auto message = received.get();
    if (message) {
        std::cout << std::string(message->payload.begin(), message->payload.end()) << "\n";
    }

    // This sends the same logical message with the same protocol identifier.
    // A receiver that already cached it suppresses it as a duplicate.
    if (!first.retransmit(*message_handle)) {
        std::cerr << "retransmit(): " << first.last_error() << "\n";
        first.stop();
        second.stop();
        return 1;
    }

    first.stop();
    second.stop();
    return message ? 0 : 1;
}
