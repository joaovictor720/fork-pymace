#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <cerrno>
#include <fstream>
#include <iostream>
#include <mutex>
#include <nlohmann/json.hpp>
#include <random>
#include <sstream>
#include <string>
#include <thread>
#include <vector>

#include <arpa/inet.h>
#include <net/if.h>
#include <netinet/in.h>
#include <sys/ioctl.h>
#include <sys/socket.h>
#include <unistd.h>

#include "../common/delta-crdts.cc"

using json = nlohmann::json;

namespace {

constexpr std::size_t MSG_MAX = 65535;
constexpr std::size_t USFD_HEADER_SIZE = 8;
constexpr int DEFAULT_PORT = 5001;
constexpr int DEFAULT_DPD_CAPACITY = 1000;
constexpr double DEFAULT_DPD_TTL_SECONDS = 60.0;
constexpr int DEFAULT_RETRANSMISSIONS = 1;
constexpr int DEFAULT_RETRANSMIT_DELAY_US = 5000;

std::atomic<bool> g_running{true};
std::atomic<int> g_active_retransmissions{0};

std::mutex gc_mutex;
std::mutex delta_mutex;
std::mutex event_log_mutex;
std::ofstream event_log;

struct NodeConfig {
    std::string id;
    std::string listen_addr;
    std::string interface_name{"eth0"};
    std::string broadcast_address;
    bool broadcast_address_configured{false};
    std::uint32_t origin_id{0};
    double ops_per_sec{1.0};
    int duration{10};
    std::string distribution{"uniform"};
    int seed{0};
    std::string log_file;
    double monitor_interval{1.0};
    double dissemination_interval{0.5};
    double cooldown{10.0};
    int dpd_capacity{DEFAULT_DPD_CAPACITY};
    double dpd_ttl_seconds{DEFAULT_DPD_TTL_SECONDS};
    int retransmissions{DEFAULT_RETRANSMISSIONS};
    int retransmit_delay_us{DEFAULT_RETRANSMIT_DELAY_US};
};

struct Stats {
    std::atomic<std::uint64_t> sent_msgs{0};
    std::atomic<std::uint64_t> recv_msgs{0};
    std::atomic<std::uint64_t> sent_bytes{0};
    std::atomic<std::uint64_t> recv_bytes{0};
    std::atomic<std::uint64_t> generated_msgs{0};
    std::atomic<std::uint64_t> forwarded_msgs{0};
    std::atomic<std::uint64_t> duplicate_drops{0};
    std::atomic<std::uint64_t> malformed_packets{0};
    std::atomic<std::uint64_t> socket_errors{0};
    std::atomic<std::uint64_t> retransmit_tasks{0};
};

struct DpdEntry {
    std::uint32_t origin{0};
    std::uint32_t seqno{0};
    std::chrono::steady_clock::time_point expires_at{};
    bool valid{false};
};

class DpdCache {
public:
    DpdCache(int capacity, double ttl_seconds)
        : entries_(static_cast<std::size_t>(std::max(1, capacity))),
          ttl_(std::chrono::duration_cast<std::chrono::steady_clock::duration>(
              std::chrono::duration<double>(std::max(0.001, ttl_seconds)))) {
    }

    bool remember_if_new(std::uint32_t origin, std::uint32_t seqno) {
        const auto now = std::chrono::steady_clock::now();
        std::lock_guard<std::mutex> lock(mutex_);

        for (auto& entry : entries_) {
            if (entry.valid && entry.expires_at <= now) {
                entry.valid = false;
            }
            if (entry.valid && entry.origin == origin && entry.seqno == seqno) {
                return false;
            }
        }

        std::size_t slot = next_;
        for (std::size_t i = 0; i < entries_.size(); ++i) {
            if (!entries_[i].valid) {
                slot = i;
                break;
            }
        }

        entries_[slot] = DpdEntry{origin, seqno, now + ttl_, true};
        next_ = (slot + 1) % entries_.size();
        return true;
    }

