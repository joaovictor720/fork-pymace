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
#include <cerrno>
#include <nlohmann/json.hpp>
#include <arpa/inet.h>
#include <sys/socket.h>
#include <unistd.h>
#include <mutex>
#include <condition_variable>
#include <sstream>
#include <algorithm>
#include <unordered_map>

#include "/home/mace/git/fork-pymace/apps/crdt/common/delta-crdts.cc"

using json = nlohmann::json;

constexpr size_t MSG_MAX = 8192;
static const double BETA = 2.5;
static const int CACHE_TTL = 60;
static const int HEARTBEAT_INTERVAL_MS = 1000;
static const int GOSSIP_INTERVAL_MS = 1000;
static const int SHORT_JITTER_MIN_MS = 10;
static const int SHORT_JITTER_MAX_MS = 40;
static const int LONG_JITTER_MIN_MS = 200;
static const int LONG_JITTER_MAX_MS = 600;
static const int PORT_DEFAULT = 9000;

static std::atomic<bool> g_running{true};

std::mutex _gc_mutex;

gcounter<int, std::string> _pending_state;
bool _has_pending = false;

std::mutex _pending_mutex;

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
    double cooldown;
    double dissemination_interval;
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
    for (auto& p : nc.peers) {
        std::cout << p << " ";
    }
    std::cout << "\n";
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

    nc.cooldown = cfg.value("cooldown", 10);
    nc.dissemination_interval = cfg.value("dissemination_interval", 0.5);
    return nc;
}

enum MsgType : uint8_t {
    MT_DATA = 1,
    MT_GOSSIP = 2,
    MT_REQUEST = 3,
    MT_HEARTBEAT = 4
};

struct CacheEntry {
    std::chrono::steady_clock::time_point t;
    std::vector<char> payload;
};

struct CastEntry {
    uint64_t msgid;
    double prob;
    std::chrono::steady_clock::time_point when;
    std::vector<char> payload;
    std::string reason;
};

class CastQueue {
private:
    std::vector<CastEntry> q_;
    std::mutex mu_;
    std::condition_variable cv_;

public:
    void push(const CastEntry& e) {
        {
            std::lock_guard<std::mutex> lg(mu_);
            q_.push_back(e);
        }
        cv_.notify_one();
    }

    bool pop_due(CastEntry& out) {
        std::unique_lock<std::mutex> lk(mu_);
        if (q_.empty()) {
            cv_.wait_for(lk, std::chrono::milliseconds(50));
        }
        auto now = std::chrono::steady_clock::now();
        for (auto it = q_.begin(); it != q_.end(); ++it) {
            if (it->when <= now) {
                out = *it;
                q_.erase(it);
                return true;
            }
        }
        return false;
    }
};

CastQueue _cast_queue;
std::unordered_map<uint64_t, CacheEntry> _cache;
std::mutex _cache_mutex;
std::unordered_map<uint64_t, std::chrono::steady_clock::time_point> _neighbors;
std::mutex _neighbors_mutex;

int _sockfd = -1;
sockaddr_in _broadcast_addr;
int _listen_port = PORT_DEFAULT;

std::mt19937_64 _rng((uint64_t)std::chrono::steady_clock::now().time_since_epoch().count());
std::uniform_real_distribution<double> _uniform_01(0.0, 1.0);

static void append_uint64(std::vector<char>& v, uint64_t x) {
    for (int i = 0; i < 8; ++i) {
        v.push_back(static_cast<char>((x >> (i * 8)) & 0xFF));
    }
}

static void append_uint32(std::vector<char>& v, uint32_t x) {
    for (int i = 0; i < 4; ++i) {
        v.push_back(static_cast<char>((x >> (i * 8)) & 0xFF));
    }
}

static uint64_t read_uint64(const char* p) {
    uint64_t x = 0;
    for (int i = 0; i < 8; ++i) {
        x |= (static_cast<uint64_t>(static_cast<unsigned char>(p[i])) << (i * 8));
    }
    return x;
}

static uint32_t read_uint32(const char* p) {
    uint32_t x = 0;
    for (int i = 0; i < 4; ++i) {
        x |= (static_cast<uint32_t>(static_cast<unsigned char>(p[i])) << (i * 8));
    }
    return x;
}

static uint64_t gen_id() {
    return _rng();
}

