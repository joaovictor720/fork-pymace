#ifndef RAPID_RAPID_HPP
#define RAPID_RAPID_HPP

#include "../common.hpp"

#include <chrono>
#include <cstddef>
#include <cstdint>
#include <memory>
#include <optional>
#include <string>
#include <vector>

namespace gossip {
namespace rapid {

using gossip::Bytes;
using gossip::PeerId;

struct Config {
    PeerId local_peer_id{1};
    std::string bind_address{"0.0.0.0"};
    std::string broadcast_address{"255.255.255.255"};
    std::uint16_t port{9000};

    double beta{2.5};
    // Zero uses a non-deterministic seed. Set an explicit seed for experiments
    // that need repeatable jitter and message identifiers.
    std::uint64_t seed{0};
    std::size_t max_packet_size{8192};
    std::size_t max_gossip_headers{50};
    std::size_t delivery_queue_capacity{1024};

    std::chrono::milliseconds cache_ttl{60000};
    std::chrono::milliseconds neighbor_ttl{5000};
    std::chrono::milliseconds gossip_interval{1000};
    std::chrono::milliseconds heartbeat_interval{1000};
    std::chrono::milliseconds maintenance_interval{5000};
    std::chrono::milliseconds short_jitter_min{10};
    std::chrono::milliseconds short_jitter_max{40};
    std::chrono::milliseconds long_jitter_min{200};
    std::chrono::milliseconds long_jitter_max{600};
};

struct ReceivedMessage {
    Bytes payload;
};

struct Stats {
    std::uint64_t disseminated_messages{0};
    std::uint64_t sent_packets{0};
    std::uint64_t received_packets{0};
    std::uint64_t sent_bytes{0};
    std::uint64_t received_bytes{0};
    std::uint64_t delivered_messages{0};
    std::uint64_t duplicate_messages{0};
    std::uint64_t malformed_packets{0};
    std::uint64_t socket_errors{0};
    std::uint64_t rejected_disseminations{0};
    std::uint64_t delivery_queue_overflows{0};
    std::size_t cached_messages{0};
    std::size_t known_neighbors{0};
    std::size_t pending_deliveries{0};
};

// High-level facade for one independent RAPID protocol instance.
//
// Each call to disseminate() creates a new logical message for the whole
// dissemination domain. receive() blocks until a remote message is available
// or stop() is called. All protocol timers, sockets, workers and caches are
// owned by the instance.
class Rapid {
public:
    explicit Rapid(Config config);
    ~Rapid();

    Rapid(const Rapid&) = delete;
    Rapid& operator=(const Rapid&) = delete;
    Rapid(Rapid&&) = delete;
    Rapid& operator=(Rapid&&) = delete;

    bool start();
    bool disseminate(Bytes payload);
    std::optional<ReceivedMessage> receive();
    void stop();

    bool is_running() const;
    Stats stats() const;
    std::string last_error() const;

private:
    class Impl;
    std::unique_ptr<Impl> impl_;
};

}  // namespace rapid
}  // namespace gossip

#endif  // RAPID_RAPID_HPP
