#ifndef MACE_CRDT_SPATIAL_COVERAGE_HPP
#define MACE_CRDT_SPATIAL_COVERAGE_HPP

#include <algorithm>
#include <atomic>
#include <cerrno>
#include <chrono>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <functional>
#include <iterator>
#include <limits>
#include <mutex>
#include <optional>
#include <set>
#include <stdexcept>
#include <string>
#include <thread>
#include <utility>
#include <vector>

#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

namespace mace {
namespace coverage {

using CellId = std::uint16_t;
using Bytes = std::vector<std::uint8_t>;

inline double unix_time_seconds() {
    return std::chrono::duration<double>(
               std::chrono::system_clock::now().time_since_epoch())
        .count();
}

constexpr std::size_t kDefaultMaxDatagramBytes = 1200;
constexpr std::chrono::milliseconds kDefaultPollInterval{100};
constexpr std::chrono::milliseconds kDefaultGpsTimeout{50};
constexpr char kGpsV1Request[4] = {'G', 'P', 'S', '1'};

struct Position {
    double x{0.0};
    double y{0.0};
    double z{0.0};
};

struct Cell {
    std::uint32_t row{0};
    std::uint32_t col{0};
    CellId id{0};
};

// Canonical spatial contract used by the C++ workload. The Python trace
// checker in evaluation/spatial_coverage.py implements the same boundary and
// corner rules and is checked against shared conformance vectors.
struct GridSpec {
    double origin_x_m{0.0};
    double origin_y_m{0.0};
    double width_m{0.0};
    double height_m{0.0};
    std::uint32_t rows{0};
    std::uint32_t cols{0};

    std::uint64_t cell_count() const {
        return static_cast<std::uint64_t>(rows) *
               static_cast<std::uint64_t>(cols);
    }

    double cell_width() const {
        return width_m / static_cast<double>(cols);
    }

    double cell_height() const {
        return height_m / static_cast<double>(rows);
    }

    void validate() const {
        if (!std::isfinite(origin_x_m) || !std::isfinite(origin_y_m) ||
            !std::isfinite(width_m) || !std::isfinite(height_m)) {
            throw std::invalid_argument("GridSpec values must be finite");
        }
        if (width_m <= 0.0 || height_m <= 0.0) {
            throw std::invalid_argument("GridSpec width_m and height_m must be > 0");
        }
        const double upper_x = origin_x_m + width_m;
        const double upper_y = origin_y_m + height_m;
        if (!std::isfinite(upper_x) || !std::isfinite(upper_y) ||
            upper_x <= origin_x_m || upper_y <= origin_y_m) {
            throw std::invalid_argument(
                "GridSpec upper bounds must be finite and representable");
        }
        if (rows == 0 || cols == 0) {
            throw std::invalid_argument("GridSpec rows and cols must be > 0");
        }
        const double derived_cell_width =
            width_m / static_cast<double>(cols);
        const double derived_cell_height =
            height_m / static_cast<double>(rows);
        if (!std::isfinite(derived_cell_width) ||
            !std::isfinite(derived_cell_height) ||
            derived_cell_width <= 0.0 || derived_cell_height <= 0.0) {
            throw std::invalid_argument(
                "GridSpec cell dimensions must be finite and representable");
        }
        constexpr std::uint64_t capacity =
            static_cast<std::uint64_t>(std::numeric_limits<CellId>::max()) + 1ULL;
        if (cell_count() > capacity) {
            throw std::invalid_argument("GridSpec has more cells than CellId can represent");
        }
    }

    double tolerance() const {
        const double upper_x = origin_x_m + width_m;
        const double upper_y = origin_y_m + height_m;
        return 1e-9 * std::max(
            {1.0,
             std::abs(origin_x_m),
             std::abs(origin_y_m),
             std::abs(upper_x),
             std::abs(upper_y),
             width_m,
             height_m});
    }

