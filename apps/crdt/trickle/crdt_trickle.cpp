#include <arpa/inet.h>
#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstdint>
#include <cstring>
#include <cerrno>
#include <fstream>
#include <iostream>
#include <map>
#include <mutex>
#include <nlohmann/json.hpp>
#include <random>
#include <sstream>
#include <string>
#include <sys/socket.h>
#include <thread>
#include <unistd.h>
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

std::map<std::string, int> _known_state;

int _sockfd = -1;
sockaddr_in _broadcast_addr{};

struct node_config {
    std::string id;
    std::string listen_addr;
    std::string listen_host;
    std::vector<std::string> peers;
    double ops_per_sec;
    int duration;
    std::string distribution;
    int seed;
    std::string log_file;
    double monitor_interval;
    double dissemination_interval;
    double cooldown;
    uint32_t trickle_k;
    uint32_t trickle_imax_ticks;
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

inline double now_ts() {
    return std::chrono::duration<double>(std::chrono::steady_clock::now().time_since_epoch()).count();
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

node_config load_config(const std::string& cfg_path, const std::string& id) {
    std::ifstream f(cfg_path);
    json cfg = json::parse(f);
    node_config nc;
    nc.id = id;

    try {
        nc.listen_addr = cfg.at("address").at(id);
    } catch (...) {
        return nc;
    }

    int listen_port = PORT_DEFAULT;
    if (!parse_host_port(nc.listen_addr, nc.listen_host, listen_port, PORT_DEFAULT)) {
        nc.listen_host.clear();
        return nc;
    }

    for (auto& [nid, addr] : cfg["address"].items()) {
        if (nid != id) {
            nc.peers.push_back(addr);
        }
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
    if (k == 0) {
        k = TRICKLE_K_DEFAULT;
    }
    nc.trickle_k = k;

    uint32_t imax_ticks = cfg.value("trickle_imax_ticks", static_cast<int>(TRICKLE_IMAX_TICKS_DEFAULT));
    if (imax_ticks == 0) {
        imax_ticks = TRICKLE_IMAX_TICKS_DEFAULT;
    }
    nc.trickle_imax_ticks = imax_ticks;

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
    std::cout << "listen: " << nc.listen_addr << "\n";
    std::cout << "ops_per_sec: " << nc.ops_per_sec << "\n";
    std::cout << "duration: " << nc.duration << "\n";
    std::cout << "seed: " << nc.seed << "\n";
    std::cout << "monitor_interval: " << nc.monitor_interval << "\n";
    std::cout << "dissemination_interval: " << nc.dissemination_interval << "\n";
    std::cout << "trickle_k: " << nc.trickle_k << "\n";
    std::cout << "trickle_imax_ticks: " << nc.trickle_imax_ticks << "\n";
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

static std::string serialize_map_as_gcounter_bytes(const std::map<std::string, int>& entries) {
    std::ostringstream oss;
    size_t map_size = entries.size();
    oss.write(reinterpret_cast<const char*>(&map_size), sizeof(map_size));

    for (const auto& kv : entries) {
        size_t key_len = kv.first.size();
        oss.write(reinterpret_cast<const char*>(&key_len), sizeof(key_len));
        oss.write(kv.first.data(), key_len);
        oss.write(reinterpret_cast<const char*>(&kv.second), sizeof(kv.second));
    }

    return oss.str();
}

static bool parse_gcounter_map_bytes(const char* data, size_t len, std::map<std::string, int>& out) {
    out.clear();
    size_t off = 0;

    if (len < sizeof(size_t)) {
        return false;
    }

    size_t map_size = 0;
    std::memcpy(&map_size, data + off, sizeof(size_t));
    off += sizeof(size_t);

    for (size_t i = 0; i < map_size; ++i) {
        if (off + sizeof(size_t) > len) {
            return false;
        }

        size_t key_len = 0;
        std::memcpy(&key_len, data + off, sizeof(size_t));
        off += sizeof(size_t);

        if (off + key_len + sizeof(int) > len) {
            return false;
        }

        std::string key(data + off, data + off + key_len);
        off += key_len;

        int value = 0;
        std::memcpy(&value, data + off, sizeof(int));
        off += sizeof(int);

        out[key] = value;
    }

    return off == len;
}

static std::map<std::string, int> snapshot_known_state() {
    std::lock_guard<std::mutex> lk(_gc_mutex);
    return _known_state;
}

static SummaryRelation compare_summaries(
    const std::map<std::string, int>& local,
    const std::map<std::string, int>& remote
) {
    bool local_gt = false;
    bool remote_gt = false;

    for (const auto& kv : local) {
        auto it = remote.find(kv.first);
        int remote_value = (it == remote.end()) ? 0 : it->second;
        if (kv.second > remote_value) {
            local_gt = true;
        } else if (kv.second < remote_value) {
            remote_gt = true;
        }
    }

    for (const auto& kv : remote) {
        if (local.find(kv.first) == local.end() && kv.second > 0) {
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

static std::map<std::string, int> build_repair_entries(
    const std::map<std::string, int>& local,
    const std::map<std::string, int>& remote
) {
    std::map<std::string, int> repair;
    for (const auto& kv : local) {
        auto it = remote.find(kv.first);
        int remote_value = (it == remote.end()) ? 0 : it->second;
        if (kv.second > remote_value) {
            repair[kv.first] = kv.second;
        }
    }
    return repair;
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

    bool tick() {
        std::lock_guard<std::mutex> lk(mu_);
        bool should_transmit = false;

        if (!transmitted_in_interval_ && elapsed_ticks_ == tx_tick_ && consistent_count_ < k_) {
            transmitted_in_interval_ = true;
            should_transmit = true;
        }

        elapsed_ticks_++;
        if (elapsed_ticks_ >= interval_ticks_) {
            advance_locked();
        }

        return should_transmit;
    }

    void note_consistent_summary() {
        std::lock_guard<std::mutex> lk(mu_);
        consistent_count_++;
    }

    void note_new_information() {
        std::lock_guard<std::mutex> lk(mu_);
        reset_locked();
    }
};

static void send_packet(const std::vector<char>& pkt, stats& st) {
    ssize_t sent = sendto(_sockfd, pkt.data(), pkt.size(), 0, (sockaddr*)&_broadcast_addr, sizeof(_broadcast_addr));
    if (sent > 0) {
        st.sent_msgs++;
        st.sent_bytes += static_cast<int>(sent);
    }
}

static void build_map_packet(MsgType type, const std::map<std::string, int>& entries, std::vector<char>& out) {
    out.clear();
    out.push_back(static_cast<char>(type));
    std::string body = serialize_map_as_gcounter_bytes(entries);
    out.insert(out.end(), body.begin(), body.end());
}

static void send_summary(stats& st) {
    std::map<std::string, int> local = snapshot_known_state();
    std::vector<char> pkt;
    build_map_packet(MT_SUMMARY, local, pkt);
    send_packet(pkt, st);
}

static void send_repair(const std::map<std::string, int>& repair_entries, stats& st) {
    if (repair_entries.empty()) {
        return;
    }

    std::vector<char> pkt;
    build_map_packet(MT_REPAIR, repair_entries, pkt);
    send_packet(pkt, st);
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

        if (trickle.tick()) {
            send_summary(st);
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
            std::map<std::string, int> remote_summary;
            if (!parse_gcounter_map_bytes(buffer + 1, static_cast<size_t>(n - 1), remote_summary)) {
                continue;
            }

            std::map<std::string, int> local_summary = snapshot_known_state();
            SummaryRelation rel = compare_summaries(local_summary, remote_summary);

            if (rel == REL_EQUAL) {
                trickle.note_consistent_summary();
            } else if (rel == REL_LOCAL_NEWER) {
                send_repair(build_repair_entries(local_summary, remote_summary), st);
            } else if (rel == REL_REMOTE_NEWER) {
                trickle.note_new_information();
            } else {
                send_repair(build_repair_entries(local_summary, remote_summary), st);
                trickle.note_new_information();
            }
        } else if (type == MT_REPAIR) {
            std::map<std::string, int> repair_entries;
            if (!parse_gcounter_map_bytes(buffer + 1, static_cast<size_t>(n - 1), repair_entries)) {
                continue;
            }

            if (repair_entries.empty()) {
                continue;
            }

            bool any_new = false;
            int total = 0;

            {
                std::lock_guard<std::mutex> lk(_gc_mutex);

                for (const auto& kv : repair_entries) {
                    int& local_value = _known_state[kv.first];
                    if (kv.second > local_value) {
                        local_value = kv.second;
                        any_new = true;
                    }
                }

                if (any_new) {
                    std::string ser = serialize_map_as_gcounter_bytes(repair_entries);
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
            _known_state[nc.id] = local_value;
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
    if (nc.listen_addr.empty() || nc.listen_host.empty()) {
        return 1;
    }

    print_config(nc);

    _event_log.open(nc.log_file + ".events", std::ios::trunc);
    _event_log.setf(std::ios::unitbuf);
    if (!_event_log.is_open()) {
        return 1;
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
