# Trickle

C++17 implementation of Trickle as a high-level state-maintenance facade.
Each `Trickle` instance owns its UDP socket, internal timer, receive worker,
summary suppression state and bounded application-delivery queue.

Trickle maintains replicas of evolving state. It does not treat every trigger
as a new independent message. The application owns the state and its
serialization; the library decides when summaries and updates are transmitted.

## Public API

Include the algorithm-specific header:

```cpp
#include "trickle.hpp"

using gossip::trickle::Trickle;
```

The complete facade is:

```cpp
class Trickle {
public:
    Trickle(Config config, StateAdapter& adapter);
    ~Trickle();

    bool start();
    bool notify_state_changed();
    std::optional<ReceivedUpdate> receive();
    void stop();

    bool is_running() const;
    Stats stats() const;
    std::string last_error() const;
};
```

| Method | Meaning |
| --- | --- |
| `Trickle(config, adapter)` | Creates one independent protocol instance. The adapter remains owned by the application and must outlive the `Trickle` object. |
| `~Trickle()` | Stops the instance when necessary and releases protocol resources. |
| `start()` | Validates configuration, opens the UDP socket and starts protocol workers. Returns `false` on failure. |
| `notify_state_changed()` | Tells Trickle that the application state changed and resets the interval to `minimum_interval`. It does not send synchronously. |
| `receive()` | Blocks until remote update bytes are available. Returns `std::nullopt` after shutdown and queue draining. |
| `stop()` | Stops timers and network workers and wakes blocked receivers. Safe to call more than once. |
| `is_running()` | Reports whether the instance is currently running. |
| `stats()` | Returns a snapshot of protocol counters and current interval state. |
| `last_error()` | Returns the latest startup, socket, adapter or API error description. |

A stopped instance cannot be restarted. Copying and moving a `Trickle`
instance are disabled.

### Supporting public types

The application supplies the state semantics through:

```cpp
class StateAdapter {
public:
    virtual ~StateAdapter() = default;

    virtual Bytes summary() const = 0;
    virtual StateRelation compare(const Bytes& remote_summary) const = 0;
    virtual Bytes make_update(const Bytes& remote_summary) const = 0;
};
```

Comparison results are:

```cpp
enum class StateRelation {
    Equivalent,
    LocalNewer,
    RemoteNewer,
    Incomparable
};
```

`receive()` returns:

```cpp
struct ReceivedUpdate {
    PeerId sender;
    Bytes payload;
};
```

`Bytes` is `std::vector<std::uint8_t>` and `PeerId` is `std::uint64_t`.

## Minimal usage

```cpp
ApplicationState state;
ApplicationStateAdapter adapter(state);

gossip::trickle::Config config;
config.local_peer_id = 1;
config.broadcast_address = "255.255.255.255";
config.port = 9000;

Trickle trickle(config, adapter);
if (!trickle.start()) {
    throw std::runtime_error(trickle.last_error());
}

// A local application operation changes the current state.
state.change();
trickle.notify_state_changed();

while (auto update = trickle.receive()) {
    // The application interprets and applies its own bytes.
    if (state.apply(update->payload)) {
        // Notify only when local state actually advanced.
        trickle.notify_state_changed();
    }
}

trickle.stop();
```

## Adapting application state

The adapter does not store application state inside Trickle. It normally keeps
a reference to state that already belongs to the application:

```cpp
class ApplicationStateAdapter final
    : public gossip::trickle::StateAdapter {
public:
    explicit ApplicationStateAdapter(ApplicationState& state)
        : state_(state) {}

    gossip::Bytes summary() const override {
        return state_.serialize_summary();
    }

    gossip::trickle::StateRelation compare(
        const gossip::Bytes& remote) const override {
        return state_.compare_summary(remote);
    }

    gossip::Bytes make_update(
        const gossip::Bytes& remote) const override {
        return state_.serialize_update_for(remote);
    }

private:
    ApplicationState& state_;
};
```

The three adapter methods answer:

1. What compact metadata represents the current state?
2. How does remote knowledge compare with local knowledge?
3. Which application bytes can advance that remote state?

The library reacts to `compare()` as follows:

| Result | Library action |
| --- | --- |
| `Equivalent` | Increment consistency counter `c`; suppress the local summary when `c >= k`. |
| `LocalNewer` | Call `make_update()` and broadcast the returned application bytes. |
| `RemoteNewer` | Reset to `Imin` and immediately announce the local summary as an implicit update request. |
| `Incomparable` | Send the locally newer information, reset to `Imin` and announce the local summary to request information missing locally. |

`make_update()` may return a complete state, a delta or any other
application-defined representation. Trickle never parses update bytes.

## Timing behavior

For each interval `I`, Trickle:

1. chooses a random transmission time in `[I/2, I)`;
2. listens for equivalent summaries and increments `c`;
3. transmits its summary when `c < redundancy_constant`;
4. otherwise suppresses the redundant summary;
5. doubles `I` at the end of the interval, up to `maximum_interval`.

New local information, a newer remote summary or incomparable state resets the
interval to `minimum_interval`. Receiving update bytes does not reset the
interval by itself because the library cannot know whether they changed
application state. The application applies them and then calls
`notify_state_changed()` when appropriate.

## Concurrency and lifetime

The adapter and referenced application state must outlive `Trickle`.

