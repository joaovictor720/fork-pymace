#include <algorithm>
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
#include <sstream>
#include <stdexcept>
#include <string>
#include <thread>
#include <utility>
#include <vector>

#include "../common/delta-crdts.cc"
#include "../common/lib_gossip/gossip.hpp"

using json = nlohmann::json;
using gossip::trickle::StateRelation;
using gossip::trickle::Trickle;

namespace {

constexpr std::size_t MSG_MAX = 16384;
constexpr int PORT_DEFAULT = 9000;
constexpr std::uint32_t TRICKLE_K_DEFAULT = 1;
constexpr std::uint32_t TRICKLE_IMAX_TICKS_DEFAULT = 16;

std::atomic<bool> g_running{true};
std::mutex event_log_mutex;
std::ofstream event_log;

struct NodeConfig {
    std::string id;
    std::uint32_t node_index{0};
    std::uint32_t node_count{0};
    std::string listen_addr;
    double ops_per_sec{1.0};
    int duration{10};
    std::string distribution{"uniform"};
    int seed{0};
    std::string log_file;
    double monitor_interval{1.0};
    double dissemination_interval{0.5};
    double cooldown{10.0};
    std::uint32_t trickle_k{TRICKLE_K_DEFAULT};
    std::uint32_t trickle_imax_ticks{TRICKLE_IMAX_TICKS_DEFAULT};
    std::string broadcast_address{"255.255.255.255"};
    std::size_t delivery_queue_capacity{1024};
};

double now_ts() {
    return std::chrono::duration<double>(
               std::chrono::steady_clock::now().time_since_epoch())
        .count();
}

void append_u32(gossip::Bytes& output, std::uint32_t value) {
    for (int i = 0; i < 4; ++i) {
        output.push_back(
            static_cast<std::uint8_t>((value >> (i * 8)) & 0xff));
    }
}

bool read_u32(const gossip::Bytes& input,
              std::size_t& position,
              std::uint32_t& value) {
    if (position + 4 > input.size()) {
        return false;
    }
    value = 0;
    for (int i = 0; i < 4; ++i) {
        value |= static_cast<std::uint32_t>(
                     input[position + static_cast<std::size_t>(i)])
                 << (i * 8);
    }
    position += 4;
    return true;
}

std::vector<std::uint32_t> decode_summary(const gossip::Bytes& bytes) {
    std::size_t position = 0;
    std::uint32_t count = 0;
    if (!read_u32(bytes, position, count) ||
        static_cast<std::size_t>(count) >
            (bytes.size() - position) / sizeof(std::uint32_t) ||
        position + static_cast<std::size_t>(count) * sizeof(std::uint32_t) !=
            bytes.size()) {
        throw std::invalid_argument("invalid GCounter summary");
    }

    std::vector<std::uint32_t> summary;
    summary.reserve(count);
    for (std::uint32_t i = 0; i < count; ++i) {
        std::uint32_t value = 0;
        if (!read_u32(bytes, position, value)) {
            throw std::invalid_argument("truncated GCounter summary");
        }
        summary.push_back(value);
    }
    return summary;
}

std::vector<std::pair<std::uint32_t, std::uint32_t>> decode_update(
    const gossip::Bytes& bytes) {
    std::size_t position = 0;
    std::uint32_t count = 0;
    constexpr std::size_t entry_size = 2 * sizeof(std::uint32_t);
    if (!read_u32(bytes, position, count) ||
        static_cast<std::size_t>(count) >
            (bytes.size() - position) / entry_size ||
        position + static_cast<std::size_t>(count) * entry_size !=
            bytes.size()) {
        throw std::invalid_argument("invalid GCounter update");
    }

    std::vector<std::pair<std::uint32_t, std::uint32_t>> entries;
    entries.reserve(count);
    for (std::uint32_t i = 0; i < count; ++i) {
        std::uint32_t index = 0;
        std::uint32_t value = 0;
        if (!read_u32(bytes, position, index) ||
            !read_u32(bytes, position, value)) {
            throw std::invalid_argument("truncated GCounter update");
        }
        entries.emplace_back(index, value);
    }
    return entries;
}

std::string serialize_as_gcounter(
    const std::vector<std::pair<std::uint32_t, std::uint32_t>>& entries) {
    std::ostringstream output;
    const std::size_t map_size = entries.size();
    output.write(
        reinterpret_cast<const char*>(&map_size),
        sizeof(map_size));

    for (const auto& entry : entries) {
        const std::string key = std::to_string(entry.first);
        const std::size_t key_length = key.size();
        const int value = static_cast<int>(entry.second);
        output.write(
            reinterpret_cast<const char*>(&key_length),
            sizeof(key_length));
        output.write(key.data(), static_cast<std::streamsize>(key_length));
        output.write(
            reinterpret_cast<const char*>(&value),
            sizeof(value));
    }
    return output.str();
}

class ReplicatedGCounter {
public:
    struct Snapshot {
        int local{0};
        int total{0};
    };

