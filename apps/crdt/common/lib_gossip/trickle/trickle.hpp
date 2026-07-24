#ifndef GOSSIP_TRICKLE_HPP
#define GOSSIP_TRICKLE_HPP

#include "../common.hpp"

#include <chrono>
#include <cstddef>
#include <cstdint>
#include <memory>
#include <optional>
#include <string>

namespace gossip {
namespace trickle {

using gossip::Bytes;
using gossip::PeerId;

enum class StateRelation {
    Equivalent,
    LocalNewer,
    RemoteNewer,
    Incomparable,
};

// Application-defined semantics for the state maintained through Trickle.
//
// The adapter does not transfer ownership of application state to the
// protocol. It exposes a serialized summary, compares a received summary,
// builds application bytes for an outdated peer and applies received update
// bytes. These methods are called by Trickle workers and must be thread-safe.
class StateAdapter {
public:
    virtual ~StateAdapter() = default;

    virtual Bytes summary() const = 0;
    virtual StateRelation compare(const Bytes& remote_summary) const = 0;
    virtual Bytes make_update(const Bytes& remote_summary) const = 0;
    virtual bool apply_update(PeerId sender, const Bytes& update) = 0;
};

struct Config {
    PeerId local_peer_id{1};
    std::string bind_address{"0.0.0.0"};
    std::string broadcast_address{"255.255.255.255"};
    std::uint16_t port{9000};

    std::uint32_t redundancy_constant{1};
    std::chrono::milliseconds minimum_interval{500};
    std::chrono::milliseconds maximum_interval{8000};

    // Zero selects a non-deterministic seed.
    std::uint64_t seed{0};
    std::size_t max_packet_size{16384};
    std::size_t delivery_queue_capacity{1024};
};

struct ReceivedUpdate {
    PeerId sender{0};
    Bytes payload;
    bool state_changed{false};
};

struct Stats {
    std::uint64_t sent_packets{0};
    std::uint64_t received_packets{0};
    std::uint64_t sent_bytes{0};
    std::uint64_t received_bytes{0};

    std::uint64_t sent_summaries{0};
    std::uint64_t received_summaries{0};
    std::uint64_t sent_updates{0};
    std::uint64_t received_updates{0};
    std::uint64_t delivered_updates{0};

    std::uint64_t consistent_summaries{0};
    std::uint64_t suppressed_summaries{0};
    std::uint64_t interval_resets{0};

    std::uint64_t malformed_packets{0};
    std::uint64_t adapter_errors{0};
    std::uint64_t oversized_payloads{0};
    std::uint64_t socket_errors{0};
    std::uint64_t rejected_state_changes{0};
    std::uint64_t delivery_queue_overflows{0};

    std::chrono::milliseconds current_interval{0};
    std::uint32_t consistent_count{0};
    std::size_t pending_deliveries{0};
};

// High-level facade for one independent Trickle protocol instance.
//
// notify_state_changed() resets the Trickle interval after a local application
// change. Remote updates are applied synchronously through StateAdapter before
// the protocol processes another packet. receive() only reports updates that
// have already been applied and blocks until one is available or stop() is
// called.
class Trickle {
public:
    Trickle(Config config, StateAdapter& adapter);
    ~Trickle();

    Trickle(const Trickle&) = delete;
    Trickle& operator=(const Trickle&) = delete;
    Trickle(Trickle&&) = delete;
    Trickle& operator=(Trickle&&) = delete;

    bool start();
    bool notify_state_changed();
    std::optional<ReceivedUpdate> receive();
    void stop();

    bool is_running() const;
    Stats stats() const;
    std::string last_error() const;

private:
    class Impl;
    std::unique_ptr<Impl> impl_;
};

}  // namespace trickle
}  // namespace gossip

#endif  // GOSSIP_TRICKLE_HPP
