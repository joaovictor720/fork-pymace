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
| `notify_state_changed()` | Tells Trickle that the application changed the state locally and resets the interval to `minimum_interval`. It does not send synchronously. Do not call it for remote updates: the library handles those resets. |
| `receive()` | Blocks until a notification about an already-applied remote update is available. Returns `std::nullopt` after shutdown and queue draining. |
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
    virtual bool apply_update(PeerId sender, const Bytes& update) = 0;
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
    bool state_changed;
};
```

`Bytes` is `std::vector<std::uint8_t>` and `PeerId` is `std::uint64_t`.
`state_changed` is the result already returned by
`StateAdapter::apply_update()`.

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
    // The adapter has already applied the update. This loop observes it and
    // keeps the bounded notification queue drained.
    if (update->state_changed) {
        application_observe_new_state();
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

    bool apply_update(
        gossip::PeerId sender,
        const gossip::Bytes& update) override {
        return state_.apply_remote_update(sender, update);
    }

private:
    ApplicationState& state_;
};
```

The four adapter methods answer:

1. What compact metadata represents the current state?
2. How does remote knowledge compare with local knowledge?
3. Which application bytes can advance that remote state?
4. How are received application bytes applied, and did they actually advance
   local state?

`apply_update()` runs synchronously in the network receive worker. It must
fully validate and apply the update before returning. Return `true` only when
the local state changed; Trickle then resets the interval to
`minimum_interval` before processing another incoming packet. Returning
`false` identifies a valid update that was already known or otherwise caused
no change. Throwing rejects the update and records an adapter error.

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

New local information, a newer remote summary, incomparable state or a remote
update for which `apply_update()` returns `true` resets the interval to
`minimum_interval`.

Use `notify_state_changed()` only after state changes made outside the adapter,
such as a local application operation. Updates received by Trickle are applied
and reported to the timer internally.

## Concurrency and lifetime

The adapter and referenced application state must outlive `Trickle`.

`summary()`, `compare()`, `make_update()` and `apply_update()` run on protocol
workers. They must be thread-safe with local application mutations, return
promptly and validate remote bytes before using them. They should not call
blocking Trickle operations.

Public lifecycle operations and `stats()` are thread-safe. `stop()` wakes a
thread blocked in `receive()`.

## Receiving updates

Trickle applies every valid update through `StateAdapter::apply_update()`
before it puts a notification in the library-owned queue consumed by
`receive()`. Consequently, correctness and protocol progress do not depend on
how quickly the application calls `receive()`.

The notification queue capacity is configured by
`delivery_queue_capacity`. When it is full, only the notification is dropped:
the update has already been applied and any necessary interval reset has
already happened. The condition increments
`Stats::delivery_queue_overflows`; it is never silent.

Already queued updates are returned after `stop()`. When the queue becomes
empty, `receive()` returns `std::nullopt`.

Trickle can receive the same semantic update more than once. `apply_update()`
must recognize a repeated update as unchanged, or the application's update
encoding must carry enough identity to recognize it. This does not require a
CRDT; it is a consequence of gossip communication allowing retransmission.

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
| `delivery_queue_capacity` | Maximum already-applied update notifications waiting for the application. |

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
    std::uint64_t delivered_updates; // notifications queued for receive()

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

Each packet is a one-hop UDP broadcast. State crosses multiple hops because a
node applies a received update synchronously, resets its interval when the
state changed and subsequently participates with its new summary.

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