`summary()`, `compare()` and `make_update()` run on protocol workers. They must
be thread-safe with local application mutations, return promptly and validate
remote summary bytes before using them. They should not call blocking Trickle
operations.

Public lifecycle operations and `stats()` are thread-safe. `stop()` wakes a
thread blocked in `receive()`.

## Receiving updates

`receive()` consumes a library-owned queue whose capacity is configured by
`delivery_queue_capacity`.

When this queue is full, network reception and protocol timing continue, but
the update cannot be delivered locally. The condition increments
`Stats::delivery_queue_overflows`; it is never silent.

Already queued updates are returned after `stop()`. When the queue becomes
empty, `receive()` returns `std::nullopt`.

Trickle can send the same semantic update more than once. Applications should
ignore updates that do not advance state or attach identifiers when an
operation cannot safely be repeated.

## Configuration

```cpp
struct Config {
    PeerId local_peer_id{1};
    std::string bind_address{"0.0.0.0"};
    std::string broadcast_address{"255.255.255.255"};
    std::uint16_t port{9000};

    std::uint32_t redundancy_constant{1};
    std::chrono::milliseconds minimum_interval{500};
    std::chrono::milliseconds maximum_interval{8000};

    std::uint64_t seed{0};
    std::size_t max_packet_size{16384};
    std::size_t delivery_queue_capacity{1024};
};
```

| Field | Meaning |
| --- | --- |
| `local_peer_id` | Non-zero identity used to ignore self-originated broadcasts. |
| `bind_address` | Local IPv4 address used by the UDP socket. |
| `broadcast_address` | IPv4 broadcast destination. |
| `port` | UDP port shared by compatible participants. |
| `redundancy_constant` | Trickle redundancy constant `k`. |
| `minimum_interval` | `Imin`, used after new information. |
| `maximum_interval` | `Imax`, upper bound for interval doubling. |
| `seed` | Random seed; zero selects a non-deterministic seed. |
| `max_packet_size` | Maximum complete UDP packet accepted and emitted. |
| `delivery_queue_capacity` | Maximum updates waiting for the application. |

Invalid configuration makes `start()` return `false` and records an explanation
in `last_error()`.

## Statistics

`stats()` returns:

```cpp
struct Stats {
    std::uint64_t sent_packets;
    std::uint64_t received_packets;
    std::uint64_t sent_bytes;
    std::uint64_t received_bytes;

    std::uint64_t sent_summaries;
    std::uint64_t received_summaries;
    std::uint64_t sent_updates;
    std::uint64_t received_updates;
    std::uint64_t delivered_updates;

    std::uint64_t consistent_summaries;
    std::uint64_t suppressed_summaries;
    std::uint64_t interval_resets;

    std::uint64_t malformed_packets;
    std::uint64_t adapter_errors;
    std::uint64_t oversized_payloads;
    std::uint64_t socket_errors;
    std::uint64_t rejected_state_changes;
    std::uint64_t delivery_queue_overflows;

    std::chrono::milliseconds current_interval;
    std::uint32_t consistent_count;
    std::size_t pending_deliveries;
};
```

Exceptions thrown by adapter methods do not terminate protocol workers. They
increment `adapter_errors`, update `last_error()` and reject only the affected
protocol action.

## Network model

Each packet is a one-hop UDP broadcast. State crosses multiple hops because
nodes that apply an update call `notify_state_changed()` and subsequently
participate with their new summary.

Participants in the same dissemination domain need distinct, non-zero peer
identifiers and compatible ports, broadcast setup, serialization and state
comparison semantics.

The wire protocol contains `Summary` and `Update` packets. Each packet includes
a type, 64-bit sender identifier, payload length and application bytes.
`max_packet_size` includes this 13-byte header. Fragmentation, authentication
and encryption are not implemented.

## Examples and tests

[`gcounter_example.cpp`](gcounter_example.cpp) is a complete executable example
with two replicas. It uses the complete component vector as summary, compares
vectors component by component, transmits only locally greater components and
applies updates through component-wise `max`.

The research application uses the same adapter strategy in
[`apps/crdt/trickle/crdt_trickle.cpp`](../../../trickle/crdt_trickle.cpp).

Build and run the standalone example:

```sh
g++ -std=c++17 -O2 -pthread \
  apps/crdt/common/lib_gossip/trickle/gcounter_example.cpp \
  apps/crdt/common/lib_gossip/trickle/trickle.cpp \
  -o /tmp/trickle_gcounter_example

/tmp/trickle_gcounter_example
```

Expected output:

```text
first=2, second=2
```

```sh
g++ -std=c++17 -O2 -pthread \
  apps/crdt/common/lib_gossip/trickle/trickle_test.cpp \
  apps/crdt/common/lib_gossip/trickle/trickle.cpp \
  -o /tmp/trickle_test

/tmp/trickle_test
```

The tests cover configuration validation, state dissemination, convergence of
incomparable states, redundancy suppression, bounded-queue overflow and
shutdown of a blocked receiver.

## Reference

Philip Levis, Neil Patel, David Culler and Scott Shenker. *Trickle: A
Self-Regulating Algorithm for Code Propagation and Maintenance in Wireless
Sensor Networks*. First USENIX/ACM Symposium on Networked Systems Design and
Implementation (NSDI), 2004.