    struct AppliedUpdate {
        bool changed{false};
        int total{0};
    };

    ReplicatedGCounter(std::string id,
                       std::uint32_t node_index,
                       std::uint32_t node_count)
        : counter_(std::move(id)),
          node_index_(node_index),
          known_state_(node_count, 0) {
    }

    Snapshot increment(int value) {
        std::lock_guard<std::mutex> lock(mutex_);
        (void)counter_.inc(value);
        known_state_[node_index_] =
            static_cast<std::uint32_t>(counter_.local());
        return Snapshot{counter_.local(), counter_.read()};
    }

    Snapshot snapshot() {
        std::lock_guard<std::mutex> lock(mutex_);
        return Snapshot{counter_.local(), counter_.read()};
    }

    gossip::Bytes summary() const {
        std::lock_guard<std::mutex> lock(mutex_);
        gossip::Bytes bytes;
        bytes.reserve(
            sizeof(std::uint32_t) +
            known_state_.size() * sizeof(std::uint32_t));
        append_u32(bytes, static_cast<std::uint32_t>(known_state_.size()));
        for (const std::uint32_t value : known_state_) {
            append_u32(bytes, value);
        }
        return bytes;
    }

    StateRelation compare(const gossip::Bytes& remote_bytes) const {
        const auto remote = decode_summary(remote_bytes);
        std::lock_guard<std::mutex> lock(mutex_);
        require_matching_size(remote);

        bool local_greater = false;
        bool remote_greater = false;
        for (std::size_t i = 0; i < known_state_.size(); ++i) {
            local_greater = local_greater ||
                            known_state_[i] > remote[i];
            remote_greater = remote_greater ||
                             known_state_[i] < remote[i];
        }

        if (!local_greater && !remote_greater) {
            return StateRelation::Equivalent;
        }
        if (local_greater && !remote_greater) {
            return StateRelation::LocalNewer;
        }
        if (!local_greater && remote_greater) {
            return StateRelation::RemoteNewer;
        }
        return StateRelation::Incomparable;
    }

    gossip::Bytes make_update(
        const gossip::Bytes& remote_bytes) const {
        const auto remote = decode_summary(remote_bytes);
        std::lock_guard<std::mutex> lock(mutex_);
        require_matching_size(remote);

        gossip::Bytes update;
        std::uint32_t count = 0;
        for (std::size_t i = 0; i < known_state_.size(); ++i) {
            if (known_state_[i] > remote[i]) {
                ++count;
            }
        }

        update.reserve(
            sizeof(std::uint32_t) +
            static_cast<std::size_t>(count) *
                2 * sizeof(std::uint32_t));
        append_u32(update, count);
        for (std::size_t i = 0; i < known_state_.size(); ++i) {
            if (known_state_[i] > remote[i]) {
                append_u32(update, static_cast<std::uint32_t>(i));
                append_u32(update, known_state_[i]);
            }
        }
        return update;
    }

    AppliedUpdate apply(const gossip::Bytes& update_bytes) {
        const auto entries = decode_update(update_bytes);
        std::lock_guard<std::mutex> lock(mutex_);

        for (const auto& entry : entries) {
            if (entry.first >= known_state_.size()) {
                throw std::invalid_argument(
                    "GCounter update contains an unknown replica");
            }
        }

        std::vector<std::pair<std::uint32_t, std::uint32_t>>
            applied_entries;
        applied_entries.reserve(entries.size());
        for (const auto& entry : entries) {
            auto& local_value = known_state_[entry.first];
            if (entry.second > local_value) {
                local_value = entry.second;
                applied_entries.push_back(entry);
            }
        }

        if (applied_entries.empty()) {
            return AppliedUpdate{false, counter_.read()};
        }

        const auto serialized = serialize_as_gcounter(applied_entries);
        const auto update =
            gcounter<int, std::string>::deserialize(serialized);
        counter_.join(update);
        return AppliedUpdate{true, counter_.read()};
    }

private:
    void require_matching_size(
        const std::vector<std::uint32_t>& remote) const {
        if (remote.size() != known_state_.size()) {
            throw std::invalid_argument(
                "GCounter summaries have different replica counts");
        }
    }

