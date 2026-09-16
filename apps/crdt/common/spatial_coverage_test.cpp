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
    // 44-byte grid header, 13-byte RAPID/Trickle header, 1343 bitmap bytes.
    mace::coverage::validate_datagram_budget(
        GridSpec{0, 0, 10, 10, 1, 10744}, 1400, 13);
    mace::coverage::validate_datagram_budget(
        GridSpec{0, 0, 10, 10, 1, 10744}, 1400, 0);
    check_throws([] {
        mace::coverage::validate_datagram_budget(
            GridSpec{0, 0, 10, 10, 1, 10745}, 1400, 13);
    }, "one bitmap byte above the exact datagram budget is rejected");
    check_throws([] {
        mace::coverage::validate_datagram_budget(
            GridSpec{0, 0, 10, 10, 256, 256}, 1400, 13);
    }, "65536-cell grid is representable but exceeds the datagram budget");
    check_throws([] {
        mace::coverage::validate_datagram_budget(
            four_by_four(), std::numeric_limits<std::size_t>::max(),
            std::numeric_limits<std::size_t>::max());
    }, "datagram budget arithmetic cannot wrap around");

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

Bytes frame(const GridSpec& grid, const Bytes& bitmap) {
    return mace::coverage::serialize_bitmap(bitmap, grid);
}

void test_codec_and_merge() {
    using mace::coverage::BitmapReplica;
    using mace::coverage::deserialize_bitmap;
    using mace::coverage::bitmap_contains;
    const GridSpec grid{0, 0, 130, 10, 1, 13};
    BitmapReplica replica(grid);
    check_equal(replica.bitmap(), Bytes({0, 0}), "bitmap starts all zero");
    check(!replica.contains(7), "unset bit is absent");
    const auto initial = replica.add_local({0, 7, 8, 12, 7});
    check_equal(replica.bitmap(), Bytes({0x81, 0x11}),
                "cell IDs use LSB-first bit order within each byte");
    check(replica.contains(7) && replica.contains(12) && replica.size() == 4,
          "contains and count agree with activated bits");
    check(!replica.add_local({7}).changed, "adding a known bit is idempotent");
    check_equal(initial.snapshot.size(), std::size_t{46},
                "frame has 44 grid bytes plus two raw bitmap bytes");
    check_equal(deserialize_bitmap(initial.snapshot, grid), replica.bitmap(),
                "binary bitmap round-trips including padding");
    check_equal(Bytes(initial.snapshot.end() - 2, initial.snapshot.end()),
                Bytes({0x81, 0x11}), "wire carries raw bitmap bytes");
    auto invalid = initial.snapshot;
    invalid.back() |= 0x80;
    check_throws([&] { replica.merge_serialized(invalid); },
                 "nonzero padding bits are rejected");
    invalid = initial.snapshot;
    invalid.pop_back();
    check_throws([&] { replica.merge_serialized(invalid); },
                 "truncated frame is rejected");
    invalid = initial.snapshot;
    invalid.push_back(0);
    check_throws([&] { replica.merge_serialized(invalid); },
                 "oversized frame is rejected");
    check_throws([&] { replica.merge_serialized(Bytes{}); },
                 "empty datagram cannot stand for an empty fixed-size bitmap");
    check_throws([&] {
        deserialize_bitmap(static_cast<const std::uint8_t*>(nullptr), 46, grid);
    }, "null payload is rejected");
    for (const auto& other : {
             GridSpec{0, 0, 130, 10, 13, 1},
             GridSpec{1, 0, 130, 10, 1, 13},
             GridSpec{0, 1, 130, 10, 1, 13},
             GridSpec{0, 0, 140, 10, 1, 13},
             GridSpec{0, 0, 130, 20, 1, 13},
             GridSpec{0, 0, 130, 10, 1, 14}}) {
        check_throws([&] { replica.merge_serialized(frame(other, {0, 0})); },
                     "incompatible grid is rejected even at the same byte length");
    }
    check_throws([&] { replica.add_local({1, 13}); },
                 "out-of-grid local cell rejected before partial mutation");
    check_equal(replica.size(), std::size_t{4}, "invalid inputs leave state intact");
    const auto remote = frame(grid, {0x82, 0x01});
    const auto first = replica.merge_serialized(remote);
    const auto second = replica.merge_serialized(remote);
    check(first.changed && first.replica_size == 5 && first.snapshot.empty(),
          "overlapping remote merge adds one bit without an application snapshot");
    check(first.mutation_sequence == 2 && first.timestamp_unix_s > 0.0,
          "remote mutation uses the shared version sequence");
    check(!second.changed && second.mutation_sequence == 2 &&
          second.timestamp_unix_s == 0.0, "remote merge is idempotent");
    check_equal(replica.bitmap(), Bytes({0x83, 0x11}), "merge is bytewise OR");
    check(bitmap_contains({3}, {1}) && !bitmap_contains({1}, {3}),
          "subset comparison distinguishes local and remote newer");
    check(!bitmap_contains({1}, {2}) && !bitmap_contains({2}, {1}),
          "equal cardinality does not imply consistency");
    check(bitmap_contains({3}, {3}), "equal states are consistent");
    const Bytes a{0x81, 0x10}, b{0x02, 0x01}, c{0x04, 0x10};
    BitmapReplica abc(grid), cba(grid), bc(grid), grouped(grid);
    for (const auto& v : {a, b, c}) abc.merge_serialized(frame(grid, v));
    for (const auto& v : {c, b, a}) cba.merge_serialized(frame(grid, v));
    bc.merge_serialized(frame(grid, b));
    bc.merge_serialized(frame(grid, c));
    grouped.merge_serialized(frame(grid, a));
    grouped.merge_serialized(bc.serialize());
    check_equal(abc.bitmap(), cba.bitmap(), "merge is commutative");
    check_equal(abc.bitmap(), grouped.bitmap(), "merge is associative");
    const GridSpec large{0, 0, 256, 256, 256, 256};
    BitmapReplica full(large);
    full.add_local({0, 65535});
    check(full.contains(65535) && full.bitmap().size() == 8192 && full.size() == 2,
          "65536 cells do not overflow cell count or final bit");
    check_equal(deserialize_bitmap(full.serialize(), large), full.bitmap(),
                "largest representable grid round-trips");
    check_equal(frame(GridSpec{-0.0, 0, 130, 10, 1, 13}, {0, 0}),
                frame(grid, {0, 0}), "signed zero origins have one canonical header");
}

