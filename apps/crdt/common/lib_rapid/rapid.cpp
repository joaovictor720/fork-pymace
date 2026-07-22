#include "rapid.hpp"

#include <algorithm>
#include <arpa/inet.h>
#include <atomic>
#include <cerrno>
#include <cmath>
#include <condition_variable>
#include <cstring>
#include <deque>
#include <limits>
#include <mutex>
#include <queue>
#include <random>
#include <sys/socket.h>
#include <thread>
#include <unordered_map>
#include <unistd.h>
#include <utility>

namespace rapid {
namespace {

using Clock = std::chrono::steady_clock;
using MessageId = std::uint64_t;

constexpr std::uint8_t kData = 1;
constexpr std::uint8_t kGossip = 2;
constexpr std::uint8_t kRequest = 3;
constexpr std::uint8_t kHeartbeat = 4;
constexpr std::size_t kDataHeaderSize = 1 + 8 + 4;
constexpr std::size_t kGossipHeaderSize = 1 + 2;
constexpr std::size_t kMessageIdSize = 8;

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
    out = static_cast<std::uint16_t>(
        static_cast<std::uint16_t>(in[pos]) |
        static_cast<std::uint16_t>(static_cast<std::uint16_t>(in[pos + 1]) << 8));
    pos += 2;
    return true;
}

bool read_u32(const Bytes& in, std::size_t& pos, std::uint32_t& out) {
    if (pos + 4 > in.size()) {
        return false;
    }
    out = 0;
    for (int i = 0; i < 4; ++i) {
        out |= static_cast<std::uint32_t>(in[pos + static_cast<std::size_t>(i)]) << (i * 8);
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
        out |= static_cast<std::uint64_t>(in[pos + static_cast<std::size_t>(i)]) << (i * 8);
    }
    pos += 8;
    return true;
}

Bytes encode_data(MessageId id, const Bytes& payload) {
    Bytes out;
    out.reserve(kDataHeaderSize + payload.size());
    out.push_back(kData);
    append_u64(out, id);
    append_u32(out, static_cast<std::uint32_t>(payload.size()));
    out.insert(out.end(), payload.begin(), payload.end());
    return out;
}

Bytes encode_gossip(const std::vector<MessageId>& ids) {
    Bytes out;
    out.reserve(kGossipHeaderSize + ids.size() * kMessageIdSize);
    out.push_back(kGossip);
    append_u16(out, static_cast<std::uint16_t>(ids.size()));
    for (MessageId id : ids) {
        append_u64(out, id);
    }
    return out;
}

Bytes encode_request(MessageId id) {
    Bytes out;
    out.reserve(1 + kMessageIdSize);
    out.push_back(kRequest);
    append_u64(out, id);
    return out;
}

Bytes encode_heartbeat(PeerId peer_id) {
    Bytes out;
    out.reserve(1 + sizeof(PeerId));
    out.push_back(kHeartbeat);
    append_u64(out, peer_id);
    return out;
}

enum class PacketType : std::uint8_t {
    Data,
    Gossip,
    Request,
    Heartbeat,
};

struct DecodedPacket {
    PacketType type{PacketType::Data};
    MessageId id{0};
    PeerId peer_id{0};
    Bytes payload;
    std::vector<MessageId> headers;
};

bool decode_packet(const Bytes& packet, DecodedPacket& out) {
    if (packet.empty()) {
        return false;
    }

    std::size_t pos = 1;
    switch (packet[0]) {
        case kData: {
            out.type = PacketType::Data;
            std::uint32_t payload_size = 0;
            if (!read_u64(packet, pos, out.id) ||
                !read_u32(packet, pos, payload_size) ||
                static_cast<std::size_t>(payload_size) > packet.size() - pos) {
                return false;
            }
            if (pos + static_cast<std::size_t>(payload_size) != packet.size()) {
                return false;
            }
            out.payload.assign(packet.begin() + static_cast<std::ptrdiff_t>(pos), packet.end());
            return true;
        }
        case kGossip: {
            out.type = PacketType::Gossip;
            std::uint16_t count = 0;
            if (!read_u16(packet, pos, count) ||
                static_cast<std::size_t>(count) > (packet.size() - pos) / kMessageIdSize ||
                pos + static_cast<std::size_t>(count) * kMessageIdSize != packet.size()) {
                return false;
            }
            out.headers.reserve(count);
            for (std::uint16_t i = 0; i < count; ++i) {
                MessageId id = 0;
                if (!read_u64(packet, pos, id)) {
                    return false;
                }
                out.headers.push_back(id);
            }
            return true;
        }
        case kRequest:
            out.type = PacketType::Request;
            return read_u64(packet, pos, out.id) && pos == packet.size();
        case kHeartbeat:
            out.type = PacketType::Heartbeat;
            return read_u64(packet, pos, out.peer_id) && pos == packet.size();
        default:
            return false;
    }
}

}  // namespace

