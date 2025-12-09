// gcounter_with_rapid_unix.cpp
// Build: g++ -O2 -std=c++17 -pthread -o gcounter_with_rapid_unix gcounter_with_rapid_unix.cpp -I/path/to/nlohmann
// Run: start rapid_daemon with socat:
// socat UNIX-LISTEN:/tmp/rapid.sock,fork EXEC:./rapid_daemon
// then: ./gcounter_with_rapid_unix -id <ID> -config config.json -socket /tmp/rapid.sock

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
#include <mutex>
#include <queue>
#include <condition_variable>
#include <sstream>
#include <algorithm>
#include <sys/types.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

#include "/home/mace/git/delta-enabled-crdts/delta-crdts.cc"

using json = nlohmann::json;
using namespace std::chrono_literals;

constexpr size_t MSG_MAX = 8192;

std::mutex gc_mtx;
std::queue<gcounter<int, std::string>> send_queue;
std::mutex q_mutex;
std::condition_variable q_cv;

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
    std::string socket_path;
};

struct stats {
    std::atomic<int> sent_msgs{0};
    std::atomic<int> recv_msgs{0};
    std::atomic<int> sent_bytes{0};
    std::atomic<int> recv_bytes{0};
};

void print_config(const node_config& nc) {
    std::cout << "node id: " << nc.id << "\n";
    std::cout << "socket: " << nc.socket_path << "\n";
    std::cout << "ops_per_sec: " << nc.ops_per_sec << "\n";
    std::cout << "duration: " << nc.duration << "\n";
    std::cout << "seed: " << nc.seed << "\n";
    std::cout << "log_file: " << nc.log_file << "\n";
    std::cout << "monitor_interval: " << nc.monitor_interval << "\n";
}

// load_config similar to antes, but includes socket_path default
node_config load_config(const std::string& cfg_path, const std::string& id) {
    std::ifstream f(cfg_path);
    if (!f) {
        throw std::runtime_error("Cannot open config file");
    }
    json cfg = json::parse(f);
    node_config nc;
    nc.id = id;
    try {
        nc.listen_addr = cfg.at("address").at(id);
    } catch (std::exception& e) {
        nc.listen_addr = "";
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
    nc.seed = cfg.value("seed", rd());
    nc.monitor_interval = cfg.value("monitor_interval", 1.0);
    std::string log_dir = cfg.value("log_dir", ".");
    if (!log_dir.empty() && log_dir.back() != '/') log_dir.push_back('/');
    nc.log_file = log_dir + "node_" + id + ".log";
    nc.socket_path = cfg.value("socket_path", std::string("/tmp/rapid.sock"));
    return nc;
}

// ----------------- Base64 helpers -----------------
static const std::string b64_chars =
             "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
             "abcdefghijklmnopqrstuvwxyz"
             "0123456789+/";

inline bool is_b64(unsigned char c) {
    return (isalnum(c) || (c == '+') || (c == '/'));
}

std::string base64_encode(const std::string &in) {
    std::string out;
    int val=0, valb=-6;
    for (unsigned char c : in) {
        val = (val<<8) + c;
        valb += 8;
        while (valb>=0) {
            out.push_back(b64_chars[(val>>valb)&0x3F]);
            valb-=6;
        }
    }
    if (valb>-6) out.push_back(b64_chars[((val<<8)>>(valb+8))&0x3F]);
    while (out.size()%4) out.push_back('=');
    return out;
}

std::string base64_decode(const std::string &in) {
    std::vector<int> T(256, -1);
    for (int i=0; i<64; i++) T[(unsigned char)b64_chars[i]] = i;
    std::string out;
    std::vector<int> vals;
    vals.reserve(in.size());
    for (unsigned char c : in) {
        if (T[c] != -1) vals.push_back(T[c]);
        else if (c == '=') vals.push_back(0);
    }
    int val=0, valb=-8;
    for (int v : vals) {
        val = (val<<6) + v;
        valb += 6;
        if (valb>=0) {
            out.push_back(char((val>>valb)&0xFF));
            valb-=8;
        }
    }
    return out;
}
// --------------------------------------------------

// UNIX socket client
int connect_unix_socket(const std::string &path) {
    int fd = socket(AF_UNIX, SOCK_STREAM, 0);
    if (fd < 0) {
        perror("socket");
        return -1;
    }
    struct sockaddr_un addr;
    memset(&addr, 0, sizeof(addr));
    addr.sun_family = AF_UNIX;
    if (path.size() >= sizeof(addr.sun_path)) {
        std::cerr << "Socket path too long\n";
        close(fd);
        return -1;
    }
    strncpy(addr.sun_path, path.c_str(), sizeof(addr.sun_path)-1);
    if (connect(fd, (struct sockaddr*)&addr, sizeof(addr)) < 0) {
        perror("connect");
        close(fd);
        return -1;
    }
    return fd;
}

// reader thread: read lines from socket, handle DELIVER:
void rapid_reader_thread(int sockfd, gcounter<int, std::string>& gc, stats& st) {
    std::string buffer;
    buffer.reserve(8192);
    char tmp[4096];
    while (true) {
        ssize_t n = read(sockfd, tmp, sizeof(tmp));
        if (n <= 0) {
            if (n == 0) {
                // socket closed
                std::cerr << "rapid socket closed by peer\n";
                break;
            }
            // error: sleep a bit and retry
            std::this_thread::sleep_for(10ms);
            continue;
        }
        buffer.append(tmp, tmp + n);
        // extract lines
        while (true) {
            auto pos = buffer.find('\n');
            if (pos == std::string::npos) break;
            std::string line = buffer.substr(0, pos);
            buffer.erase(0, pos + 1);
            if (line.rfind("DELIVER:", 0) == 0) {
                std::string b64 = line.substr(8);
                std::string decoded;
                try {
                    decoded = base64_decode(b64);
                } catch (...) { continue; }
                try {
                    auto sender_gcounter = gcounter<int, std::string>::deserialize(decoded);
                    {
                        std::unique_lock<std::mutex> lock(gc_mtx);
                        gc.join(sender_gcounter);
                    }
                    st.recv_msgs++;
                    st.recv_bytes += decoded.size();
                } catch (...) {
                    // ignore parse errors
                }
            } else {
                // ignore other lines or debug prints
            }
        }
    }
}

// send thread: take delta from send_queue, send "DATA:<b64>\n" to socket
void send_loop_to_daemon(int sockfd, stats& st) {
    while (true) {
        std::unique_lock<std::mutex> lock(q_mutex);
        q_cv.wait(lock, []{
            return !send_queue.empty();
        });
        gcounter<int, std::string> next = send_queue.front();
        send_queue.pop();
        lock.unlock();

        std::string ser = next.serialize();
        std::string b64 = base64_encode(ser);
        std::string line = "DATA:" + b64 + "\n";
        ssize_t w = write(sockfd, line.data(), line.size());
        if (w == (ssize_t)line.size()) {
            st.sent_msgs++;
            st.sent_bytes += w;
        } else {
            // try to requeue (simple strategy)
            std::this_thread::sleep_for(50ms);
            std::unique_lock<std::mutex> lk(q_mutex);
            send_queue.push(next);
            q_cv.notify_one();
        }
        std::this_thread::sleep_for(20ms);
    }
}

// operations generator (same as before)
void run_random_mode(const node_config& nc, gcounter<int, std::string>& gc) {
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
            std::unique_lock<std::mutex> gc_lock(gc_mtx);
            delta_obj = gc.inc(val);
        }

        std::unique_lock<std::mutex> op_lock(q_mutex);
        send_queue.push(delta_obj);
        q_cv.notify_one();
    }
}

