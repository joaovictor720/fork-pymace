#include <arpa/inet.h>
#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstdint>
#include <cstring>
#include <cerrno>
#include <fstream>
#include <iostream>
#include <mutex>
#include <nlohmann/json.hpp>
#include <numeric>
#include <random>
#include <sstream>
#include <string>
#include <sys/socket.h>
#include <thread>
#include <unistd.h>
#include <utility>
#include <vector>

#include "/home/mace/git/fork-pymace/apps/crdt/common/delta-crdts.cc"

using json = nlohmann::json;

constexpr size_t MSG_MAX = 16384;
constexpr int PORT_DEFAULT = 9000;
constexpr uint32_t TRICKLE_K_DEFAULT = 1;
constexpr uint32_t TRICKLE_IMAX_TICKS_DEFAULT = 16;

static std::atomic<bool> g_running{true};

std::mutex _gc_mutex;
std::mutex _event_log_mutex;
std::ofstream _event_log;

std::vector<uint32_t> _known_state;

int _sockfd = -1;
sockaddr_in _broadcast_addr{};

struct node_config {
    std::string id;
    uint32_t node_index{0};
    uint32_t node_count{0};
    std::string listen_addr;
    std::string listen_host;
    double ops_per_sec{1.0};
    int duration{10};
    std::string distribution{"uniform"};
    int seed{0};
    std::string log_file;
    double monitor_interval{1.0};
    double dissemination_interval{0.5};
    double cooldown{10.0};
    uint32_t trickle_k{TRICKLE_K_DEFAULT};
    uint32_t trickle_imax_ticks{TRICKLE_IMAX_TICKS_DEFAULT};
};

struct stats {
    std::atomic<int> sent_msgs{0};
    std::atomic<int> recv_msgs{0};
    std::atomic<int> sent_bytes{0};
    std::atomic<int> recv_bytes{0};
};

enum MsgType : uint8_t {
    MT_SUMMARY = 1,
    MT_REPAIR = 2
};

enum SummaryRelation {
    REL_EQUAL = 0,
    REL_LOCAL_NEWER = 1,
    REL_REMOTE_NEWER = 2,
    REL_CONCURRENT = 3
};

enum TickAction {
    TA_NONE = 0,
    TA_TRANSMIT = 1,
    TA_SUPPRESS = 2
};

struct TrickleStateSnapshot {
    uint32_t interval_ticks{0};
    uint32_t elapsed_ticks{0};
    uint32_t tx_tick{0};
    uint32_t consistent_count{0};
    bool transmitted_in_interval{false};
};

struct TickResult {
    TickAction action{TA_NONE};
    TrickleStateSnapshot state;
    bool interval_changed{false};
    TrickleStateSnapshot next_state;
};

inline double now_ts() {
    return std::chrono::duration<double>(std::chrono::steady_clock::now().time_since_epoch()).count();
}

static void append_uint32(std::vector<char>& out, uint32_t value) {
    for (int i = 0; i < 4; ++i) {
        out.push_back(static_cast<char>((value >> (i * 8)) & 0xFF));
    }
}

static uint32_t read_uint32(const char* p) {
    uint32_t value = 0;
    for (int i = 0; i < 4; ++i) {
        value |= (static_cast<uint32_t>(static_cast<unsigned char>(p[i])) << (i * 8));
    }
    return value;
}

static const char* relation_name(SummaryRelation rel) {
    switch (rel) {
        case REL_EQUAL:
            return "equal";
        case REL_LOCAL_NEWER:
            return "local_newer";
        case REL_REMOTE_NEWER:
            return "remote_newer";
        case REL_CONCURRENT:
            return "concurrent";
        default:
            return "unknown";
    }
}

static bool parse_host_port(const std::string& s, std::string& host, int& port_out, int default_port) {
    host.clear();
    port_out = default_port;

    auto pos = s.rfind(':');
    if (pos == std::string::npos) {
        host = s;
        return !host.empty();
    }

    host = s.substr(0, pos);
    std::string pstr = s.substr(pos + 1);
    if (host.empty() || pstr.empty()) {
        return false;
    }

    try {
        port_out = std::stoi(pstr);
    } catch (...) {
        return false;
    }

    return true;
}