class Rapid::Impl {
public:
    explicit Impl(Config config)
        : config_(std::move(config)), rng_(make_seed()) {
    }

    ~Impl() {
        stop();
    }

    bool start() {
        std::lock_guard<std::mutex> lifecycle_lock(lifecycle_mutex_);
        if (running_.load()) {
            return true;
        }
        if (started_once_) {
            set_error("a Rapid instance cannot be restarted after stop()");
            return false;
        }
        started_once_ = true;

        std::string validation_error;
        if (!validate_config(validation_error)) {
            set_error(validation_error);
            stopped_ = true;
            return false;
        }

        const int fd = ::socket(AF_INET, SOCK_DGRAM, 0);
        if (fd < 0) {
            set_errno_error("socket");
            stopped_ = true;
            return false;
        }

        int reuse = 1;
        int broadcast = 1;
        timeval timeout{};
        timeout.tv_usec = 200000;
        if (::setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &reuse, sizeof(reuse)) < 0 ||
            ::setsockopt(fd, SOL_SOCKET, SO_BROADCAST, &broadcast, sizeof(broadcast)) < 0 ||
            ::setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout)) < 0) {
            set_errno_error("setsockopt");
            ::close(fd);
            stopped_ = true;
            return false;
        }

        sockaddr_in bind_address{};
        bind_address.sin_family = AF_INET;
        bind_address.sin_port = htons(config_.port);
        if (!parse_ipv4(config_.bind_address, bind_address.sin_addr)) {
            set_error("invalid bind_address: " + config_.bind_address);
            ::close(fd);
            stopped_ = true;
            return false;
        }
        if (::bind(fd, reinterpret_cast<sockaddr*>(&bind_address), sizeof(bind_address)) < 0) {
            set_errno_error("bind");
            ::close(fd);
            stopped_ = true;
            return false;
        }

        broadcast_address_ = {};
        broadcast_address_.sin_family = AF_INET;
        broadcast_address_.sin_port = htons(config_.port);
        if (!parse_ipv4(config_.broadcast_address, broadcast_address_.sin_addr)) {
            set_error("invalid broadcast_address: " + config_.broadcast_address);
            ::close(fd);
            stopped_ = true;
            return false;
        }

        socket_fd_.store(fd);
        clear_error();
        running_.store(true);
        stopped_ = false;

        try {
            receive_thread_ = std::thread(&Impl::receive_loop, this);
            cast_thread_ = std::thread(&Impl::cast_loop, this);
            gossip_thread_ = std::thread(&Impl::gossip_loop, this);
            heartbeat_thread_ = std::thread(&Impl::heartbeat_loop, this);
            maintenance_thread_ = std::thread(&Impl::maintenance_loop, this);
        } catch (const std::exception& error) {
            set_error(std::string("failed to start RAPID workers: ") + error.what());
            stop_workers_and_socket();
            stopped_ = true;
            return false;
        }

        return true;
    }

    bool disseminate(Bytes payload) {
        std::lock_guard<std::mutex> lifecycle_lock(lifecycle_mutex_);
        if (!running_.load()) {
            counters_.rejected_disseminations.fetch_add(1);
            set_error("disseminate() requires a running Rapid instance");
            return false;
        }
        if (payload.size() > std::numeric_limits<std::uint32_t>::max() ||
            payload.size() > config_.max_packet_size - kDataHeaderSize) {
            counters_.rejected_disseminations.fetch_add(1);
            set_error("payload exceeds max_packet_size");
            return false;
        }

        const auto now = Clock::now();
        MessageId id = 0;
        for (;;) {
            id = random_u64();
            if (id == 0) {
                continue;
            }
            std::lock_guard<std::mutex> cache_lock(cache_mutex_);
            if (cache_.find(id) == cache_.end()) {
                cache_.emplace(id, CacheEntry{now, payload});
                break;
            }
        }

        counters_.disseminated_messages.fetch_add(1);
        enqueue_cast(CastType::Data, id, std::move(payload), 1.0, now);
        clear_error();
        return true;
    }

    std::optional<ReceivedMessage> receive() {
        std::unique_lock<std::mutex> lock(delivery_mutex_);
        delivery_cv_.wait(lock, [&] {
            return !delivery_queue_.empty() || !running_.load();
        });

        if (delivery_queue_.empty()) {
            return std::nullopt;
        }

        ReceivedMessage message = std::move(delivery_queue_.front());
        delivery_queue_.pop_front();
        return message;
    }

    void stop() {
        std::lock_guard<std::mutex> lifecycle_lock(lifecycle_mutex_);
        if (stopped_) {
            return;
        }
        stop_workers_and_socket();
        stopped_ = true;
    }

    bool is_running() const {
        return running_.load();
    }

    Stats stats() const {
        Stats snapshot;
        snapshot.disseminated_messages = counters_.disseminated_messages.load();
        snapshot.sent_packets = counters_.sent_packets.load();
        snapshot.received_packets = counters_.received_packets.load();
        snapshot.sent_bytes = counters_.sent_bytes.load();
        snapshot.received_bytes = counters_.received_bytes.load();
        snapshot.delivered_messages = counters_.delivered_messages.load();
        snapshot.duplicate_messages = counters_.duplicate_messages.load();
        snapshot.malformed_packets = counters_.malformed_packets.load();
        snapshot.socket_errors = counters_.socket_errors.load();
        snapshot.rejected_disseminations = counters_.rejected_disseminations.load();
        snapshot.delivery_queue_overflows = counters_.delivery_queue_overflows.load();
        {
            std::lock_guard<std::mutex> lock(cache_mutex_);
            snapshot.cached_messages = cache_.size();
        }
        {
            std::lock_guard<std::mutex> lock(neighbors_mutex_);
            snapshot.known_neighbors = neighbors_.size();
        }
        {
            std::lock_guard<std::mutex> lock(delivery_mutex_);
            snapshot.pending_deliveries = delivery_queue_.size();
        }
        return snapshot;
    }

    std::string last_error() const {
        std::lock_guard<std::mutex> lock(error_mutex_);
        return last_error_;
    }

