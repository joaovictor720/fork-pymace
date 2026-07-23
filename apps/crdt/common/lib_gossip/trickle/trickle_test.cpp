#include "trickle.hpp"

#include <chrono>
#include <cstdlib>
#include <future>
#include <iostream>
#include <mutex>
#include <stdexcept>
#include <string>
#include <thread>
#include <unistd.h>
#include <vector>

using namespace std::chrono_literals;
using gossip::Bytes;
using gossip::trickle::Config;
using gossip::trickle::StateAdapter;
using gossip::trickle::StateRelation;
using gossip::trickle::Trickle;

namespace {

[[noreturn]] void fail(const std::string& message) {
    std::cerr << "FAIL: " << message << "\n";
    std::exit(1);
}

void require(bool condition, const std::string& message) {
    if (!condition) {
        fail(message);
    }
}

template <typename Predicate>
bool wait_until(Predicate predicate,
                std::chrono::milliseconds timeout) {
    const auto deadline =
        std::chrono::steady_clock::now() + timeout;
    do {
        if (predicate()) {
            return true;
        }
        std::this_thread::sleep_for(5ms);
    } while (std::chrono::steady_clock::now() < deadline);
    return predicate();
}

class VectorState final : public StateAdapter {
public:
    explicit VectorState(Bytes values)
        : values_(std::move(values)) {
    }

    Bytes summary() const override {
        std::lock_guard<std::mutex> lock(mutex_);
        return values_;
    }

