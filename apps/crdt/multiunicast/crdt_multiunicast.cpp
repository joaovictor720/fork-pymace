#include <iostream>
#include <fstream>
#include <thread>
#include <chrono>
#include <atomic>
#include <vector>
#include <string>
#include <random>
#include <cstring>
#include <cerrno>
#include <nlohmann/json.hpp>
#include <arpa/inet.h>
#include <sys/socket.h>
#include <unistd.h>
#include <mutex>
#include <sstream>
#include <algorithm>
#include <net/if.h>

#include "/home/mace/git/fork-pymace/apps/crdt/common/delta-crdts.cc"

using json = nlohmann::json;

constexpr size_t MSG_MAX = 4096;

static std::atomic<bool> g_running{true};

std::mutex _gc_mutex;
std::mutex _delta_mutex;
std::mutex _event_log_mutex;
std::ofstream _event_log;

struct node_config {
    std::string id;
    std::string listen_addr;
    std::vector<std::string> peers;
    double ops_per_sec;
    int duration;
    std::string distribution;
    int seed;
    std::string log_file;
    double monitor_interval;
    double dissemination_interval;
    double cooldown;
};

struct stats {
    std::atomic<int> sent_msgs{0};
    std::atomic<int> recv_msgs{0};
    std::atomic<int> sent_bytes{0};
    std::atomic<int> recv_bytes{0};
};

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
    nc.dissemination_interval = cfg.value("dissemination_interval", 0.5);

    std::random_device rd;
    nc.seed = cfg.value("seed", rd()) + std::atoi(id.c_str());
    nc.monitor_interval = cfg.value("monitor_interval", 1.0);

    std::string log_dir = cfg.value("log_dir", ".");
    if (!log_dir.empty() && log_dir.back() != '/') {
        log_dir.push_back('/');
    }
    nc.log_file = log_dir + "node_" + id + ".log";

    nc.cooldown = cfg.value("cooldown", 10);
    return nc;
}

inline double now_ts() {
    return std::chrono::duration<double>(std::chrono::steady_clock::now().time_since_epoch()).count();
}