    std::optional<Position> normalize(const Position& position) const {
        validate();
        if (!std::isfinite(position.x) || !std::isfinite(position.y) ||
            !std::isfinite(position.z)) {
            return std::nullopt;
        }

        const double upper_x = origin_x_m + width_m;
        const double upper_y = origin_y_m + height_m;
        const double epsilon = tolerance();
        if (position.x < origin_x_m - epsilon ||
            position.x > upper_x + epsilon ||
            position.y < origin_y_m - epsilon ||
            position.y > upper_y + epsilon) {
            return std::nullopt;
        }

        Position normalized = position;
        normalized.x = std::max(origin_x_m, std::min(position.x, upper_x));
        normalized.y = std::max(origin_y_m, std::min(position.y, upper_y));
        // The upper bound is conceptually exclusive. An exact (or tolerated)
        // upper-bound sample belongs deterministically to the final cell.
        if (normalized.x >= upper_x) {
            normalized.x = std::nextafter(upper_x, origin_x_m);
        }
        if (normalized.y >= upper_y) {
            normalized.y = std::nextafter(upper_y, origin_y_m);
        }
        return normalized;
    }

    std::optional<Cell> locate(const Position& position) const {
        const auto normalized = normalize(position);
        if (!normalized) {
            return std::nullopt;
        }

        const double raw_col = std::floor(
            (normalized->x - origin_x_m) / cell_width());
        const double raw_row = std::floor(
            (normalized->y - origin_y_m) / cell_height());
        if (raw_row < 0.0 || raw_col < 0.0 ||
            raw_row > static_cast<double>(rows) ||
            raw_col > static_cast<double>(cols)) {
            return std::nullopt;
        }
        // nextafter(upper, origin) can be lost when subtracting a negative
        // origin and dividing by the cell size (for example [-40, 0]).  The
        // position was already normalized into the domain, so an index that
        // rounded to exactly rows/cols still denotes the final cell.
        const auto col = raw_col == static_cast<double>(cols)
                             ? cols - 1
                             : static_cast<std::uint32_t>(raw_col);
        const auto row = raw_row == static_cast<double>(rows)
                             ? rows - 1
                             : static_cast<std::uint32_t>(raw_row);
        const std::uint64_t raw_id =
            static_cast<std::uint64_t>(row) * cols + col;
        return Cell{row, col, static_cast<CellId>(raw_id)};
    }

    Cell from_id(CellId id) const {
        validate();
        if (static_cast<std::uint64_t>(id) >= cell_count()) {
            throw std::out_of_range("cell id is outside GridSpec");
        }
        return Cell{
            static_cast<std::uint32_t>(id / cols),
            static_cast<std::uint32_t>(id % cols),
            id};
    }