    StateRelation compare(
        const Bytes& remote_summary) const override {
        std::lock_guard<std::mutex> lock(mutex_);
        if (remote_summary.size() != values_.size()) {
            throw std::invalid_argument("different vector sizes");
        }

        bool local_greater = false;
        bool remote_greater = false;
        for (std::size_t i = 0; i < values_.size(); ++i) {
            local_greater =
                local_greater || values_[i] > remote_summary[i];
            remote_greater =
                remote_greater || values_[i] < remote_summary[i];
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

    Bytes make_update(
        const Bytes& remote_summary) const override {
        std::lock_guard<std::mutex> lock(mutex_);
        if (remote_summary.size() != values_.size()) {
            throw std::invalid_argument("different vector sizes");
        }

        Bytes update(values_.size(), 0);
        for (std::size_t i = 0; i < values_.size(); ++i) {
            if (values_[i] > remote_summary[i]) {
                update[i] = values_[i];
            }
        }
        return update;
    }

    bool apply(const Bytes& update) {
        std::lock_guard<std::mutex> lock(mutex_);
        if (update.size() != values_.size()) {
            return false;
        }

        bool changed = false;
        for (std::size_t i = 0; i < values_.size(); ++i) {
            if (update[i] > values_[i]) {
                values_[i] = update[i];
                changed = true;
            }
        }
        return changed;
    }

    Bytes values() const {
        std::lock_guard<std::mutex> lock(mutex_);
        return values_;
    }

private:
    mutable std::mutex mutex_;
    Bytes values_;
};

Config local_config(std::uint16_t port,
                    gossip::PeerId peer_id,
                    std::uint64_t seed) {
    Config config;
    config.local_peer_id = peer_id;
    config.port = port;
    config.broadcast_address = "127.255.255.255";
    config.minimum_interval = 30ms;
    config.maximum_interval = 120ms;
    config.redundancy_constant = 1;
    config.seed = seed;
    return config;
}

void test_invalid_config() {
    VectorState state(Bytes{0});
    Config config;
    config.local_peer_id = 0;
    Trickle trickle(config, state);

    require(!trickle.start(),
            "a zero local_peer_id must be rejected");
    require(!trickle.last_error().empty(),
            "a startup error must be observable");
}

void test_dissemination_and_shutdown(std::uint16_t port) {
    VectorState first_state(Bytes{3, 0});
    VectorState second_state(Bytes{0, 0});
    Trickle first(local_config(port, 1, 11), first_state);
    Trickle second(local_config(port, 2, 22), second_state);

    require(first.start(),
            "first instance failed to start: " +
                first.last_error());
    require(second.start(),
            "second instance failed to start: " +
                second.last_error());
    require(first.notify_state_changed(),
            "the initial local state change was rejected");

    auto received = std::async(std::launch::async, [&] {
        return second.receive();
    });
    require(received.wait_for(2s) == std::future_status::ready,
            "the outdated instance did not receive an update");

    auto update = received.get();
    require(update.has_value(),
            "receive returned shutdown instead of an update");
    require(update->sender == 1,
            "the update did not expose its sender");
    require(second_state.apply(update->payload),
            "the received update did not change local state");
    require(second.notify_state_changed(),
            "a remotely applied state change was rejected");
    require(second_state.values() == Bytes({3, 0}),
            "the second state did not converge");

    require(wait_until(
                [&] {
                    const auto a = first.stats();
                    const auto b = second.stats();
                    return a.suppressed_summaries +
                               b.suppressed_summaries >
                           0;
                },
                1500ms),
            "equal states never suppressed a redundant summary");

    second.stop();
    first.stop();
    require(!first.notify_state_changed(),
            "a stopped instance accepted a state change");
    require(first.stats().rejected_state_changes == 1,
            "a rejected state change was not counted");
}

void test_incomparable_states(std::uint16_t port) {
    VectorState first_state(Bytes{2, 0});
    VectorState second_state(Bytes{0, 3});
    Trickle first(local_config(port, 10, 101), first_state);
    Trickle second(local_config(port, 20, 202), second_state);

    require(first.start(), "first incomparable instance failed");
    require(second.start(), "second incomparable instance failed");
    require(first.notify_state_changed(),
            "first incomparable state change was rejected");
    require(second.notify_state_changed(),
            "second incomparable state change was rejected");

    auto receive_and_apply = [](Trickle& trickle,
                                VectorState& state) {
        auto update = trickle.receive();
        if (update && state.apply(update->payload)) {
            trickle.notify_state_changed();
        }
        return update.has_value();
    };

    auto first_received = std::async(
        std::launch::async,
        receive_and_apply,
        std::ref(first),
        std::ref(first_state));
    auto second_received = std::async(
        std::launch::async,
        receive_and_apply,
        std::ref(second),
        std::ref(second_state));

    require(first_received.wait_for(2s) ==
                std::future_status::ready &&
                first_received.get(),
            "the first incomparable state received no update");
    require(second_received.wait_for(2s) ==
                std::future_status::ready &&
                second_received.get(),
            "the second incomparable state received no update");
    require(first_state.values() == Bytes({2, 3}),
            "the first incomparable state did not merge");
    require(second_state.values() == Bytes({2, 3}),
            "the second incomparable state did not merge");

    first.stop();
    second.stop();
}

void test_delivery_queue_overflow(std::uint16_t port) {
    VectorState sender_state(Bytes{5});
    VectorState receiver_state(Bytes{0});
    Config sender_config = local_config(port, 30, 303);
    Config receiver_config = local_config(port, 40, 404);
    receiver_config.delivery_queue_capacity = 1;

    Trickle sender(sender_config, sender_state);
    Trickle receiver(receiver_config, receiver_state);
    require(sender.start(), "overflow sender failed to start");
    require(receiver.start(), "overflow receiver failed to start");
    require(sender.notify_state_changed(),
            "overflow sender state change was rejected");

    require(wait_until(
                [&] {
                    return receiver.stats().delivery_queue_overflows > 0;
                },
                2s),
            "a slow consumer did not report delivery queue overflow");

    const auto snapshot = receiver.stats();
    require(snapshot.pending_deliveries == 1,
            "the bounded delivery queue retained an unexpected count");

    sender.stop();
    receiver.stop();
}

void test_shutdown_unblocks_receive(std::uint16_t port) {
    VectorState state(Bytes{0});
    Trickle trickle(local_config(port, 50, 505), state);
    require(trickle.start(), "shutdown instance failed to start");

    auto blocked = std::async(std::launch::async, [&] {
        return trickle.receive();
    });
    std::this_thread::sleep_for(20ms);
    trickle.stop();

    require(blocked.wait_for(1s) == std::future_status::ready,
            "stop did not unblock receive");
    require(!blocked.get().has_value(),
            "an empty instance returned an update during shutdown");
}

}  // namespace

int main() {
    const auto base =
        static_cast<std::uint16_t>(47000 + (::getpid() % 7000));
    test_invalid_config();
    test_dissemination_and_shutdown(base);
    test_incomparable_states(
        static_cast<std::uint16_t>(base + 1));
    test_delivery_queue_overflow(
        static_cast<std::uint16_t>(base + 2));
    test_shutdown_unblocks_receive(
        static_cast<std::uint16_t>(base + 3));
    std::cout << "PASS\n";
    return 0;
}