    std::size_t live_count() const {
        const auto now = std::chrono::steady_clock::now();
        std::lock_guard<std::mutex> lock(mutex_);
        std::size_t count = 0;
        for (const auto& entry : entries_) {
            if (entry.valid && entry.expires_at > now) {
                ++count;
            }
        }
        return count;
    }

private:
    std::vector<DpdEntry> entries_;
    std::chrono::steady_clock::duration ttl_;
    std::size_t next_{0};
    mutable std::mutex mutex_;
};

double now_ts() {
    return std::chrono::duration<double>(
               std::chrono::steady_clock::now().time_since_epoch())
        .count();
}

void log_event(const std::string& node_id,
               const std::string& event,
               const std::string& details = "") {
    std::lock_guard<std::mutex> lock(event_log_mutex);
    event_log << std::fixed << now_ts()
              << ", event=" << event
              << ", node=" << node_id;
    if (!details.empty()) {
        event_log << ", " << details;
    }
    event_log << "\n";
}

void log_apply(const std::string& node_id, int total) {
    std::lock_guard<std::mutex> lock(event_log_mutex);
    event_log << std::fixed << now_ts()
              << ", event=op_apply, node=" << node_id
              << ", total=" << total << "\n";
}

bool parse_host_port(const std::string& value,
                     std::string& host,
                     std::uint16_t& port) {
    const auto separator = value.rfind(':');
    if (separator == std::string::npos || separator + 1 == value.size()) {
        return false;
    }

    host = value.substr(0, separator);
    if (host.empty()) {
        return false;
    }

    try {
        std::size_t used = 0;
        const auto parsed = std::stoul(value.substr(separator + 1), &used, 10);
        if (used != value.size() - separator - 1 ||
            parsed == 0 ||
            parsed > 65535) {
            return false;
        }
        port = static_cast<std::uint16_t>(parsed);
        return true;
    } catch (...) {
        return false;
    }
}

std::uint32_t fnv1a32(const std::string& value) {
    std::uint32_t hash = 2166136261u;
    for (unsigned char c : value) {
        hash ^= c;
        hash *= 16777619u;
    }
    return hash == 0 ? 1 : hash;
}

std::uint32_t derive_origin_id(const std::string& listen_addr,
                               const std::string& node_id) {
    std::string host;
    std::uint16_t port = 0;
    in_addr parsed_addr{};
    if (parse_host_port(listen_addr, host, port) &&
        inet_pton(AF_INET, host.c_str(), &parsed_addr) == 1) {
        const std::uint32_t ip = ntohl(parsed_addr.s_addr);
        if (ip != 0) {
            return ip;
        }
    }

    try {
        std::size_t used = 0;
        const auto parsed = std::stoul(node_id, &used, 10);
        if (used == node_id.size() &&
            parsed < static_cast<unsigned long>(UINT32_MAX)) {
            return static_cast<std::uint32_t>(parsed + 1);
        }
    } catch (...) {
    }

    return fnv1a32(node_id);
}

std::string ipv4_to_string(const in_addr& addr) {
    char text[INET_ADDRSTRLEN] = {};
    if (inet_ntop(AF_INET, &addr, text, sizeof(text)) == nullptr) {
        return "255.255.255.255";
    }
    return text;
}

bool parse_ipv4(const std::string& value, in_addr& out) {
    return inet_pton(AF_INET, value.c_str(), &out) == 1;
}

bool is_unspecified_addr(const in_addr& addr) {
    return addr.s_addr == INADDR_ANY;
}

bool get_interface_broadcast(const std::string& interface_name, in_addr& out) {
    if (interface_name.empty()) {
        return false;
    }

    const int sock = socket(AF_INET, SOCK_DGRAM, 0);
    if (sock < 0) {
        return false;
    }

    ifreq ifr{};
    std::snprintf(ifr.ifr_name, sizeof(ifr.ifr_name), "%s", interface_name.c_str());
    const bool ok = ioctl(sock, SIOCGIFBRDADDR, &ifr) == 0;
    if (ok) {
        const auto* sin = reinterpret_cast<const sockaddr_in*>(&ifr.ifr_broadaddr);
        out = sin->sin_addr;
    }
    close(sock);
    return ok && !is_unspecified_addr(out);
}

void bind_to_device(int sockfd, const std::string& interface_name) {
    if (interface_name.empty()) {
        return;
    }

    ifreq ifr{};
    std::snprintf(ifr.ifr_name, sizeof(ifr.ifr_name), "%s", interface_name.c_str());
    if (setsockopt(sockfd,
                   SOL_SOCKET,
                   SO_BINDTODEVICE,
                   &ifr,
                   sizeof(ifr)) < 0) {
        std::cerr << "AVISO: falha ao prender socket na interface "
                  << interface_name << ": " << std::strerror(errno) << "\n";
    }
}

void set_receive_timeout(int sockfd) {
    timeval tv{};
    tv.tv_sec = 0;
    tv.tv_usec = 200000;
    (void)setsockopt(sockfd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
}

int make_udp_socket(const std::string& interface_name, bool receive_socket) {
    const int sockfd = socket(AF_INET, SOCK_DGRAM, 0);
    if (sockfd < 0) {
        return -1;
    }

    int enabled = 1;
    (void)setsockopt(sockfd, SOL_SOCKET, SO_REUSEADDR, &enabled, sizeof(enabled));
#ifdef SO_REUSEPORT
    (void)setsockopt(sockfd, SOL_SOCKET, SO_REUSEPORT, &enabled, sizeof(enabled));
#endif
    (void)setsockopt(sockfd, SOL_SOCKET, SO_BROADCAST, &enabled, sizeof(enabled));
    bind_to_device(sockfd, interface_name);
    if (receive_socket) {
        set_receive_timeout(sockfd);
    }
    return sockfd;
}

bool bind_receive_socket(int sockfd, std::uint16_t port) {
    sockaddr_in addr{};
    addr.sin_family = AF_INET;
    addr.sin_addr.s_addr = INADDR_ANY;
    addr.sin_port = htons(port);
    return bind(sockfd,
                reinterpret_cast<const sockaddr*>(&addr),
                sizeof(addr)) == 0;
}

bool resolve_broadcast_addr(NodeConfig& config,
                            std::uint16_t port,
                            sockaddr_in& out) {
    in_addr broadcast{};
    if (config.broadcast_address_configured) {
        if (!parse_ipv4(config.broadcast_address, broadcast) ||
            is_unspecified_addr(broadcast)) {
            return false;
        }
    } else if (get_interface_broadcast(config.interface_name, broadcast)) {
        config.broadcast_address = ipv4_to_string(broadcast);
    } else {
        config.broadcast_address = "255.255.255.255";
        if (!parse_ipv4(config.broadcast_address, broadcast)) {
            return false;
        }
    }

    out = sockaddr_in{};
    out.sin_family = AF_INET;
    out.sin_port = htons(port);
    out.sin_addr = broadcast;
    return true;
}

std::vector<char> make_usfd_packet(std::uint32_t origin,
                                   std::uint32_t seqno,
                                   const std::string& payload) {
    std::vector<char> packet(USFD_HEADER_SIZE + payload.size());
    const std::uint32_t origin_net = htonl(origin);
    const std::uint32_t seqno_net = htonl(seqno);
    std::memcpy(packet.data(), &origin_net, sizeof(origin_net));
    std::memcpy(packet.data() + sizeof(origin_net), &seqno_net, sizeof(seqno_net));
    if (!payload.empty()) {
        std::memcpy(packet.data() + USFD_HEADER_SIZE,
                    payload.data(),
                    payload.size());
    }
    return packet;
}

bool parse_usfd_packet(const char* data,
                       std::size_t size,
                       std::uint32_t& origin,
                       std::uint32_t& seqno,
                       const char*& payload,
                       std::size_t& payload_size) {
    if (size < USFD_HEADER_SIZE) {
        return false;
    }

    std::uint32_t origin_net = 0;
    std::uint32_t seqno_net = 0;
    std::memcpy(&origin_net, data, sizeof(origin_net));
    std::memcpy(&seqno_net, data + sizeof(origin_net), sizeof(seqno_net));
    origin = ntohl(origin_net);
    seqno = ntohl(seqno_net);
    payload = data + USFD_HEADER_SIZE;
    payload_size = size - USFD_HEADER_SIZE;
    return true;
}

void spawn_retransmission(int send_sock,
                          sockaddr_in broadcast_addr,
                          std::vector<char> packet,
                          Stats& stats,
                          int copies,
                          int delay_us) {
    g_active_retransmissions.fetch_add(1, std::memory_order_relaxed);
    stats.retransmit_tasks.fetch_add(1, std::memory_order_relaxed);

    std::thread([send_sock,
                 broadcast_addr,
                 packet = std::move(packet),
                 &stats,
                 copies,
                 delay_us]() {
        for (int i = 0; i < copies; ++i) {
            const ssize_t sent = sendto(
                send_sock,
                packet.data(),
                packet.size(),
                0,
                reinterpret_cast<const sockaddr*>(&broadcast_addr),
                sizeof(broadcast_addr));
            if (sent >= 0) {
                stats.sent_msgs.fetch_add(1, std::memory_order_relaxed);
                stats.sent_bytes.fetch_add(static_cast<std::uint64_t>(sent),
                                           std::memory_order_relaxed);
            } else {
                stats.socket_errors.fetch_add(1, std::memory_order_relaxed);
            }

            if (i + 1 < copies) {
                usleep(static_cast<useconds_t>(delay_us));
            }
        }
        g_active_retransmissions.fetch_sub(1, std::memory_order_relaxed);
    }).detach();
}

NodeConfig load_config(const std::string& path, const std::string& id) {
    std::ifstream input(path);
    if (!input.is_open()) {
        return NodeConfig{};
    }

    const json document = json::parse(input);
    NodeConfig config;
    config.id = id;
    try {
        config.listen_addr = document.at("address").at(id).get<std::string>();
    } catch (...) {
        return config;
    }

    config.interface_name = document.value(
        "usfd_interface",
        document.value("interface", std::string{"eth0"}));
    if (document.contains("broadcast_address")) {
        config.broadcast_address =
            document.value("broadcast_address", std::string{"255.255.255.255"});
        config.broadcast_address_configured = true;
    }

    config.origin_id =
        document.value("usfd_origin_id", derive_origin_id(config.listen_addr, id));
    if (config.origin_id == 0) {
        config.origin_id = derive_origin_id(config.listen_addr, id);
    }

    config.ops_per_sec = document.value("ops_per_sec", 1.0);
    if (config.ops_per_sec <= 0.0) {
        config.ops_per_sec = 1.0;
    }
    config.duration = document.value("duration", 10);
    config.distribution = document.value("distribution", std::string{"uniform"});
    config.dissemination_interval =
        document.value("dissemination_interval", 0.5);
    if (config.dissemination_interval <= 0.0) {
        config.dissemination_interval = 0.5;
    }

    config.seed =
        document.value("seed", 1) +
        std::atoi(id.c_str());
    config.monitor_interval = document.value("monitor_interval", 1.0);
    if (config.monitor_interval <= 0.0) {
        config.monitor_interval = 1.0;
    }
    config.cooldown = document.value("cooldown", 10.0);

    config.dpd_capacity =
        document.value("usfd_dpd_capacity", DEFAULT_DPD_CAPACITY);
    if (config.dpd_capacity <= 0) {
        config.dpd_capacity = DEFAULT_DPD_CAPACITY;
    }
    config.dpd_ttl_seconds =
        document.value("usfd_dpd_ttl_seconds", DEFAULT_DPD_TTL_SECONDS);
    if (config.dpd_ttl_seconds <= 0.0) {
        config.dpd_ttl_seconds = DEFAULT_DPD_TTL_SECONDS;
    }
    config.retransmissions =
        document.value("usfd_retransmissions", DEFAULT_RETRANSMISSIONS);
    if (config.retransmissions <= 0) {
        config.retransmissions = DEFAULT_RETRANSMISSIONS;
    }
    config.retransmit_delay_us =
        document.value("usfd_retransmit_delay_us", DEFAULT_RETRANSMIT_DELAY_US);
    if (config.retransmit_delay_us < 0) {
        config.retransmit_delay_us = DEFAULT_RETRANSMIT_DELAY_US;
    }

    std::string log_dir = document.value("log_dir", std::string{"."});
    if (!log_dir.empty() && log_dir.back() != '/') {
        log_dir.push_back('/');
    }
    config.log_file = log_dir + "node_" + id + ".log";
    return config;
}

void print_config(const NodeConfig& config) {
    std::cout << "===== USFD APPLICATION =====\n";
    std::cout << "node id: " << config.id << "\n";
    std::cout << "listen: " << config.listen_addr << "\n";
    std::cout << "interface: " << config.interface_name << "\n";
    std::cout << "broadcast: " << config.broadcast_address << "\n";
    std::cout << "origin_id: " << config.origin_id << "\n";
    std::cout << "ops_per_sec: " << config.ops_per_sec << "\n";
    std::cout << "duration: " << config.duration << "\n";
    std::cout << "seed: " << config.seed << "\n";
    std::cout << "monitor_interval: " << config.monitor_interval << "\n";
    std::cout << "dissemination_interval: "
              << config.dissemination_interval << "\n";
    std::cout << "dpd_capacity: " << config.dpd_capacity << "\n";
    std::cout << "dpd_ttl_seconds: " << config.dpd_ttl_seconds << "\n";
    std::cout << "retransmissions: " << config.retransmissions << "\n";
    std::cout << "retransmit_delay_us: "
              << config.retransmit_delay_us << "\n";
}

void apply_payload(const char* payload,
                   std::size_t payload_size,
                   gcounter<int, std::string>& counter,
                   Stats& stats,
                   const std::string& node_id,
                   std::size_t packet_size) {
    try {
        const auto received_counter =
            gcounter<int, std::string>::deserialize(
                std::string(payload, payload_size));

        int total = 0;
        {
            std::lock_guard<std::mutex> lock(gc_mutex);
            counter.join(received_counter);
            total = counter.read();
        }
        log_apply(node_id, total);
        stats.recv_msgs.fetch_add(1, std::memory_order_relaxed);
        stats.recv_bytes.fetch_add(static_cast<std::uint64_t>(packet_size),
                                   std::memory_order_relaxed);
    } catch (...) {
        stats.malformed_packets.fetch_add(1, std::memory_order_relaxed);
    }
}

void receive_loop(int recv_sock,
                  int send_sock,
                  sockaddr_in broadcast_addr,
                  DpdCache& dpd,
                  gcounter<int, std::string>& counter,
                  Stats& stats,
                  const NodeConfig& config) {
    std::vector<char> buffer(MSG_MAX);
    while (g_running.load()) {
        sockaddr_in src{};
        socklen_t src_len = sizeof(src);
        const ssize_t n = recvfrom(
            recv_sock,
            buffer.data(),
            buffer.size(),
            0,
            reinterpret_cast<sockaddr*>(&src),
            &src_len);

        if (n <= 0) {
            if (!g_running.load()) {
                break;
            }
            if (n < 0) {
                if (errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR) {
                    continue;
                }
                if (errno == EBADF) {
                    break;
                }
                stats.socket_errors.fetch_add(1, std::memory_order_relaxed);
            }
            continue;
        }

        std::uint32_t origin = 0;
        std::uint32_t seqno = 0;
        const char* payload = nullptr;
        std::size_t payload_size = 0;
        if (!parse_usfd_packet(buffer.data(),
                               static_cast<std::size_t>(n),
                               origin,
                               seqno,
                               payload,
                               payload_size)) {
            stats.malformed_packets.fetch_add(1, std::memory_order_relaxed);
            continue;
        }

        if (!dpd.remember_if_new(origin, seqno)) {
            stats.duplicate_drops.fetch_add(1, std::memory_order_relaxed);
            continue;
        }

        stats.forwarded_msgs.fetch_add(1, std::memory_order_relaxed);
        spawn_retransmission(
            send_sock,
            broadcast_addr,
            std::vector<char>(buffer.begin(), buffer.begin() + n),
            stats,
            config.retransmissions,
            config.retransmit_delay_us);

        apply_payload(payload,
                      payload_size,
                      counter,
                      stats,
                      config.id,
                      static_cast<std::size_t>(n));
    }
}

void dissemination_loop(int send_sock,
                        sockaddr_in broadcast_addr,
                        DpdCache& dpd,
                        gcounter<int, std::string>& delta_buffer,
                        Stats& stats,
                        const NodeConfig& config) {
    std::uint32_t next_seqno = 0;
    auto next = std::chrono::steady_clock::now();

    while (g_running.load()) {
        next += std::chrono::duration_cast<std::chrono::steady_clock::duration>(
            std::chrono::duration<double>(config.dissemination_interval));
        std::this_thread::sleep_until(next);
        if (!g_running.load()) {
            break;
        }

        std::string payload;
        {
            std::lock_guard<std::mutex> lock(delta_mutex);
            if (delta_buffer == gcounter<int, std::string>()) {
                continue;
            }
            payload = delta_buffer.serialize();
            delta_buffer = gcounter<int, std::string>();
        }

        const std::uint32_t seqno = next_seqno++;
        if (!dpd.remember_if_new(config.origin_id, seqno)) {
            stats.duplicate_drops.fetch_add(1, std::memory_order_relaxed);
            continue;
        }

        std::ostringstream details;
        details << "origin=" << config.origin_id
                << ", seqno=" << seqno
                << ", payload_bytes=" << payload.size();
        log_event(config.id, "usfd_emit", details.str());

        stats.generated_msgs.fetch_add(1, std::memory_order_relaxed);
        spawn_retransmission(
            send_sock,
            broadcast_addr,
            make_usfd_packet(config.origin_id, seqno, payload),
            stats,
            config.retransmissions,
            config.retransmit_delay_us);
    }
}

void run_random_mode(const NodeConfig& config,
                     gcounter<int, std::string>& counter,
                     gcounter<int, std::string>& delta_buffer) {
    std::default_random_engine generator(static_cast<unsigned int>(config.seed));
    std::exponential_distribution<double> wait_distribution(config.ops_per_sec);
    std::uniform_int_distribution<int> increment_distribution(1, 1);

    const auto start = std::chrono::steady_clock::now();
    double next_event_s = 0.0;
    while (g_running.load()) {
        next_event_s += wait_distribution(generator);
        if (next_event_s > config.duration) {
            break;
        }

        const auto event_time =
            start + std::chrono::duration_cast<std::chrono::steady_clock::duration>(
                        std::chrono::duration<double>(next_event_s));
        std::this_thread::sleep_until(event_time);
        if (!g_running.load()) {
            break;
        }

        const int increment = increment_distribution(generator);
        gcounter<int, std::string> delta;
        int total = 0;
        {
            std::lock_guard<std::mutex> lock(gc_mutex);
            delta = counter.inc(increment);
            total = counter.read();
        }

        {
            std::lock_guard<std::mutex> lock(event_log_mutex);
            const double timestamp = now_ts();
            event_log << std::fixed << timestamp
                      << ", event=op_create, node=" << config.id
                      << ", delta_size=" << increment << "\n";
            event_log << std::fixed << timestamp
                      << ", event=op_apply, node=" << config.id
                      << ", total=" << total << "\n";
        }

        {
            std::lock_guard<std::mutex> lock(delta_mutex);
            delta_buffer.join(delta);
        }
    }
}

void write_monitor_sample(std::ofstream& log,
                          gcounter<int, std::string>& counter,
                          const DpdCache& dpd,
                          const Stats& stats) {
    int local = 0;
    int total = 0;
    {
        std::lock_guard<std::mutex> lock(gc_mutex);
        local = counter.local();
        total = counter.read();
    }

    log << std::fixed << now_ts()
        << ", local=" << local
        << ", total=" << total
        << ", sent_msgs=" << stats.sent_msgs.load()
        << ", recv_msgs=" << stats.recv_msgs.load()
        << ", sent_bytes=" << stats.sent_bytes.load()
        << ", recv_bytes=" << stats.recv_bytes.load()
        << ", generated_msgs=" << stats.generated_msgs.load()
        << ", forwarded_msgs=" << stats.forwarded_msgs.load()
        << ", duplicate_drops=" << stats.duplicate_drops.load()
        << ", malformed_packets=" << stats.malformed_packets.load()
        << ", socket_errors=" << stats.socket_errors.load()
        << ", retransmit_tasks=" << stats.retransmit_tasks.load()
        << ", active_retransmissions="
        << g_active_retransmissions.load()
        << ", dpd_entries=" << dpd.live_count()
        << "\n";
    log.flush();
}

void monitor_loop(gcounter<int, std::string>& counter,
                  const DpdCache& dpd,
                  const Stats& stats,
                  double interval,
                  const std::string& logfile) {
    std::ofstream log(logfile, std::ios::trunc);
    while (g_running.load()) {
        std::this_thread::sleep_for(std::chrono::duration<double>(interval));
        if (!g_running.load()) {
            break;
        }
        write_monitor_sample(log, counter, dpd, stats);
        {
            std::lock_guard<std::mutex> lock(event_log_mutex);
            event_log.flush();
        }
    }
    write_monitor_sample(log, counter, dpd, stats);
}

void wait_for_retransmissions() {
    for (int i = 0; i < 2000; ++i) {
        if (g_active_retransmissions.load(std::memory_order_relaxed) == 0) {
            return;
        }
        usleep(1000);
    }
}

}  // namespace

int main(int argc, char* argv[]) {
    if (argc < 3) {
        return 1;
    }

    std::string node_id;
    std::string config_path;
    for (int i = 1; i < argc; ++i) {
        const std::string argument = argv[i];
        if (argument == "-id" && i + 1 < argc) {
            node_id = argv[++i];
        } else if (argument == "-config" && i + 1 < argc) {
            config_path = argv[++i];
        }
    }
    if (node_id.empty() || config_path.empty()) {
        return 1;
    }

    NodeConfig config = load_config(config_path, node_id);
    std::string listen_host;
    std::uint16_t listen_port = DEFAULT_PORT;
    if (config.listen_addr.empty() ||
        !parse_host_port(config.listen_addr, listen_host, listen_port)) {
        std::cerr << "Invalid USFD configuration for node "
                  << node_id << "\n";
        return 1;
    }

    sockaddr_in broadcast_addr{};
    if (!resolve_broadcast_addr(config, listen_port, broadcast_addr)) {
        std::cerr << "Invalid USFD broadcast configuration for node "
                  << node_id << "\n";
        return 1;
    }
    print_config(config);

    event_log.open(config.log_file + ".events", std::ios::trunc);
    event_log.setf(std::ios::unitbuf);
    if (!event_log.is_open()) {
        return 1;
    }

    {
        std::ostringstream details;
        details << "origin=" << config.origin_id
                << ", dpd_capacity=" << config.dpd_capacity
                << ", dpd_ttl_seconds=" << config.dpd_ttl_seconds
                << ", retransmissions=" << config.retransmissions
                << ", retransmit_delay_us=" << config.retransmit_delay_us
                << ", broadcast=" << config.broadcast_address
                << ", interface=" << config.interface_name;
        log_event(config.id, "usfd_init", details.str());
    }

    const int recv_sock = make_udp_socket(config.interface_name, true);
    if (recv_sock < 0 || !bind_receive_socket(recv_sock, listen_port)) {
        std::cerr << "Failed to bind USFD receive socket on port "
                  << listen_port << ": " << std::strerror(errno) << "\n";
        if (recv_sock >= 0) {
            close(recv_sock);
        }
        return 1;
    }

    const int send_sock = make_udp_socket(config.interface_name, false);
    if (send_sock < 0) {
        std::cerr << "Failed to create USFD send socket: "
                  << std::strerror(errno) << "\n";
        close(recv_sock);
        return 1;
    }

    DpdCache dpd(config.dpd_capacity, config.dpd_ttl_seconds);
    gcounter<int, std::string> counter(config.id);
    gcounter<int, std::string> delta_buffer;
    Stats stats;

    std::thread receiver(receive_loop,
                         recv_sock,
                         send_sock,
                         broadcast_addr,
                         std::ref(dpd),
                         std::ref(counter),
                         std::ref(stats),
                         std::cref(config));
    std::thread disseminator(dissemination_loop,
                             send_sock,
                             broadcast_addr,
                             std::ref(dpd),
                             std::ref(delta_buffer),
                             std::ref(stats),
                             std::cref(config));
    std::thread monitor(monitor_loop,
                        std::ref(counter),
                        std::cref(dpd),
                        std::cref(stats),
                        config.monitor_interval,
                        std::cref(config.log_file));

    run_random_mode(config, counter, delta_buffer);
    log_event(config.id, "ops_finished");

    std::this_thread::sleep_for(std::chrono::duration<double>(config.cooldown));

    g_running.store(false);
    shutdown(recv_sock, SHUT_RDWR);
    close(recv_sock);

    if (disseminator.joinable()) {
        disseminator.join();
    }
    if (receiver.joinable()) {
        receiver.join();
    }
    if (monitor.joinable()) {
        monitor.join();
    }

    wait_for_retransmissions();
    close(send_sock);

    {
        std::lock_guard<std::mutex> lock(event_log_mutex);
        event_log.flush();
        event_log.close();
    }
    return 0;
}
