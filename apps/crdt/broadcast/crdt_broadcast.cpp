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
#include <sstream>
#include <algorithm>
#include <net/if.h>

#include "../common/delta-crdts.cc"
#include "../common/spatial_coverage.hpp"

using json = nlohmann::json;

constexpr size_t LEGACY_MSG_MAX = 4096;

static std::atomic<bool> g_running{true};

std::mutex _gc_mutex;
std::mutex _delta_mutex;
std::mutex _event_log_mutex;
std::ofstream _event_log;
mace::coverage::Bytes _pending_snapshot;
bool _has_pending_snapshot = false;

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
    bool spatial_coverage{false};
    mace::coverage::GridSpec grid;
    std::chrono::milliseconds position_poll_interval{
        mace::coverage::kDefaultPollInterval};
    std::chrono::milliseconds gps_timeout{
        mace::coverage::kDefaultGpsTimeout};
    std::string gps_socket_path;
    std::size_t max_datagram_bytes{
        mace::coverage::kDefaultMaxDatagramBytes};
};

struct stats {
    std::atomic<int> sent_msgs{0};
    std::atomic<int> recv_msgs{0};
    std::atomic<int> sent_bytes{0};
    std::atomic<int> recv_bytes{0};
};

inline double event_ts();

void publish_spatial_snapshot(const node_config& nc,
                              const mace::coverage::StateUpdate& update) {
    const double timestamp = update.timestamp_unix_s;
    {
        std::lock_guard<std::mutex> lock(_event_log_mutex);
        _event_log << std::fixed << timestamp
                   << ", event=local_coverage, node=" << nc.id
                   << ", cell_ids_added="
                   << mace::coverage::format_cell_ids(update.added)
                   << ", replica_size=" << update.replica_size
                   << ", replica_version=" << update.mutation_sequence
                   << "\n";
        _event_log << std::fixed << timestamp
                   << ", event=dissemination_trigger, node=" << nc.id
                   << ", replica_size=" << update.replica_size
                   << ", serialized_state_size=" << update.snapshot.size()
                   << ", replica_version=" << update.mutation_sequence
                   << "\n";
    }
    {
        std::lock_guard<std::mutex> lock(_delta_mutex);
        // The primitive's periodic publisher coalesces monotonic snapshots by
        // retaining the newest full state, matching its previous batching.
        _pending_snapshot = update.snapshot;
        _has_pending_snapshot = true;
    }
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
    nc.dissemination_interval = cfg.value("dissemination_interval", 0.5);
    nc.seed = cfg.value("seed", 1) + std::atoi(id.c_str());
    nc.monitor_interval = cfg.value("monitor_interval", 1.0);
    std::string log_dir = cfg.value("log_dir", ".");
    if (!log_dir.empty() && log_dir.back() != '/') {
        log_dir.push_back('/');
    }
    nc.log_file = log_dir + "node_" + id + ".log";
    nc.cooldown = cfg.value("cooldown", 10);

    const std::string workload = cfg.value("workload", std::string{"gcounter"});
    if (workload != "gcounter" && workload != "spatial_coverage") {
        throw std::invalid_argument(
            "workload must be either gcounter or spatial_coverage");
    }
    nc.spatial_coverage = workload == "spatial_coverage";
    if (nc.spatial_coverage) {
        if (nc.dissemination_interval <= 0.0 || nc.monitor_interval <= 0.0) {
            throw std::invalid_argument(
                "dissemination and monitor intervals must be positive");
        }
        const auto& grid = cfg.contains("grid")
                               ? cfg.at("grid")
                               : cfg.at("grid_spec");
        nc.grid = mace::coverage::GridSpec{
            grid.at("origin_x_m").get<double>(),
            grid.at("origin_y_m").get<double>(),
            grid.at("width_m").get<double>(),
            grid.at("height_m").get<double>(),
            grid.at("rows").get<std::uint32_t>(),
            grid.at("cols").get<std::uint32_t>()};
        const int poll_ms = cfg.value(
            "position_poll_interval_ms", cfg.value("poll_interval_ms", 100));
        const int gps_timeout_ms = cfg.value("gps_timeout_ms", 50);
        if (poll_ms <= 0 || gps_timeout_ms <= 0) {
            throw std::invalid_argument("GPS polling intervals must be positive");
        }
        nc.position_poll_interval = std::chrono::milliseconds(poll_ms);
        nc.gps_timeout = std::chrono::milliseconds(gps_timeout_ms);
        nc.max_datagram_bytes = cfg.value(
            "max_datagram_bytes",
            cfg.value("max_payload_bytes",
                      mace::coverage::kDefaultMaxDatagramBytes));
        if (nc.max_datagram_bytes >
            mace::coverage::kDefaultMaxDatagramBytes) {
            throw std::invalid_argument("max_datagram_bytes cannot exceed 1200");
        }
        std::string gps_template = cfg.value(
            "gps_socket_template",
            cfg.value("gps_socket_path",
                      std::string{"/tmp/node{id}_gps.sock"}));
        const std::string marker = "{id}";
        std::size_t marker_position = 0;
        while ((marker_position = gps_template.find(marker, marker_position)) !=
               std::string::npos) {
            gps_template.replace(marker_position, marker.size(), id);
            marker_position += id.size();
        }
        nc.gps_socket_path = std::move(gps_template);
        mace::coverage::validate_datagram_budget(
            nc.grid, nc.max_datagram_bytes, 0);
    }
    return nc;
}