    mutable std::mutex mutex_;
    gcounter<int, std::string> counter_;
    std::uint32_t node_index_;
    std::vector<std::uint32_t> known_state_;
};

class GCounterTrickleAdapter final
    : public gossip::trickle::StateAdapter {
public:
    explicit GCounterTrickleAdapter(ReplicatedGCounter& state)
        : state_(state) {
    }

    gossip::Bytes summary() const override {
        return state_.summary();
    }

    StateRelation compare(
        const gossip::Bytes& remote_summary) const override {
        return state_.compare(remote_summary);
    }

    gossip::Bytes make_update(
        const gossip::Bytes& remote_summary) const override {
        return state_.make_update(remote_summary);
    }

    bool apply_update(
        gossip::PeerId,
        const gossip::Bytes& update) override {
        return state_.apply(update).changed;
    }

private:
    ReplicatedGCounter& state_;
};

bool parse_node_index(const std::string& id, std::uint32_t& index) {
    try {
        std::size_t used = 0;
        const auto parsed = std::stoul(id, &used, 10);
        if (used != id.size() ||
            parsed > std::numeric_limits<std::uint32_t>::max()) {
            return false;
        }
        index = static_cast<std::uint32_t>(parsed);
        return true;
    } catch (...) {
        return false;
    }
}

bool parse_port(const std::string& address, std::uint16_t& port) {
    const auto separator = address.rfind(':');
    if (separator == std::string::npos ||
        separator + 1 == address.size()) {
        return false;
    }

    try {
        std::size_t used = 0;
        const auto parsed =
            std::stoul(address.substr(separator + 1), &used, 10);
        if (used != address.size() - separator - 1 ||
            parsed == 0 ||
            parsed > std::numeric_limits<std::uint16_t>::max()) {
            return false;
        }
        port = static_cast<std::uint16_t>(parsed);
        return true;
    } catch (...) {
        return false;
    }
}

gossip::PeerId to_peer_id(const std::string& id) {
    try {
        std::size_t used = 0;
        const auto parsed = std::stoull(id, &used, 10);
        if (used == id.size() &&
            parsed < std::numeric_limits<gossip::PeerId>::max()) {
            return static_cast<gossip::PeerId>(parsed + 1);
        }
    } catch (...) {
    }

    const auto hashed =
        static_cast<gossip::PeerId>(std::hash<std::string>{}(id));
    return hashed == 0 ? 1 : hashed;
}

NodeConfig load_config(const std::string& path,
                       const std::string& id) {
    std::ifstream input(path);
    const json document = json::parse(input);

    NodeConfig config;
    config.id = id;
    try {
        config.listen_addr = document.at("address").at(id);
        config.node_count =
            static_cast<std::uint32_t>(document.at("address").size());
    } catch (...) {
        return config;
    }

    if (!parse_node_index(id, config.node_index) ||
        config.node_count == 0 ||
        config.node_index >= config.node_count) {
        config.listen_addr.clear();
        return config;
    }

    config.ops_per_sec = document.value("ops_per_sec", 1.0);
    config.duration = document.value("duration", 10);
    config.distribution = document.value("distribution", "uniform");

    std::random_device random_device;
    config.seed =
        document.value("seed", static_cast<int>(random_device())) +
        std::atoi(id.c_str());
    config.monitor_interval =
        document.value("monitor_interval", 1.0);
    config.dissemination_interval =
        document.value("dissemination_interval", 0.5);
    config.cooldown = document.value("cooldown", 10.0);
    if (config.dissemination_interval <= 0.0) {
        config.dissemination_interval = 0.5;
    }

    const auto configured_k =
        document.value(
            "trickle_k",
            static_cast<int>(TRICKLE_K_DEFAULT));
    config.trickle_k =
        configured_k <= 0
            ? TRICKLE_K_DEFAULT
            : static_cast<std::uint32_t>(configured_k);

    const auto configured_imax =
        document.value(
            "trickle_imax_ticks",
            static_cast<int>(TRICKLE_IMAX_TICKS_DEFAULT));
    config.trickle_imax_ticks =
        configured_imax <= 0
            ? TRICKLE_IMAX_TICKS_DEFAULT
            : static_cast<std::uint32_t>(configured_imax);

    config.broadcast_address =
        document.value(
            "broadcast_address",
            std::string{"255.255.255.255"});
    config.delivery_queue_capacity =
        document.value(
            "trickle_delivery_queue_capacity",
            std::size_t{1024});

    std::string log_directory =
        document.value("log_dir", std::string{"."});
    if (!log_directory.empty() &&
        log_directory.back() != '/') {
        log_directory.push_back('/');
    }
    config.log_file =
        log_directory + "node_" + id + ".log";
    return config;
}

gossip::trickle::Config make_trickle_config(
    const NodeConfig& application_config) {
    gossip::trickle::Config config;
    config.local_peer_id = to_peer_id(application_config.id);
    config.bind_address = "0.0.0.0";
    config.broadcast_address =
        application_config.broadcast_address;
    config.port = PORT_DEFAULT;
    (void)parse_port(application_config.listen_addr, config.port);
    config.redundancy_constant =
        application_config.trickle_k;
    config.minimum_interval =
        std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::duration<double>(
                application_config.dissemination_interval));
    config.maximum_interval =
        config.minimum_interval *
        application_config.trickle_imax_ticks;
    config.seed = static_cast<std::uint64_t>(
        static_cast<std::uint32_t>(application_config.seed));
    config.max_packet_size = MSG_MAX;
    config.delivery_queue_capacity =
        application_config.delivery_queue_capacity;
    return config;
}

