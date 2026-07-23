#include <atomic>
#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <fstream>
#include <functional>
#include <iostream>
#include <limits>
#include <mutex>
#include <nlohmann/json.hpp>
#include <random>
#include <string>
#include <thread>
#include <vector>

#include "../common/delta-crdts.cc"
#include "../common/lib_gossip/gossip.hpp"

using json = nlohmann::json;
using gossip::rapid::Rapid;

namespace {

constexpr std::size_t MSG_MAX = 8192;
constexpr double BETA = 2.5;
constexpr int CACHE_TTL_SECONDS = 60;
constexpr int HEARTBEAT_INTERVAL_MS = 1000;
constexpr int GOSSIP_INTERVAL_MS = 1000;
constexpr int SHORT_JITTER_MIN_MS = 10;
constexpr int SHORT_JITTER_MAX_MS = 40;
constexpr int LONG_JITTER_MIN_MS = 200;
constexpr int LONG_JITTER_MAX_MS = 600;
constexpr int PORT_DEFAULT = 9000;

std::atomic<bool> g_running{true};

std::mutex gc_mutex;
std::mutex pending_mutex;
std::mutex event_log_mutex;

gcounter<int, std::string> pending_state;
bool has_pending = false;
std::ofstream event_log;

struct NodeConfig {
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
    std::string broadcast_address{"255.255.255.255"};
    std::size_t delivery_queue_capacity{1024};
};

double now_ts() {
    return std::chrono::duration<double>(
               std::chrono::steady_clock::now().time_since_epoch())
        .count();
}

void print_config(const NodeConfig& config) {
    std::cout << "===== RAPID (GOSSIP) APPLICATION =====\n";
    std::cout << "node id: " << config.id << "\n";
    std::cout << "listen: " << config.listen_addr << "\n";
    std::cout << "broadcast: " << config.broadcast_address << "\n";
    std::cout << "ops_per_sec: " << config.ops_per_sec << "\n";
    std::cout << "duration: " << config.duration << "\n";
    std::cout << "seed: " << config.seed << "\n";
    std::cout << "monitor_interval: " << config.monitor_interval << "\n";
    std::cout << "dissemination_interval: " << config.dissemination_interval << "\n";
    std::cout << "delivery_queue_capacity: " << config.delivery_queue_capacity << "\n";
    std::cout << "peers: ";
    for (const auto& peer : config.peers) {
        std::cout << peer << " ";
    }
    std::cout << "\n";
}

NodeConfig load_config(const std::string& path, const std::string& id) {
    std::ifstream input(path);
    json document = json::parse(input);

    NodeConfig config;
    config.id = id;
    try {
        config.listen_addr = document.at("address").at(id);
    } catch (...) {
        return config;
    }

    for (const auto& [peer_id, address] : document["address"].items()) {
        if (peer_id != id) {
            config.peers.push_back(address);
        }
    }

    config.ops_per_sec = document.value("ops_per_sec", 1.0);
    config.duration = document.value("duration", 10);
    config.distribution = document.value("distribution", "uniform");
    std::random_device random_device;
    config.seed = document.value("seed", static_cast<int>(random_device())) +
                  std::atoi(id.c_str());
    config.monitor_interval = document.value("monitor_interval", 1.0);
    config.cooldown = document.value("cooldown", 10.0);
    config.dissemination_interval = document.value("dissemination_interval", 0.5);
    config.broadcast_address = document.value("broadcast_address", "255.255.255.255");
    config.delivery_queue_capacity =
        document.value("rapid_delivery_queue_capacity", std::size_t{1024});

    std::string log_dir = document.value("log_dir", ".");
    if (!log_dir.empty() && log_dir.back() != '/') {
        log_dir.push_back('/');
    }
    config.log_file = log_dir + "node_" + id + ".log";
    return config;
}

gossip::rapid::PeerId to_peer_id(const std::string& id) {
    try {
        std::size_t used = 0;
        const auto parsed = std::stoull(id, &used, 10);
        if (used == id.size() &&
            parsed < std::numeric_limits<gossip::rapid::PeerId>::max()) {
            return static_cast<gossip::rapid::PeerId>(parsed + 1);
        }
    } catch (...) {
    }

    auto hashed =
        static_cast<gossip::rapid::PeerId>(
            std::hash<std::string>{}(id));
    return hashed == 0 ? 1 : hashed;
}

bool parse_port(const std::string& address, std::uint16_t& port) {
    const auto separator = address.rfind(':');
    if (separator == std::string::npos || separator + 1 == address.size()) {
        return false;
    }
    try {
        std::size_t used = 0;
        const auto parsed = std::stoul(address.substr(separator + 1), &used, 10);
        if (used != address.size() - separator - 1 || parsed == 0 || parsed > 65535) {
            return false;
        }
        port = static_cast<std::uint16_t>(parsed);
        return true;
    } catch (...) {
        return false;
    }
}

gossip::rapid::Config make_rapid_config(const NodeConfig& app_config) {
    gossip::rapid::Config config;
    config.local_peer_id = to_peer_id(app_config.id);
    config.bind_address = "0.0.0.0";
    config.broadcast_address = app_config.broadcast_address;
    config.port = PORT_DEFAULT;
    (void)parse_port(app_config.listen_addr, config.port);
    config.beta = BETA;
    config.seed = static_cast<std::uint64_t>(static_cast<std::uint32_t>(app_config.seed));
    config.max_packet_size = MSG_MAX;
    config.delivery_queue_capacity = app_config.delivery_queue_capacity;
    config.cache_ttl = std::chrono::seconds(CACHE_TTL_SECONDS);
    config.heartbeat_interval = std::chrono::milliseconds(HEARTBEAT_INTERVAL_MS);
    config.gossip_interval = std::chrono::milliseconds(GOSSIP_INTERVAL_MS);
    config.short_jitter_min = std::chrono::milliseconds(SHORT_JITTER_MIN_MS);
    config.short_jitter_max = std::chrono::milliseconds(SHORT_JITTER_MAX_MS);
    config.long_jitter_min = std::chrono::milliseconds(LONG_JITTER_MIN_MS);
    config.long_jitter_max = std::chrono::milliseconds(LONG_JITTER_MAX_MS);
    return config;
}

void log_apply(const std::string& node_id, int total) {
    std::lock_guard<std::mutex> lock(event_log_mutex);
    event_log << std::fixed << now_ts()
              << ", event=op_apply, node=" << node_id
              << ", total=" << total << "\n";
}

void delivery_loop(Rapid& rapid,
                   gcounter<int, std::string>& counter,
                   const std::string& node_id) {
    while (g_running.load()) {
        auto message = rapid.receive();
        if (!message) {
            break;
        }

        try {
            const std::string serialized(message->payload.begin(), message->payload.end());
            auto received_counter = gcounter<int, std::string>::deserialize(serialized);
            int total = 0;
            {
                std::lock_guard<std::mutex> lock(gc_mutex);
                counter.join(received_counter);
                total = counter.read();
            }
            log_apply(node_id, total);
        } catch (...) {
            // A malformed application payload is ignored as in the published app.
        }
    }
}

void write_monitor_sample(std::ofstream& log,
                          gcounter<int, std::string>& counter,
                          const Rapid& rapid) {
    int local = 0;
    int total = 0;
    {
        std::lock_guard<std::mutex> lock(gc_mutex);
        local = counter.local();
        total = counter.read();
    }

    const gossip::rapid::Stats stats = rapid.stats();
    log << std::fixed << now_ts()
        << ", local=" << local << ", total=" << total
        << ", sent_msgs=" << stats.sent_packets
        << ", recv_msgs=" << stats.received_packets
        << ", sent_bytes=" << stats.sent_bytes
        << ", recv_bytes=" << stats.received_bytes
        << ", delivery_queue_overflows=" << stats.delivery_queue_overflows
        << ", rejected_disseminations=" << stats.rejected_disseminations
        << ", socket_errors=" << stats.socket_errors
        << ", malformed_packets=" << stats.malformed_packets
        << ", known_neighbors=" << stats.known_neighbors
        << ", pending_deliveries=" << stats.pending_deliveries
        << "\n";
    log.flush();
}

void monitor_loop(gcounter<int, std::string>& counter,
                  const Rapid& rapid,
                  double interval,
                  const std::string& logfile) {
    std::ofstream log(logfile, std::ios::trunc);
    while (g_running.load()) {
        std::this_thread::sleep_for(std::chrono::duration<double>(interval));
        if (!g_running.load()) {
            break;
        }
        write_monitor_sample(log, counter, rapid);
    }
    write_monitor_sample(log, counter, rapid);
}

void run_random_mode(const NodeConfig& config, gcounter<int, std::string>& counter) {
    std::default_random_engine generator(static_cast<unsigned int>(config.seed));
    std::exponential_distribution<double> wait_distribution(config.ops_per_sec);
    std::uniform_int_distribution<int> increment_distribution(1, 1);
    gcounter<int, std::string> local_since_last;

    const auto start = std::chrono::steady_clock::now();
    while (g_running.load()) {
        const double elapsed = std::chrono::duration<double>(
                                   std::chrono::steady_clock::now() - start)
                                   .count();
        if (elapsed > config.duration) {
            break;
        }

        std::this_thread::sleep_for(
            std::chrono::duration<double>(wait_distribution(generator)));
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
            const auto timestamp = now_ts();
            event_log << std::fixed << timestamp
                      << ", event=op_create, node=" << config.id
                      << ", delta_size=" << increment << "\n";
            event_log << std::fixed << timestamp
                      << ", event=op_apply, node=" << config.id
                      << ", total=" << total << "\n";
        }

        local_since_last.join(delta);
        {
            std::lock_guard<std::mutex> lock(pending_mutex);
            if (has_pending) {
                pending_state.join(local_since_last);
            } else {
                pending_state = local_since_last;
                has_pending = true;
            }
            local_since_last = gcounter<int, std::string>();
        }
    }
}