// monitor loop
void monitor_loop(gcounter<int, std::string>& gc, stats& stats_ref, double interval, const std::string& logfile) {
    std::ofstream log(logfile, std::ios::trunc);
    if (!log.is_open()) {
        std::cerr << "Warning: cannot open log file: " << logfile << "\n";
    }
    while (true) {
        std::this_thread::sleep_for(std::chrono::duration<double>(interval));
        int local = gc.local();
        int total = gc.read();
        std::ostringstream oss;
        oss << std::fixed << std::chrono::duration<double>(std::chrono::steady_clock::now().time_since_epoch()).count()
            << ", local=" << local << ", total=" << total
            << ", sent_msgs=" << stats_ref.sent_msgs
            << ", recv_msgs=" << stats_ref.recv_msgs 
            << ", sent_bytes=" << stats_ref.sent_bytes 
            << ", recv_bytes=" << stats_ref.recv_bytes << "\n";
        std::string line = oss.str();
        std::cout << line << std::flush;
        if (log.is_open()) {
            log << line << std::flush;
        }
    }
}

int main(int argc, char* argv[]) {
    if (argc < 3) {
        std::cerr << "Usage: " << argv[0] << " -id <ID> -config <config.json> [-socket <path>]\n";
        return 1;
    }

    std::string node_id;
    std::string cfgfile;
    std::string sockpath;

    for (int i = 1; i < argc; ++i) {
        std::string arg = argv[i];
        if (arg == "-id" && i + 1 < argc) {
            node_id = argv[++i];
        } else if (arg == "-config" && i + 1 < argc) {
            cfgfile = argv[++i];
        } else if (arg == "-socket" && i + 1 < argc) {
            sockpath = argv[++i];
        }
    }

    if (node_id.empty() || cfgfile.empty()) {
        std::cerr << "Missing node ID or config file.\n";
        return 1;
    }

    node_config nc;
    try {
        nc = load_config(cfgfile, node_id);
    } catch (std::exception& e) {
        std::cerr << "Failed to load config: " << e.what() << "\n";
        return 1;
    }
    if (!sockpath.empty()) nc.socket_path = sockpath;
    print_config(nc);

    // connect to unix domain socket
    int sockfd = connect_unix_socket(nc.socket_path);
    if (sockfd < 0) {
        std::cerr << "Failed to connect to rapid daemon socket at " << nc.socket_path << "\n";
        std::cerr << "If using socat, run:\n  socat UNIX-LISTEN:" << nc.socket_path << ",fork EXEC:./rapid_daemon\n";
        return 1;
    }
    std::cout << "Connected to rapid daemon at " << nc.socket_path << "\n";

    // init gcounter and stats
    gcounter<int, std::string> gc(nc.id);
    stats st;

    // threads
    std::thread t_reader(rapid_reader_thread, sockfd, std::ref(gc), std::ref(st));
    t_reader.detach();

    std::thread t_send(send_loop_to_daemon, sockfd, std::ref(st));
    t_send.detach();

    std::thread t_mon(monitor_loop, std::ref(gc), std::ref(st), nc.monitor_interval, nc.log_file);
    t_mon.detach();

    // run workload
    run_random_mode(nc, gc);

    // wait for final dissemination
    std::this_thread::sleep_for(15s);

    std::cout << "FINAL local=" << gc.local()
              << " total=" << gc.read()
              << " sent=" << st.sent_msgs
              << " recv=" << st.recv_msgs << std::endl;

    close(sockfd);
    return 0;
}