void print_config(const NodeConfig& config) {
    std::cout << "===== TRICKLE APPLICATION =====\n";
    std::cout << "node id: " << config.id << "\n";
    std::cout << "node_index: " << config.node_index << "\n";
    std::cout << "node_count: " << config.node_count << "\n";
    std::cout << "listen: " << config.listen_addr << "\n";
    std::cout << "broadcast: " << config.broadcast_address << "\n";
    std::cout << "ops_per_sec: " << config.ops_per_sec << "\n";
    std::cout << "duration: " << config.duration << "\n";
    std::cout << "seed: " << config.seed << "\n";
    std::cout << "monitor_interval: "
              << config.monitor_interval << "\n";
    std::cout << "dissemination_interval: "
              << config.dissemination_interval << "\n";
    std::cout << "trickle_k: " << config.trickle_k << "\n";
    std::cout << "trickle_imax_ticks: "
              << config.trickle_imax_ticks << "\n";
    std::cout << "delivery_queue_capacity: "
              << config.delivery_queue_capacity << "\n";
}

void log_protocol_event(const std::string& node_id,
                        const std::string& event,
                        const std::string& details = "") {
    std::lock_guard<std::mutex> lock(event_log_mutex);
    event_log << std::fixed << now_ts()
              << ", event=" << event
              << ", node=" << node_id;
    if (!details.empty()) {
        event_log << ", " << details;
    }
    event_log << "\n";
}

void log_apply(const std::string& node_id, int total) {
    std::lock_guard<std::mutex> lock(event_log_mutex);
    event_log << std::fixed << now_ts()
              << ", event=op_apply, node=" << node_id
              << ", total=" << total << "\n";
}

void delivery_loop(Trickle& trickle,
                   ReplicatedGCounter& state,
                   const std::string& node_id) {
    while (auto message = trickle.receive()) {
        if (!message->state_changed) {
            continue;
        }

        log_apply(node_id, state.snapshot().total);
        log_protocol_event(
            node_id,
            "trickle_reset",
            "reason=update_received");
    }
}

void write_monitor_sample(std::ofstream& log,
                          ReplicatedGCounter& state,
                          const Trickle& trickle) {
    const auto counter = state.snapshot();
    const auto protocol = trickle.stats();

    log << std::fixed << now_ts()
        << ", local=" << counter.local
        << ", total=" << counter.total
        << ", sent_msgs=" << protocol.sent_packets
        << ", recv_msgs=" << protocol.received_packets
        << ", sent_bytes=" << protocol.sent_bytes
        << ", recv_bytes=" << protocol.received_bytes
        << ", sent_summaries=" << protocol.sent_summaries
        << ", recv_summaries=" << protocol.received_summaries
        << ", sent_updates=" << protocol.sent_updates
        << ", recv_updates=" << protocol.received_updates
        << ", suppressed=" << protocol.suppressed_summaries
        << ", interval_resets=" << protocol.interval_resets
        << ", interval_ms=" << protocol.current_interval.count()
        << ", consistent_count=" << protocol.consistent_count
        << ", delivery_queue_overflows="
        << protocol.delivery_queue_overflows
        << ", adapter_errors=" << protocol.adapter_errors
        << ", malformed_packets=" << protocol.malformed_packets
        << ", socket_errors=" << protocol.socket_errors
        << ", pending_deliveries="
        << protocol.pending_deliveries
        << "\n";
    log.flush();
}