void local_periodic_dissemination(const NodeConfig& config, Rapid& rapid) {
    gossip::rapid::Bytes last_payload;
    bool has_last_payload = false;
    auto next = std::chrono::steady_clock::now();

    while (g_running.load()) {
        next += std::chrono::duration_cast<std::chrono::steady_clock::duration>(
            std::chrono::duration<double>(config.dissemination_interval));
        std::this_thread::sleep_until(next);
        if (!g_running.load()) {
            break;
        }

        gcounter<int, std::string> to_send;
        bool has_new_payload = false;
        {
            std::lock_guard<std::mutex> lock(pending_mutex);
            if (has_pending) {
                to_send = pending_state;
                pending_state = gcounter<int, std::string>();
                has_pending = false;
                has_new_payload = true;
            }
        }

        if (has_new_payload) {
            const std::string serialized = to_send.serialize();
            last_payload.assign(serialized.begin(), serialized.end());
            has_last_payload = true;
        }

        // The application owns the periodic trigger. By the Rapid facade
        // contract, every trigger is a new logical message, even when the
        // latest CRDT payload is byte-identical to the previous one.
        if (has_last_payload && !rapid.disseminate(last_payload) && g_running.load()) {
            std::cerr << "RAPID dissemination rejected: " << rapid.last_error() << "\n";
        }
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
    std::uint16_t port = 0;
    if (config.listen_addr.empty() || !parse_port(config.listen_addr, port)) {
        std::cerr << "Invalid listen address for node " << node_id << "\n";
        return 1;
    }
    print_config(config);

    event_log.open(config.log_file + ".events", std::ios::trunc);
    event_log.setf(std::ios::unitbuf);
    if (!event_log.is_open()) {
        return 1;
    }

    Rapid rapid(make_rapid_config(config));
    if (!rapid.start()) {
        std::cerr << "Failed to start RAPID: " << rapid.last_error() << "\n";
        return 1;
    }

    gcounter<int, std::string> counter(config.id);
    std::thread delivery_thread(delivery_loop,
                                std::ref(rapid),
                                std::ref(counter),
                                std::cref(config.id));
    std::thread monitor_thread(monitor_loop,
                               std::ref(counter),
                               std::cref(rapid),
                               config.monitor_interval,
                               std::cref(config.log_file));
    std::thread local_thread(local_periodic_dissemination,
                             std::cref(config),
                             std::ref(rapid));

    run_random_mode(config, counter);
    {
        std::lock_guard<std::mutex> lock(event_log_mutex);
        event_log << std::fixed << now_ts()
                  << ", event=ops_finished, node=" << config.id << "\n";
    }

    std::this_thread::sleep_for(std::chrono::duration<double>(config.cooldown));

    g_running.store(false);
    rapid.stop();

    if (local_thread.joinable()) {
        local_thread.join();
    }
    if (monitor_thread.joinable()) {
        monitor_thread.join();
    }
    if (delivery_thread.joinable()) {
        delivery_thread.join();
    }

    const gossip::rapid::Stats final_stats = rapid.stats();
    if (final_stats.delivery_queue_overflows > 0 || final_stats.socket_errors > 0) {
        std::cerr << "RAPID diagnostics: delivery_queue_overflows="
                  << final_stats.delivery_queue_overflows
                  << ", socket_errors=" << final_stats.socket_errors << "\n";
    }

    {
        std::lock_guard<std::mutex> lock(event_log_mutex);
        event_log.flush();
        event_log.close();
    }
    return 0;
}