void print_config(const node_config& nc) {
    std::cout << "Node: " << nc.id << " Addr: " << nc.listen_addr << "\n";
    std::cout << "Peers(" << nc.peers.size() << "): ";
    for (const auto& p : nc.peers) {
        std::cout << p << " ";
    }
    std::cout << "\n";
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

static bool make_sockaddr_in(const std::string& addr, int default_port, sockaddr_in& out) {
    std::string host;
    int port = default_port;
    if (!parse_host_port(addr, host, port, default_port)) {
        return false;
    }

    sockaddr_in sin{};
    sin.sin_family = AF_INET;
    sin.sin_port = htons(static_cast<uint16_t>(port));

    if (inet_pton(AF_INET, host.c_str(), &sin.sin_addr) != 1) {
        return false;
    }

    out = sin;
    return true;
}

static void set_socket_timeouts(int sockfd) {
    timeval tv{};
    tv.tv_sec = 0;
    tv.tv_usec = 200000;
    (void)setsockopt(sockfd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
}

static void bind_to_bat0(int sockfd) {
    struct ifreq ifr;
    std::memset(&ifr, 0, sizeof(ifr));
    std::snprintf(ifr.ifr_name, sizeof(ifr.ifr_name), "bat0");
    if (setsockopt(sockfd, SOL_SOCKET, SO_BINDTODEVICE, (void*)&ifr, sizeof(ifr)) < 0) {
        std::cerr << "AVISO: Falha no Bind Device bat0 (use sudo)\n";
    }
}

void dissemination_loop(
    int sockfd,
    const std::vector<sockaddr_in>& peers,
    gcounter<int, std::string>& delta_buffer,
    stats& st,
    double interval
) {
    std::string last_payload;
    int retriggers_left = 0;
    const int retriggers_budget = 1;

    auto next = std::chrono::steady_clock::now();

    while (g_running.load()) {
        next += std::chrono::duration_cast<std::chrono::steady_clock::duration>(std::chrono::duration<double>(interval));
        std::this_thread::sleep_until(next);

        if (!g_running.load()) {
            break;
        }

        std::string payload;
        bool has_new = false;

        {
            std::lock_guard<std::mutex> dlock(_delta_mutex);
            if (!(delta_buffer == gcounter<int, std::string>())) {
                payload = delta_buffer.serialize();
                delta_buffer = gcounter<int, std::string>();
                has_new = true;
            }
        }

        if (!has_new) {
            if (retriggers_left > 0 && !last_payload.empty()) {
                payload = last_payload;
                retriggers_left--;
            } else {
                continue;
            }
        } else {
            last_payload = payload;
            retriggers_left = retriggers_budget;
        }

        for (const auto& peer : peers) {
            ssize_t sent = sendto(sockfd, payload.data(), payload.size(), 0, (sockaddr*)&peer, sizeof(peer));
            if (sent > 0) {
                st.sent_msgs++;
                st.sent_bytes += static_cast<int>(sent);
            }
        }
    }
}

void recv_loop(int sockfd, gcounter<int, std::string>& gc, stats& st, const std::string& node_id) {
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

        try {
            auto sender_gcounter = gcounter<int, std::string>::deserialize(std::string(buffer, n));
            int total = 0;
            {
                std::lock_guard<std::mutex> lk(_gc_mutex);
                gc.join(sender_gcounter);
                total = gc.read();
            }
            {
                std::lock_guard<std::mutex> lk(_event_log_mutex);
                _event_log << std::fixed << now_ts()
                           << ", event=op_apply, node=" << node_id
                           << ", total=" << total << "\n";
            }
            st.recv_msgs++;
            st.recv_bytes += static_cast<int>(n);
        } catch (...) {
            continue;
        }
    }
}

void run_random_mode(
    const node_config& nc,
    gcounter<int, std::string>& gc,
    gcounter<int, std::string>& delta_buffer
) {
    double ops_per_sec = nc.ops_per_sec;
    double duration = nc.duration;

    std::default_random_engine gen(nc.seed);
    std::exponential_distribution<double> expd(ops_per_sec);
    std::uniform_int_distribution<int> inc_dist(1, 1);

    auto start = std::chrono::steady_clock::now();

    while (g_running.load()) {
        auto elapsed = std::chrono::duration<double>(std::chrono::steady_clock::now() - start).count();
        if (elapsed > duration) {
            break;
        }

        std::this_thread::sleep_for(std::chrono::duration<double>(expd(gen)));
        if (!g_running.load()) {
            break;
        }

        int val = inc_dist(gen);

        gcounter<int, std::string> d;
        int total = 0;

        {
            std::unique_lock<std::mutex> lock(_gc_mutex);
            d = gc.inc(val);
            total = gc.read();
        }

        {
            std::lock_guard<std::mutex> lk(_event_log_mutex);
            _event_log << std::fixed << now_ts()
                       << ", event=op_create, node=" << nc.id
                       << ", delta_size=" << val << "\n";
            _event_log << std::fixed << now_ts()
                       << ", event=op_apply, node=" << nc.id
                       << ", total=" << total << "\n";
        }

        {
            std::lock_guard<std::mutex> lock(_delta_mutex);
            delta_buffer.join(d);
        }
    }
}

void monitor_loop(gcounter<int, std::string>& gc, stats& st, double interval, const std::string& logfile) {
    std::ofstream log(logfile, std::ios::trunc);

    while (g_running.load()) {
        std::this_thread::sleep_for(std::chrono::duration<double>(interval));
        if (!g_running.load()) {
            break;
        }

        int local = gc.local();
        int total = gc.read();

        log << std::fixed << now_ts()
            << ", local=" << local << ", total=" << total
            << ", sent_msgs=" << st.sent_msgs << ", recv_msgs=" << st.recv_msgs
            << ", sent_bytes=" << st.sent_bytes << ", recv_bytes=" << st.recv_bytes << "\n";
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

    node_config nc = load_config(cfgfile, node_id);
    if (nc.listen_addr.empty()) {
        return 1;
    }
    print_config(nc);

    _event_log.open(nc.log_file + ".events", std::ios::trunc);
    _event_log.setf(std::ios::unitbuf);

    auto pos = nc.listen_addr.find(':');
    if (pos == std::string::npos) {
        return 1;
    }

    int listen_port = 0;
    try {
        listen_port = std::stoi(nc.listen_addr.substr(pos + 1));
    } catch (...) {
        return 1;
    }

    std::vector<sockaddr_in> peers;
    peers.reserve(nc.peers.size());
    for (const auto& p : nc.peers) {
        sockaddr_in sin{};
        if (make_sockaddr_in(p, listen_port, sin)) {
            peers.push_back(sin);
        }
    }

    int sockfd = socket(AF_INET, SOCK_DGRAM, 0);
    if (sockfd < 0) {
        return 1;
    }

    int reuse = 1;
    (void)setsockopt(sockfd, SOL_SOCKET, SO_REUSEADDR, &reuse, sizeof(reuse));

    bind_to_bat0(sockfd);
    set_socket_timeouts(sockfd);

    sockaddr_in addr{};
    addr.sin_family = AF_INET;
    addr.sin_addr.s_addr = INADDR_ANY;
    addr.sin_port = htons(static_cast<uint16_t>(listen_port));
    if (bind(sockfd, (sockaddr*)&addr, sizeof(addr)) < 0) {
        close(sockfd);
        return 1;
    }

    gcounter<int, std::string> gc(nc.id);
    gcounter<int, std::string> delta_buffer;
    stats st;

    std::thread t_recv(recv_loop, sockfd, std::ref(gc), std::ref(st), nc.id);
    std::thread t_mon(monitor_loop, std::ref(gc), std::ref(st), nc.monitor_interval, nc.log_file);
    std::thread t_diss(dissemination_loop, sockfd, std::cref(peers), std::ref(delta_buffer), std::ref(st), nc.dissemination_interval);

    run_random_mode(nc, gc, delta_buffer);

    {
        std::lock_guard<std::mutex> lk(_event_log_mutex);
        _event_log << std::fixed << now_ts()
                   << ", event=ops_finished, node=" << nc.id << "\n";
    }

    std::this_thread::sleep_for(std::chrono::duration<double>(nc.cooldown));

    g_running.store(false);
    shutdown(sockfd, SHUT_RDWR);
    close(sockfd);

    if (t_diss.joinable()) {
        t_diss.join();
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
