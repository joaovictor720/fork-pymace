#include "trickle.hpp"

#include <algorithm>
#include <chrono>
#include <cstdint>
#include <future>
#include <iostream>
#include <mutex>
#include <numeric>
#include <stdexcept>
#include <utility>
#include <vector>

using namespace std::chrono_literals;
using gossip::Bytes;
using gossip::trickle::StateRelation;
using gossip::trickle::Trickle;

namespace {

void append_u32(Bytes& output, std::uint32_t value) {
    for (int i = 0; i < 4; ++i) {
        output.push_back(
            static_cast<std::uint8_t>((value >> (i * 8)) & 0xff));
    }
}

bool read_u32(const Bytes& input,
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

std::vector<std::uint32_t> decode_vector(const Bytes& bytes) {
    std::size_t position = 0;
    std::uint32_t count = 0;
    if (!read_u32(bytes, position, count) ||
        position + static_cast<std::size_t>(count) * 4 != bytes.size()) {
        throw std::invalid_argument("invalid GCounter vector");
    }

    std::vector<std::uint32_t> values;
    values.reserve(count);
    for (std::uint32_t i = 0; i < count; ++i) {
        std::uint32_t value = 0;
        if (!read_u32(bytes, position, value)) {
            throw std::invalid_argument("truncated GCounter vector");
        }
        values.push_back(value);
    }
    return values;
}

class GCounterState {
public:
    GCounterState(std::size_t replica_count, std::size_t local_index)
        : components_(replica_count, 0),
          local_index_(local_index) {
    }

    void increment() {
        std::lock_guard<std::mutex> lock(mutex_);
        ++components_.at(local_index_);
    }

    std::uint32_t value() const {
        std::lock_guard<std::mutex> lock(mutex_);
        return std::accumulate(
            components_.begin(),
            components_.end(),
            std::uint32_t{0});
    }

    Bytes summary() const {
        std::lock_guard<std::mutex> lock(mutex_);
        Bytes bytes;
        append_u32(
            bytes,
            static_cast<std::uint32_t>(components_.size()));
        for (const auto value : components_) {
            append_u32(bytes, value);
        }
        return bytes;
    }

    StateRelation compare(const Bytes& remote_summary) const {
        const auto remote = decode_vector(remote_summary);
        std::lock_guard<std::mutex> lock(mutex_);
        require_same_size(remote);

        bool local_greater = false;
        bool remote_greater = false;
        for (std::size_t i = 0; i < components_.size(); ++i) {
            local_greater =
                local_greater || components_[i] > remote[i];
            remote_greater =
                remote_greater || components_[i] < remote[i];
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

    Bytes make_update(const Bytes& remote_summary) const {
        const auto remote = decode_vector(remote_summary);
        std::lock_guard<std::mutex> lock(mutex_);
        require_same_size(remote);

        std::vector<std::pair<std::uint32_t, std::uint32_t>>
            newer_components;
        for (std::size_t i = 0; i < components_.size(); ++i) {
            if (components_[i] > remote[i]) {
                newer_components.emplace_back(
                    static_cast<std::uint32_t>(i),
                    components_[i]);
            }
        }

        Bytes update;
        append_u32(
            update,
            static_cast<std::uint32_t>(newer_components.size()));
        for (const auto& [index, value] : newer_components) {
            append_u32(update, index);
            append_u32(update, value);
        }
        return update;
    }

    bool apply_update(const Bytes& update) {
        std::size_t position = 0;
        std::uint32_t count = 0;
        if (!read_u32(update, position, count) ||
            position + static_cast<std::size_t>(count) * 8 !=
                update.size()) {
            throw std::invalid_argument("invalid GCounter update");
        }

        std::vector<std::pair<std::uint32_t, std::uint32_t>>
            entries;
        entries.reserve(count);
        for (std::uint32_t i = 0; i < count; ++i) {
            std::uint32_t index = 0;
            std::uint32_t value = 0;
            if (!read_u32(update, position, index) ||
                !read_u32(update, position, value)) {
                throw std::invalid_argument(
                    "truncated GCounter update");
            }
            entries.emplace_back(index, value);
        }

        std::lock_guard<std::mutex> lock(mutex_);
        for (const auto& [index, value] : entries) {
            if (index >= components_.size()) {
                throw std::invalid_argument(
                    "unknown GCounter replica");
            }
        }

        bool changed = false;
        for (const auto& [index, value] : entries) {
            if (value > components_[index]) {
                components_[index] = value;
                changed = true;
            }
        }
        return changed;
    }

private:
    void require_same_size(
        const std::vector<std::uint32_t>& remote) const {
        if (remote.size() != components_.size()) {
            throw std::invalid_argument(
                "different GCounter replica counts");
        }
    }

    mutable std::mutex mutex_;
    std::vector<std::uint32_t> components_;
    std::size_t local_index_;
};

class GCounterAdapter final
    : public gossip::trickle::StateAdapter {
public:
    explicit GCounterAdapter(GCounterState& state)
        : state_(state) {
    }

    Bytes summary() const override {
        return state_.summary();
    }

    StateRelation compare(
        const Bytes& remote_summary) const override {
        return state_.compare(remote_summary);
    }

    Bytes make_update(
        const Bytes& remote_summary) const override {
        return state_.make_update(remote_summary);
    }

    bool apply_update(
        gossip::PeerId,
        const Bytes& update) override {
        return state_.apply_update(update);
    }

private:
    GCounterState& state_;
};

gossip::trickle::Config local_config(
    gossip::PeerId peer_id) {
    gossip::trickle::Config config;
    config.local_peer_id = peer_id;
    config.port = 39002;
    config.broadcast_address = "127.255.255.255";
    config.minimum_interval = 50ms;
    config.maximum_interval = 400ms;
    return config;
}

bool receive_one(Trickle& trickle) {
    const auto update = trickle.receive();
    return update && update->state_changed;
}

}  // namespace

int main() {
    GCounterState first_state(2, 0);
    GCounterState second_state(2, 1);
    GCounterAdapter first_adapter(first_state);
    GCounterAdapter second_adapter(second_state);

    Trickle first(local_config(1), first_adapter);
    Trickle second(local_config(2), second_adapter);
    if (!first.start() || !second.start()) {
        std::cerr << "Could not start Trickle instances\n";
        first.stop();
        second.stop();
        return 1;
    }

    first_state.increment();
    first.notify_state_changed();

    auto second_receive = std::async(
        std::launch::async,
        receive_one,
        std::ref(second));
    if (second_receive.wait_for(2s) != std::future_status::ready ||
        !second_receive.get()) {
        std::cerr << "Second replica did not receive the update\n";
        first.stop();
        second.stop();
        return 1;
    }

    second_state.increment();
    second.notify_state_changed();

    auto first_receive = std::async(
        std::launch::async,
        receive_one,
        std::ref(first));
    if (first_receive.wait_for(2s) != std::future_status::ready ||
        !first_receive.get()) {
        std::cerr << "First replica did not receive the update\n";
        first.stop();
        second.stop();
        return 1;
    }

    std::cout << "first=" << first_state.value()
              << ", second=" << second_state.value() << "\n";

    first.stop();
    second.stop();
    return first_state.value() == 2 &&
                   second_state.value() == 2
               ? 0
               : 1;
}