static void build_data_packet(uint64_t msgid, const std::vector<char>& payload, std::vector<char>& out) {
    out.clear();
    out.push_back(static_cast<char>(MT_DATA));
    append_uint64(out, msgid);
    append_uint32(out, static_cast<uint32_t>(payload.size()));
    out.insert(out.end(), payload.begin(), payload.end());
}

static void build_gossip_packet(const std::vector<uint64_t>& headers, std::vector<char>& out) {
    out.clear();
    out.push_back(static_cast<char>(MT_GOSSIP));
    uint16_t n = static_cast<uint16_t>(headers.size());
    out.push_back(static_cast<char>(n & 0xFF));
    out.push_back(static_cast<char>((n >> 8) & 0xFF));
    for (uint64_t h : headers) {
        append_uint64(out, h);
    }
}

static void build_request_packet(uint64_t msgid, std::vector<char>& out) {
    out.clear();
    out.push_back(static_cast<char>(MT_REQUEST));
    append_uint64(out, msgid);
}

static void build_heartbeat_packet(uint64_t peerid, std::vector<char>& out) {
    out.clear();
    out.push_back(static_cast<char>(MT_HEARTBEAT));
    append_uint64(out, peerid);
}

static bool send_broadcast(
    const std::vector<char>& pkt,
    stats& st,
    const std::string& node_id,
    const std::string& event,
    const std::string& details = ""
) {
    ssize_t sent = sendto(_sockfd, pkt.data(), pkt.size(), 0, (sockaddr*)&_broadcast_addr, sizeof(_broadcast_addr));
    if (sent > 0) {
        st.sent_msgs++;
        st.sent_bytes += static_cast<int>(sent);
        std::ostringstream oss;
        oss << "bytes=" << sent;
        if (!details.empty()) {
            oss << ", " << details;
        }
        log_protocol_event(node_id, event, oss.str());
        return true;
    }
    return false;
}

static bool cache_exists(uint64_t id) {
    std::lock_guard<std::mutex> lg(_cache_mutex);
    return _cache.find(id) != _cache.end();
}

static void cache_insert(uint64_t id, const std::vector<char>& payload) {
    std::lock_guard<std::mutex> lg(_cache_mutex);
    CacheEntry e;
    e.t = std::chrono::steady_clock::now();
    e.payload = payload;
    _cache[id] = std::move(e);
}

static bool cache_get(uint64_t id, std::vector<char>& out_payload) {
    std::lock_guard<std::mutex> lg(_cache_mutex);
    auto it = _cache.find(id);
    if (it == _cache.end()) {
        return false;
    }
    out_payload = it->second.payload;
    return true;
}

static void cache_cleanup() {
    std::lock_guard<std::mutex> lg(_cache_mutex);
    auto now = std::chrono::steady_clock::now();
    for (auto it = _cache.begin(); it != _cache.end();) {
        auto age = std::chrono::duration_cast<std::chrono::seconds>(now - it->second.t).count();
        if (age > CACHE_TTL) {
            it = _cache.erase(it);
        } else {
            ++it;
        }
    }
}

static void neighbor_seen(uint64_t id) {
    std::lock_guard<std::mutex> lg(_neighbors_mutex);
    _neighbors[id] = std::chrono::steady_clock::now();
}

static int neighbor_count() {
    std::lock_guard<std::mutex> lg(_neighbors_mutex);
    return static_cast<int>(_neighbors.size());
}

