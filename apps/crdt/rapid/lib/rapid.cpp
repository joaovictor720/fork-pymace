#include "rapid.hpp"

#include <algorithm>
#include <limits>
#include <utility>

namespace rapid {
namespace {

constexpr std::uint8_t kData = 1;
constexpr std::uint8_t kGossip = 2;
constexpr std::uint8_t kRequest = 3;
constexpr std::uint8_t kHeartbeat = 4;

void append_u16(Bytes& out, std::uint16_t value) {
    out.push_back(static_cast<std::uint8_t>(value & 0xff));
    out.push_back(static_cast<std::uint8_t>((value >> 8) & 0xff));
}

void append_u32(Bytes& out, std::uint32_t value) {
    for (int i = 0; i < 4; ++i) {
        out.push_back(static_cast<std::uint8_t>((value >> (i * 8)) & 0xff));
    }
}

void append_u64(Bytes& out, std::uint64_t value) {
    for (int i = 0; i < 8; ++i) {
        out.push_back(static_cast<std::uint8_t>((value >> (i * 8)) & 0xff));
    }
}

bool read_u16(const Bytes& in, std::size_t& pos, std::uint16_t& out) {
    if (pos + 2 > in.size()) {
        return false;
    }
    out = static_cast<std::uint16_t>(in[pos]) |
          static_cast<std::uint16_t>(in[pos + 1] << 8);
    pos += 2;
    return true;
}

bool read_u32(const Bytes& in, std::size_t& pos, std::uint32_t& out) {
    if (pos + 4 > in.size()) {
        return false;
    }
    out = 0;
    for (int i = 0; i < 4; ++i) {
        out |= static_cast<std::uint32_t>(in[pos + i]) << (i * 8);
    }
    pos += 4;
    return true;
}

bool read_u64(const Bytes& in, std::size_t& pos, std::uint64_t& out) {
    if (pos + 8 > in.size()) {
        return false;
    }
    out = 0;
    for (int i = 0; i < 8; ++i) {
        out |= static_cast<std::uint64_t>(in[pos + i]) << (i * 8);
    }
    pos += 8;
    return true;
}

bool read_message_id(const Bytes& in, std::size_t& pos, MessageId& out) {
    return read_u64(in, pos, out.origin) && read_u64(in, pos, out.sequence);
}

void append_message_id(Bytes& out, const MessageId& id) {
    append_u64(out, id.origin);
    append_u64(out, id.sequence);
}

}  // namespace

Node::Node(Config config)
    : config_(config),
      next_sequence_(config.first_sequence),
      rng_(config.seed),
      next_gossip_(Clock::time_point{}),
      next_heartbeat_(Clock::time_point{}),
      next_cleanup_(Clock::time_point{}) {
}

void Node::set_send_callback(SendCallback callback) {
    send_ = std::move(callback);
}

void Node::set_deliver_callback(DeliverCallback callback) {
    deliver_ = std::move(callback);
}

MessageId Node::publish(const Bytes& payload, Clock::time_point now) {
    MessageId id{config_.node_id, next_sequence_++};
    cache_[id] = CacheEntry{now, payload};
    enqueue(CastType::Data, id, payload, 1.0, now);
    run_cast_queue(now);
    return id;
}

bool Node::rebroadcast(const MessageId& id, Clock::time_point now) {
    auto cached = cache_.find(id);
    if (cached == cache_.end()) {
        return false;
    }
    enqueue(CastType::Data, id, cached->second.payload, 1.0, now);
    run_cast_queue(now);
    return true;
}

bool Node::receive(const Bytes& packet, NodeId sender, Clock::time_point now) {
    DecodedPacket decoded;
    if (!decode(packet, decoded)) {
        return false;
    }

    if (sender != kUnknownNode && sender != config_.node_id) {
        remember_neighbor(sender, now);
    }

    switch (decoded.type) {
        case PacketType::Data:
            handle_data(decoded, now);
            break;
        case PacketType::Gossip:
            handle_gossip(decoded, now);
            break;
        case PacketType::Request:
            handle_request(decoded, now);
            break;
        case PacketType::Heartbeat:
            handle_heartbeat(decoded, sender, now);
            break;
    }
    return true;
}

void Node::tick(Clock::time_point now) {
    run_cast_queue(now);
    send_gossip(now);
    send_heartbeat(now);
    cleanup(now);
}

std::size_t Node::cached_messages() const {
    return cache_.size();
}

std::size_t Node::known_neighbors() const {
    return neighbors_.size();
}

Bytes Node::encode_data(const MessageId& id, const Bytes& payload) const {
    Bytes out;
    out.reserve(1 + 16 + 4 + payload.size());
    out.push_back(kData);
    append_message_id(out, id);
    append_u32(out, static_cast<std::uint32_t>(payload.size()));
    out.insert(out.end(), payload.begin(), payload.end());
    return out;
}

Bytes Node::encode_gossip(const std::vector<MessageId>& ids) const {
    Bytes out;
    const auto count = static_cast<std::uint16_t>(
        std::min<std::size_t>(ids.size(), std::numeric_limits<std::uint16_t>::max()));
    out.reserve(1 + 2 + static_cast<std::size_t>(count) * 16);
    out.push_back(kGossip);
    append_u16(out, count);
    for (std::size_t i = 0; i < count; ++i) {
        append_message_id(out, ids[i]);
    }
    return out;
}

Bytes Node::encode_request(const MessageId& id) const {
    Bytes out;
    out.reserve(1 + 16);
    out.push_back(kRequest);
    append_message_id(out, id);
    return out;
}

Bytes Node::encode_heartbeat() const {
    Bytes out;
    out.reserve(1 + 8);
    out.push_back(kHeartbeat);
    append_u64(out, config_.node_id);
    return out;
}

bool Node::decode(const Bytes& packet, DecodedPacket& out) const {
    if (packet.empty()) {
        return false;
    }

    std::size_t pos = 1;
    switch (packet[0]) {
        case kData: {
            out.type = PacketType::Data;
            std::uint32_t payload_size = 0;
            if (!read_message_id(packet, pos, out.id) ||
                !read_u32(packet, pos, payload_size) ||
                pos + payload_size > packet.size()) {
                return false;
            }
            out.payload.assign(packet.begin() + static_cast<std::ptrdiff_t>(pos),
                               packet.begin() + static_cast<std::ptrdiff_t>(pos + payload_size));
            return pos + payload_size == packet.size();
        }
        case kGossip: {
            out.type = PacketType::Gossip;
            std::uint16_t count = 0;
            if (!read_u16(packet, pos, count)) {
                return false;
            }
            out.headers.reserve(count);
            for (std::uint16_t i = 0; i < count; ++i) {
                MessageId id;
                if (!read_message_id(packet, pos, id)) {
                    return false;
                }
                out.headers.push_back(id);
            }
            return pos == packet.size();
        }
        case kRequest:
            out.type = PacketType::Request;
            return read_message_id(packet, pos, out.id) && pos == packet.size();
        case kHeartbeat:
            out.type = PacketType::Heartbeat;
            return read_u64(packet, pos, out.node_id) && pos == packet.size();
        default:
            return false;
    }
}

void Node::handle_data(const DecodedPacket& packet, Clock::time_point now) {
    cancel_data_casts(packet.id);
    cancel_request_casts(packet.id);

    if (cache_.find(packet.id) != cache_.end()) {
        return;
    }

    cache_[packet.id] = CacheEntry{now, packet.payload};
    if (deliver_) {
        deliver_(packet.id, packet.payload);
    }

    enqueue(CastType::Data,
            packet.id,
            packet.payload,
            retransmit_probability(),
            now + random_between(config_.short_jitter_min, config_.short_jitter_max));
}

void Node::handle_gossip(const DecodedPacket& packet, Clock::time_point now) {
    for (const auto& id : packet.headers) {
        if (cache_.find(id) == cache_.end()) {
            enqueue(CastType::Request,
                    id,
                    Bytes{},
                    retransmit_probability(),
                    now + random_between(config_.short_jitter_min, config_.short_jitter_max));
        }
    }
}

void Node::handle_request(const DecodedPacket& packet, Clock::time_point now) {
    const auto cached = cache_.find(packet.id);
    if (cached == cache_.end()) {
        return;
    }

    enqueue(CastType::Data,
            packet.id,
            cached->second.payload,
            retransmit_probability(),
            now + random_between(config_.short_jitter_min, config_.short_jitter_max));
}

void Node::handle_heartbeat(const DecodedPacket& packet, NodeId sender, Clock::time_point now) {
    NodeId id = packet.node_id != kUnknownNode ? packet.node_id : sender;
    if (id != kUnknownNode && id != config_.node_id) {
        remember_neighbor(id, now);
    }
}

void Node::enqueue(CastType type,
                   const MessageId& id,
                   const Bytes& payload,
                   double probability,
                   Clock::time_point when) {
    CastEntry entry;
    entry.type = type;
    entry.id = id;
    entry.payload = payload;
    entry.probability = std::max(0.0, std::min(1.0, probability));
    entry.when = when;
    cast_queue_.push_back(std::move(entry));
}

void Node::cancel_data_casts(const MessageId& id) {
    cast_queue_.erase(
        std::remove_if(cast_queue_.begin(), cast_queue_.end(), [&](const CastEntry& entry) {
            return entry.type == CastType::Data && entry.id == id;
        }),
        cast_queue_.end());
}

void Node::cancel_request_casts(const MessageId& id) {
    cast_queue_.erase(
        std::remove_if(cast_queue_.begin(), cast_queue_.end(), [&](const CastEntry& entry) {
            return entry.type == CastType::Request && entry.id == id;
        }),
        cast_queue_.end());
}

void Node::run_cast_queue(Clock::time_point now) {
    for (std::size_t i = 0; i < cast_queue_.size();) {
        if (cast_queue_[i].when > now) {
            ++i;
            continue;
        }

        CastEntry entry = std::move(cast_queue_[i]);
        cast_queue_.erase(cast_queue_.begin() + static_cast<std::ptrdiff_t>(i));

        if (coin_(rng_) <= entry.probability) {
            if (entry.type == CastType::Data) {
                send_packet(encode_data(entry.id, entry.payload));
            } else {
                send_packet(encode_request(entry.id));
            }
        } else {
            entry.probability = 1.0;
            entry.when = now + random_between(config_.long_jitter_min, config_.long_jitter_max);
            cast_queue_.push_back(std::move(entry));
        }
    }
}

void Node::send_packet(const Bytes& packet) {
    if (send_) {
        send_(packet);
    }
}

void Node::send_gossip(Clock::time_point now) {
    if (now < next_gossip_) {
        return;
    }
    next_gossip_ = now + config_.gossip_interval;

    std::vector<MessageId> ids;
    ids.reserve(std::min(config_.max_gossip_headers, cache_.size()));
    for (const auto& item : cache_) {
        ids.push_back(item.first);
        if (ids.size() >= config_.max_gossip_headers) {
            break;
        }
    }
    if (!ids.empty()) {
        send_packet(encode_gossip(ids));
    }
}

void Node::send_heartbeat(Clock::time_point now) {
    if (now < next_heartbeat_) {
        return;
    }
    next_heartbeat_ = now + config_.heartbeat_interval;
    send_packet(encode_heartbeat());
}

void Node::cleanup(Clock::time_point now) {
    if (now < next_cleanup_) {
        return;
    }
    next_cleanup_ = now + std::chrono::seconds(1);

    for (auto it = cache_.begin(); it != cache_.end();) {
        if (now - it->second.seen_at > config_.cache_ttl) {
            it = cache_.erase(it);
        } else {
            ++it;
        }
    }

    for (auto it = neighbors_.begin(); it != neighbors_.end();) {
        if (now - it->second > config_.neighbor_ttl) {
            it = neighbors_.erase(it);
        } else {
            ++it;
        }
    }
}

void Node::remember_neighbor(NodeId id, Clock::time_point now) {
    neighbors_[id] = now;
}

double Node::retransmit_probability() const {
    const auto n = std::max<std::size_t>(1, neighbors_.size());
    return std::min(1.0, config_.beta / static_cast<double>(n));
}

std::chrono::milliseconds Node::random_between(std::chrono::milliseconds min,
                                               std::chrono::milliseconds max) {
    if (max <= min) {
        return min;
    }
    std::uniform_int_distribution<long long> dist(min.count(), max.count());
    return std::chrono::milliseconds(dist(rng_));
}

}  // namespace rapid
