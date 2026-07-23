#include "rapid.hpp"

#include <chrono>
#include <cstdlib>
#include <future>
#include <iostream>
#include <string>
#include <thread>
#include <unistd.h>

using namespace std::chrono_literals;
using gossip::rapid::Bytes;
using gossip::rapid::Config;
using gossip::rapid::Rapid;

namespace {

[[noreturn]] void fail(const std::string& message) {
    std::cerr << "FAIL: " << message << "\n";
    std::exit(1);
}

void require(bool condition, const std::string& message) {
    if (!condition) {
        fail(message);
    }
}

Config local_config(std::uint16_t port, gossip::rapid::PeerId peer_id) {
    Config config;
    config.local_peer_id = peer_id;
    config.port = port;
    config.broadcast_address = "127.255.255.255";
    config.heartbeat_interval = 50ms;
    config.gossip_interval = 50ms;
    config.maintenance_interval = 50ms;
    config.neighbor_ttl = 500ms;
    config.cache_ttl = 2s;
    return config;
}

void test_invalid_config() {
    Config config;
    config.local_peer_id = 0;
    Rapid rapid(config);
    require(!rapid.start(), "a zero local_peer_id must be rejected");
    require(!rapid.last_error().empty(), "a startup error must be observable");
}

void test_dissemination_and_shutdown(std::uint16_t port) {
    Rapid first(local_config(port, 1));
    Rapid second(local_config(port, 2));
    require(first.start(), "first instance failed to start: " + first.last_error());
    require(second.start(), "second instance failed to start: " + second.last_error());

    auto received = std::async(std::launch::async, [&] {
        return second.receive();
    });

    const std::string text = "independent RAPID instances";
    require(first.disseminate(Bytes(text.begin(), text.end())),
            "disseminate failed: " + first.last_error());
    require(received.wait_for(2s) == std::future_status::ready,
            "the second instance did not receive the dissemination");

    const auto message = received.get();
    require(message.has_value(), "receive returned shutdown instead of a message");
    require(std::string(message->payload.begin(), message->payload.end()) == text,
            "received payload differs from disseminated payload");

    auto repeated = std::async(std::launch::async, [&] {
        return second.receive();
    });
    require(first.disseminate(Bytes(text.begin(), text.end())),
            "a repeated payload was not accepted as a new logical message");
    require(repeated.wait_for(2s) == std::future_status::ready,
            "an identical payload with a new message id was not delivered");
    require(repeated.get().has_value(),
            "the repeated payload receive returned shutdown");

    const auto first_stats = first.stats();
    const auto second_stats = second.stats();
    require(first_stats.disseminated_messages == 2,
            "the sender did not count both disseminations");
    require(second_stats.delivered_messages == 2,
            "the receiver did not deliver identical payloads as distinct messages");

    auto blocked_receive = std::async(std::launch::async, [&] {
        return second.receive();
    });
    std::this_thread::sleep_for(20ms);
    second.stop();
    require(blocked_receive.wait_for(1s) == std::future_status::ready,
            "stop did not unblock receive");
    require(!blocked_receive.get().has_value(),
            "receive returned a message after an empty shutdown");

    first.stop();
    require(!first.disseminate(Bytes{1}),
            "a stopped instance accepted a dissemination");
    require(first.stats().rejected_disseminations == 1,
            "a post-stop dissemination rejection was not counted");
    require(!first.start(), "a stopped instance was restarted");
}

void test_delivery_queue_overflow(std::uint16_t port) {
    Config sender_config = local_config(port, 10);
    Config receiver_config = local_config(port, 20);
    receiver_config.delivery_queue_capacity = 1;

    Rapid sender(sender_config);
    Rapid receiver(receiver_config);
    require(sender.start(), "overflow sender failed to start: " + sender.last_error());
    require(receiver.start(), "overflow receiver failed to start: " + receiver.last_error());

    constexpr int kMessages = 8;
    for (int i = 0; i < kMessages; ++i) {
        const std::string text = "message-" + std::to_string(i);
        require(sender.disseminate(Bytes(text.begin(), text.end())),
                "overflow test dissemination was rejected");
    }

    const auto deadline = std::chrono::steady_clock::now() + 2s;
    gossip::rapid::Stats snapshot;
    do {
        snapshot = receiver.stats();
        if (snapshot.delivered_messages + snapshot.delivery_queue_overflows >= kMessages) {
            break;
        }
        std::this_thread::sleep_for(10ms);
    } while (std::chrono::steady_clock::now() < deadline);

    require(snapshot.pending_deliveries == 1,
            "the bounded delivery queue did not retain exactly one message");
    require(snapshot.delivery_queue_overflows > 0,
            "delivery queue overflow was not reported");

    sender.stop();
    receiver.stop();
}

}  // namespace

int main() {
    const auto port_base = static_cast<std::uint16_t>(45000 + (::getpid() % 9000));
    test_invalid_config();
    test_dissemination_and_shutdown(port_base);
    test_delivery_queue_overflow(static_cast<std::uint16_t>(port_base + 1));
    std::cout << "PASS\n";
    return 0;
}
