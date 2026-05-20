#include <arpa/inet.h>
#include <atomic>
#include <cerrno>
#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iostream>
#include <mutex>
#include <nlohmann/json.hpp>
#include <random>
#include <string>
#include <sys/socket.h>
#include <thread>
#include <unistd.h>
#include <vector>

#include "lib/rapid.hpp"
#include "../common/delta-crdts.cc"

using json = nlohmann::json;

constexpr size_t MSG_MAX = 8192;
constexpr int PORT_DEFAULT = 9000;
constexpr double RAPID_BETA = 2.5;
constexpr int RAPID_CACHE_TTL_SEC = 60;
constexpr int RAPID_HEARTBEAT_INTERVAL_MS = 1000;
constexpr int RAPID_GOSSIP_INTERVAL_MS = 1000;
constexpr int RAPID_SHORT_JITTER_MIN_MS = 10;
constexpr int RAPID_SHORT_JITTER_MAX_MS = 40;
constexpr int RAPID_LONG_JITTER_MIN_MS = 200;
constexpr int RAPID_LONG_JITTER_MAX_MS = 600;

static std::atomic<bool> g_running{true};

std::mutex _gc_mutex;
std::mutex _pending_mutex;
std::mutex _event_log_mutex;
std::mutex _rapid_mutex;

gcounter<int, std::string> _pending_state;
bool _has_pending = false;

std::ofstream _event_log;

int _sockfd = -1;
sockaddr_in _broadcast_addr{};
int _listen_port = PORT_DEFAULT;

struct node_config {
    std::string id;
    std::string listen_addr;
    std::vector<std::string> peers;
    double ops_per_sec{1.0};
    int duration{10};
    std::string distribution{"uniform"};
    int seed{0};
    std::string log_file;
    double monitor_interval{1.0};
    double cooldown{10.0};
    double dissemination_interval{0.5};
};

struct stats {
    std::atomic<int> sent_msgs{0};
    std::atomic<int> recv_msgs{0};
    std::atomic<int> sent_bytes{0};
    std::atomic<int> recv_bytes{0};
};

inline double now_ts() {
    return std::chrono::duration<double>(std::chrono::steady_clock::now().time_since_epoch()).count();
}

void print_config(const node_config& nc) {
    std::cout << "===== RAPID (GOSSIP) APPLICATION =====\n";
    std::cout << "node id: " << nc.id << "\n";
    std::cout << "listen: " << nc.listen_addr << "\n";
    std::cout << "ops_per_sec: " << nc.ops_per_sec << "\n";
    std::cout << "duration: " << nc.duration << "\n";
    std::cout << "seed: " << nc.seed << "\n";
    std::cout << "monitor_interval: " << nc.monitor_interval << "\n";
    std::cout << "dissemination_interval: " << nc.dissemination_interval << "\n";
    std::cout << "peers: ";
    for (const auto& p : nc.peers) {
        std::cout << p << " ";
    }
    std::cout << "\n";
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

    std::string log_dir = cfg.value("log_dir", ".");
    if (!log_dir.empty() && log_dir.back() != '/') {
        log_dir.push_back('/');
    }
    nc.log_file = log_dir + "node_" + id + ".log";

    nc.cooldown = cfg.value("cooldown", 10.0);
    nc.dissemination_interval = cfg.value("dissemination_interval", 0.5);
    return nc;
}

static rapid::NodeId to_rapid_node_id(const std::string& id) {
    try {
        size_t used = 0;
        unsigned long long parsed = std::stoull(id, &used, 10);
        if (used == id.size()) {
            return static_cast<rapid::NodeId>(parsed + 1);
        }
    } catch (...) {
    }

    rapid::NodeId hashed = static_cast<rapid::NodeId>(std::hash<std::string>{}(id));
    return hashed == rapid::kUnknownNode ? 1 : hashed;
}

static rapid::Config make_rapid_config(const node_config& nc) {
    rapid::Config cfg;
    cfg.node_id = to_rapid_node_id(nc.id);
    cfg.seed = static_cast<std::uint64_t>(nc.seed);
    cfg.beta = RAPID_BETA;
    cfg.cache_ttl = std::chrono::seconds(RAPID_CACHE_TTL_SEC);
    cfg.gossip_interval = std::chrono::milliseconds(RAPID_GOSSIP_INTERVAL_MS);
    cfg.heartbeat_interval = std::chrono::milliseconds(RAPID_HEARTBEAT_INTERVAL_MS);
    cfg.short_jitter_min = std::chrono::milliseconds(RAPID_SHORT_JITTER_MIN_MS);
    cfg.short_jitter_max = std::chrono::milliseconds(RAPID_SHORT_JITTER_MAX_MS);
    cfg.long_jitter_min = std::chrono::milliseconds(RAPID_LONG_JITTER_MIN_MS);
    cfg.long_jitter_max = std::chrono::milliseconds(RAPID_LONG_JITTER_MAX_MS);
    return cfg;
}