inline double now_ts() {
    return std::chrono::duration<double>(std::chrono::steady_clock::now().time_since_epoch()).count();
}

inline double event_ts() {
    return mace::coverage::unix_time_seconds();
}

void stop_handler(int) {
    g_running.store(false);
}

void log_spatial_event_at(double timestamp,
                          const std::string& node_id,
                          const std::string& event,
                          const std::string& details = "") {
    std::lock_guard<std::mutex> lock(_event_log_mutex);
    _event_log << std::fixed << timestamp
               << ", event=" << event
               << ", node=" << node_id;
    if (!details.empty()) {
        _event_log << ", " << details;
    }
    _event_log << "\n";
}

void log_spatial_event(const std::string& node_id,
                       const std::string& event,
                       const std::string& details = "") {
    log_spatial_event_at(event_ts(), node_id, event, details);
}

void print_config(const node_config& nc) {
    std::cout << "Node: " << nc.id << " Addr: " << nc.listen_addr << "\n";
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

static bool setup_broadcast_socket(const std::string& listen_addr, int& sockfd_out, sockaddr_in& bcast_out) {
    auto pos = listen_addr.find(':');
    if (pos == std::string::npos) {
        return false;
    }
    int listen_port = 0;
    try {
        listen_port = std::stoi(listen_addr.substr(pos + 1));
    } catch (...) {
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

    bind_to_bat0(sockfd);
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
    inet_pton(AF_INET, "255.255.255.255", &bcast.sin_addr);

    sockfd_out = sockfd;
    bcast_out = bcast;
    return true;
}

void dissemination_loop(
    int sockfd,
    sockaddr_in broadcast_addr,
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

        ssize_t sent = sendto(
            sockfd,
            payload.data(),
            payload.size(),
            0,
            (sockaddr*)&broadcast_addr,
            sizeof(broadcast_addr)
        );

        if (sent > 0) {
            st.sent_msgs++;
            st.sent_bytes += static_cast<int>(sent);
        }
    }
}

void recv_loop(int sockfd, gcounter<int, std::string>& gc, stats& st, const std::string& node_id) {
    char buffer[LEGACY_MSG_MAX];
    sockaddr_in src{};
    socklen_t srclen = sizeof(src);

    while (g_running.load()) {
        ssize_t n = recvfrom(sockfd, buffer, LEGACY_MSG_MAX, 0, (sockaddr*)&src, &srclen);
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
                _event_log << std::fixed << now_ts() << ", event=op_apply, node=" << node_id << ", total=" << total << "\n";
            }
            st.recv_msgs++;
            st.recv_bytes += static_cast<int>(n);
        } catch (...) {
            continue;
        }
    }
}

