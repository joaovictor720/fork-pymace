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
    const auto first_message =
        first.disseminate(Bytes(text.begin(), text.end()));
    require(first_message.has_value(),
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
    require(first.retransmit(*first_message),
            "retransmit failed: " + first.last_error());
    require(repeated.wait_for(200ms) == std::future_status::timeout,
            "retransmitting a cached message delivered it as a new logical message");

    require(!second.retransmit(*first_message),
            "a different RAPID instance accepted a foreign message handle");
    require(second.stats().rejected_retransmissions == 1,
            "a foreign message handle rejection was not counted");

    const auto distinct_message =
        first.disseminate(Bytes(text.begin(), text.end()));
    require(distinct_message.has_value(),
            "a repeated payload was not accepted as a new logical message");
    require(repeated.wait_for(2s) == std::future_status::ready,
            "an identical payload with a new message id was not delivered");
    require(repeated.get().has_value(),
            "the repeated payload receive returned shutdown");

    const auto first_stats = first.stats();
    const auto second_stats = second.stats();
    require(first_stats.disseminated_messages == 2,
            "the sender did not count both disseminations");
    require(first_stats.explicit_retransmissions == 1,
            "the sender did not count the explicit retransmission");
    require(second_stats.delivered_messages == 2,
            "the receiver did not deliver identical payloads as distinct messages");
    require(second_stats.duplicate_messages > 0,
            "the receiver did not suppress the explicitly retransmitted message");

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
    require(!first.disseminate(Bytes{1}).has_value(),
            "a stopped instance accepted a dissemination");
    require(!first.retransmit(*first_message),
            "a stopped instance accepted a retransmission");
    require(first.stats().rejected_disseminations == 1,
            "a post-stop dissemination rejection was not counted");
    require(first.stats().rejected_retransmissions == 1,
            "a post-stop retransmission rejection was not counted");
    require(!first.start(), "a stopped instance was restarted");
}

void test_expired_message_handle(std::uint16_t port) {
    Config config = local_config(port, 30);
    config.cache_ttl = 60ms;
    config.maintenance_interval = 10ms;

    Rapid rapid(config);
    require(rapid.start(), "expiry instance failed to start: " + rapid.last_error());

    const auto message = rapid.disseminate(Bytes{1, 2, 3});
    require(message.has_value(), "expiry test dissemination was rejected");

    const auto deadline = std::chrono::steady_clock::now() + 1s;
    while (rapid.stats().cached_messages != 0 &&
           std::chrono::steady_clock::now() < deadline) {
        std::this_thread::sleep_for(10ms);
    }

    require(rapid.stats().cached_messages == 0,
            "the expiry test message did not leave the cache");
    require(!rapid.retransmit(*message),
            "an expired message handle was accepted");
    require(rapid.stats().rejected_retransmissions == 1,
            "an expired message handle rejection was not counted");

    rapid.stop();
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
        require(sender.disseminate(Bytes(text.begin(), text.end())).has_value(),
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
    test_expired_message_handle(static_cast<std::uint16_t>(port_base + 1));
    test_delivery_queue_overflow(static_cast<std::uint16_t>(port_base + 2));
    std::cout << "PASS\n";
    return 0;
}