static void set_socket_timeouts(int sockfd) {
    timeval tv{};
    tv.tv_sec = 0;
    tv.tv_usec = 200000;
    (void)setsockopt(sockfd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
}

static bool setup_socket_and_peers(const node_config& nc) {
    auto pos = nc.listen_addr.find(':');
    if (pos == std::string::npos) {
        return false;
    }
    _listen_port = std::stoi(nc.listen_addr.substr(pos + 1));

    _sockfd = socket(AF_INET, SOCK_DGRAM, 0);
    if (_sockfd < 0) {
        return false;
    }

    int reuse = 1;
    (void)setsockopt(_sockfd, SOL_SOCKET, SO_REUSEADDR, &reuse, sizeof(reuse));

    int bc = 1;
    (void)setsockopt(_sockfd, SOL_SOCKET, SO_BROADCAST, &bc, sizeof(bc));

    set_socket_timeouts(_sockfd);

    sockaddr_in addr{};
    addr.sin_family = AF_INET;
    addr.sin_addr.s_addr = INADDR_ANY;
    addr.sin_port = htons(static_cast<uint16_t>(_listen_port));
    if (bind(_sockfd, (sockaddr*)&addr, sizeof(addr)) < 0) {
        close(_sockfd);
        _sockfd = -1;
        return false;
    }

    sockaddr_in broadcast{};
    broadcast.sin_family = AF_INET;
    broadcast.sin_port = htons(static_cast<uint16_t>(_listen_port));
    broadcast.sin_addr.s_addr = INADDR_BROADCAST;
    _broadcast_addr = broadcast;
    return true;
}

static void send_broadcast(const rapid::Bytes& pkt, stats& st) {
    ssize_t sent = sendto(_sockfd,
                          pkt.data(),
                          pkt.size(),
                          0,
                          (sockaddr*)&_broadcast_addr,
                          sizeof(_broadcast_addr));
    if (sent > 0) {
        st.sent_msgs++;
        st.sent_bytes += static_cast<int>(sent);
    }
}

static void deliver_crdt_payload(gcounter<int, std::string>& gc,
                                 const std::string& node_id,
                                 const rapid::MessageId&,
                                 const rapid::Bytes& payload) {
    try {
        std::string s(payload.begin(), payload.end());
        auto recv_gc = gcounter<int, std::string>::deserialize(s);
        int total = 0;
        {
            std::unique_lock<std::mutex> lg(_gc_mutex);
            gc.join(recv_gc);
            total = gc.read();
        }
        {
            std::lock_guard<std::mutex> lk(_event_log_mutex);
            _event_log << std::fixed << now_ts()
                       << ", event=op_apply, node=" << node_id
                       << ", total=" << total << "\n";
        }
    } catch (...) {
    }
}

void recv_loop(int sockfd_local, rapid::Node& rapid_node, stats& st) {
    char buf[MSG_MAX];
    sockaddr_in src{};
    socklen_t srclen = sizeof(src);

    while (g_running.load()) {
        ssize_t n = recvfrom(sockfd_local, buf, MSG_MAX, 0, (sockaddr*)&src, &srclen);
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

        st.recv_msgs++;
        st.recv_bytes += static_cast<int>(n);

        rapid::Bytes packet(static_cast<size_t>(n));
        std::memcpy(packet.data(), buf, static_cast<size_t>(n));

        std::lock_guard<std::mutex> lk(_rapid_mutex);
        rapid_node.receive(packet, rapid::kUnknownNode, rapid::Clock::now());
    }
}

void rapid_tick_loop(rapid::Node& rapid_node) {
    while (g_running.load()) {
        {
            std::lock_guard<std::mutex> lk(_rapid_mutex);
            rapid_node.tick(rapid::Clock::now());
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(5));
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
            std::unique_lock<std::mutex> lg(_gc_mutex);
            local = gc.local();
            total = gc.read();
        }

        log << std::fixed << now_ts()
            << ", local=" << local << ", total=" << total
            << ", sent_msgs=" << st.sent_msgs
            << ", recv_msgs=" << st.recv_msgs
            << ", sent_bytes=" << st.sent_bytes
            << ", recv_bytes=" << st.recv_bytes << "\n";
        log.flush();
    }
}