void spatial_dissemination_loop(
    int sockfd,
    sockaddr_in broadcast_addr,
    stats& st,
    const node_config& nc
) {
    mace::coverage::Bytes last_payload;
    int retriggers_left = 0;
    const int retriggers_budget = 1;
    auto next = std::chrono::steady_clock::now();

    while (g_running.load()) {
        next += std::chrono::duration_cast<std::chrono::steady_clock::duration>(
            std::chrono::duration<double>(nc.dissemination_interval));
        if (mace::coverage::sleep_until_or_stopped(g_running, next)) {
            break;
        }

        mace::coverage::Bytes payload;
        bool has_new = false;
        {
            std::lock_guard<std::mutex> lock(_delta_mutex);
            if (_has_pending_snapshot) {
                payload = _pending_snapshot;
                _pending_snapshot.clear();
                _has_pending_snapshot = false;
                has_new = true;
            }
        }
        if (!has_new) {
            if (retriggers_left > 0 && !last_payload.empty()) {
                payload = last_payload;
                --retriggers_left;
            } else {
                continue;
            }
        } else {
            last_payload = payload;
            retriggers_left = retriggers_budget;
        }

        if (payload.size() > nc.max_datagram_bytes) {
            log_spatial_event(nc.id, "network_transmit_rejected",
                              "reason=payload_budget");
            continue;
        }
        const ssize_t sent = sendto(
            sockfd,
            payload.data(),
            payload.size(),
            0,
            reinterpret_cast<sockaddr*>(&broadcast_addr),
            sizeof(broadcast_addr));
        if (sent > 0) {
            st.sent_msgs++;
            st.sent_bytes += static_cast<int>(sent);
            std::ostringstream details;
            details << "bytes=" << sent
                    << ", kind=" << (has_new ? "publish" : "retransmission");
            log_spatial_event(nc.id, "network_transmit", details.str());
        }
    }
}

void spatial_recv_loop(int sockfd,
                       mace::coverage::CoverageWorkload& workload,
                       stats& st,
                       const node_config& nc) {
    std::vector<std::uint8_t> buffer(nc.max_datagram_bytes);
    sockaddr_in src{};
    socklen_t source_length = sizeof(src);

    while (g_running.load()) {
        const ssize_t received = recvfrom(
            sockfd,
            buffer.data(),
            buffer.size(),
            MSG_TRUNC,
            reinterpret_cast<sockaddr*>(&src),
            &source_length);
        if (received <= 0) {
            if (!g_running.load()) {
                break;
            }
            if (received < 0 &&
                (errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR)) {
                continue;
            }
            if (received < 0 && errno == EBADF) {
                break;
            }
            continue;
        }

        st.recv_msgs++;
        st.recv_bytes += static_cast<int>(received);
        if (static_cast<std::size_t>(received) > nc.max_datagram_bytes) {
            std::ostringstream details;
            details << "bytes=" << received << ", status=oversized";
            log_spatial_event(nc.id, "network_receive", details.str());
            continue;
        }

        try {
            const auto update = workload.merge_remote(
                buffer.data(), static_cast<std::size_t>(received));
            {
                std::ostringstream details;
                details << "bytes=" << received << ", status=accepted";
                log_spatial_event(nc.id, "network_receive", details.str());
            }
            if (update.changed) {
                std::ostringstream details;
                details << "cell_ids_added="
                        << mace::coverage::format_cell_ids(update.added)
                        << ", replica_size=" << update.replica_size
                        << ", replica_version="
                        << update.mutation_sequence;
                log_spatial_event_at(
                    update.timestamp_unix_s,
                    nc.id,
                    "remote_merge",
                    details.str());
            }
        } catch (const std::exception&) {
            std::ostringstream details;
            details << "bytes=" << received << ", status=malformed";
            log_spatial_event(nc.id, "network_receive", details.str());
        }
    }
}