    // Amanatides-Woo/2D-DDA traversal. At an exact corner both axes advance
    // together, so only the diagonally-entered cell is added. A segment that
    // lies exactly on an internal boundary follows the cell selected by
    // locate() (floor/right-and-upper side). These rules are deterministic.
    std::vector<CellId> traverse(const Position& start,
                                 const Position& end) const {
        const auto normalized_start = normalize(start);
        const auto normalized_end = normalize(end);
        if (!normalized_start || !normalized_end) {
            return {};
        }
        const auto first = locate(*normalized_start);
        const auto last = locate(*normalized_end);
        if (!first || !last) {
            return {};
        }

        std::vector<CellId> cells;
        std::vector<bool> seen(static_cast<std::size_t>(cell_count()), false);
        auto append = [&](CellId id) {
            const auto index = static_cast<std::size_t>(id);
            if (!seen[index]) {
                seen[index] = true;
                cells.push_back(id);
            }
        };

        std::int64_t row = first->row;
        std::int64_t col = first->col;
        const std::int64_t target_row = last->row;
        const std::int64_t target_col = last->col;
        append(first->id);
        if (row == target_row && col == target_col) {
            return cells;
        }

        const double dx = normalized_end->x - normalized_start->x;
        const double dy = normalized_end->y - normalized_start->y;
        const int step_x = (dx > 0.0) - (dx < 0.0);
        const int step_y = (dy > 0.0) - (dy < 0.0);
        const double infinity = std::numeric_limits<double>::infinity();

        double t_delta_x = infinity;
        double t_delta_y = infinity;
        double t_max_x = infinity;
        double t_max_y = infinity;

        if (step_x != 0) {
            t_delta_x = cell_width() / std::abs(dx);
            const double boundary_x =
                origin_x_m +
                (step_x > 0 ? static_cast<double>(col + 1)
                            : static_cast<double>(col)) *
                    cell_width();
            t_max_x = (boundary_x - normalized_start->x) / dx;
            if (t_max_x < 0.0 && t_max_x > -1e-14) {
                t_max_x = 0.0;
            }
        }
        if (step_y != 0) {
            t_delta_y = cell_height() / std::abs(dy);
            const double boundary_y =
                origin_y_m +
                (step_y > 0 ? static_cast<double>(row + 1)
                            : static_cast<double>(row)) *
                    cell_height();
            t_max_y = (boundary_y - normalized_start->y) / dy;
            if (t_max_y < 0.0 && t_max_y > -1e-14) {
                t_max_y = 0.0;
            }
        }

        const std::uint64_t iteration_limit =
            static_cast<std::uint64_t>(rows) + cols + 4ULL;
        for (std::uint64_t iteration = 0;
             iteration < iteration_limit &&
             (row != target_row || col != target_col);
             ++iteration) {
            if (col == target_col) {
                row += step_y;
                t_max_y += t_delta_y;
            } else if (row == target_row) {
                col += step_x;
                t_max_x += t_delta_x;
            } else {
                const double scale =
                    std::max({1.0, std::abs(t_max_x), std::abs(t_max_y)});
                const double tie_epsilon =
                    64.0 * std::numeric_limits<double>::epsilon() * scale;

                if (t_max_x + tie_epsilon < t_max_y) {
                    col += step_x;
                    t_max_x += t_delta_x;
                } else if (t_max_y + tie_epsilon < t_max_x) {
                    row += step_y;
                    t_max_y += t_delta_y;
                } else {
                    col += step_x;
                    row += step_y;
                    t_max_x += t_delta_x;
                    t_max_y += t_delta_y;
                }
            }

            if (row < 0 || col < 0 ||
                row >= static_cast<std::int64_t>(rows) ||
                col >= static_cast<std::int64_t>(cols)) {
                throw std::logic_error("grid traversal escaped a validated GridSpec");
            }
            const auto raw_id =
                static_cast<std::uint64_t>(row) * cols +
                static_cast<std::uint64_t>(col);
            append(static_cast<CellId>(raw_id));
        }

        if (row != target_row || col != target_col) {
            throw std::logic_error("grid traversal failed to reach its final cell");
        }
        return cells;
    }
};

inline std::size_t serialized_size_for_cells(std::uint64_t count) {
    if (count > std::numeric_limits<std::size_t>::max() / sizeof(CellId)) {
        throw std::overflow_error("serialized GSet size overflows size_t");
    }
    return static_cast<std::size_t>(count) * sizeof(CellId);
}

inline void validate_datagram_budget(const GridSpec& grid,
                                     std::size_t max_datagram_bytes,
                                     std::size_t primitive_overhead_bytes) {
    grid.validate();
    if (max_datagram_bytes == 0 ||
        primitive_overhead_bytes > max_datagram_bytes) {
        throw std::invalid_argument("invalid maximum datagram budget");
    }
    const std::size_t state_size =
        serialized_size_for_cells(grid.cell_count());
    // Subtracting the already-validated overhead avoids wrapping size_t when
    // this reusable helper is called with a budget larger than the workload's
    // normal 1200-byte cap.
    if (state_size > max_datagram_bytes - primitive_overhead_bytes) {
        throw std::invalid_argument(
            "full GSet plus primitive metadata exceeds max_datagram_bytes");
    }
}

inline Bytes serialize_gset(const std::set<CellId>& cells) {
    Bytes output;
    output.reserve(serialized_size_for_cells(cells.size()));
    for (CellId id : cells) {
        // Network byte order, deterministic ascending order.
        output.push_back(static_cast<std::uint8_t>((id >> 8) & 0xff));
        output.push_back(static_cast<std::uint8_t>(id & 0xff));
    }
    return output;
}

inline std::set<CellId> deserialize_gset(const std::uint8_t* data,
                                        std::size_t size,
                                        std::uint64_t cell_count) {
    if (size != 0 && data == nullptr) {
        throw std::invalid_argument("GSet payload pointer is null");
    }
    if (size % sizeof(CellId) != 0) {
        throw std::invalid_argument("GSet payload has an odd byte count");
    }
    std::set<CellId> cells;
    std::optional<CellId> previous;
    for (std::size_t offset = 0; offset < size; offset += 2) {
        const CellId id = static_cast<CellId>(
            (static_cast<std::uint16_t>(data[offset]) << 8) |
            static_cast<std::uint16_t>(data[offset + 1]));
        if (static_cast<std::uint64_t>(id) >= cell_count) {
            throw std::invalid_argument("GSet payload contains an invalid cell id");
        }
        if (previous && id <= *previous) {
            throw std::invalid_argument(
                "GSet payload ids must be unique and strictly increasing");
        }
        cells.insert(id);
        previous = id;
    }
    return cells;
}

inline std::set<CellId> deserialize_gset(const Bytes& data,
                                        std::uint64_t cell_count) {
    return deserialize_gset(data.data(), data.size(), cell_count);
}

inline std::set<CellId> deserialize_gset(const char* data,
                                        std::size_t size,
                                        std::uint64_t cell_count) {
    return deserialize_gset(
        reinterpret_cast<const std::uint8_t*>(data), size, cell_count);
}

struct StateUpdate {
    bool changed{false};
    std::vector<CellId> added;
    std::size_t replica_size{0};
    Bytes snapshot;
    // Assigned while holding the replica mutex so delayed logging callbacks
    // can reconstruct mutation order without trusting file append order.
    std::uint64_t mutation_sequence{0};
    double timestamp_unix_s{0.0};
};

class GSetReplica {
public:
    explicit GSetReplica(std::uint64_t cell_count)
        : cell_count_(cell_count) {
        constexpr std::uint64_t capacity =
            static_cast<std::uint64_t>(std::numeric_limits<CellId>::max()) + 1ULL;
        if (cell_count_ == 0 || cell_count_ > capacity) {
            throw std::invalid_argument("invalid GSet cell count");
        }
    }