void test_concurrent_snapshots() {
    const GridSpec grid{0, 0, 256, 256, 16, 16};
    mace::coverage::BitmapReplica replica(grid);
    std::thread local([&] {
        for (unsigned id = 0; id < 128; ++id) replica.add_local({CellId(id)});
    });
    std::thread remote([&] {
        mace::coverage::BitmapReplica other(grid);
        for (unsigned id = 128; id < 256; ++id) {
            other.add_local({CellId(id)});
            replica.merge_serialized(other.serialize());
        }
    });
    Bytes previous(32, 0);
    for (int i = 0; i < 256; ++i) {
        const auto snapshot = mace::coverage::deserialize_bitmap(replica.serialize(), grid);
        check(mace::coverage::bitmap_contains(snapshot, previous),
              "concurrent complete snapshots grow monotonically");
        previous = snapshot;
    }
    local.join();
    remote.join();
    check_equal(replica.bitmap(), Bytes(32, 0xff), "concurrent merges lose no bits");
    check_equal(replica.size(), std::size_t{256}, "concurrent count stays consistent");
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
                frame(workload.grid(), {1}),
                "first position provides full trigger snapshot");
    check(initial.update.mutation_sequence == 1,
          "first local trigger carries replica version one");

    const auto same = workload.observe(Position{2, 5, 0});
    check(same.status == ObservationStatus::NoChange,
          "remaining in one cell does not trigger");

    const auto remote = workload.merge_remote(frame(workload.grid(), {4}));
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
    check_equal(mace::coverage::deserialize_bitmap(known.update.snapshot, workload.grid()),
                Bytes({7}),
                "trigger includes knowledge previously learned remotely");

    CoverageWorkload known_only(GridSpec{0, 0, 20, 10, 1, 2});
    (void)known_only.observe(Position{1, 5, 0});
    (void)known_only.merge_remote(frame(known_only.grid(), {2}));
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
    const auto merged = workload.merge_remote(frame(workload.grid(), {2}));
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
    test_concurrent_snapshots();
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