static bool parse_node_index(const std::string& id, uint32_t& out) {
    try {
        size_t used = 0;
        unsigned long parsed = std::stoul(id, &used, 10);
        if (used != id.size()) {
            return false;
        }
        out = static_cast<uint32_t>(parsed);
        return true;
    } catch (...) {
        return false;
    }
}

node_config load_config(const std::string& cfg_path, const std::string& id) {
    std::ifstream f(cfg_path);
    json cfg = json::parse(f);
    node_config nc;
    nc.id = id;

    try {
        nc.listen_addr = cfg.at("address").at(id);
        nc.node_count = static_cast<uint32_t>(cfg.at("address").size());
    } catch (...) {
        return nc;
    }

    if (!parse_node_index(id, nc.node_index)) {
        nc.listen_addr.clear();
        return nc;
    }
    if (nc.node_count == 0 || nc.node_index >= nc.node_count) {
        nc.listen_addr.clear();
        return nc;
    }

    int listen_port = PORT_DEFAULT;
    if (!parse_host_port(nc.listen_addr, nc.listen_host, listen_port, PORT_DEFAULT)) {
        nc.listen_host.clear();
        return nc;
    }

    nc.ops_per_sec = cfg.value("ops_per_sec", 1.0);
    nc.duration = cfg.value("duration", 10);
    nc.distribution = cfg.value("distribution", "uniform");

    std::random_device rd;
    nc.seed = cfg.value("seed", rd()) + std::atoi(id.c_str());

    nc.monitor_interval = cfg.value("monitor_interval", 1.0);
    nc.dissemination_interval = cfg.value("dissemination_interval", 0.5);
    nc.cooldown = cfg.value("cooldown", 10);
    if (nc.dissemination_interval <= 0.0) {
        nc.dissemination_interval = 0.5;
    }

    uint32_t k = cfg.value("trickle_k", static_cast<int>(TRICKLE_K_DEFAULT));
    nc.trickle_k = (k == 0) ? TRICKLE_K_DEFAULT : k;

    uint32_t imax_ticks = cfg.value("trickle_imax_ticks", static_cast<int>(TRICKLE_IMAX_TICKS_DEFAULT));
    nc.trickle_imax_ticks = (imax_ticks == 0) ? TRICKLE_IMAX_TICKS_DEFAULT : imax_ticks;

    std::string log_dir = cfg.value("log_dir", ".");
    if (!log_dir.empty() && log_dir.back() != '/') {
        log_dir.push_back('/');
    }
    nc.log_file = log_dir + "node_" + id + ".log";

    return nc;
}

void print_config(const node_config& nc) {
    std::cout << "===== TRICKLE APPLICATION =====\n";
    std::cout << "node id: " << nc.id << "\n";
    std::cout << "node_index: " << nc.node_index << "\n";
    std::cout << "node_count: " << nc.node_count << "\n";
    std::cout << "listen: " << nc.listen_addr << "\n";
    std::cout << "ops_per_sec: " << nc.ops_per_sec << "\n";
    std::cout << "duration: " << nc.duration << "\n";
    std::cout << "seed: " << nc.seed << "\n";
    std::cout << "monitor_interval: " << nc.monitor_interval << "\n";
    std::cout << "dissemination_interval: " << nc.dissemination_interval << "\n";
    std::cout << "trickle_k: " << nc.trickle_k << "\n";
    std::cout << "trickle_imax_ticks: " << nc.trickle_imax_ticks << "\n";
}

static void log_protocol_event(const std::string& node_id, const std::string& event, const std::string& details = "") {
    std::lock_guard<std::mutex> lk(_event_log_mutex);
    _event_log << std::fixed << now_ts()
               << ", event=" << event
               << ", node=" << node_id;
    if (!details.empty()) {
        _event_log << ", " << details;
    }
    _event_log << "\n";
}