    StateUpdate add_local(const std::vector<CellId>& candidates) {
        std::lock_guard<std::mutex> lock(mutex_);
        StateUpdate result;
        for (CellId id : candidates) {
            require_valid(id);
            if (cells_.insert(id).second) {
                result.added.push_back(id);
            }
        }
        std::sort(result.added.begin(), result.added.end());
        result.changed = !result.added.empty();
        result.replica_size = cells_.size();
        if (result.changed) {
            stamp_mutation(result);
            // State mutation and trigger snapshot are one critical section.
            result.snapshot = serialize_gset(cells_);
        } else {
            result.mutation_sequence = mutation_sequence_;
        }
        return result;
    }

    StateUpdate merge(const std::set<CellId>& remote) {
        std::lock_guard<std::mutex> lock(mutex_);
        StateUpdate result;
        for (CellId id : remote) {
            require_valid(id);
            if (cells_.insert(id).second) {
                result.added.push_back(id);
            }
        }
        result.changed = !result.added.empty();
        result.replica_size = cells_.size();
        if (result.changed) {
            stamp_mutation(result);
        } else {
            result.mutation_sequence = mutation_sequence_;
        }
        return result;
    }

    StateUpdate merge_serialized(const std::uint8_t* data, std::size_t size) {
        return merge(deserialize_gset(data, size, cell_count_));
    }

    StateUpdate merge_serialized(const Bytes& data) {
        return merge(deserialize_gset(data, cell_count_));
    }

    StateUpdate merge_serialized(const char* data, std::size_t size) {
        return merge(deserialize_gset(data, size, cell_count_));
    }

    std::set<CellId> cells() const {
        std::lock_guard<std::mutex> lock(mutex_);
        return cells_;
    }

    Bytes serialize() const {
        std::lock_guard<std::mutex> lock(mutex_);
        return serialize_gset(cells_);
    }

    std::size_t size() const {
        std::lock_guard<std::mutex> lock(mutex_);
        return cells_.size();
    }

private:
    void stamp_mutation(StateUpdate& result) {
        ++mutation_sequence_;
        result.mutation_sequence = mutation_sequence_;
        // The version is the authoritative tie-breaker. Clamping also avoids
        // a wall-clock adjustment making a single replica's timeline regress.
        last_mutation_timestamp_unix_s_ = std::max(
            last_mutation_timestamp_unix_s_, unix_time_seconds());
        result.timestamp_unix_s = last_mutation_timestamp_unix_s_;
    }

    void require_valid(CellId id) const {
        if (static_cast<std::uint64_t>(id) >= cell_count_) {
            throw std::out_of_range("cell id is outside this replica's grid");
        }
    }

    std::uint64_t cell_count_;
    mutable std::mutex mutex_;
    std::set<CellId> cells_;
    std::uint64_t mutation_sequence_{0};
    double last_mutation_timestamp_unix_s_{0.0};
};

enum class ObservationStatus {
    InvalidPosition,
    NoChange,
    Changed,
};

struct Observation {
    ObservationStatus status{ObservationStatus::InvalidPosition};
    StateUpdate update;
};

class CoverageWorkload {
public:
    explicit CoverageWorkload(GridSpec grid)
        : grid_(std::move(grid)), replica_(grid_.cell_count()) {
        grid_.validate();
    }

