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

#include "/home/mace/git/delta-enabled-crdts/delta-crdts.cc"

using json = nlohmann::json;
using namespace std::chrono_literals;

constexpr size_t MSG_MAX = 4096;

std::mutex _gc_mutex;
std::queue<gcounter<int, std::string>> _send_queue;
std::mutex _send_queue_mutex;
std::condition_variable _send_queue_cond_var;

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
};

struct stats {
    std::atomic<int> sent_msgs{0};
    std::atomic<int> recv_msgs{0};
    std::atomic<int> sent_bytes{0};
    std::atomic<int> recv_bytes{0};
};

void print_config(const node_config& nc) {
    std::cout << "===== NO GOSSIP APPLICATION =====" << "\n";
    std::cout << nc.id << "\n";
    std::cout << nc.listen_addr << "\n";
    for (auto& p : nc.peers) {
        std::cout << p << ", ";
    }
    std::cout << "\n";
    std::cout << nc.ops_per_sec << "\n";
    std::cout << nc.duration << "\n";
    std::cout << nc.seed << "\n";
    std::cout << nc.log_file << "\n";
    std::cout << nc.monitor_interval << "\n";
}

node_config load_config(const std::string& cfg_path, const std::string& id) {
    std::ifstream f(cfg_path);
    json cfg = json::parse(f);
    node_config nc;

    nc.id = id;
    
    try {
        nc.listen_addr = cfg.at("address").at(id);
    } catch (std::exception& e) {
        std::cout << "Node ID " << id << " not found in config file.\n";
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

    // if not specified, the chosen seed will be truly random (operating system's entropy)
    std::random_device rd;
    nc.seed = cfg.value("seed", rd());
    nc.monitor_interval = cfg.value("monitor_interval", 1.0);

    std::string log_dir = cfg.value("log_dir", ".");
    nc.log_file = log_dir + "node_" + id + ".log";

    return nc;
}


// networking
void send_msg(int sockfd, const sockaddr_in& peer, const json& msg, stats& stats) {
    std::string s = msg.dump();
    ssize_t sent = sendto(sockfd, s.data(), s.size(), 0, (sockaddr*)&peer, sizeof(peer));
    if (sent > 0) {
        stats.sent_msgs++;
        stats.sent_bytes += sent;
    }
}

void send_msg(int sockfd, const sockaddr_in& peer, const std::string& msg, stats& stats) {
    ssize_t sent = sendto(sockfd, msg.data(), msg.size(), 0, (sockaddr*)&peer, sizeof(peer));
    if (sent > 0) {
        stats.sent_msgs++;
        stats.sent_bytes += sent;
    }
}

void send_loop(int sockfd, const std::vector<sockaddr_in>& peers, stats& stats) {
    while (true) {
        std::unique_lock<std::mutex> lock(_send_queue_mutex);
        _send_queue_cond_var.wait(lock, []{
            return !_send_queue.empty();
        });

        gcounter<int, std::string> next = _send_queue.front();
        _send_queue.pop();
        lock.unlock();

        for (auto& p : peers) {
            send_msg(sockfd, p, next.serialize(), stats);
            std::this_thread::sleep_for(20ms);
        }
    }
}

void recv_loop(int sockfd, gcounter<int, std::string>& gc, stats& stats) {
    char buffer[MSG_MAX];
    sockaddr_in src;
    socklen_t srclen = sizeof(src);

    while (true) {
        ssize_t n = recvfrom(sockfd, buffer, MSG_MAX, 0, (sockaddr*)&src, &srclen);
        if (n <= 0) {
            continue;
        }

        try {
            auto sender_gcounter = gcounter<int, std::string>::deserialize(std::string(buffer, n));
            {
                std::unique_lock<std::mutex> lock(_gc_mutex);
                gc.join(sender_gcounter);
            }
            stats.recv_msgs++;
            stats.recv_bytes += n;
        } catch (...) {
            continue;
        }
    }
}


// operations order follow some given random distribution
void run_random_mode(const node_config& nc, gcounter<int, std::string>& gc, int sockfd, const std::vector<sockaddr_in>& peers, stats& stats) {
    double ops_per_sec = nc.ops_per_sec;
    double duration = nc.duration;
    unsigned seed = nc.seed;
    std::default_random_engine gen(seed);
    std::exponential_distribution<double> expd(ops_per_sec);

    auto start = std::chrono::steady_clock::now();
    while (true) {
        auto elapsed = std::chrono::duration<double>(std::chrono::steady_clock::now() - start).count();
        if (elapsed > duration) {
            break;
        }

        double wait = expd(gen);
        std::this_thread::sleep_for(std::chrono::duration<double>(wait));

        std::uniform_int_distribution<int> inc_dist(1, 10);
        int val = inc_dist(gen);
        
        gcounter<int, std::string> delta_obj;
        {
            std::unique_lock<std::mutex> gc_lock(_gc_mutex);
            delta_obj = gc.inc(val);
        }

        std::unique_lock<std::mutex> op_lock(_send_queue_mutex);
        _send_queue.push(delta_obj);
        _send_queue_cond_var.notify_one();
    }
}

// monitor loop
void monitor_loop(gcounter<int, std::string>& gc, stats& stats, double interval, const std::string& logfile) {
    std::ofstream log(logfile, std::ios::trunc);
    while (true) {
        std::this_thread::sleep_for(std::chrono::duration<double>(interval));
        int local = gc.local();
        int total = gc.read();
        log << std::fixed << std::chrono::duration<double>(std::chrono::steady_clock::now().time_since_epoch()).count()
            << ", local=" << local << ", total=" << total
            << ", sent_msgs=" << stats.sent_msgs
            << ", recv_msgs=" << stats.recv_msgs 
            << ", sent_bytes=" << stats.sent_bytes 
            << ", recv_bytes=" << stats.recv_bytes << "\n";
        std::cout << std::fixed << std::chrono::duration<double>(std::chrono::steady_clock::now().time_since_epoch()).count()
            << ", local=" << local << ", total=" << total
            << ", sent_msgs=" << stats.sent_msgs
            << ", recv_msgs=" << stats.recv_msgs
            << ", sent_bytes=" << stats.sent_bytes 
            << ", recv_bytes=" << stats.recv_bytes << "\n";
        log.flush();
    }
}

// main
int main(int argc, char* argv[]) {
    if (argc < 3) {
        std::cerr << "Usage: " << argv[0] << " -id <ID> -config <config.json>\n";
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

    std::cout << "Reading cfgfile: " << cfgfile << "\n";
    std::cout << "Node id: " << node_id << "\n";

    if (node_id.empty() || cfgfile.empty()) {
        std::cerr << "Missing node ID or config file.\n";
        return 1;
    }

    node_config nc = load_config(cfgfile, node_id);
    print_config(nc);

    if (nc.listen_addr.empty()) {
        std::cerr << "Failed to load configuration for node " << node_id << ".\n";
        return 1;
    }

    auto pos = nc.listen_addr.find(':');
    std::string self_ip = nc.listen_addr.substr(0, pos);
    int listen_port = std::stoi(nc.listen_addr.substr(pos + 1));

    std::vector<sockaddr_in> peers;
    for (auto& p : nc.peers) {
        auto pos2 = p.find(':');
        std::string ip = p.substr(0, pos2);
        int port = std::stoi(p.substr(pos2 + 1));
        sockaddr_in peer{};
        peer.sin_family = AF_INET;
        peer.sin_port = htons(port);
        inet_pton(AF_INET, ip.c_str(), &peer.sin_addr);
        peers.push_back(peer);
    }

    int sockfd = socket(AF_INET, SOCK_DGRAM, 0);
    if (sockfd < 0) {
        std::cerr << "Failed to create socket.\n";
        return 1;
    }

    sockaddr_in addr{};
    addr.sin_family = AF_INET;
    addr.sin_addr.s_addr = INADDR_ANY;
    addr.sin_port = htons(listen_port);
    if (bind(sockfd, (sockaddr*)&addr, sizeof(addr)) < 0) {
        std::cerr << "Failed to bind socket.\n";
        return 1;
    }

    gcounter<int, std::string> gc(nc.id);
    stats stats;

    std::thread t_recv(recv_loop, sockfd, std::ref(gc), std::ref(stats));
    t_recv.detach();

    std::thread t_mon(monitor_loop, std::ref(gc), std::ref(stats), nc.monitor_interval, nc.log_file);
    t_mon.detach();

    std::thread t_send(send_loop, sockfd, peers, std::ref(stats));
    t_send.detach();

    run_random_mode(nc, gc, sockfd, peers, stats);

    std::this_thread::sleep_for(15s);
    std::cout << "FINAL local=" << gc.local()
              << " total=" << gc.read()
              << " sent=" << stats.sent_msgs
              << " recv=" << stats.recv_msgs << std::endl;

    close(sockfd);
    return 0;
}
