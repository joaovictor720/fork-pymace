#include "trickle.hpp"

#include <algorithm>
#include <arpa/inet.h>
#include <atomic>
#include <cerrno>
#include <condition_variable>
#include <cstring>
#include <deque>
#include <exception>
#include <limits>
#include <mutex>
#include <random>
#include <sys/socket.h>
#include <thread>
#include <unistd.h>
#include <utility>

namespace gossip {
namespace trickle {
namespace {

using Clock = std::chrono::steady_clock;

enum class PacketType : std::uint8_t {
    Summary = 1,
    Update = 2,
};

constexpr std::size_t kPacketHeaderSize = 1 + 8 + 4;

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

bool read_u32(const Bytes& in, std::size_t& position, std::uint32_t& value) {
    if (position + 4 > in.size()) {
        return false;
    }
    value = 0;
    for (int i = 0; i < 4; ++i) {
        value |= static_cast<std::uint32_t>(
                     in[position + static_cast<std::size_t>(i)])
                 << (i * 8);
    }
    position += 4;
    return true;
}

bool read_u64(const Bytes& in, std::size_t& position, std::uint64_t& value) {
    if (position + 8 > in.size()) {
        return false;
    }
    value = 0;
    for (int i = 0; i < 8; ++i) {
        value |= static_cast<std::uint64_t>(
                     in[position + static_cast<std::size_t>(i)])
                 << (i * 8);
    }
    position += 8;
    return true;
}

Bytes encode_packet(PacketType type, PeerId sender, const Bytes& payload) {
    Bytes packet;
    packet.reserve(kPacketHeaderSize + payload.size());
    packet.push_back(static_cast<std::uint8_t>(type));
    append_u64(packet, sender);
    append_u32(packet, static_cast<std::uint32_t>(payload.size()));
    packet.insert(packet.end(), payload.begin(), payload.end());
    return packet;
}

struct DecodedPacket {
    PacketType type{PacketType::Summary};
    PeerId sender{0};
    Bytes payload;
};

bool decode_packet(const Bytes& packet, DecodedPacket& decoded) {
    if (packet.size() < kPacketHeaderSize) {
        return false;
    }

    switch (packet[0]) {
        case static_cast<std::uint8_t>(PacketType::Summary):
            decoded.type = PacketType::Summary;
            break;
        case static_cast<std::uint8_t>(PacketType::Update):
            decoded.type = PacketType::Update;
            break;
        default:
            return false;
    }

    std::size_t position = 1;
    std::uint32_t payload_size = 0;
    if (!read_u64(packet, position, decoded.sender) ||
        decoded.sender == 0 ||
        !read_u32(packet, position, payload_size) ||
        static_cast<std::size_t>(payload_size) != packet.size() - position) {
        return false;
    }

    decoded.payload.assign(
        packet.begin() + static_cast<std::ptrdiff_t>(position),
        packet.end());
    return true;
}

bool parse_ipv4(const std::string& text, in_addr& address) {
    return ::inet_pton(AF_INET, text.c_str(), &address) == 1;
}

}  // namespace

class Trickle::Impl {
public:
    Impl(Config config, StateAdapter& adapter)
        : config_(std::move(config)),
          adapter_(adapter),
          rng_(make_seed()) {
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
            set_error("a Trickle instance cannot be restarted after stop()");
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
        if (::bind(fd,
                   reinterpret_cast<sockaddr*>(&bind_address),
                   sizeof(bind_address)) < 0) {
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
        {
            std::lock_guard<std::mutex> protocol_lock(protocol_mutex_);
            schedule_interval_locked(config_.minimum_interval, Clock::now());
        }

        clear_error();
        running_.store(true);
        stopped_ = false;

        try {
            receive_thread_ = std::thread(&Impl::receive_loop, this);
            timer_thread_ = std::thread(&Impl::timer_loop, this);
        } catch (const std::exception& error) {
            set_error(std::string("failed to start Trickle workers: ") + error.what());
            stop_workers_and_socket();
            stopped_ = true;
            return false;
        }

        return true;
    }

    bool notify_state_changed() {
        std::lock_guard<std::mutex> lifecycle_lock(lifecycle_mutex_);
        if (!running_.load()) {
            counters_.rejected_state_changes.fetch_add(1);
            set_error("notify_state_changed() requires a running Trickle instance");
            return false;
        }

        reset_interval();
        clear_error();
        return true;
    }

    std::optional<ReceivedUpdate> receive() {
        std::unique_lock<std::mutex> delivery_lock(delivery_mutex_);
        delivery_cv_.wait(delivery_lock, [&] {
            return !delivery_queue_.empty() || !running_.load();
        });

        if (delivery_queue_.empty()) {
            return std::nullopt;
        }

        ReceivedUpdate update = std::move(delivery_queue_.front());
        delivery_queue_.pop_front();
        return update;
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
        snapshot.sent_packets = counters_.sent_packets.load();
        snapshot.received_packets = counters_.received_packets.load();
        snapshot.sent_bytes = counters_.sent_bytes.load();
        snapshot.received_bytes = counters_.received_bytes.load();
        snapshot.sent_summaries = counters_.sent_summaries.load();
        snapshot.received_summaries = counters_.received_summaries.load();
        snapshot.sent_updates = counters_.sent_updates.load();
        snapshot.received_updates = counters_.received_updates.load();
        snapshot.delivered_updates = counters_.delivered_updates.load();
        snapshot.consistent_summaries = counters_.consistent_summaries.load();
        snapshot.suppressed_summaries = counters_.suppressed_summaries.load();
        snapshot.interval_resets = counters_.interval_resets.load();
        snapshot.malformed_packets = counters_.malformed_packets.load();
        snapshot.adapter_errors = counters_.adapter_errors.load();
        snapshot.oversized_payloads = counters_.oversized_payloads.load();
        snapshot.socket_errors = counters_.socket_errors.load();
        snapshot.rejected_state_changes = counters_.rejected_state_changes.load();
        snapshot.delivery_queue_overflows =
            counters_.delivery_queue_overflows.load();

        {
            std::lock_guard<std::mutex> protocol_lock(protocol_mutex_);
            snapshot.current_interval = current_interval_;
            snapshot.consistent_count = consistent_count_;
        }
        {
            std::lock_guard<std::mutex> delivery_lock(delivery_mutex_);
            snapshot.pending_deliveries = delivery_queue_.size();
        }
        return snapshot;
    }

    std::string last_error() const {
        std::lock_guard<std::mutex> error_lock(error_mutex_);
        return last_error_;
    }

private:
    struct Counters {
        std::atomic<std::uint64_t> sent_packets{0};
        std::atomic<std::uint64_t> received_packets{0};
        std::atomic<std::uint64_t> sent_bytes{0};
        std::atomic<std::uint64_t> received_bytes{0};
        std::atomic<std::uint64_t> sent_summaries{0};
        std::atomic<std::uint64_t> received_summaries{0};
        std::atomic<std::uint64_t> sent_updates{0};
        std::atomic<std::uint64_t> received_updates{0};
        std::atomic<std::uint64_t> delivered_updates{0};
        std::atomic<std::uint64_t> consistent_summaries{0};
        std::atomic<std::uint64_t> suppressed_summaries{0};
        std::atomic<std::uint64_t> interval_resets{0};
        std::atomic<std::uint64_t> malformed_packets{0};
        std::atomic<std::uint64_t> adapter_errors{0};
        std::atomic<std::uint64_t> oversized_payloads{0};
        std::atomic<std::uint64_t> socket_errors{0};
        std::atomic<std::uint64_t> rejected_state_changes{0};
        std::atomic<std::uint64_t> delivery_queue_overflows{0};
    };

    std::uint64_t make_seed() const {
        if (config_.seed != 0) {
            return config_.seed;
        }
        std::random_device random_device;
        const auto high = static_cast<std::uint64_t>(random_device()) << 32;
        return high ^ static_cast<std::uint64_t>(random_device());
    }

    bool validate_config(std::string& error) const {
        if (config_.local_peer_id == 0) {
            error = "local_peer_id must be non-zero";
            return false;
        }
        if (config_.port == 0) {
            error = "port must be non-zero";
            return false;
        }
        if (config_.redundancy_constant == 0) {
            error = "redundancy_constant must be non-zero";
            return false;
        }
        if (config_.minimum_interval.count() <= 0) {
            error = "minimum_interval must be positive";
            return false;
        }
        if (config_.maximum_interval < config_.minimum_interval) {
            error = "maximum_interval must be at least minimum_interval";
            return false;
        }
        if (config_.max_packet_size <= kPacketHeaderSize) {
            error = "max_packet_size is too small for a Trickle packet";
            return false;
        }
        if (config_.delivery_queue_capacity == 0) {
            error = "delivery_queue_capacity must be non-zero";
            return false;
        }
        return true;
    }

    Clock::duration random_transmit_offset_locked(
        std::chrono::milliseconds interval) {
        const auto duration = std::chrono::duration_cast<std::chrono::nanoseconds>(
            interval);
        const auto total = duration.count();
        const auto first = total / 2;
        const auto last = std::max(first, total - 1);
        std::uniform_int_distribution<std::int64_t> distribution(first, last);
        return std::chrono::nanoseconds(distribution(rng_));
    }

    void schedule_interval_locked(std::chrono::milliseconds interval,
                                  Clock::time_point start) {
        current_interval_ = interval;
        interval_end_ = start + interval;
        transmit_at_ = start + random_transmit_offset_locked(interval);
        transmitted_in_interval_ = false;
        consistent_count_ = 0;
    }

    void reset_interval() {
        {
            std::lock_guard<std::mutex> protocol_lock(protocol_mutex_);
            schedule_interval_locked(config_.minimum_interval, Clock::now());
        }
        counters_.interval_resets.fetch_add(1);
        protocol_cv_.notify_all();
    }

    void advance_interval_locked() {
        const auto current = current_interval_.count();
        const auto maximum = config_.maximum_interval.count();
        const auto doubled =
            current > maximum / 2 ? maximum : std::min(maximum, current * 2);
        schedule_interval_locked(
            std::chrono::milliseconds(doubled),
            Clock::now());
    }

    void timer_loop() {
        std::unique_lock<std::mutex> protocol_lock(protocol_mutex_);
        while (running_.load()) {
            const auto now = Clock::now();
            if (now >= interval_end_) {
                advance_interval_locked();
                continue;
            }

            if (!transmitted_in_interval_ && now >= transmit_at_) {
                transmitted_in_interval_ = true;
                const bool should_transmit =
                    consistent_count_ < config_.redundancy_constant;
                if (!should_transmit) {
                    counters_.suppressed_summaries.fetch_add(1);
                }

                protocol_lock.unlock();
                if (should_transmit && running_.load()) {
                    send_summary();
                }
                protocol_lock.lock();
                continue;
            }

            const auto wake_at = transmitted_in_interval_
                                     ? interval_end_
                                     : std::min(transmit_at_, interval_end_);
            protocol_cv_.wait_until(protocol_lock, wake_at);
        }
    }

    void receive_loop() {
        Bytes buffer(config_.max_packet_size);
        while (running_.load()) {
            sockaddr_in source{};
            socklen_t source_length = sizeof(source);
            const int fd = socket_fd_.load();
            if (fd < 0) {
                break;
            }

            const ssize_t received = ::recvfrom(
                fd,
                buffer.data(),
                buffer.size(),
                0,
                reinterpret_cast<sockaddr*>(&source),
                &source_length);
            if (received == 0 && !running_.load()) {
                break;
            }
            if (received < 0) {
                if (!running_.load()) {
                    break;
                }
                if (errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR) {
                    continue;
                }
                if (errno != EBADF) {
                    counters_.socket_errors.fetch_add(1);
                    set_errno_error("recvfrom");
                }
                continue;
            }

            Bytes packet(buffer.begin(),
                         buffer.begin() + static_cast<std::ptrdiff_t>(received));
            DecodedPacket decoded;
            if (!decode_packet(packet, decoded)) {
                counters_.received_packets.fetch_add(1);
                counters_.received_bytes.fetch_add(
                    static_cast<std::uint64_t>(received));
                counters_.malformed_packets.fetch_add(1);
                continue;
            }
            if (decoded.sender == config_.local_peer_id) {
                continue;
            }

            counters_.received_packets.fetch_add(1);
            counters_.received_bytes.fetch_add(
                static_cast<std::uint64_t>(received));

            if (decoded.type == PacketType::Summary) {
                counters_.received_summaries.fetch_add(1);
                handle_summary(decoded.payload);
            } else {
                counters_.received_updates.fetch_add(1);
                deliver_update(decoded.sender, std::move(decoded.payload));
            }
        }
    }

    void handle_summary(const Bytes& remote_summary) {
        StateRelation relation = StateRelation::Equivalent;
        try {
            relation = adapter_.compare(remote_summary);
        } catch (const std::exception& error) {
            counters_.adapter_errors.fetch_add(1);
            set_error(std::string("StateAdapter::compare(): ") + error.what());
            return;
        } catch (...) {
            counters_.adapter_errors.fetch_add(1);
            set_error("StateAdapter::compare() failed");
            return;
        }

        switch (relation) {
            case StateRelation::Equivalent: {
                std::lock_guard<std::mutex> protocol_lock(protocol_mutex_);
                if (consistent_count_ <
                    std::numeric_limits<std::uint32_t>::max()) {
                    ++consistent_count_;
                }
                counters_.consistent_summaries.fetch_add(1);
                break;
            }
            case StateRelation::LocalNewer:
                send_update(remote_summary);
                break;
            case StateRelation::RemoteNewer:
                reset_interval();
                send_summary();
                break;
            case StateRelation::Incomparable:
                send_update(remote_summary);
                reset_interval();
                send_summary();
                break;
        }
    }

    void send_summary() {
        Bytes summary;
        try {
            summary = adapter_.summary();
        } catch (const std::exception& error) {
            counters_.adapter_errors.fetch_add(1);
            set_error(std::string("StateAdapter::summary(): ") + error.what());
            return;
        } catch (...) {
            counters_.adapter_errors.fetch_add(1);
            set_error("StateAdapter::summary() failed");
            return;
        }
        send_packet(PacketType::Summary, summary);
    }

    void send_update(const Bytes& remote_summary) {
        Bytes update;
        try {
            update = adapter_.make_update(remote_summary);
        } catch (const std::exception& error) {
            counters_.adapter_errors.fetch_add(1);
            set_error(std::string("StateAdapter::make_update(): ") + error.what());
            return;
        } catch (...) {
            counters_.adapter_errors.fetch_add(1);
            set_error("StateAdapter::make_update() failed");
            return;
        }
        send_packet(PacketType::Update, update);
    }

    bool send_packet(PacketType type, const Bytes& payload) {
        if (payload.size() > std::numeric_limits<std::uint32_t>::max() ||
            payload.size() > config_.max_packet_size - kPacketHeaderSize) {
            counters_.oversized_payloads.fetch_add(1);
            set_error("Trickle payload exceeds max_packet_size");
            return false;
        }

        const Bytes packet =
            encode_packet(type, config_.local_peer_id, payload);
        const int fd = socket_fd_.load();
        if (fd < 0 || !running_.load()) {
            return false;
        }

        const ssize_t sent = ::sendto(
            fd,
            packet.data(),
            packet.size(),
            0,
            reinterpret_cast<const sockaddr*>(&broadcast_address_),
            sizeof(broadcast_address_));
        if (sent < 0) {
            if (running_.load() && errno != EBADF) {
                counters_.socket_errors.fetch_add(1);
                set_errno_error("sendto");
            }
            return false;
        }

        counters_.sent_packets.fetch_add(1);
        counters_.sent_bytes.fetch_add(static_cast<std::uint64_t>(sent));
        if (type == PacketType::Summary) {
            counters_.sent_summaries.fetch_add(1);
        } else {
            counters_.sent_updates.fetch_add(1);
        }
        return true;
    }

    void deliver_update(PeerId sender, Bytes payload) {
        std::lock_guard<std::mutex> delivery_lock(delivery_mutex_);
        if (delivery_queue_.size() >= config_.delivery_queue_capacity) {
            counters_.delivery_queue_overflows.fetch_add(1);
            return;
        }

        delivery_queue_.push_back(
            ReceivedUpdate{sender, std::move(payload)});
        counters_.delivered_updates.fetch_add(1);
        delivery_cv_.notify_one();
    }

    void stop_workers_and_socket() {
        running_.store(false);
        protocol_cv_.notify_all();
        delivery_cv_.notify_all();

        const int fd = socket_fd_.load();
        if (fd >= 0) {
            ::shutdown(fd, SHUT_RDWR);
        }

        if (timer_thread_.joinable()) {
            timer_thread_.join();
        }
        if (receive_thread_.joinable()) {
            receive_thread_.join();
        }

        const int fd_to_close = socket_fd_.exchange(-1);
        if (fd_to_close >= 0) {
            ::close(fd_to_close);
        }
    }

    void clear_error() {
        std::lock_guard<std::mutex> error_lock(error_mutex_);
        last_error_.clear();
    }

    void set_error(std::string error) const {
        std::lock_guard<std::mutex> error_lock(error_mutex_);
        last_error_ = std::move(error);
    }

    void set_errno_error(const char* operation) const {
        const int error_number = errno;
        set_error(std::string(operation) + ": " + std::strerror(error_number));
    }

    Config config_;
    StateAdapter& adapter_;

    mutable std::mutex lifecycle_mutex_;
    std::atomic<bool> running_{false};
    bool started_once_{false};
    bool stopped_{false};
    std::atomic<int> socket_fd_{-1};
    sockaddr_in broadcast_address_{};
    std::thread receive_thread_;
    std::thread timer_thread_;

    mutable std::mutex protocol_mutex_;
    std::condition_variable protocol_cv_;
    std::chrono::milliseconds current_interval_{0};
    Clock::time_point interval_end_{};
    Clock::time_point transmit_at_{};
    bool transmitted_in_interval_{false};
    std::uint32_t consistent_count_{0};
    std::mt19937_64 rng_;

    mutable std::mutex delivery_mutex_;
    std::condition_variable delivery_cv_;
    std::deque<ReceivedUpdate> delivery_queue_;

    mutable std::mutex error_mutex_;
    mutable std::string last_error_;
    Counters counters_;
};

Trickle::Trickle(Config config, StateAdapter& adapter)
    : impl_(std::make_unique<Impl>(std::move(config), adapter)) {
}

Trickle::~Trickle() = default;

bool Trickle::start() {
    return impl_->start();
}

bool Trickle::notify_state_changed() {
    return impl_->notify_state_changed();
}

std::optional<ReceivedUpdate> Trickle::receive() {
    return impl_->receive();
}

void Trickle::stop() {
    impl_->stop();
}

bool Trickle::is_running() const {
    return impl_->is_running();
}

Stats Trickle::stats() const {
    return impl_->stats();
}

std::string Trickle::last_error() const {
    return impl_->last_error();
}

}  // namespace trickle
}  // namespace gossip