static void set_socket_timeouts(int sockfd) {
    timeval tv{};
    tv.tv_sec = 0;
    tv.tv_usec = 200000;
    (void)setsockopt(sockfd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
}

static bool setup_broadcast_socket(const std::string& listen_addr, int& sockfd_out, sockaddr_in& bcast_out) {
    std::string host;
    int listen_port = PORT_DEFAULT;
    if (!parse_host_port(listen_addr, host, listen_port, PORT_DEFAULT)) {
        return false;
    }

    int sockfd = socket(AF_INET, SOCK_DGRAM, 0);
    if (sockfd < 0) {
        return false;
    }

    int reuse = 1;
    (void)setsockopt(sockfd, SOL_SOCKET, SO_REUSEADDR, &reuse, sizeof(reuse));

    int bc = 1;
    (void)setsockopt(sockfd, SOL_SOCKET, SO_BROADCAST, &bc, sizeof(bc));

    set_socket_timeouts(sockfd);

    sockaddr_in addr{};
    addr.sin_family = AF_INET;
    addr.sin_addr.s_addr = INADDR_ANY;
    addr.sin_port = htons(static_cast<uint16_t>(listen_port));
    if (bind(sockfd, (sockaddr*)&addr, sizeof(addr)) < 0) {
        close(sockfd);
        return false;
    }

    sockaddr_in bcast{};
    bcast.sin_family = AF_INET;
    bcast.sin_port = htons(static_cast<uint16_t>(listen_port));
    bcast.sin_addr.s_addr = INADDR_BROADCAST;

    sockfd_out = sockfd;
    bcast_out = bcast;
    return true;
}

static std::vector<uint32_t> snapshot_known_state() {
    std::lock_guard<std::mutex> lk(_gc_mutex);
    return _known_state;
}

static SummaryRelation compare_summaries(const std::vector<uint32_t>& local, const std::vector<uint32_t>& remote) {
    bool local_gt = false;
    bool remote_gt = false;

    size_t n = std::min(local.size(), remote.size());
    for (size_t i = 0; i < n; ++i) {
        if (local[i] > remote[i]) {
            local_gt = true;
        } else if (local[i] < remote[i]) {
            remote_gt = true;
        }
    }

    if (!local_gt && !remote_gt) {
        return REL_EQUAL;
    }
    if (local_gt && !remote_gt) {
        return REL_LOCAL_NEWER;
    }
    if (!local_gt && remote_gt) {
        return REL_REMOTE_NEWER;
    }
    return REL_CONCURRENT;
}

static std::vector<std::pair<uint32_t, uint32_t>> build_repair_entries(
    const std::vector<uint32_t>& local,
    const std::vector<uint32_t>& remote
) {
    std::vector<std::pair<uint32_t, uint32_t>> repair;
    repair.reserve(local.size());

    size_t n = std::min(local.size(), remote.size());
    for (size_t i = 0; i < n; ++i) {
        if (local[i] > remote[i]) {
            repair.emplace_back(static_cast<uint32_t>(i), local[i]);
        }
    }

    return repair;
}

static std::string serialize_repair_as_gcounter_bytes(const std::vector<std::pair<uint32_t, uint32_t>>& entries) {
    std::ostringstream oss;
    size_t map_size = entries.size();
    oss.write(reinterpret_cast<const char*>(&map_size), sizeof(map_size));

    for (const auto& entry : entries) {
        std::string key = std::to_string(entry.first);
        size_t key_len = key.size();
        int value = static_cast<int>(entry.second);

        oss.write(reinterpret_cast<const char*>(&key_len), sizeof(key_len));
        oss.write(key.data(), key_len);
        oss.write(reinterpret_cast<const char*>(&value), sizeof(value));
    }

    return oss.str();
}

static void build_summary_packet(const std::vector<uint32_t>& summary, std::vector<char>& out) {
    out.clear();
    out.reserve(1 + 4 + summary.size() * 4);
    out.push_back(static_cast<char>(MT_SUMMARY));
    append_uint32(out, static_cast<uint32_t>(summary.size()));
    for (uint32_t value : summary) {
        append_uint32(out, value);
    }
}

static bool parse_summary_packet(
    const char* data,
    size_t len,
    uint32_t expected_count,
    std::vector<uint32_t>& out
) {
    out.clear();
    if (len < 1 + 4) {
        return false;
    }

    uint32_t count = read_uint32(data + 1);
    if (count != expected_count) {
        return false;
    }

    size_t expected_len = 1 + 4 + static_cast<size_t>(count) * 4;
    if (len != expected_len) {
        return false;
    }

    out.resize(count);
    const char* p = data + 5;
    for (uint32_t i = 0; i < count; ++i) {
        out[i] = read_uint32(p);
        p += 4;
    }

    return true;
}

static void build_repair_packet(const std::vector<std::pair<uint32_t, uint32_t>>& entries, std::vector<char>& out) {
    out.clear();
    out.reserve(1 + 4 + entries.size() * 8);
    out.push_back(static_cast<char>(MT_REPAIR));
    append_uint32(out, static_cast<uint32_t>(entries.size()));
    for (const auto& entry : entries) {
        append_uint32(out, entry.first);
        append_uint32(out, entry.second);
    }
}

static bool parse_repair_packet(
    const char* data,
    size_t len,
    std::vector<std::pair<uint32_t, uint32_t>>& out
) {
    out.clear();
    if (len < 1 + 4) {
        return false;
    }

    uint32_t count = read_uint32(data + 1);
    size_t expected_len = 1 + 4 + static_cast<size_t>(count) * 8;
    if (len != expected_len) {
        return false;
    }

    out.reserve(count);
    const char* p = data + 5;
    for (uint32_t i = 0; i < count; ++i) {
        uint32_t idx = read_uint32(p);
        p += 4;
        uint32_t value = read_uint32(p);
        p += 4;
        out.emplace_back(idx, value);
    }

    return true;
}

class TrickleController {
private:
    std::mutex mu_;
    std::mt19937 rng_;
    uint32_t k_;
    uint32_t imin_ticks_;
    uint32_t imax_ticks_;
    uint32_t interval_ticks_;
    uint32_t elapsed_ticks_{0};
    uint32_t tx_tick_{0};
    uint32_t consistent_count_{0};
    bool transmitted_in_interval_{false};

    TrickleStateSnapshot snapshot_locked() const {
        return TrickleStateSnapshot{
            interval_ticks_,
            elapsed_ticks_,
            tx_tick_,
            consistent_count_,
            transmitted_in_interval_
        };
    }

    void choose_tx_tick_locked() {
        uint32_t start = interval_ticks_ / 2;
        if (start >= interval_ticks_) {
            start = interval_ticks_ - 1;
        }
        std::uniform_int_distribution<uint32_t> dist(start, interval_ticks_ - 1);
        tx_tick_ = dist(rng_);
    }

    void reset_locked() {
        interval_ticks_ = imin_ticks_;
        elapsed_ticks_ = 0;
        consistent_count_ = 0;
        transmitted_in_interval_ = false;
        choose_tx_tick_locked();
    }

    void advance_locked() {
        if (interval_ticks_ < imax_ticks_) {
            uint64_t doubled = static_cast<uint64_t>(interval_ticks_) * 2ULL;
            interval_ticks_ = static_cast<uint32_t>(std::min<uint64_t>(imax_ticks_, doubled));
        }
        elapsed_ticks_ = 0;
        consistent_count_ = 0;
        transmitted_in_interval_ = false;
        choose_tx_tick_locked();
    }

public:
    TrickleController(uint32_t k, uint32_t imin_ticks, uint32_t imax_ticks, uint32_t seed)
        : rng_(seed),
          k_(std::max<uint32_t>(1, k)),
          imin_ticks_(std::max<uint32_t>(1, imin_ticks)),
          imax_ticks_(std::max<uint32_t>(imin_ticks_, imax_ticks)),
          interval_ticks_(std::max<uint32_t>(1, imin_ticks)) {
        choose_tx_tick_locked();
    }

    TickResult tick() {
        std::lock_guard<std::mutex> lk(mu_);
        TickResult result;

        if (!transmitted_in_interval_ && elapsed_ticks_ == tx_tick_) {
            transmitted_in_interval_ = true;
            result.action = (consistent_count_ < k_) ? TA_TRANSMIT : TA_SUPPRESS;
        }

        result.state = snapshot_locked();

        elapsed_ticks_++;
        if (elapsed_ticks_ >= interval_ticks_) {
            uint32_t previous_interval = interval_ticks_;
            advance_locked();
            result.next_state = snapshot_locked();
            result.interval_changed = (previous_interval != result.next_state.interval_ticks);
        }

        return result;
    }

    void note_consistent_summary() {
        std::lock_guard<std::mutex> lk(mu_);
        consistent_count_++;
    }

    TrickleStateSnapshot note_new_information() {
        std::lock_guard<std::mutex> lk(mu_);
        TrickleStateSnapshot before = snapshot_locked();
        reset_locked();
        return before;
    }

    TrickleStateSnapshot snapshot() {
        std::lock_guard<std::mutex> lk(mu_);
        return snapshot_locked();
    }
};

static int send_packet(const std::vector<char>& pkt, stats& st) {
    ssize_t sent = sendto(_sockfd, pkt.data(), pkt.size(), 0, (sockaddr*)&_broadcast_addr, sizeof(_broadcast_addr));
    if (sent > 0) {
        st.sent_msgs++;
        st.sent_bytes += static_cast<int>(sent);
        return static_cast<int>(sent);
    }
    return -1;
}

static void send_summary(
    stats& st,
    const node_config& nc,
    const char* reason,
    const TrickleStateSnapshot* trickle_state = nullptr
) {
    std::vector<uint32_t> local = snapshot_known_state();
    std::vector<char> pkt;
    build_summary_packet(local, pkt);
    int sent = send_packet(pkt, st);
    if (sent <= 0) {
        return;
    }

    std::ostringstream details;
    details << "reason=" << reason
            << ", entries=" << local.size()
            << ", bytes=" << sent;
    if (trickle_state != nullptr) {
        details << ", interval_ticks=" << trickle_state->interval_ticks
                << ", elapsed_ticks=" << trickle_state->elapsed_ticks
                << ", tx_tick=" << trickle_state->tx_tick
                << ", consistent_count=" << trickle_state->consistent_count;
    }

    log_protocol_event(nc.id, "trickle_summary_send", details.str());
}

static void send_repair(
    const std::vector<std::pair<uint32_t, uint32_t>>& repair_entries,
    stats& st,
    const node_config& nc,
    const char* reason
) {
    if (repair_entries.empty()) {
        return;
    }

    std::vector<char> pkt;
    build_repair_packet(repair_entries, pkt);
    int sent = send_packet(pkt, st);
    if (sent <= 0) {
        return;
    }

    std::ostringstream details;
    details << "reason=" << reason
            << ", entries=" << repair_entries.size()
            << ", bytes=" << sent;
    log_protocol_event(nc.id, "trickle_repair_send", details.str());
}

void trickle_timer_loop(const node_config& nc, TrickleController& trickle, stats& st) {
    auto next = std::chrono::steady_clock::now();

    while (g_running.load()) {
        next += std::chrono::duration_cast<std::chrono::steady_clock::duration>(
            std::chrono::duration<double>(nc.dissemination_interval)
        );
        std::this_thread::sleep_until(next);

        if (!g_running.load()) {
            break;
        }

        TickResult result = trickle.tick();
        if (result.action == TA_TRANSMIT) {
            send_summary(st, nc, "periodic", &result.state);
        } else if (result.action == TA_SUPPRESS) {
            std::ostringstream details;
            details << "interval_ticks=" << result.state.interval_ticks
                    << ", elapsed_ticks=" << result.state.elapsed_ticks
                    << ", tx_tick=" << result.state.tx_tick
                    << ", consistent_count=" << result.state.consistent_count;
            log_protocol_event(nc.id, "trickle_suppressed", details.str());
        }

        if (result.interval_changed) {
            std::ostringstream details;
            details << "from_interval_ticks=" << result.state.interval_ticks
                    << ", to_interval_ticks=" << result.next_state.interval_ticks
                    << ", next_tx_tick=" << result.next_state.tx_tick;
            log_protocol_event(nc.id, "trickle_interval_change", details.str());
        }
    }
}

void recv_loop(
    int sockfd,
    gcounter<int, std::string>& gc,
    TrickleController& trickle,
    stats& st,
    const node_config& nc
) {
    char buffer[MSG_MAX];
    sockaddr_in src{};
    socklen_t srclen = sizeof(src);

    while (g_running.load()) {
        ssize_t n = recvfrom(sockfd, buffer, MSG_MAX, 0, (sockaddr*)&src, &srclen);
        if (n <= 0) {
            if (!g_running.load()) {
                break;
            }
            if (n < 0) {
                if (errno == EAGAIN || errno == EWOULDBLOCK) {
                    continue;
                }
                if (errno == EBADF) {
                    break;
                }
            }
            continue;
        }

        char src_addr_buf[INET_ADDRSTRLEN] = {0};
        const char* src_ip = inet_ntop(AF_INET, &src.sin_addr, src_addr_buf, sizeof(src_addr_buf));
        if (src_ip != nullptr && nc.listen_host == src_ip) {
            continue;
        }

        st.recv_msgs++;
        st.recv_bytes += static_cast<int>(n);

        uint8_t type = static_cast<uint8_t>(buffer[0]);

        if (type == MT_SUMMARY) {
            std::vector<uint32_t> remote_summary;
            if (!parse_summary_packet(buffer, static_cast<size_t>(n), nc.node_count, remote_summary)) {
                continue;
            }

            std::vector<uint32_t> local_summary = snapshot_known_state();
            SummaryRelation rel = compare_summaries(local_summary, remote_summary);

            {
                std::ostringstream details;
                details << "relation=" << relation_name(rel)
                        << ", entries=" << remote_summary.size()
                        << ", bytes=" << n;
                log_protocol_event(nc.id, "trickle_summary_recv", details.str());
            }

            if (rel == REL_EQUAL) {
                trickle.note_consistent_summary();
            } else if (rel == REL_LOCAL_NEWER) {
                send_repair(build_repair_entries(local_summary, remote_summary), st, nc, "summary_older");
                trickle.note_new_information();
                log_protocol_event(nc.id, "trickle_reset", "reason=summary_local_newer");
            } else if (rel == REL_REMOTE_NEWER) {
                trickle.note_new_information();
                log_protocol_event(nc.id, "trickle_reset", "reason=summary_remote_newer");
                send_summary(st, nc, "implicit_request");
            } else {
                send_repair(build_repair_entries(local_summary, remote_summary), st, nc, "summary_concurrent");
                trickle.note_new_information();
                log_protocol_event(nc.id, "trickle_reset", "reason=summary_concurrent");
                send_summary(st, nc, "implicit_request");
            }
        } else if (type == MT_REPAIR) {
            std::vector<std::pair<uint32_t, uint32_t>> repair_entries;
            if (!parse_repair_packet(buffer, static_cast<size_t>(n), repair_entries)) {
                continue;
            }

            if (repair_entries.empty()) {
                continue;
            }

            {
                std::ostringstream details;
                details << "entries=" << repair_entries.size()
                        << ", bytes=" << n;
                log_protocol_event(nc.id, "trickle_repair_recv", details.str());
            }

            bool any_new = false;
            int total = 0;
            std::vector<std::pair<uint32_t, uint32_t>> applied_entries;
            applied_entries.reserve(repair_entries.size());

            {
                std::lock_guard<std::mutex> lk(_gc_mutex);

                for (const auto& entry : repair_entries) {
                    if (entry.first >= _known_state.size()) {
                        continue;
                    }

                    uint32_t& local_value = _known_state[entry.first];
                    if (entry.second > local_value) {
                        local_value = entry.second;
                        any_new = true;
                        applied_entries.push_back(entry);
                    }
                }

                if (any_new) {
                    std::string ser = serialize_repair_as_gcounter_bytes(applied_entries);
                    auto repair_gc = gcounter<int, std::string>::deserialize(ser);
                    gc.join(repair_gc);
                    total = gc.read();
                }
            }

            if (!any_new) {
                continue;
            }

            {
                std::lock_guard<std::mutex> lk(_event_log_mutex);
                _event_log << std::fixed << now_ts()
                           << ", event=op_apply, node=" << nc.id
                           << ", total=" << total << "\n";
            }

            trickle.note_new_information();
            log_protocol_event(nc.id, "trickle_reset", "reason=repair_received");
        }
    }
}

void run_random_mode(const node_config& nc, gcounter<int, std::string>& gc, TrickleController& trickle) {
    double ops_per_sec = nc.ops_per_sec;
    double duration = nc.duration;

    std::default_random_engine gen(nc.seed);
    std::exponential_distribution<double> expd(ops_per_sec);
    std::uniform_int_distribution<int> inc_dist(1, 1);

    auto start = std::chrono::steady_clock::now();

    while (g_running.load()) {
        double elapsed = std::chrono::duration<double>(std::chrono::steady_clock::now() - start).count();
        if (elapsed > duration) {
            break;
        }

        std::this_thread::sleep_for(std::chrono::duration<double>(expd(gen)));
        if (!g_running.load()) {
            break;
        }

        int val = inc_dist(gen);
        int local_value = 0;
        int total = 0;

        {
            std::lock_guard<std::mutex> lk(_gc_mutex);
            (void)gc.inc(val);
            local_value = gc.local();
            total = gc.read();
            _known_state[nc.node_index] = static_cast<uint32_t>(local_value);
        }

        {
            std::lock_guard<std::mutex> lk(_event_log_mutex);
            double ts = now_ts();
            _event_log << std::fixed << ts
                       << ", event=op_create, node=" << nc.id
                       << ", delta_size=" << val << "\n";
            _event_log << std::fixed << ts
                       << ", event=op_apply, node=" << nc.id
                       << ", total=" << total << "\n";
        }

        trickle.note_new_information();
        log_protocol_event(nc.id, "trickle_reset", "reason=local_op");
    }
}

void monitor_loop(gcounter<int, std::string>& gc, stats& st, double interval, const std::string& logfile) {
    std::ofstream log(logfile, std::ios::trunc);

    while (g_running.load()) {
        std::this_thread::sleep_for(std::chrono::duration<double>(interval));
        if (!g_running.load()) {
            break;
        }

        int local = 0;
        int total = 0;
        {
            std::lock_guard<std::mutex> lk(_gc_mutex);
            local = gc.local();
            total = gc.read();
        }

        log << std::fixed << now_ts()
            << ", local=" << local
            << ", total=" << total
            << ", sent_msgs=" << st.sent_msgs
            << ", recv_msgs=" << st.recv_msgs
            << ", sent_bytes=" << st.sent_bytes
            << ", recv_bytes=" << st.recv_bytes << "\n";
        log.flush();

        {
            std::lock_guard<std::mutex> lk(_event_log_mutex);
            _event_log.flush();
        }
    }
}

int main(int argc, char* argv[]) {
    if (argc < 3) {
        return 1;
    }

    std::string node_id;
    std::string cfgfile;

    for (int i = 1; i < argc; ++i) {
        std::string arg = argv[i];
        if (arg == "-id" && i + 1 < argc) {
            node_id = argv[++i];
        } else if (arg == "-config" && i + 1 < argc) {
            cfgfile = argv[++i];
        }
    }

    if (node_id.empty() || cfgfile.empty()) {
        return 1;
    }

    node_config nc = load_config(cfgfile, node_id);
    if (nc.listen_addr.empty() || nc.listen_host.empty() || nc.node_count == 0) {
        return 1;
    }

    print_config(nc);

    _event_log.open(nc.log_file + ".events", std::ios::trunc);
    _event_log.setf(std::ios::unitbuf);
    if (!_event_log.is_open()) {
        return 1;
    }

    _known_state.assign(nc.node_count, 0);

    {
        std::ostringstream details;
        details << "trickle_k=" << nc.trickle_k
                << ", tau_l_s=" << nc.dissemination_interval
                << ", tau_h_s=" << (nc.dissemination_interval * nc.trickle_imax_ticks)
                << ", trickle_imax_ticks=" << nc.trickle_imax_ticks
                << ", node_count=" << nc.node_count;
        log_protocol_event(nc.id, "trickle_init", details.str());
    }

    int sockfd = -1;
    sockaddr_in bcast_addr{};
    if (!setup_broadcast_socket(nc.listen_addr, sockfd, bcast_addr)) {
        return 1;
    }

    _sockfd = sockfd;
    _broadcast_addr = bcast_addr;

    gcounter<int, std::string> gc(nc.id);
    stats st;
    TrickleController trickle(nc.trickle_k, 1, nc.trickle_imax_ticks, static_cast<uint32_t>(nc.seed));

    std::thread t_recv(recv_loop, sockfd, std::ref(gc), std::ref(trickle), std::ref(st), std::cref(nc));
    std::thread t_mon(monitor_loop, std::ref(gc), std::ref(st), nc.monitor_interval, nc.log_file);
    std::thread t_trickle(trickle_timer_loop, std::cref(nc), std::ref(trickle), std::ref(st));

    run_random_mode(nc, gc, trickle);

    {
        std::lock_guard<std::mutex> lk(_event_log_mutex);
        _event_log << std::fixed << now_ts()
                   << ", event=ops_finished, node=" << nc.id << "\n";
    }

    std::this_thread::sleep_for(std::chrono::duration<double>(nc.cooldown));

    g_running.store(false);
    shutdown(sockfd, SHUT_RDWR);
    close(sockfd);

    if (t_trickle.joinable()) {
        t_trickle.join();
    }
    if (t_recv.joinable()) {
        t_recv.join();
    }
    if (t_mon.joinable()) {
        t_mon.join();
    }

    {
        std::lock_guard<std::mutex> lk(_event_log_mutex);
        _event_log.flush();
        _event_log.close();
    }

    return 0;
}
