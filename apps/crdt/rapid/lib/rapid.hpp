#ifndef RAPID_RAPID_HPP
#define RAPID_RAPID_HPP

#include <chrono>
#include <cstddef>
#include <cstdint>
#include <functional>
#include <random>
#include <unordered_map>
#include <vector>

namespace rapid {

using Bytes = std::vector<std::uint8_t>;
using NodeId = std::uint64_t;
using Clock = std::chrono::steady_clock;

constexpr NodeId kUnknownNode = 0;

struct MessageId {
    NodeId origin{0};
    std::uint64_t sequence{0};

    bool operator==(const MessageId& other) const {
        return origin == other.origin && sequence == other.sequence;
    }
};

struct MessageIdHash {
    std::size_t operator()(const MessageId& id) const {
        return std::hash<NodeId>{}(id.origin) ^
               (std::hash<std::uint64_t>{}(id.sequence) << 1);
    }
};

struct Config {
    NodeId node_id{1};
    double beta{2.5};
    std::uint64_t first_sequence{1};
    std::uint64_t seed{1};
    std::size_t max_gossip_headers{50};
    std::chrono::milliseconds cache_ttl{60000};
    std::chrono::milliseconds neighbor_ttl{5000};
    std::chrono::milliseconds gossip_interval{1000};
    std::chrono::milliseconds heartbeat_interval{1000};
    std::chrono::milliseconds short_jitter_min{10};
    std::chrono::milliseconds short_jitter_max{40};
    std::chrono::milliseconds long_jitter_min{200};
    std::chrono::milliseconds long_jitter_max{600};
};

class Node {
public:
    using SendCallback = std::function<void(const Bytes&)>;
    using DeliverCallback = std::function<void(const MessageId&, const Bytes&)>;

    explicit Node(Config config);

    void set_send_callback(SendCallback callback);
    void set_deliver_callback(DeliverCallback callback);

    MessageId publish(const Bytes& payload, Clock::time_point now = Clock::now());
    bool rebroadcast(const MessageId& id, Clock::time_point now = Clock::now());
    bool receive(const Bytes& packet,
                 NodeId sender = kUnknownNode,
                 Clock::time_point now = Clock::now());
    void tick(Clock::time_point now = Clock::now());

    std::size_t cached_messages() const;
    std::size_t known_neighbors() const;

private:
    enum class PacketType : std::uint8_t {
        Data = 1,
        Gossip = 2,
        Request = 3,
        Heartbeat = 4,
    };

    enum class CastType : std::uint8_t {
        Data,
        Request,
    };

    struct CacheEntry {
        Clock::time_point seen_at;
        Bytes payload;
    };

    struct CastEntry {
        CastType type{CastType::Data};
        MessageId id;
        Bytes payload;
        double probability{1.0};
        Clock::time_point when;
    };

    struct DecodedPacket {
        PacketType type{PacketType::Data};
        MessageId id;
        NodeId node_id{kUnknownNode};
        Bytes payload;
        std::vector<MessageId> headers;
    };

    Bytes encode_data(const MessageId& id, const Bytes& payload) const;
    Bytes encode_gossip(const std::vector<MessageId>& ids) const;
    Bytes encode_request(const MessageId& id) const;
    Bytes encode_heartbeat() const;

    bool decode(const Bytes& packet, DecodedPacket& out) const;
    void handle_data(const DecodedPacket& packet, Clock::time_point now);
    void handle_gossip(const DecodedPacket& packet, Clock::time_point now);
    void handle_request(const DecodedPacket& packet, Clock::time_point now);
    void handle_heartbeat(const DecodedPacket& packet, NodeId sender, Clock::time_point now);

    void enqueue(CastType type,
                 const MessageId& id,
                 const Bytes& payload,
                 double probability,
                 Clock::time_point when);
    void cancel_data_casts(const MessageId& id);
    void cancel_request_casts(const MessageId& id);
    void run_cast_queue(Clock::time_point now);
    void send_packet(const Bytes& packet);
    void send_gossip(Clock::time_point now);
    void send_heartbeat(Clock::time_point now);
    void cleanup(Clock::time_point now);
    void remember_neighbor(NodeId id, Clock::time_point now);
    double retransmit_probability() const;
    std::chrono::milliseconds random_between(std::chrono::milliseconds min,
                                             std::chrono::milliseconds max);

    Config config_;
    std::uint64_t next_sequence_;
    mutable std::mt19937_64 rng_;
    std::uniform_real_distribution<double> coin_{0.0, 1.0};
    SendCallback send_;
    DeliverCallback deliver_;
    std::unordered_map<MessageId, CacheEntry, MessageIdHash> cache_;
    std::unordered_map<NodeId, Clock::time_point> neighbors_;
    std::vector<CastEntry> cast_queue_;
    Clock::time_point next_gossip_;
    Clock::time_point next_heartbeat_;
    Clock::time_point next_cleanup_;
};

}  // namespace rapid

#endif  // RAPID_RAPID_HPP