static void neighbor_cleanup() {
    std::lock_guard<std::mutex> lg(_neighbors_mutex);
    auto now = std::chrono::steady_clock::now();
    for (auto it = _neighbors.begin(); it != _neighbors.end();) {
        auto age = std::chrono::duration_cast<std::chrono::seconds>(now - it->second).count();
        if (age > 5) {
            it = _neighbors.erase(it);
        } else {
            ++it;
        }
    }
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

void cast_worker(stats& st, const std::string& node_id) {
    std::uniform_int_distribution<int> long_jitter(LONG_JITTER_MIN_MS, LONG_JITTER_MAX_MS);

    while (g_running.load()) {
        CastEntry e;
        if (_cast_queue.pop_due(e)) {
            double r = _uniform_01(_rng);
            if (r <= e.prob) {
                std::vector<char> pkt;
                build_data_packet(e.msgid, e.payload, pkt);
                std::ostringstream details;
                details << "msgid=" << e.msgid
                        << ", payload_bytes=" << e.payload.size()
                        << ", reason=" << (e.reason.empty() ? "cast" : e.reason)
                        << ", probability=" << e.prob;
                send_broadcast(pkt, st, node_id, "rapid_data_send", details.str());
            } else {
                e.when = std::chrono::steady_clock::now() + std::chrono::milliseconds(long_jitter(_rng));
                e.prob = 1.0;
                _cast_queue.push(e);
            }
        } else {
            std::this_thread::sleep_for(std::chrono::milliseconds(5));
        }
    }
}

void gossip_worker(stats& st, const std::string& node_id) {
    while (g_running.load()) {
        std::this_thread::sleep_for(std::chrono::milliseconds(GOSSIP_INTERVAL_MS));
        if (!g_running.load()) {
            break;
        }

        std::vector<uint64_t> headers;
        {
            std::lock_guard<std::mutex> lg(_cache_mutex);
            int taken = 0;
            for (auto& kv : _cache) {
                headers.push_back(kv.first);
                taken++;
                if (taken >= 50) {
                    break;
                }
            }
        }

        if (!headers.empty()) {
            std::vector<char> pkt;
            build_gossip_packet(headers, pkt);
            std::ostringstream details;
            details << "entries=" << headers.size();
            send_broadcast(pkt, st, node_id, "rapid_gossip_send", details.str());
        }
    }
}

void heartbeat_worker(uint64_t selfid, stats& st, const std::string& node_id) {
    while (g_running.load()) {
        std::vector<char> pkt;
        build_heartbeat_packet(selfid, pkt);
        std::ostringstream details;
        details << "peerid=" << selfid;
        send_broadcast(pkt, st, node_id, "rapid_heartbeat_send", details.str());
        std::this_thread::sleep_for(std::chrono::milliseconds(HEARTBEAT_INTERVAL_MS));
    }
}

static void send_request_once(uint64_t msgid, stats& st, const std::string& node_id) {
    std::vector<char> pkt;
    build_request_packet(msgid, pkt);
    std::ostringstream details;
    details << "msgid=" << msgid;
    send_broadcast(pkt, st, node_id, "rapid_request_send", details.str());
}

void recv_loop(int sockfd_local, gcounter<int, std::string>& gc, stats& st, const std::string& node_id) {
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

        uint8_t type = static_cast<uint8_t>(buf[0]);

        if (type == MT_DATA) {
            if (n < 1 + 8 + 4) {
                continue;
            }
            uint64_t msgid = read_uint64(buf + 1);
            uint32_t len = read_uint32(buf + 1 + 8);
            if (1 + 8 + 4 + (ssize_t)len > n) {
                continue;
            }

            const char* payload_ptr = buf + 1 + 8 + 4;
            std::vector<char> payload(payload_ptr, payload_ptr + len);
            bool is_new = !cache_exists(msgid);
            {
                std::ostringstream details;
                details << "bytes=" << n
                        << ", msgid=" << msgid
                        << ", payload_bytes=" << len
                        << ", new_cache=" << (is_new ? 1 : 0);
                log_protocol_event(node_id, "rapid_data_recv", details.str());
            }

            if (is_new) {
                cache_insert(msgid, payload);
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

                int ncount = neighbor_count();
                double pr = BETA / std::max(1, ncount);
                if (pr > 1.0) {
                    pr = 1.0;
                }

                CastEntry e;
                e.msgid = msgid;
                e.prob = pr;
                e.when = std::chrono::steady_clock::now() + std::chrono::milliseconds(
                    (int)(_uniform_01(_rng) * (SHORT_JITTER_MAX_MS - SHORT_JITTER_MIN_MS) + SHORT_JITTER_MIN_MS)
                );
                e.payload = payload;
                e.reason = "forward";
                _cast_queue.push(e);
            }
        } else if (type == MT_GOSSIP) {
            if (n < 1 + 2) {
                continue;
            }

            uint16_t nn = static_cast<uint16_t>(
                static_cast<unsigned char>(buf[1]) |
                (static_cast<unsigned char>(buf[2]) << 8)
            );
            {
                std::ostringstream details;
                details << "bytes=" << n << ", entries=" << nn;
                log_protocol_event(node_id, "rapid_gossip_recv", details.str());
            }

            const char* p = buf + 3;
            for (int i = 0; i < (int)nn; ++i) {
                if (p + 8 > buf + n) {
                    break;
                }
                uint64_t hid = read_uint64(p);
                p += 8;

                if (!cache_exists(hid)) {
                    int jitter = SHORT_JITTER_MIN_MS + (_rng() % (SHORT_JITTER_MAX_MS - SHORT_JITTER_MIN_MS + 1));
                    std::this_thread::sleep_for(std::chrono::milliseconds(jitter));
                    send_request_once(hid, st, node_id);
                }
            }
        } else if (type == MT_REQUEST) {
            if (n < 1 + 8) {
                continue;
            }
            uint64_t hid = read_uint64(buf + 1);
            {
                std::ostringstream details;
                details << "bytes=" << n << ", msgid=" << hid;
                log_protocol_event(node_id, "rapid_request_recv", details.str());
            }
            std::vector<char> payload;
            if (cache_get(hid, payload)) {
                std::vector<char> pkt;
                build_data_packet(hid, payload, pkt);
                std::ostringstream details;
                details << "msgid=" << hid
                        << ", payload_bytes=" << payload.size()
                        << ", reason=request_response";
                send_broadcast(pkt, st, node_id, "rapid_data_send", details.str());
            }
        } else if (type == MT_HEARTBEAT) {
            if (n < 1 + 8) {
                continue;
            }
            uint64_t pid = read_uint64(buf + 1);
            {
                std::ostringstream details;
                details << "bytes=" << n << ", peerid=" << pid;
                log_protocol_event(node_id, "rapid_heartbeat_recv", details.str());
            }
            neighbor_seen(pid);
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
            << ", sent_msgs=" << st.sent_msgs
            << ", recv_msgs=" << st.recv_msgs
            << ", sent_bytes=" << st.sent_bytes
            << ", recv_bytes=" << st.recv_bytes << "\n";
        log.flush();
    }
}

void periodic_maintenance() {
    while (g_running.load()) {
        cache_cleanup();
        neighbor_cleanup();
        std::this_thread::sleep_for(std::chrono::seconds(5));
    }
}

void run_random_mode(const node_config& nc, gcounter<int, std::string>& gc) {
    double ops_per_sec = nc.ops_per_sec;
    double duration = nc.duration;

    std::default_random_engine gen(nc.seed);
    std::exponential_distribution<double> expd(ops_per_sec);
    std::uniform_int_distribution<int> inc_dist(1, 1);

    gcounter<int, std::string> local_since_last;

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

void local_periodic_dissemination(const node_config& nc, stats& st) {
    uint64_t last_local_msgid = 0;
    bool has_last = false;

    auto next = std::chrono::steady_clock::now();

    while (g_running.load()) {
        next += std::chrono::duration_cast<std::chrono::steady_clock::duration>(std::chrono::duration<double>(nc.dissemination_interval));
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

        if (has_new) {
            std::string ser = to_send.serialize();
            std::vector<char> payload(ser.begin(), ser.end());
            uint64_t msgid = gen_id();

            cache_insert(msgid, payload);

            CastEntry e;
            e.msgid = msgid;
            e.prob = 1.0;
            e.when = std::chrono::steady_clock::now();
            e.payload = payload;
            e.reason = "local_new";
            _cast_queue.push(e);

            last_local_msgid = msgid;
            has_last = true;
        } else {
            if (has_last) {
                std::vector<char> payload;
                if (cache_get(last_local_msgid, payload)) {
                    CastEntry e;
                    e.msgid = last_local_msgid;
                    e.prob = 1.0;
                    e.when = std::chrono::steady_clock::now();
                    e.payload = payload;
                    e.reason = "local_repeat";
                    _cast_queue.push(e);
                }
            }
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
    uint64_t selfid = gen_id();

    std::thread t_recv(recv_loop, _sockfd, std::ref(gc), std::ref(st), nc.id);
    std::thread t_cast(cast_worker, std::ref(st), nc.id);
    std::thread t_gossip(gossip_worker, std::ref(st), nc.id);
    std::thread t_hb(heartbeat_worker, selfid, std::ref(st), nc.id);
    std::thread t_mon(monitor_loop, std::ref(gc), std::ref(st), nc.monitor_interval, nc.log_file);
    std::thread t_maint(periodic_maintenance);
    std::thread t_local(local_periodic_dissemination, std::cref(nc), std::ref(st));

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
    if (t_maint.joinable()) {
        t_maint.join();
    }
    if (t_mon.joinable()) {
        t_mon.join();
    }
    if (t_hb.joinable()) {
        t_hb.join();
    }
    if (t_gossip.joinable()) {
        t_gossip.join();
    }
    if (t_cast.joinable()) {
        t_cast.join();
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