private:
    enum class CastType : std::uint8_t {
        Data,
        Request,
    };

    struct CacheEntry {
        Clock::time_point seen_at;
        Bytes payload;
    };

    struct CastEntry {
        Clock::time_point when;
        std::uint64_t order{0};
        CastType type{CastType::Data};
        MessageId id{0};
        Bytes payload;
        double probability{1.0};
    };

    struct LaterCast {
        bool operator()(const CastEntry& left, const CastEntry& right) const {
            if (left.when == right.when) {
                return left.order > right.order;
            }
            return left.when > right.when;
        }
    };

    struct Counters {
        std::atomic<std::uint64_t> disseminated_messages{0};
        std::atomic<std::uint64_t> sent_packets{0};
        std::atomic<std::uint64_t> received_packets{0};
        std::atomic<std::uint64_t> sent_bytes{0};
        std::atomic<std::uint64_t> received_bytes{0};
        std::atomic<std::uint64_t> delivered_messages{0};
        std::atomic<std::uint64_t> duplicate_messages{0};
        std::atomic<std::uint64_t> malformed_packets{0};
        std::atomic<std::uint64_t> socket_errors{0};
        std::atomic<std::uint64_t> rejected_disseminations{0};
        std::atomic<std::uint64_t> delivery_queue_overflows{0};
    };

    std::uint64_t make_seed() const {
        std::uint64_t seed = config_.seed;
        if (seed == 0) {
            seed = static_cast<std::uint64_t>(Clock::now().time_since_epoch().count());
            try {
                std::random_device random_device;
                seed ^= (static_cast<std::uint64_t>(random_device()) << 32) ^ random_device();
            } catch (...) {
                // The monotonic clock seed still prevents deterministic restarts.
            }
        }
        seed ^= config_.local_peer_id + 0x9e3779b97f4a7c15ULL + (seed << 6) + (seed >> 2);
        return seed;
    }

    bool validate_config(std::string& error) const {
        if (config_.local_peer_id == 0) {
            error = "local_peer_id must be non-zero";
        } else if (config_.port == 0) {
            error = "port must be non-zero";
        } else if (!std::isfinite(config_.beta) || config_.beta <= 0.0) {
            error = "beta must be finite and greater than zero";
        } else if (config_.max_packet_size < kDataHeaderSize) {
            error = "max_packet_size is too small for a RAPID data header";
        } else if (config_.max_gossip_headers == 0 ||
                   config_.max_gossip_headers > std::numeric_limits<std::uint16_t>::max()) {
            error = "max_gossip_headers must be between 1 and 65535";
        } else if (config_.delivery_queue_capacity == 0) {
            error = "delivery_queue_capacity must be greater than zero";
        } else if (!positive(config_.cache_ttl) ||
                   !positive(config_.neighbor_ttl) ||
                   !positive(config_.gossip_interval) ||
                   !positive(config_.heartbeat_interval) ||
                   !positive(config_.maintenance_interval)) {
            error = "cache, neighbor, gossip, heartbeat and maintenance intervals must be positive";
        } else if (config_.short_jitter_min.count() < 0 ||
                   config_.long_jitter_min.count() < 0 ||
                   config_.short_jitter_max < config_.short_jitter_min ||
                   config_.long_jitter_max < config_.long_jitter_min) {
            error = "jitter ranges are invalid";
        } else {
            return true;
        }
        return false;
    }

    static bool positive(std::chrono::milliseconds value) {
        return value.count() > 0;
    }

    static bool parse_ipv4(const std::string& text, in_addr& output) {
        return ::inet_pton(AF_INET, text.c_str(), &output) == 1;
    }

    void stop_workers_and_socket() {
        running_.store(false);
        cast_cv_.notify_all();
        timer_cv_.notify_all();
        delivery_cv_.notify_all();

        const int fd = socket_fd_.load();
        if (fd >= 0) {
            ::shutdown(fd, SHUT_RDWR);
        }

        join(receive_thread_);
        join(cast_thread_);
        join(gossip_thread_);
        join(heartbeat_thread_);
        join(maintenance_thread_);

        const int socket_to_close = socket_fd_.exchange(-1);
        if (socket_to_close >= 0) {
            ::close(socket_to_close);
        }
        delivery_cv_.notify_all();
    }

    static void join(std::thread& thread) {
        if (thread.joinable()) {
            thread.join();
        }
    }

    void receive_loop() {
        Bytes buffer(config_.max_packet_size);
        while (running_.load()) {
            const int fd = socket_fd_.load();
            if (fd < 0) {
                break;
            }

            const ssize_t received = ::recvfrom(fd,
                                                buffer.data(),
                                                buffer.size(),
                                                0,
                                                nullptr,
                                                nullptr);
            if (received <= 0) {
                if (!running_.load()) {
                    break;
                }
                if (received < 0 && errno != EAGAIN && errno != EWOULDBLOCK && errno != EINTR) {
                    counters_.socket_errors.fetch_add(1);
                    set_errno_error("recvfrom");
                }
                continue;
            }

            counters_.received_packets.fetch_add(1);
            counters_.received_bytes.fetch_add(static_cast<std::uint64_t>(received));
            Bytes packet(buffer.begin(), buffer.begin() + received);
            DecodedPacket decoded;
            if (!decode_packet(packet, decoded)) {
                counters_.malformed_packets.fetch_add(1);
                continue;
            }

            const auto now = Clock::now();
            switch (decoded.type) {
                case PacketType::Data:
                    handle_data(decoded.id, std::move(decoded.payload), now);
                    break;
                case PacketType::Gossip:
                    handle_gossip(decoded.headers, now);
                    break;
                case PacketType::Request:
                    handle_request(decoded.id);
                    break;
                case PacketType::Heartbeat:
                    handle_heartbeat(decoded.peer_id, now);
                    break;
            }
        }
    }

    void cast_loop() {
        while (true) {
            CastEntry entry;
            {
                std::unique_lock<std::mutex> lock(cast_mutex_);
                cast_cv_.wait(lock, [&] {
                    return !running_.load() || !cast_queue_.empty();
                });
                if (!running_.load()) {
                    return;
                }

                while (running_.load() && !cast_queue_.empty()) {
                    const auto due = cast_queue_.top().when;
                    if (due <= Clock::now()) {
                        break;
                    }
                    cast_cv_.wait_until(lock, due);
                }
                if (!running_.load()) {
                    return;
                }
                if (cast_queue_.empty() || cast_queue_.top().when > Clock::now()) {
                    continue;
                }
                entry = cast_queue_.top();
                cast_queue_.pop();
            }

            if (random_unit() <= entry.probability) {
                if (entry.type == CastType::Data) {
                    send_packet(encode_data(entry.id, entry.payload));
                } else {
                    send_packet(encode_request(entry.id));
                }
                continue;
            }

            entry.probability = 1.0;
            entry.when = Clock::now() + random_between(config_.long_jitter_min,
                                                       config_.long_jitter_max);
            enqueue_cast(std::move(entry));
        }
    }

    void gossip_loop() {
        while (!wait_for_stop(config_.gossip_interval)) {
            std::vector<MessageId> headers;
            const std::size_t packet_limit =
                config_.max_packet_size > kGossipHeaderSize
                    ? (config_.max_packet_size - kGossipHeaderSize) / kMessageIdSize
                    : 0;
            const std::size_t limit = std::min(config_.max_gossip_headers, packet_limit);
            {
                std::lock_guard<std::mutex> lock(cache_mutex_);
                headers.reserve(std::min(limit, cache_.size()));
                for (const auto& item : cache_) {
                    if (headers.size() >= limit) {
                        break;
                    }
                    headers.push_back(item.first);
                }
            }
            if (!headers.empty()) {
                send_packet(encode_gossip(headers));
            }
        }
    }

    void heartbeat_loop() {
        while (running_.load()) {
            send_packet(encode_heartbeat(config_.local_peer_id));
            if (wait_for_stop(config_.heartbeat_interval)) {
                break;
            }
        }
    }

    void maintenance_loop() {
        while (running_.load()) {
            cleanup(Clock::now());
            if (wait_for_stop(config_.maintenance_interval)) {
                break;
            }
        }
    }

    bool wait_for_stop(std::chrono::milliseconds duration) {
        std::unique_lock<std::mutex> lock(timer_mutex_);
        return timer_cv_.wait_for(lock, duration, [&] {
            return !running_.load();
        });
    }

    void handle_data(MessageId id, Bytes payload, Clock::time_point now) {
        {
            std::lock_guard<std::mutex> lock(cache_mutex_);
            if (cache_.find(id) != cache_.end()) {
                counters_.duplicate_messages.fetch_add(1);
                return;
            }
            cache_.emplace(id, CacheEntry{now, payload});
        }

        enqueue_delivery(payload);
        enqueue_cast(CastType::Data,
                     id,
                     std::move(payload),
                     retransmit_probability(),
                     now + random_between(config_.short_jitter_min,
                                          config_.short_jitter_max));
    }

    void handle_gossip(const std::vector<MessageId>& headers, Clock::time_point now) {
        auto request_time = now;
        for (MessageId id : headers) {
            bool missing = false;
            {
                std::lock_guard<std::mutex> lock(cache_mutex_);
                missing = cache_.find(id) == cache_.end();
            }
            if (missing) {
                request_time += random_between(config_.short_jitter_min,
                                               config_.short_jitter_max);
                enqueue_cast(CastType::Request,
                             id,
                             Bytes{},
                             1.0,
                             request_time);
            }
        }
    }

    void handle_request(MessageId id) {
        Bytes payload;
        {
            std::lock_guard<std::mutex> lock(cache_mutex_);
            const auto cached = cache_.find(id);
            if (cached == cache_.end()) {
                return;
            }
            payload = cached->second.payload;
        }
        send_packet(encode_data(id, payload));
    }

    void handle_heartbeat(PeerId peer_id, Clock::time_point now) {
        if (peer_id == 0 || peer_id == config_.local_peer_id) {
            return;
        }
        std::lock_guard<std::mutex> lock(neighbors_mutex_);
        neighbors_[peer_id] = now;
    }

    void enqueue_delivery(const Bytes& payload) {
        {
            std::lock_guard<std::mutex> lock(delivery_mutex_);
            if (delivery_queue_.size() >= config_.delivery_queue_capacity) {
                counters_.delivery_queue_overflows.fetch_add(1);
                return;
            }
            delivery_queue_.push_back(ReceivedMessage{payload});
        }
        counters_.delivered_messages.fetch_add(1);
        delivery_cv_.notify_one();
    }

    void enqueue_cast(CastType type,
                      MessageId id,
                      Bytes payload,
                      double probability,
                      Clock::time_point when) {
        CastEntry entry;
        entry.when = when;
        entry.type = type;
        entry.id = id;
        entry.payload = std::move(payload);
        entry.probability = std::clamp(probability, 0.0, 1.0);
        enqueue_cast(std::move(entry));
    }

    void enqueue_cast(CastEntry entry) {
        {
            std::lock_guard<std::mutex> lock(cast_mutex_);
            entry.order = next_cast_order_++;
            cast_queue_.push(std::move(entry));
        }
        cast_cv_.notify_one();
    }

    void send_packet(const Bytes& packet) {
        if (!running_.load()) {
            return;
        }
        const int fd = socket_fd_.load();
        if (fd < 0) {
            return;
        }

        const ssize_t sent = ::sendto(fd,
                                      packet.data(),
                                      packet.size(),
                                      0,
                                      reinterpret_cast<const sockaddr*>(&broadcast_address_),
                                      sizeof(broadcast_address_));
        if (sent < 0) {
            if (running_.load()) {
                counters_.socket_errors.fetch_add(1);
                set_errno_error("sendto");
            }
            return;
        }
        counters_.sent_packets.fetch_add(1);
        counters_.sent_bytes.fetch_add(static_cast<std::uint64_t>(sent));
    }

    void cleanup(Clock::time_point now) {
        {
            std::lock_guard<std::mutex> lock(cache_mutex_);
            for (auto it = cache_.begin(); it != cache_.end();) {
                if (now - it->second.seen_at > config_.cache_ttl) {
                    it = cache_.erase(it);
                } else {
                    ++it;
                }
            }
        }
        {
            std::lock_guard<std::mutex> lock(neighbors_mutex_);
            for (auto it = neighbors_.begin(); it != neighbors_.end();) {
                if (now - it->second > config_.neighbor_ttl) {
                    it = neighbors_.erase(it);
                } else {
                    ++it;
                }
            }
        }
    }

    double retransmit_probability() const {
        std::lock_guard<std::mutex> lock(neighbors_mutex_);
        const auto count = std::max<std::size_t>(1, neighbors_.size());
        return std::min(1.0, config_.beta / static_cast<double>(count));
    }

    std::uint64_t random_u64() {
        std::lock_guard<std::mutex> lock(rng_mutex_);
        return rng_();
    }

    double random_unit() {
        std::lock_guard<std::mutex> lock(rng_mutex_);
        return uniform_(rng_);
    }

    std::chrono::milliseconds random_between(std::chrono::milliseconds min,
                                             std::chrono::milliseconds max) {
        if (max <= min) {
            return min;
        }
        std::lock_guard<std::mutex> lock(rng_mutex_);
        std::uniform_int_distribution<long long> distribution(min.count(), max.count());
        return std::chrono::milliseconds(distribution(rng_));
    }

    void set_errno_error(const std::string& operation) {
        const int error_number = errno;
        set_error(operation + ": " + std::strerror(error_number));
    }

    void set_error(std::string message) {
        std::lock_guard<std::mutex> lock(error_mutex_);
        last_error_ = std::move(message);
    }

    void clear_error() {
        std::lock_guard<std::mutex> lock(error_mutex_);
        last_error_.clear();
    }

    Config config_;
    mutable std::mutex lifecycle_mutex_;
    bool started_once_{false};
    bool stopped_{false};
    std::atomic<bool> running_{false};
    std::atomic<int> socket_fd_{-1};
    sockaddr_in broadcast_address_{};

    std::thread receive_thread_;
    std::thread cast_thread_;
    std::thread gossip_thread_;
    std::thread heartbeat_thread_;
    std::thread maintenance_thread_;

    mutable std::mutex cache_mutex_;
    std::unordered_map<MessageId, CacheEntry> cache_;
    mutable std::mutex neighbors_mutex_;
    std::unordered_map<PeerId, Clock::time_point> neighbors_;

    std::mutex cast_mutex_;
    std::condition_variable cast_cv_;
    std::priority_queue<CastEntry, std::vector<CastEntry>, LaterCast> cast_queue_;
    std::uint64_t next_cast_order_{0};

    mutable std::mutex delivery_mutex_;
    std::condition_variable delivery_cv_;
    std::deque<ReceivedMessage> delivery_queue_;

    std::mutex timer_mutex_;
    std::condition_variable timer_cv_;

    std::mutex rng_mutex_;
    std::mt19937_64 rng_;
    std::uniform_real_distribution<double> uniform_{0.0, 1.0};

    mutable std::mutex error_mutex_;
    std::string last_error_;
    Counters counters_;
};

Rapid::Rapid(Config config)
    : impl_(std::make_unique<Impl>(std::move(config))) {
}

Rapid::~Rapid() = default;

bool Rapid::start() {
    return impl_->start();
}

bool Rapid::disseminate(Bytes payload) {
    return impl_->disseminate(std::move(payload));
}

std::optional<ReceivedMessage> Rapid::receive() {
    return impl_->receive();
}

void Rapid::stop() {
    impl_->stop();
}

bool Rapid::is_running() const {
    return impl_->is_running();
}

Stats Rapid::stats() const {
    return impl_->stats();
}

std::string Rapid::last_error() const {
    return impl_->last_error();
}

}  // namespace rapid
