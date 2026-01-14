#include <iostream>
#include <fstream>
#include <thread>
#include <chrono>
#include <atomic>
#include <vector>
#include <string>
#include <random>
#include <map>
#include <csignal>
#include <cstring>
#include <nlohmann/json.hpp>
#include <arpa/inet.h>
#include <sys/socket.h>
#include <unistd.h>
#include <mutex>
#include <queue>
#include <condition_variable>
#include <sstream>
#include <algorithm>
#include <net/if.h> // <--- Importante

#include "/home/mace/git/fork-pymace/apps/crdt/common/delta-crdts.cc"

using json = nlohmann::json;
using namespace std::chrono_literals;

constexpr size_t MSG_MAX = 4096;

std::mutex _gc_mutex;
std::mutex _delta_mutex;
std::mutex _event_log_mutex;
std::ofstream _event_log;
std::condition_variable _diss_cond;
std::mutex _diss_mutex;
bool _diss_dirty = false;

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

// ... Funções auxiliares (print_config, load_config, now_ts, send_msg) mantidas iguais ...
node_config load_config(const std::string& cfg_path, const std::string& id) {
    std::ifstream f(cfg_path);
    json cfg = json::parse(f);
    node_config nc;
    nc.id = id;
    try { nc.listen_addr = cfg.at("address").at(id); } catch (...) { return nc; }
    for (auto& [nid, addr] : cfg["address"].items()) { if (nid != id) nc.peers.push_back(addr); }
    nc.ops_per_sec = cfg.value("ops_per_sec", 1.0);
    nc.duration = cfg.value("duration", 10);
    nc.distribution = cfg.value("distribution", "uniform");
    nc.dissemination_interval = cfg.value("dissemination_interval", 0.5);
    std::random_device rd;
    nc.seed = cfg.value("seed", rd()) + std::atoi(id.c_str());
    nc.monitor_interval = cfg.value("monitor_interval", 1.0);
    std::string log_dir = cfg.value("log_dir", ".");
    nc.log_file = log_dir + "node_" + id + ".log";
    nc.cooldown = cfg.value("cooldown", 10);
    return nc;
}

inline double now_ts() {
    return std::chrono::duration<double>(std::chrono::steady_clock::now().time_since_epoch()).count();
}

void print_config(const node_config& nc) {
    std::cout << "Node: " << nc.id << " Addr: " << nc.listen_addr << "\n";
}

void dissemination_loop(
    int sockfd,
    const std::vector<sockaddr_in>& peers,
    gcounter<int, std::string>& delta_buffer,
    stats& stats,
    double interval,
    sockaddr_in& broadcast_addr
){
    std::unique_lock<std::mutex> lk(_diss_mutex);

    while (true) {
        _diss_cond.wait_for(lk, std::chrono::duration<double>(interval), [] { return _diss_dirty; });
        _diss_dirty = false;

        std::string payload;
        {
            std::lock_guard<std::mutex> dlock(_delta_mutex);
            if (delta_buffer == gcounter<int, std::string>()) continue;
            payload = delta_buffer.serialize();
            // Lógica de aplicação mantida intacta (sem limpar buffer)
        }

        ssize_t sent = sendto(sockfd, payload.data(), payload.size(), 0, 
                             (sockaddr*)&broadcast_addr, sizeof(broadcast_addr));

        if (sent > 0) {
            stats.sent_msgs++;
            stats.sent_bytes += sent;
        } 
        // Se quiser debugar o Nó 0:
        // else { perror("Sendto Error"); }
    }
}

void recv_loop(int sockfd, gcounter<int, std::string>& gc, stats& stats, std::string node_id) {
    char buffer[MSG_MAX];
    sockaddr_in src;
    socklen_t srclen = sizeof(src);

    while (true) {
        ssize_t n = recvfrom(sockfd, buffer, MSG_MAX, 0, (sockaddr*)&src, &srclen);
        if (n <= 0) continue;

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
                _event_log << std::fixed << now_ts() << ", event=op_apply, node=" << node_id << ", total=" << total << "\n";
            }
            stats.recv_msgs++;
            stats.recv_bytes += n;
        } catch (...) { continue; }
    }
}