void run_random_mode(const node_config& nc, gcounter<int, std::string>& gc) {
    std::default_random_engine gen(nc.seed);
    std::exponential_distribution<double> expd(nc.ops_per_sec);
    std::uniform_int_distribution<int> inc_dist(1, 1);

    gcounter<int, std::string> local_since_last;

    auto start = std::chrono::steady_clock::now();
    while (g_running.load()) {
        double elapsed = std::chrono::duration<double>(std::chrono::steady_clock::now() - start).count();
        if (elapsed > nc.duration) {
            break;
        }

        std::this_thread::sleep_for(std::chrono::duration<double>(expd(gen)));
        if (!g_running.load()) {
            break;
        }

        int val = inc_dist(gen);

        gcounter<int, std::string> delta_obj;
        int total = 0;
        {
            std::unique_lock<std::mutex> lg(_gc_mutex);
            delta_obj = gc.inc(val);
            total = gc.read();
        }

        {
            std::lock_guard<std::mutex> lk(_event_log_mutex);
            auto ts = now_ts();
            _event_log << std::fixed << ts
                       << ", event=op_create, node=" << nc.id
                       << ", delta_size=" << val << "\n";
            _event_log << std::fixed << ts
                       << ", event=op_apply, node=" << nc.id
                       << ", total=" << total << "\n";
        }

        local_since_last.join(delta_obj);

        {
            std::lock_guard<std::mutex> lk(_pending_mutex);
            if (_has_pending) {
                _pending_state.join(local_since_last);
            } else {
                _pending_state = local_since_last;
                _has_pending = true;
            }
            local_since_last = gcounter<int, std::string>();
        }
    }
}

void local_periodic_dissemination(const node_config& nc, rapid::Node& rapid_node) {
    rapid::MessageId last_local_msgid;
    bool has_last = false;

    auto next = std::chrono::steady_clock::now();

    while (g_running.load()) {
        next += std::chrono::duration_cast<std::chrono::steady_clock::duration>(
            std::chrono::duration<double>(nc.dissemination_interval));
        std::this_thread::sleep_until(next);

        if (!g_running.load()) {
            break;
        }

        gcounter<int, std::string> to_send;
        bool has_new = false;
        {
            std::lock_guard<std::mutex> lk(_pending_mutex);
            if (_has_pending) {
                to_send = _pending_state;
                _pending_state = gcounter<int, std::string>();
                _has_pending = false;
                has_new = true;
            }
        }

        if (!has_new) {
            if (has_last) {
                std::lock_guard<std::mutex> lk(_rapid_mutex);
                rapid_node.rebroadcast(last_local_msgid, rapid::Clock::now());
            }
            continue;
        }

        std::string ser = to_send.serialize();
        rapid::Bytes payload(ser.begin(), ser.end());
        std::lock_guard<std::mutex> lk(_rapid_mutex);
        last_local_msgid = rapid_node.publish(payload, rapid::Clock::now());
        has_last = true;
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
    if (nc.listen_addr.empty()) {
        return 1;
    }

    print_config(nc);

    _event_log.open(nc.log_file + ".events", std::ios::trunc);
    _event_log.setf(std::ios::unitbuf);
    if (!_event_log.is_open()) {
        return 1;
    }

    stats st;
    if (!setup_socket_and_peers(nc)) {
        return 1;
    }

    gcounter<int, std::string> gc(nc.id);
    rapid::Node rapid_node(make_rapid_config(nc));
    rapid_node.set_send_callback([&st](const rapid::Bytes& pkt) {
        send_broadcast(pkt, st);
    });
    rapid_node.set_deliver_callback([&gc, &nc](const rapid::MessageId& id, const rapid::Bytes& payload) {
        deliver_crdt_payload(gc, nc.id, id, payload);
    });

    std::thread t_recv(recv_loop, _sockfd, std::ref(rapid_node), std::ref(st));
    std::thread t_rapid(rapid_tick_loop, std::ref(rapid_node));
    std::thread t_mon(monitor_loop, std::ref(gc), std::ref(st), nc.monitor_interval, nc.log_file);
    std::thread t_local(local_periodic_dissemination, std::cref(nc), std::ref(rapid_node));

    run_random_mode(nc, gc);

    {
        std::lock_guard<std::mutex> lk(_event_log_mutex);
        _event_log << std::fixed << now_ts()
                   << ", event=ops_finished, node=" << nc.id << "\n";
    }

    std::this_thread::sleep_for(std::chrono::duration<double>(nc.cooldown));

    g_running.store(false);
    shutdown(_sockfd, SHUT_RDWR);
    close(_sockfd);
    _sockfd = -1;

    if (t_local.joinable()) {
        t_local.join();
    }
    if (t_mon.joinable()) {
        t_mon.join();
    }
    if (t_rapid.joinable()) {
        t_rapid.join();
    }
    if (t_recv.joinable()) {
        t_recv.join();
    }

    {
        std::lock_guard<std::mutex> lk(_event_log_mutex);
        _event_log.flush();
        _event_log.close();
    }

    return 0;
}
