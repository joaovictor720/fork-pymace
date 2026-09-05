#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <csignal>
#include <cstdint>
#include <cstdlib>
#include <cstring>
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
#include "../common/spatial_coverage.hpp"

using json = nlohmann::json;
using gossip::trickle::StateRelation;
using gossip::trickle::Trickle;

namespace {

constexpr std::size_t LEGACY_MSG_MAX = 16384;
constexpr std::size_t TRICKLE_PACKET_OVERHEAD = 1 + 8 + 4;
constexpr int PORT_DEFAULT = 9000;
constexpr std::uint32_t TRICKLE_K_DEFAULT = 1;
constexpr std::uint32_t TRICKLE_IMAX_TICKS_DEFAULT = 16;

std::atomic<bool> g_running{true};
std::mutex event_log_mutex;
std::ofstream event_log;

struct NodeConfig {
    std::string id;
    std::string workload{"gcounter"};
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
    mace::coverage::GridSpec grid;
    std::string gps_socket_path;
    std::chrono::milliseconds position_poll_interval{
        mace::coverage::kDefaultPollInterval};
    std::chrono::milliseconds gps_timeout{
        mace::coverage::kDefaultGpsTimeout};
    std::size_t max_datagram_bytes{
        mace::coverage::kDefaultMaxDatagramBytes};
    std::string config_error;
};

bool is_spatial_workload(const NodeConfig& config) {
    return config.workload == "spatial_coverage";
}

void request_shutdown(int) {
    g_running.store(false);
}

double now_ts() {
    return std::chrono::duration<double>(
               std::chrono::steady_clock::now().time_since_epoch())
        .count();
}

double event_ts() {
    return mace::coverage::unix_time_seconds();
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

std::string expand_node_id(std::string value, const std::string& id) {
    constexpr const char* placeholder = "{id}";
    std::size_t position = 0;
    while ((position = value.find(placeholder, position)) != std::string::npos) {
        value.replace(position, std::strlen(placeholder), id);
        position += id.size();
    }
    return value;
}

std::chrono::milliseconds milliseconds_from_config(
    const json& document,
    const char* primary,
    const char* fallback,
    std::chrono::milliseconds default_value) {
    double value = static_cast<double>(default_value.count());
    if (document.contains(primary)) {
        value = document.at(primary).get<double>();
    } else if (fallback != nullptr && document.contains(fallback)) {
        value = document.at(fallback).get<double>();
    }
    if (!std::isfinite(value) || value <= 0.0) {
        throw std::invalid_argument(std::string(primary) + " must be positive and finite");
    }

    const auto duration = std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::duration<double, std::milli>(value));
    if (duration.count() <= 0) {
        throw std::invalid_argument(std::string(primary) + " is below one millisecond");
    }
    return duration;
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

    config.workload = document.value("workload", std::string{"gcounter"});
    if (config.workload != "gcounter" &&
        config.workload != "spatial_coverage") {
        config.config_error =
            "workload must be either 'gcounter' or 'spatial_coverage'";
        return config;
    }

    if (config.node_count == 0 ||
        (!is_spatial_workload(config) &&
         (!parse_node_index(id, config.node_index) ||
          config.node_index >= config.node_count))) {
        config.listen_addr.clear();
        return config;
    }

    config.ops_per_sec = document.value("ops_per_sec", 1.0);
    config.duration = document.value("duration", 10);
    config.distribution = document.value("distribution", "uniform");

    config.seed =
        document.value("seed", 1) +
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

    if (is_spatial_workload(config)) {
        try {
            if (!std::isfinite(config.monitor_interval) ||
                config.monitor_interval <= 0.0 ||
                !std::isfinite(config.dissemination_interval) ||
                config.dissemination_interval <= 0.0) {
                throw std::invalid_argument(
                    "monitor_interval and dissemination_interval must be "
                    "positive and finite");
            }

            const json& grid = document.contains("grid")
                                   ? document.at("grid")
                                   : document.at("grid_spec");
            config.grid = mace::coverage::GridSpec{
                grid.at("origin_x_m").get<double>(),
                grid.at("origin_y_m").get<double>(),
                grid.at("width_m").get<double>(),
                grid.at("height_m").get<double>(),
                grid.at("rows").get<std::uint32_t>(),
                grid.at("cols").get<std::uint32_t>()};

            std::string gps_socket_template = "/tmp/node{id}_gps.sock";
            if (document.contains("gps_socket_template")) {
                gps_socket_template =
                    document.at("gps_socket_template").get<std::string>();
            } else if (document.contains("gps_socket_path")) {
                gps_socket_template =
                    document.at("gps_socket_path").get<std::string>();
            }
            config.gps_socket_path =
                expand_node_id(std::move(gps_socket_template), id);
            config.position_poll_interval = milliseconds_from_config(
                document,
                "position_poll_interval_ms",
                "poll_interval_ms",
                mace::coverage::kDefaultPollInterval);
            config.gps_timeout = milliseconds_from_config(
                document,
                "gps_timeout_ms",
                nullptr,
                mace::coverage::kDefaultGpsTimeout);

            std::int64_t configured_budget = static_cast<std::int64_t>(
                mace::coverage::kDefaultMaxDatagramBytes);
            if (document.contains("max_datagram_bytes")) {
                configured_budget =
                    document.at("max_datagram_bytes").get<std::int64_t>();
            } else if (document.contains("max_payload_bytes")) {
                configured_budget =
                    document.at("max_payload_bytes").get<std::int64_t>();
            }
            if (configured_budget <= 0 ||
                static_cast<std::uint64_t>(configured_budget) >
                    mace::coverage::kDefaultMaxDatagramBytes) {
                throw std::invalid_argument(
                    "max_datagram_bytes must be between 1 and 1200");
            }
            config.max_datagram_bytes =
                static_cast<std::size_t>(configured_budget);

            config.grid.validate();
            mace::coverage::validate_datagram_budget(
                config.grid,
                config.max_datagram_bytes,
                TRICKLE_PACKET_OVERHEAD);
        } catch (const std::exception& error) {
            config.config_error =
                std::string("invalid spatial coverage configuration: ") +
                error.what();
        }
    }

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
    config.max_packet_size = is_spatial_workload(application_config)
                                 ? application_config.max_datagram_bytes
                                 : LEGACY_MSG_MAX;
    config.delivery_queue_capacity =
        application_config.delivery_queue_capacity;
    return config;
}

void print_config(const NodeConfig& config) {
    std::cout << "===== TRICKLE APPLICATION =====\n";
    std::cout << "node id: " << config.id << "\n";
    std::cout << "workload: " << config.workload << "\n";
    if (!is_spatial_workload(config)) {
        std::cout << "node_index: " << config.node_index << "\n";
    }
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
    if (is_spatial_workload(config)) {
        std::cout << "grid: origin=("
                  << config.grid.origin_x_m << ","
                  << config.grid.origin_y_m << "), size=("
                  << config.grid.width_m << ","
                  << config.grid.height_m << "), cells="
                  << config.grid.rows << "x" << config.grid.cols << "\n";
        std::cout << "gps_socket_path: " << config.gps_socket_path << "\n";
        std::cout << "position_poll_interval_ms: "
                  << config.position_poll_interval.count() << "\n";
        std::cout << "gps_timeout_ms: "
                  << config.gps_timeout.count() << "\n";
        std::cout << "max_datagram_bytes: "
                  << config.max_datagram_bytes << "\n";
    }
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

void log_spatial_event_at(double timestamp,
                          const std::string& node_id,
                          const std::string& event,
                          const std::string& details = "") {
    std::lock_guard<std::mutex> lock(event_log_mutex);
    event_log << std::fixed << timestamp
              << ", event=" << event
              << ", node=" << node_id;
    if (!details.empty()) {
        event_log << ", " << details;
    }
    event_log << "\n";
}

void log_spatial_event(const std::string& node_id,
                       const std::string& event,
                       const std::string& details = "") {
    log_spatial_event_at(event_ts(), node_id, event, details);
}

void log_local_coverage_and_trigger(
    const std::string& node_id,
    const mace::coverage::StateUpdate& update) {
    const double timestamp = update.timestamp_unix_s;
    std::lock_guard<std::mutex> lock(event_log_mutex);
    event_log << std::fixed << timestamp
              << ", event=local_coverage, node=" << node_id
              << ", cell_ids_added="
              << mace::coverage::format_cell_ids(update.added)
              << ", replica_size=" << update.replica_size
              << ", replica_version=" << update.mutation_sequence
              << "\n";
    event_log << std::fixed << timestamp
              << ", event=dissemination_trigger, node=" << node_id
              << ", replica_size=" << update.replica_size
              << ", serialized_state_size=" << update.snapshot.size()
              << ", replica_version=" << update.mutation_sequence
              << "\n";
}

void log_apply(const std::string& node_id, int total) {
    std::lock_guard<std::mutex> lock(event_log_mutex);
    event_log << std::fixed << now_ts()
              << ", event=op_apply, node=" << node_id
              << ", total=" << total << "\n";
}

class SpatialTrickleAdapter final
    : public gossip::trickle::StateAdapter {
public:
    SpatialTrickleAdapter(mace::coverage::CoverageWorkload& state,
                          std::string node_id)
        : state_(state), node_id_(std::move(node_id)) {
    }

    gossip::Bytes summary() const override {
        // A Trickle summary is the complete, deterministic GSet snapshot.
        return state_.snapshot();
    }

    StateRelation compare(
        const gossip::Bytes& remote_summary) const override {
        const auto remote = mace::coverage::deserialize_gset(
            remote_summary, state_.grid().cell_count());
        const auto local = state_.cells();
        const bool local_contains_remote = std::includes(
            local.begin(), local.end(), remote.begin(), remote.end());
        const bool remote_contains_local = std::includes(
            remote.begin(), remote.end(), local.begin(), local.end());

        if (local_contains_remote && remote_contains_local) {
            return StateRelation::Equivalent;
        }
        if (local_contains_remote) {
            return StateRelation::LocalNewer;
        }
        if (remote_contains_local) {
            return StateRelation::RemoteNewer;
        }
        return StateRelation::Incomparable;
    }

    gossip::Bytes make_update(
        const gossip::Bytes& remote_summary) const override {
        // Validate the summary supplied by the protocol, but deliberately do
        // not compute a delta. Spatial coverage always transfers full state.
        (void)mace::coverage::deserialize_gset(
            remote_summary, state_.grid().cell_count());
        return state_.snapshot();
    }

    bool apply_update(gossip::PeerId sender,
                      const gossip::Bytes& update) override {
        mace::coverage::StateUpdate result;
        try {
            result = state_.merge_remote(update);
        } catch (...) {
            std::ostringstream details;
            details << "sender=" << sender
                    << ", serialized_state_size=" << update.size()
                    << ", valid=0";
            log_spatial_event(node_id_, "network_receive", details.str());
            throw;
        }

        {
            std::ostringstream details;
            details << "sender=" << sender
                    << ", serialized_state_size=" << update.size()
                    << ", valid=1"
                    << ", state_changed=" << (result.changed ? 1 : 0)
                    << ", replica_size=" << result.replica_size;
            log_spatial_event(node_id_, "network_receive", details.str());
        }
        if (result.changed) {
            std::ostringstream details;
            details << "cell_ids_added="
                    << mace::coverage::format_cell_ids(result.added)
                    << ", replica_size=" << result.replica_size
                    << ", replica_version="
                    << result.mutation_sequence;
            log_spatial_event_at(
                result.timestamp_unix_s,
                node_id_,
                "remote_merge",
                details.str());
        }
        // Trickle itself resets its interval when this returns true. This is
        // protocol-internal behavior, not an application dissemination trigger.
        return result.changed;
    }

private:
    mace::coverage::CoverageWorkload& state_;
    std::string node_id_;
};

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

void spatial_delivery_loop(Trickle& trickle,
                           const std::string& node_id) {
    while (auto message = trickle.receive()) {
        if (message->state_changed) {
            log_spatial_event(
                node_id,
                "trickle_reset",
                "reason=remote_merge");
        }
    }
}

void write_spatial_monitor_sample(
    std::ofstream& log,
    const mace::coverage::CoverageWorkload& state,
    const Trickle& trickle,
    const std::atomic<std::uint64_t>& invalid_position_samples) {
    const auto protocol = trickle.stats();
    log << std::fixed << event_ts()
        << ", replica_size=" << state.size()
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
        << ", oversized_payloads=" << protocol.oversized_payloads
        << ", rejected_state_changes="
        << protocol.rejected_state_changes
        << ", socket_errors=" << protocol.socket_errors
        << ", pending_deliveries=" << protocol.pending_deliveries
        << ", invalid_position_samples="
        << invalid_position_samples.load()
        << "\n";
    log.flush();
}

void spatial_monitor_loop(
    const mace::coverage::CoverageWorkload& state,
    const Trickle& trickle,
    const std::atomic<std::uint64_t>& invalid_position_samples,
    double interval,
    const std::string& logfile) {
    std::ofstream log(logfile, std::ios::trunc);
    while (g_running.load()) {
        if (mace::coverage::sleep_for_or_stopped(
                g_running, std::chrono::duration<double>(interval))) {
            break;
        }
        write_spatial_monitor_sample(
            log, state, trickle, invalid_position_samples);
    }
    write_spatial_monitor_sample(
        log, state, trickle, invalid_position_samples);
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
    double next_event_s = 0.0;
    while (g_running.load()) {
        next_event_s += wait_distribution(generator);
        if (next_event_s > config.duration) {
            break;
        }

        const auto event_time =
            start + std::chrono::duration_cast<std::chrono::steady_clock::duration>(
                        std::chrono::duration<double>(next_event_s));
        std::this_thread::sleep_until(event_time);
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

void report_final_diagnostics(const Trickle& trickle) {
    const auto final_stats = trickle.stats();
    if (final_stats.delivery_queue_overflows > 0 ||
        final_stats.socket_errors > 0 ||
        final_stats.adapter_errors > 0 ||
        final_stats.oversized_payloads > 0 ||
        final_stats.rejected_state_changes > 0) {
        std::cerr
            << "Trickle diagnostics: delivery_queue_overflows="
            << final_stats.delivery_queue_overflows
            << ", socket_errors="
            << final_stats.socket_errors
            << ", adapter_errors="
            << final_stats.adapter_errors
            << ", oversized_payloads="
            << final_stats.oversized_payloads
            << ", rejected_state_changes="
            << final_stats.rejected_state_changes << "\n";
    }
}

int run_gcounter_application(const NodeConfig& config) {
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
    report_final_diagnostics(trickle);
    return 0;
}

int run_spatial_application(const NodeConfig& config) {
    mace::coverage::CoverageWorkload state(config.grid);
    SpatialTrickleAdapter adapter(state, config.id);
    Trickle trickle(make_trickle_config(config), adapter);
    if (!trickle.start()) {
        std::cerr
            << "Failed to start Trickle: "
            << trickle.last_error() << "\n";
        return 1;
    }

    std::atomic<std::uint64_t> invalid_position_samples{0};
    mace::coverage::UnixGpsPositionSource position_source(
        config.gps_socket_path,
        config.gps_timeout);

    std::thread delivery_thread(
        spatial_delivery_loop,
        std::ref(trickle),
        std::cref(config.id));
    std::thread monitor_thread(
        spatial_monitor_loop,
        std::cref(state),
        std::cref(trickle),
        std::cref(invalid_position_samples),
        config.monitor_interval,
        std::cref(config.log_file));
    std::thread polling_thread([&] {
        mace::coverage::polling_loop(
            position_source,
            state,
            g_running,
            config.position_poll_interval,
            [&](const mace::coverage::StateUpdate& update) {
                log_local_coverage_and_trigger(config.id, update);

                if (!trickle.notify_state_changed()) {
                    if (g_running.load()) {
                        std::cerr
                            << "Trickle state change rejected: "
                            << trickle.last_error() << "\n";
                    }
                    return;
                }
                log_spatial_event(
                    config.id,
                    "trickle_reset",
                    "reason=local_coverage");
            },
            [&] {
                invalid_position_samples.fetch_add(
                    1, std::memory_order_relaxed);
            });
    });

    // Spatial coverage has no application duration or cooldown. The emulator
    // owns run lifetime and stops this process with SIGINT/SIGTERM.
    while (g_running.load() && trickle.is_running()) {
        std::this_thread::sleep_for(std::chrono::milliseconds(50));
    }
    g_running.store(false);

    if (polling_thread.joinable()) {
        polling_thread.join();
    }
    trickle.stop();
    if (monitor_thread.joinable()) {
        monitor_thread.join();
    }
    if (delivery_thread.joinable()) {
        delivery_thread.join();
    }

    report_final_diagnostics(trickle);
    return 0;
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

    NodeConfig config;
    try {
        config = load_config(config_path, node_id);
    } catch (const std::exception& error) {
        std::cerr
            << "Failed to load Trickle configuration for node "
            << node_id << ": " << error.what() << "\n";
        return 1;
    }
    std::uint16_t port = 0;
    if (!config.config_error.empty()) {
        std::cerr << config.config_error << "\n";
        return 1;
    }
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
                << ", node_count=" << config.node_count
                << ", workload=" << config.workload;
        if (is_spatial_workload(config)) {
            details << ", grid_rows=" << config.grid.rows
                    << ", grid_cols=" << config.grid.cols
                    << ", max_datagram_bytes="
                    << config.max_datagram_bytes
                    << ", position_poll_interval_ms="
                    << config.position_poll_interval.count();
        }
        if (is_spatial_workload(config)) {
            log_spatial_event(
                config.id,
                "trickle_init",
                details.str());
        } else {
            log_protocol_event(
                config.id,
                "trickle_init",
                details.str());
        }
    }

    int result = 0;
    try {
        if (is_spatial_workload(config)) {
            std::signal(SIGINT, request_shutdown);
            std::signal(SIGTERM, request_shutdown);
            result = run_spatial_application(config);
        } else {
            result = run_gcounter_application(config);
        }
    } catch (const std::exception& error) {
        std::cerr << "Trickle application failed: " << error.what() << "\n";
        result = 1;
    }

    {
        std::lock_guard<std::mutex> lock(event_log_mutex);
        event_log.flush();
        event_log.close();
    }
    return result;
}