void run_random_mode(const node_config& nc, gcounter<int, std::string>& gc, gcounter<int, std::string>& delta_buffer, int sockfd, const std::vector<sockaddr_in>& peers, stats& stats) {
    double ops_per_sec = nc.ops_per_sec;
    double duration = nc.duration;
    std::default_random_engine gen(nc.seed);
    std::exponential_distribution<double> expd(ops_per_sec);
    std::uniform_int_distribution<int> inc_dist(1, 1);

    auto start = std::chrono::steady_clock::now();
    while (true) {
        auto elapsed = std::chrono::duration<double>(std::chrono::steady_clock::now() - start).count();
        if (elapsed > duration) break;

        std::this_thread::sleep_for(std::chrono::duration<double>(expd(gen)));
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
            _event_log << std::fixed << now_ts() << ", event=op_create, node=" << nc.id << ", delta_size=" << val << "\n";
            _event_log << std::fixed << now_ts() << ", event=op_apply, node=" << nc.id << ", total=" << total << "\n";
        }
        {
            std::unique_lock<std::mutex> lock(_delta_mutex);
            delta_buffer.join(d);
        }
        {
            std::lock_guard<std::mutex> lk(_diss_mutex);
            _diss_dirty = true;
        }
        _diss_cond.notify_one();
    }
}

void monitor_loop(gcounter<int, std::string>& gc, stats& stats, double interval, const std::string& logfile) {
    std::ofstream log(logfile, std::ios::trunc);
    while (true) {
        std::this_thread::sleep_for(std::chrono::duration<double>(interval));
        int local = gc.local();
        int total = gc.read();
        log << std::fixed << now_ts() 
            << ", local=" << local << ", total=" << total
            << ", sent_msgs=" << stats.sent_msgs << ", recv_msgs=" << stats.recv_msgs 
            << ", sent_bytes=" << stats.sent_bytes << ", recv_bytes=" << stats.recv_bytes << "\n";
        log.flush();
        { std::unique_lock<std::mutex> lk(_event_log_mutex); _event_log.flush(); }
    }
}

int main(int argc, char* argv[]) {
    if (argc < 3) return 1;
    std::string node_id, cfgfile;
    for (int i = 1; i < argc; ++i) {
        std::string arg = argv[i];
        if (arg == "-id" && i + 1 < argc) node_id = argv[++i];
        else if (arg == "-config" && i + 1 < argc) cfgfile = argv[++i];
    }
    
    node_config nc = load_config(cfgfile, node_id);
    print_config(nc);
    _event_log.open(nc.log_file + ".events", std::ios::trunc);

    auto pos = nc.listen_addr.find(':');
    int listen_port = std::stoi(nc.listen_addr.substr(pos + 1));

    // --- CORREÇÃO DE REDE ---
    // Destino: 255.255.255.255 (Global Broadcast)
    // Isso garante que saia pela placa vinculada (bat0) e não pela rota default do Linux
    std::vector<sockaddr_in> peers;
    sockaddr_in bcast_addr{};
    bcast_addr.sin_family = AF_INET;
    bcast_addr.sin_port = htons(listen_port);
    inet_pton(AF_INET, "255.255.255.255", &bcast_addr.sin_addr);
    peers.push_back(bcast_addr);

    // Socket
    int sockfd = socket(AF_INET, SOCK_DGRAM, 0);
    if (sockfd < 0) return 1;

    // Habilitar Broadcast
    int broadcastEnable = 1;
    setsockopt(sockfd, SOL_SOCKET, SO_BROADCAST, &broadcastEnable, sizeof(broadcastEnable));

    // Bind Device (FORÇA SAÍDA PELA BAT0)
    struct ifreq ifr;
    memset(&ifr, 0, sizeof(ifr));
    snprintf(ifr.ifr_name, sizeof(ifr.ifr_name), "bat0");
    if (setsockopt(sockfd, SOL_SOCKET, SO_BINDTODEVICE, (void *)&ifr, sizeof(ifr)) < 0) {
        std::cerr << "AVISO: Falha no Bind Device bat0 (use sudo)\n";
    }

    sockaddr_in addr{};
    addr.sin_family = AF_INET;
    addr.sin_addr.s_addr = INADDR_ANY;
    addr.sin_port = htons(listen_port);
    if (bind(sockfd, (sockaddr*)&addr, sizeof(addr)) < 0) return 1;
    // ------------------------

    gcounter<int, std::string> gc(nc.id);
    gcounter<int, std::string> delta_buffer;
    stats stats;

    std::thread t_recv(recv_loop, sockfd, std::ref(gc), std::ref(stats), nc.id);
    t_recv.detach();

    std::thread t_mon(monitor_loop, std::ref(gc), std::ref(stats), nc.monitor_interval, nc.log_file);
    t_mon.detach();

    std::thread t_diss(dissemination_loop, sockfd, peers, std::ref(delta_buffer), std::ref(stats), nc.dissemination_interval, std::ref(bcast_addr));
    t_diss.detach();

    run_random_mode(nc, gc, delta_buffer, sockfd, peers, stats);

    { std::lock_guard<std::mutex> lk(_event_log_mutex); _event_log << std::fixed << now_ts() << ", event=ops_finished, node=" << nc.id << "\n"; }

    std::this_thread::sleep_for(std::chrono::duration<double>(nc.cooldown));
    close(sockfd);
    return 0;
}