void monitor_loop(ReplicatedGCounter& state,
                  const Trickle& trickle,
                  double interval,
                  const std::string& logfile) {
    std::ofstream log(logfile, std::ios::trunc);
    while (g_running.load()) {
        std::this_thread::sleep_for(
            std::chrono::duration<double>(interval));
        if (!g_running.load()) {
            break;
        }
        write_monitor_sample(log, state, trickle);
    }
    write_monitor_sample(log, state, trickle);
}

void run_random_mode(const NodeConfig& config,
                     ReplicatedGCounter& state,
                     Trickle& trickle) {
    std::default_random_engine generator(
        static_cast<unsigned int>(config.seed));
    std::exponential_distribution<double> wait_distribution(
        config.ops_per_sec);
    std::uniform_int_distribution<int> increment_distribution(1, 1);

    const auto start = std::chrono::steady_clock::now();
    while (g_running.load()) {
        const double elapsed =
            std::chrono::duration<double>(
                std::chrono::steady_clock::now() - start)
                .count();
        if (elapsed > config.duration) {
            break;
        }

        std::this_thread::sleep_for(
            std::chrono::duration<double>(
                wait_distribution(generator)));
        if (!g_running.load()) {
            break;
        }

        const int increment = increment_distribution(generator);
        const auto counter = state.increment(increment);

        {
            std::lock_guard<std::mutex> lock(event_log_mutex);
            const auto timestamp = now_ts();
            event_log << std::fixed << timestamp
                      << ", event=op_create, node=" << config.id
                      << ", delta_size=" << increment << "\n";
            event_log << std::fixed << timestamp
                      << ", event=op_apply, node=" << config.id
                      << ", total=" << counter.total << "\n";
        }

        if (!trickle.notify_state_changed() && g_running.load()) {
            std::cerr
                << "Trickle state change rejected: "
                << trickle.last_error() << "\n";
        } else {
            log_protocol_event(
                config.id,
                "trickle_reset",
                "reason=local_op");
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

    const NodeConfig config =
        load_config(config_path, node_id);
    std::uint16_t port = 0;
    if (config.listen_addr.empty() ||
        config.node_count == 0 ||
        !parse_port(config.listen_addr, port)) {
        std::cerr
            << "Invalid Trickle configuration for node "
            << node_id << "\n";
        return 1;
    }
    print_config(config);

    event_log.open(
        config.log_file + ".events",
        std::ios::trunc);
    event_log.setf(std::ios::unitbuf);
    if (!event_log.is_open()) {
        return 1;
    }

    {
        std::ostringstream details;
        details << "trickle_k=" << config.trickle_k
                << ", tau_l_s="
                << config.dissemination_interval
                << ", tau_h_s="
                << config.dissemination_interval *
                       config.trickle_imax_ticks
                << ", trickle_imax_ticks="
                << config.trickle_imax_ticks
                << ", node_count=" << config.node_count;
        log_protocol_event(
            config.id,
            "trickle_init",
            details.str());
    }

    ReplicatedGCounter state(
        config.id,
        config.node_index,
        config.node_count);
    GCounterTrickleAdapter adapter(state);
    Trickle trickle(make_trickle_config(config), adapter);
    if (!trickle.start()) {
        std::cerr
            << "Failed to start Trickle: "
            << trickle.last_error() << "\n";
        return 1;
    }

    std::thread delivery_thread(
        delivery_loop,
        std::ref(trickle),
        std::ref(state),
        std::cref(config.id));
    std::thread monitor_thread(
        monitor_loop,
        std::ref(state),
        std::cref(trickle),
        config.monitor_interval,
        std::cref(config.log_file));

    run_random_mode(config, state, trickle);
    log_protocol_event(config.id, "ops_finished");

    std::this_thread::sleep_for(
        std::chrono::duration<double>(config.cooldown));

    g_running.store(false);
    trickle.stop();

    if (monitor_thread.joinable()) {
        monitor_thread.join();
    }
    if (delivery_thread.joinable()) {
        delivery_thread.join();
    }

    const auto final_stats = trickle.stats();
    if (final_stats.delivery_queue_overflows > 0 ||
        final_stats.socket_errors > 0 ||
        final_stats.adapter_errors > 0) {
        std::cerr
            << "Trickle diagnostics: delivery_queue_overflows="
            << final_stats.delivery_queue_overflows
            << ", socket_errors="
            << final_stats.socket_errors
            << ", adapter_errors="
            << final_stats.adapter_errors << "\n";
    }

    {
        std::lock_guard<std::mutex> lock(event_log_mutex);
        event_log.flush();
        event_log.close();
    }
    return 0;
}