void spatial_monitor_loop(mace::coverage::CoverageWorkload& workload,
                          stats& st,
                          double interval,
                          const std::string& logfile) {
    std::ofstream log(logfile, std::ios::trunc);
    const auto write_sample = [&] {
        log << std::fixed << event_ts()
            << ", replica_size=" << workload.size()
            << ", sent_msgs=" << st.sent_msgs
            << ", recv_msgs=" << st.recv_msgs
            << ", sent_bytes=" << st.sent_bytes
            << ", recv_bytes=" << st.recv_bytes << "\n";
        log.flush();
    };
    while (g_running.load()) {
        if (mace::coverage::sleep_for_or_stopped(
                g_running, std::chrono::duration<double>(interval))) {
            break;
        }
        write_sample();
    }
    write_sample();
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
    double next_event_s = 0.0;

    while (g_running.load()) {
        next_event_s += expd(gen);
        if (next_event_s > duration) {
            break;
        }

        const auto event_time =
            start + std::chrono::duration_cast<std::chrono::steady_clock::duration>(
                        std::chrono::duration<double>(next_event_s));
        std::this_thread::sleep_until(event_time);
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
            _event_log << std::fixed << now_ts() << ", event=op_create, node=" << nc.id << ", delta_size=" << val << "\n";
            _event_log << std::fixed << now_ts() << ", event=op_apply, node=" << nc.id << ", total=" << total << "\n";
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

    std::signal(SIGINT, stop_handler);
    std::signal(SIGTERM, stop_handler);

    node_config nc;
    try {
        nc = load_config(cfgfile, node_id);
    } catch (const std::exception& error) {
        std::cerr << "Invalid configuration: " << error.what() << "\n";
        return 1;
    }
    if (nc.listen_addr.empty()) {
        return 1;
    }
    print_config(nc);

    _event_log.open(nc.log_file + ".events", std::ios::trunc);
    _event_log.setf(std::ios::unitbuf);

    int sockfd = -1;
    sockaddr_in bcast_addr{};
    if (!setup_broadcast_socket(nc.listen_addr, sockfd, bcast_addr)) {
        return 1;
    }

    if (nc.spatial_coverage) {
        mace::coverage::CoverageWorkload workload(nc.grid);
        mace::coverage::UnixGpsPositionSource position_source(
            nc.gps_socket_path, nc.gps_timeout);
        stats st;

        {
            std::ostringstream details;
            details << "workload=spatial_coverage"
                    << ", rows=" << nc.grid.rows
                    << ", cols=" << nc.grid.cols
                    << ", max_datagram_bytes=" << nc.max_datagram_bytes
                    << ", gps_socket=" << nc.gps_socket_path;
            log_spatial_event(nc.id, "application_start", details.str());
        }

        std::thread receiver(
            spatial_recv_loop,
            sockfd,
            std::ref(workload),
            std::ref(st),
            std::cref(nc));
        std::thread monitor(
            spatial_monitor_loop,
            std::ref(workload),
            std::ref(st),
            nc.monitor_interval,
            std::cref(nc.log_file));
        std::thread disseminator(
            spatial_dissemination_loop,
            sockfd,
            bcast_addr,
            std::ref(st),
            std::cref(nc));
        std::thread poller([&] {
            mace::coverage::polling_loop(
                position_source,
                workload,
                g_running,
                nc.position_poll_interval,
                [&](const mace::coverage::StateUpdate& update) {
                    publish_spatial_snapshot(nc, update);
                });
        });

        while (g_running.load()) {
            std::this_thread::sleep_for(std::chrono::milliseconds(100));
        }
        shutdown(sockfd, SHUT_RDWR);
        close(sockfd);

        if (poller.joinable()) {
            poller.join();
        }
        if (disseminator.joinable()) {
            disseminator.join();
        }
        if (receiver.joinable()) {
            receiver.join();
        }
        if (monitor.joinable()) {
            monitor.join();
        }
        log_spatial_event(nc.id, "application_stop",
                          "replica_size=" + std::to_string(workload.size()));
        {
            std::lock_guard<std::mutex> lock(_event_log_mutex);
            _event_log.flush();
            _event_log.close();
        }
        return 0;
    }

    gcounter<int, std::string> gc(nc.id);
    gcounter<int, std::string> delta_buffer;
    stats st;

    std::thread t_recv(recv_loop, sockfd, std::ref(gc), std::ref(st), nc.id);
    std::thread t_mon(monitor_loop, std::ref(gc), std::ref(st), nc.monitor_interval, nc.log_file);
    std::thread t_diss(dissemination_loop, sockfd, bcast_addr, std::ref(delta_buffer), std::ref(st), nc.dissemination_interval);

    run_random_mode(nc, gc, delta_buffer);

    {
        std::lock_guard<std::mutex> lk(_event_log_mutex);
        _event_log << std::fixed << now_ts() << ", event=ops_finished, node=" << nc.id << "\n";
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
