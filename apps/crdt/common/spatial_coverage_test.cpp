#include "spatial_coverage.hpp"

#include <atomic>
#include <chrono>
#include <cstring>
#include <cstdlib>
#include <iostream>
#include <limits>
#include <set>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

using mace::coverage::Bytes;
using mace::coverage::CellId;
using mace::coverage::CoverageWorkload;
using mace::coverage::GridSpec;
using mace::coverage::ObservationStatus;
using mace::coverage::Position;

namespace {

int failures = 0;

void check(bool condition, const char* description) {
    if (!condition) {
        std::cerr << "FAIL: " << description << "\n";
        ++failures;
    }
}

template <typename T>
void check_equal(const T& actual, const T& expected, const char* description) {
    check(actual == expected, description);
}

template <typename Function>
void check_throws(Function&& function, const char* description) {
    try {
        function();
        check(false, description);
    } catch (const std::exception&) {
    }
}

GridSpec four_by_four() {
    return GridSpec{0.0, 0.0, 40.0, 40.0, 4, 4};
}

void test_grid_mapping_and_boundaries() {
    const GridSpec grid = four_by_four();
    check_equal(grid.locate(Position{0.0, 0.0, 0.0})->id,
                CellId{0},
                "origin maps to cell zero");
    check_equal(grid.locate(Position{10.0, 10.0, 0.0})->id,
                CellId{5},
                "internal boundary maps right and up");
    check_equal(grid.locate(Position{40.0, 40.0, 0.0})->id,
                CellId{15},
                "exact upper boundary is tolerated and clamped");
    check(grid.locate(Position{40.0 + grid.tolerance() * 0.5, 20.0, 0.0})
              .has_value(),
          "minimal upper-bound floating tolerance is accepted");
    check(!grid.locate(Position{40.0 + grid.tolerance() * 2.0, 20.0, 0.0})
               .has_value(),
          "clearly external upper coordinate is rejected");
    check(!grid.locate(Position{-1.0, 0.0, 0.0}).has_value(),
          "clearly external lower coordinate is rejected");

    const GridSpec negative_origin{-40.0, -40.0, 40.0, 40.0, 4, 4};
    check_equal(negative_origin.locate(Position{0.0, 0.0, 0.0})->id,
                CellId{15},
                "upper clamp survives rounding with a negative origin");

    for (std::uint32_t row = 0; row < grid.rows; ++row) {
        for (std::uint32_t col = 0; col < grid.cols; ++col) {
            const auto id = static_cast<CellId>(row * grid.cols + col);
            const auto cell = grid.from_id(id);
            check(cell.row == row && cell.col == col && cell.id == id,
                  "cell id inverse transformation round-trips");
        }
    }
}

void test_grid_validation() {
    check_throws(
        [] { GridSpec{0, 0, 10, 10, 0, 1}.validate(); },
        "zero rows are rejected");
    check_throws(
        [] { GridSpec{0, 0, 10, 10, 257, 256}.validate(); },
        "CellId overflow is rejected");
    check_throws(
        [] {
            GridSpec{std::numeric_limits<double>::max(), 0, 1, 10, 1, 1}
                .validate();
        },
        "an unrepresentable upper grid bound is rejected");
    check_throws(
        [] {
            GridSpec{std::numeric_limits<double>::max(),
                     0,
                     std::numeric_limits<double>::max(),
                     10,
                     1,
                     1}
                .validate();
        },
        "an infinite derived upper grid bound is rejected");
    check_throws(
        [] {
            GridSpec{0,
                     0,
                     std::numeric_limits<double>::denorm_min(),
                     1,
                     1,
                     2}
                .validate();
        },
        "an underflowed cell dimension is rejected");
    check_throws(
        [] {
            mace::coverage::validate_datagram_budget(
                GridSpec{0, 0, 10, 10, 25, 24}, 1200, 13);
        },
        "full state above datagram budget is rejected");
    mace::coverage::validate_datagram_budget(four_by_four(), 1200, 13);
    mace::coverage::validate_datagram_budget(
        GridSpec{0, 0, 10, 10, 1, 600}, 1200, 0);
    mace::coverage::validate_datagram_budget(
        GridSpec{0, 0, 10, 10, 1, 593}, 1200, 14);
    check_throws(
        [] {
            mace::coverage::validate_datagram_budget(
                GridSpec{0, 0, 10, 10, 1, 593}, 1200, 15);
        },
        "one byte above the exact datagram budget is rejected");
    check_throws(
        [] {
            mace::coverage::validate_datagram_budget(
                GridSpec{0, 0, 10, 10, 1, 1},
                std::numeric_limits<std::size_t>::max(),
                std::numeric_limits<std::size_t>::max());
        },
        "datagram budget arithmetic cannot wrap around");
}

void test_traversal() {
    const GridSpec grid = four_by_four();
    check_equal(grid.traverse(Position{1, 1, 0}, Position{39, 1, 0}),
                std::vector<CellId>({0, 1, 2, 3}),
                "horizontal traversal");
    check_equal(grid.traverse(Position{1, 1, 0}, Position{1, 39, 0}),
                std::vector<CellId>({0, 4, 8, 12}),
                "vertical traversal");
    check_equal(grid.traverse(Position{1, 1, 0}, Position{39, 39, 0}),
                std::vector<CellId>({0, 5, 10, 15}),
                "diagonal corner traversal advances both axes");
    check_equal(grid.traverse(Position{1, 1, 0}, Position{19, 39, 0}),
                std::vector<CellId>({0, 4, 9, 13}),
                "non-square diagonal reaches one axis before the other");
    check_equal(grid.traverse(Position{10, 1, 0}, Position{10, 39, 0}),
                std::vector<CellId>({1, 5, 9, 13}),
                "segment on boundary follows floor-selected side");
    check(grid.traverse(Position{-1, 1, 0}, Position{20, 1, 0}).empty(),
          "traversal with invalid endpoint is ignored");
}

void test_codec_and_merge() {
    const std::set<CellId> expected{0, 1, 255, 256, 65535};
    const Bytes encoded = mace::coverage::serialize_gset(expected);
    check_equal(encoded,
                Bytes({0x00, 0x00,
                       0x00, 0x01,
                       0x00, 0xff,
                       0x01, 0x00,
                       0xff, 0xff}),
                "GSet uses sorted uint16 network byte order");
    check_equal(mace::coverage::deserialize_gset(encoded, 65536),
                expected,
                "GSet serialization round-trips exactly");
    check_throws(
        [] {
            (void)mace::coverage::deserialize_gset(Bytes{0}, 16);
        },
        "odd payload is rejected");
    check_throws(
        [] {
            (void)mace::coverage::deserialize_gset(Bytes{0, 16}, 16);
        },
        "out-of-grid id is rejected");
    check_throws(
        [] {
            (void)mace::coverage::deserialize_gset(Bytes{0, 1, 0, 1}, 16);
        },
        "duplicate wire ids are rejected");
    check_throws(
        [] {
            (void)mace::coverage::deserialize_gset(Bytes{0, 2, 0, 1}, 16);
        },
        "out-of-order wire ids are rejected");
    check_throws(
        [] {
            (void)mace::coverage::deserialize_gset(
                static_cast<const std::uint8_t*>(nullptr), 2, 16);
        },
        "non-empty null payload is rejected");
    check_equal(
        mace::coverage::deserialize_gset(
            static_cast<const std::uint8_t*>(nullptr), 0, 16),
        std::set<CellId>{},
        "empty null payload is a valid empty GSet");

    mace::coverage::GSetReplica replica(16);
    const auto first = replica.merge(std::set<CellId>{1, 2});
    const auto second = replica.merge(std::set<CellId>{1, 2});
    check(first.changed && first.replica_size == 2,
          "first remote merge changes GSet");
    check(first.mutation_sequence == 1 && first.timestamp_unix_s > 0.0,
          "first mutation is stamped under the replica lock");
    check(!second.changed && second.replica_size == 2,
          "remote merge remains idempotent");
    check(second.mutation_sequence == 1 && second.timestamp_unix_s == 0.0,
          "idempotent merge does not allocate a mutation version");

    const auto third = replica.add_local(std::vector<CellId>{3});
    check(third.changed && third.mutation_sequence == 2,
          "local and remote mutations share one contiguous version sequence");
    check(third.timestamp_unix_s >= first.timestamp_unix_s,
          "mutation timestamps do not regress within one replica");
}

void test_application_trigger_semantics() {
    CoverageWorkload workload(GridSpec{0, 0, 30, 10, 1, 3});
    const auto initial = workload.observe(Position{1, 5, 0});
    check(initial.status == ObservationStatus::Changed,
          "first valid position changes state");
    check_equal(initial.update.added,
                std::vector<CellId>({0}),
                "first position covers its cell");
    check_equal(initial.update.snapshot,
                Bytes({0, 0}),
                "first position provides full trigger snapshot");
    check(initial.update.mutation_sequence == 1,
          "first local trigger carries replica version one");

    const auto same = workload.observe(Position{2, 5, 0});
    check(same.status == ObservationStatus::NoChange,
          "remaining in one cell does not trigger");

    const auto remote = workload.merge_remote(Bytes{0, 2});
    check(remote.changed && workload.size() == 2,
          "remote state changes replica without local observation");
    check(remote.mutation_sequence == 2 &&
              remote.timestamp_unix_s >= initial.update.timestamp_unix_s,
          "remote merge advances the same mutation timeline");

    const auto known = workload.observe(Position{29, 5, 0});
    check(known.status == ObservationStatus::Changed,
          "segment still triggers for another crossed new cell");
    check_equal(known.update.added,
                std::vector<CellId>({1}),
                "known remote endpoint is not reported as locally new");
    check_equal(mace::coverage::deserialize_gset(known.update.snapshot, 3),
                std::set<CellId>({0, 1, 2}),
                "trigger includes knowledge previously learned remotely");

    CoverageWorkload known_only(GridSpec{0, 0, 20, 10, 1, 2});
    (void)known_only.observe(Position{1, 5, 0});
    (void)known_only.merge_remote(Bytes{0, 1});
    const auto enter_known = known_only.observe(Position{19, 5, 0});
    check(enter_known.status == ObservationStatus::NoChange,
          "entering a remotely known cell does not trigger");

    CoverageWorkload batched(GridSpec{0, 0, 40, 10, 1, 4});
    (void)batched.observe(Position{1, 5, 0});
    const auto multi = batched.observe(Position{39, 5, 0});
    check_equal(multi.update.added,
                std::vector<CellId>({1, 2, 3}),
                "one polling segment batches all crossed new cells");
    check(multi.status == ObservationStatus::Changed,
          "batched segment produces one changed observation");
}

void test_invalid_gps_sample_preserves_last_valid_position() {
    CoverageWorkload workload(GridSpec{0, 0, 30, 10, 1, 3});
    (void)workload.observe(Position{1, 5, 0});
    const auto invalid = workload.observe(Position{-100, 5, 0});
    check(invalid.status == ObservationStatus::InvalidPosition,
          "invalid GPS position creates no artificial cell");
    const auto recovered = workload.observe(Position{29, 5, 0});
    check_equal(recovered.update.added,
                std::vector<CellId>({1, 2}),
                "recovery traverses from the last valid position");
}

class BlockingSource final : public mace::coverage::PositionSource {
public:
    std::optional<Position> get_position() override {
        std::this_thread::sleep_for(std::chrono::milliseconds(20));
        return Position{1, 1, 0};
    }
};

void test_polling_is_independent_from_remote_merge() {
    CoverageWorkload workload(GridSpec{0, 0, 20, 10, 1, 2});
    BlockingSource source;
    std::atomic<bool> running{true};
    std::atomic<int> triggers{0};
    std::atomic<std::uint64_t> trigger_version{0};
    std::thread poller([&] {
        mace::coverage::polling_loop(
            source,
            workload,
            running,
            std::chrono::milliseconds(10),
            [&](const mace::coverage::StateUpdate& update) {
                trigger_version.store(update.mutation_sequence);
                triggers.fetch_add(1);
                running.store(false);
            });
    });

    const auto before = std::chrono::steady_clock::now();
    const auto merged = workload.merge_remote(Bytes{0, 1});
    const auto elapsed = std::chrono::steady_clock::now() - before;
    check(merged.changed, "communication-side merge succeeds during GPS polling");
    check(merged.mutation_sequence == 1,
          "concurrent remote mutation receives the first replica version");
    check(elapsed < std::chrono::milliseconds(15),
          "GPS source wait does not hold replica lock");
    poller.join();
    check(triggers.load() == 1,
          "polling thread generates exactly one initial local trigger");
    check(trigger_version.load() == 2,
          "subsequent local callback carries the next replica version");
}

void encode_network_double(std::uint8_t* output, double value) {
    static_assert(sizeof(value) == sizeof(std::uint64_t),
                  "test requires binary64 doubles");
    std::uint64_t bits = 0;
    std::memcpy(&bits, &value, sizeof(bits));
    for (std::size_t index = 0; index < sizeof(bits); ++index) {
        output[index] = static_cast<std::uint8_t>(
            (bits >> (8 * (sizeof(bits) - index - 1))) & 0xff);
    }
}

void test_cpp_gps_client_reads_fragmented_binary_response() {
    const std::string socket_path =
        "/tmp/mace_spatial_gps_test_" + std::to_string(::getpid()) + ".sock";
    (void)::unlink(socket_path.c_str());
    const int server_fd = ::socket(AF_UNIX, SOCK_STREAM, 0);
    check(server_fd >= 0, "GPS test server socket is created");
    if (server_fd < 0) {
        return;
    }

    sockaddr_un address{};
    address.sun_family = AF_UNIX;
    std::memcpy(address.sun_path,
                socket_path.c_str(),
                socket_path.size() + 1);
    const bool ready =
        ::bind(server_fd,
               reinterpret_cast<const sockaddr*>(&address),
               sizeof(address)) == 0 &&
        ::listen(server_fd, 1) == 0;
    check(ready, "GPS test server binds and listens");
    if (!ready) {
        ::close(server_fd);
        (void)::unlink(socket_path.c_str());
        return;
    }

    std::atomic<bool> server_ok{true};
    std::thread server([&] {
        const int client_fd = ::accept(server_fd, nullptr, nullptr);
        if (client_fd < 0) {
            server_ok.store(false);
            return;
        }

        char request[sizeof(mace::coverage::kGpsV1Request)]{};
        std::size_t received_total = 0;
        while (received_total < sizeof(request)) {
            const ssize_t received = ::recv(
                client_fd,
                request + received_total,
                sizeof(request) - received_total,
                0);
            if (received <= 0) {
                server_ok.store(false);
                break;
            }
            received_total += static_cast<std::size_t>(received);
        }
        if (received_total == sizeof(request) &&
            !std::equal(std::begin(request),
                        std::end(request),
                        std::begin(mace::coverage::kGpsV1Request))) {
            server_ok.store(false);
        }

        std::uint8_t response[29]{'G', 'P', 'S', '1', 1};
        encode_network_double(response + 5, 12.5);
        encode_network_double(response + 13, -3.25);
        encode_network_double(response + 21, 7.75);
        // Exercise receive_exact(): a stream response need not arrive in one
        // recv() even though the server wrote the logical frame at once.
        for (std::size_t offset = 0; offset < sizeof(response);) {
            const std::size_t chunk = std::min<std::size_t>(3, sizeof(response) - offset);
            const ssize_t sent = ::send(
                client_fd, response + offset, chunk, MSG_NOSIGNAL);
            if (sent <= 0) {
                server_ok.store(false);
                break;
            }
            offset += static_cast<std::size_t>(sent);
        }
        ::close(client_fd);
    });

    mace::coverage::UnixGpsPositionSource source(
        socket_path, std::chrono::milliseconds(250));
    const auto position = source.get_position();
    server.join();
    ::close(server_fd);
    (void)::unlink(socket_path.c_str());

    check(server_ok.load(), "GPS1 request and response complete successfully");
    check(position.has_value(), "C++ GPS client accepts a valid GPS1 response");
    if (position) {
        check(std::abs(position->x - 12.5) < 1e-12,
              "GPS1 x coordinate decodes from network byte order");
        check(std::abs(position->y + 3.25) < 1e-12,
              "GPS1 y coordinate decodes from network byte order");
        check(std::abs(position->z - 7.75) < 1e-12,
              "GPS1 z coordinate decodes from network byte order");
    }
}

void test_long_wait_is_interrupted_by_shutdown() {
    std::atomic<bool> running{true};
    bool stopped = false;
    const auto started = std::chrono::steady_clock::now();
    std::thread sleeper([&] {
        stopped = mace::coverage::sleep_for_or_stopped(
            running, std::chrono::seconds(5));
    });
    std::this_thread::sleep_for(std::chrono::milliseconds(20));
    running.store(false);
    sleeper.join();
    const auto elapsed = std::chrono::steady_clock::now() - started;
    check(stopped, "interruptible wait reports shutdown");
    check(elapsed < std::chrono::milliseconds(500),
          "shutdown interrupts a long configured wait promptly");
}

}  // namespace

int main() {
    test_grid_mapping_and_boundaries();
    test_grid_validation();
    test_traversal();
    test_codec_and_merge();
    test_application_trigger_semantics();
    test_invalid_gps_sample_preserves_last_valid_position();
    test_polling_is_independent_from_remote_merge();
    test_cpp_gps_client_reads_fragmented_binary_response();
    test_long_wait_is_interrupted_by_shutdown();
    if (failures != 0) {
        std::cerr << failures << " spatial coverage test(s) failed\n";
        return EXIT_FAILURE;
    }
    std::cout << "all spatial coverage tests passed\n";
    return EXIT_SUCCESS;
}