    Observation observe(const Position& position) {
        std::lock_guard<std::mutex> position_lock(position_mutex_);
        const auto normalized = grid_.normalize(position);
        if (!normalized) {
            return Observation{ObservationStatus::InvalidPosition, {}};
        }

        std::vector<CellId> crossed;
        if (previous_position_) {
            crossed = grid_.traverse(*previous_position_, *normalized);
        } else {
            const auto cell = grid_.locate(*normalized);
            if (!cell) {
                return Observation{ObservationStatus::InvalidPosition, {}};
            }
            crossed.push_back(cell->id);
        }
        previous_position_ = *normalized;

        StateUpdate update = replica_.add_local(crossed);
        return Observation{
            update.changed ? ObservationStatus::Changed
                           : ObservationStatus::NoChange,
            std::move(update)};
    }

    StateUpdate merge_remote(const Bytes& payload) {
        return replica_.merge_serialized(payload);
    }

    StateUpdate merge_remote(const std::uint8_t* data, std::size_t size) {
        return replica_.merge_serialized(data, size);
    }

    StateUpdate merge_remote(const char* data, std::size_t size) {
        return replica_.merge_serialized(data, size);
    }

    Bytes snapshot() const {
        return replica_.serialize();
    }

    std::set<CellId> cells() const {
        return replica_.cells();
    }

    std::size_t size() const {
        return replica_.size();
    }

    const GridSpec& grid() const {
        return grid_;
    }

private:
    GridSpec grid_;
    GSetReplica replica_;
    std::mutex position_mutex_;
    std::optional<Position> previous_position_;
};

class PositionSource {
public:
    virtual ~PositionSource() = default;
    virtual std::optional<Position> get_position() = 0;
};

class UnixGpsPositionSource final : public PositionSource {
public:
    explicit UnixGpsPositionSource(
        std::string socket_path,
        std::chrono::milliseconds timeout = kDefaultGpsTimeout)
        : socket_path_(std::move(socket_path)), timeout_(timeout) {
        if (socket_path_.empty()) {
            throw std::invalid_argument("GPS socket path must not be empty");
        }
        if (socket_path_.size() >= sizeof(sockaddr_un::sun_path)) {
            throw std::invalid_argument("GPS socket path is too long");
        }
        if (timeout_.count() <= 0) {
            throw std::invalid_argument("GPS timeout must be positive");
        }
    }

    std::optional<Position> get_position() override {
        const int fd = ::socket(AF_UNIX, SOCK_STREAM, 0);
        if (fd < 0) {
            return std::nullopt;
        }

        timeval timeout{};
        timeout.tv_sec = static_cast<time_t>(timeout_.count() / 1000);
        timeout.tv_usec = static_cast<suseconds_t>(
            (timeout_.count() % 1000) * 1000);
        (void)::setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
        (void)::setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout));

        sockaddr_un address{};
        address.sun_family = AF_UNIX;
        std::memcpy(address.sun_path,
                    socket_path_.c_str(),
                    socket_path_.size() + 1);
        if (::connect(fd,
                      reinterpret_cast<sockaddr*>(&address),
                      sizeof(address)) < 0 ||
            !send_all(fd, kGpsV1Request, sizeof(kGpsV1Request))) {
            ::close(fd);
            return std::nullopt;
        }

        // GPS1 response: magic[4], status[1], x/y/z as IEEE-754 binary64 in
        // network byte order. A fixed frame avoids pulling Python pickle into
        // every C++ application.
        std::uint8_t response[29]{};
        const bool received = receive_exact(fd, response, sizeof(response));
        ::close(fd);

        if (!received ||
            !std::equal(std::begin(kGpsV1Request),
                        std::end(kGpsV1Request),
                        response)) {
            return std::nullopt;
        }
        if (response[4] != 1) {
            return std::nullopt;
        }

        static_assert(std::numeric_limits<double>::is_iec559,
                      "GPS1 requires IEEE-754 doubles");
        static_assert(sizeof(double) == sizeof(std::uint64_t),
                      "GPS1 requires IEEE-754 binary64 doubles");
        Position position{
            decode_network_double(response + 5),
            decode_network_double(response + 13),
            decode_network_double(response + 21)};
        if (
            !std::isfinite(position.x) ||
            !std::isfinite(position.y) ||
            !std::isfinite(position.z)) {
            return std::nullopt;
        }
        return position;
    }

    const std::string& socket_path() const {
        return socket_path_;
    }

private:
    static bool send_all(int fd, const char* data, std::size_t size) {
        std::size_t sent_total = 0;
        while (sent_total < size) {
            const ssize_t sent =
                ::send(fd, data + sent_total, size - sent_total, MSG_NOSIGNAL);
            if (sent > 0) {
                sent_total += static_cast<std::size_t>(sent);
                continue;
            }
            if (sent < 0 && errno == EINTR) {
                continue;
            }
            return false;
        }
        return true;
    }

    static bool receive_exact(int fd, std::uint8_t* data, std::size_t size) {
        std::size_t received_total = 0;
        while (received_total < size) {
            const ssize_t received =
                ::recv(fd, data + received_total, size - received_total, 0);
            if (received > 0) {
                received_total += static_cast<std::size_t>(received);
                continue;
            }
            if (received < 0 && errno == EINTR) {
                continue;
            }
            return false;
        }
        return true;
    }

    static double decode_network_double(const std::uint8_t* data) {
        std::uint64_t bits = 0;
        for (std::size_t index = 0; index < sizeof(bits); ++index) {
            bits = (bits << 8) | data[index];
        }
        double value = 0.0;
        std::memcpy(&value, &bits, sizeof(value));
        return value;
    }

    std::string socket_path_;
    std::chrono::milliseconds timeout_;
};

using TriggerCallback = std::function<void(const StateUpdate&)>;
using InvalidPositionCallback = std::function<void()>;

template <typename Rep, typename Period>
inline bool sleep_for_or_stopped(
    const std::atomic<bool>& running,
    const std::chrono::duration<Rep, Period>& duration) {
    using Clock = std::chrono::steady_clock;
    if (duration <= duration.zero()) {
        return !running.load();
    }

    const auto deadline = Clock::now() +
        std::chrono::duration_cast<Clock::duration>(duration);
    constexpr auto quantum = std::chrono::milliseconds(10);
    while (running.load()) {
        const auto now = Clock::now();
        if (now >= deadline) {
            return false;
        }
        const auto remaining = deadline - now;
        std::this_thread::sleep_for(std::min(
            remaining,
            std::chrono::duration_cast<Clock::duration>(quantum)));
    }
    return true;
}

inline bool sleep_until_or_stopped(
    const std::atomic<bool>& running,
    std::chrono::steady_clock::time_point deadline) {
    const auto now = std::chrono::steady_clock::now();
    if (deadline <= now) {
        return !running.load();
    }
    return sleep_for_or_stopped(running, deadline - now);
}

inline void polling_loop(PositionSource& source,
                         CoverageWorkload& workload,
                         std::atomic<bool>& running,
                         std::chrono::milliseconds interval,
                         const TriggerCallback& on_trigger,
                         const InvalidPositionCallback& on_invalid = {}) {
    if (interval.count() <= 0) {
        throw std::invalid_argument("position polling interval must be positive");
    }

    auto next = std::chrono::steady_clock::now();
    while (running.load()) {
        const auto position = source.get_position();
        if (!running.load()) {
            break;
        }
        if (!position) {
            if (on_invalid) {
                on_invalid();
            }
        } else {
            const Observation observation = workload.observe(*position);
            if (observation.status == ObservationStatus::Changed) {
                on_trigger(observation.update);
            } else if (observation.status == ObservationStatus::InvalidPosition &&
                       on_invalid) {
                on_invalid();
            }
        }

        next += interval;
        const auto now = std::chrono::steady_clock::now();
        if (next <= now) {
            // Do not busy-loop after a slow/failed GPS request.
            next = now + interval;
        }
        if (sleep_until_or_stopped(running, next)) {
            break;
        }
    }
}

inline std::string format_cell_ids(const std::vector<CellId>& ids) {
    std::string output;
    for (std::size_t index = 0; index < ids.size(); ++index) {
        if (index != 0) {
            output.push_back('|');
        }
        output += std::to_string(ids[index]);
    }
    return output;
}

}  // namespace coverage
}  // namespace mace

#endif  // MACE_CRDT_SPATIAL_COVERAGE_HPP
