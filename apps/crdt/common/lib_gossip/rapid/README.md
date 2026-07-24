# RAPID

C++17 implementation of RAPID as a high-level dissemination facade. Each
`Rapid` instance owns its UDP socket, workers, timers, message cache, neighbor
tracking and recovery state. It has no dependency on CRDT code.

## Application API

### Facade at a glance

| Method | Meaning |
| --- | --- |
| `Rapid(Config)` | Creates one independent protocol instance. It does not open the socket yet. |
| `start()` | Opens the socket and starts the protocol workers. |
| `disseminate(Bytes)` | Creates a **new logical message**, sends it, and returns an opaque `MessageHandle`. |
| `retransmit(handle)` | Sends the **same logical message** again with the same RAPID identifier. |
| `receive()` | Blocks until a new remote logical message is available or the instance is stopped. |
| `stop()` | Stops workers, closes the socket and wakes blocked receivers. |
| `is_running()` | Reports whether the instance is running. |
| `stats()` | Returns a snapshot of protocol, queue and network counters. |
| `last_error()` | Describes the last rejected operation on this instance. |

```cpp
#include "rapid.hpp"

using gossip::rapid::Rapid;

gossip::rapid::Config config;
config.local_peer_id = 1;
config.bind_address = "0.0.0.0";
config.broadcast_address = "255.255.255.255";
config.port = 9000;

Rapid rapid(config);
if (!rapid.start()) {
    // rapid.last_error() describes the startup failure.
}

gossip::rapid::Bytes payload{/* application bytes */};
auto message_handle = rapid.disseminate(std::move(payload));
if (!message_handle) {
    // rapid.last_error() describes why the message was rejected.
}

// Optional: repeat the network transmission without creating a new logical
// message. Receivers that already cached it suppress it as a duplicate.
if (message_handle && !rapid.retransmit(*message_handle)) {
    // The handle may have expired from the cache.
}

while (auto message = rapid.receive()) {
    // receive() blocks until a remote message is available or stop() is called.
    application_receive(message->payload);
}

rapid.stop();
```

### New messages and retransmissions

`disseminate()` and `retransmit()` deliberately express different application
intent:

```cpp
auto first = rapid.disseminate(bytes);
auto second = rapid.disseminate(bytes);  // New ID: a second logical message.

rapid.retransmit(*first);                // Same ID as first.
```

Each successful `disseminate()` call creates a new RAPID identifier, even when
its bytes equal an earlier payload. Byte equality cannot define message
identity: an application may intentionally publish the same bytes twice.

`retransmit()` accepts only a `MessageHandle` returned by the same `Rapid`
instance. It retrieves the cached payload and schedules another `DATA` packet
with the original identifier. It does not create another logical message, does
not cause a receiver that already knows the identifier to deliver the payload
again, and does not refresh the cache TTL. The call fails after the cache entry
expires, when the handle belongs to another instance, or when the instance is
not running.

This distinction reproduces the periodic behavior of the CRDT application used
in the publication. That application accumulates all local deltas produced
between two triggers:

```cpp
std::optional<gossip::rapid::MessageHandle> last_message;

if (has_new_delta) {
    last_message = rapid.disseminate(serialize(delta));
} else if (last_message) {
    rapid.retransmit(*last_message);
}
```

One or more accumulated operations therefore create one new logical message.
An empty interval retransmits the previous message instead of assigning a new
identifier to the same CRDT payload.

Both sending methods return after the local instance accepts the operation;
they do not wait for remote delivery. Locally disseminated messages are not
returned by that same instance's `receive()`.

`receive()` consumes a library-owned, bounded delivery queue. Its capacity is
set by `delivery_queue_capacity`. When the queue is full, the protocol keeps
forwarding and caching the network message but cannot deliver it to the local
application. This condition is never silent: it increments
`Stats::delivery_queue_overflows`.

All public operations are thread-safe. `stop()` wakes a thread blocked in
`receive()`, which returns `std::nullopt` after any already queued messages have
been consumed. A stopped instance cannot be restarted.

## Network model

The public operation targets all peers in the swarm, but every UDP broadcast
must reach only the current one-hop neighbors. RAPID relays the message across
multiple hops. Heartbeats estimate the local neighbor count used by
`min(1, beta / neighbors)`.

The current wire format follows the published CRDT application: 64-bit message
identifiers and four packet types (`DATA`, `GOSSIP`, `REQUEST`, `HEARTBEAT`).
Every process-local instance must use a non-zero, distinct `local_peer_id`.
Multiple instances can run in the same process when their socket/network setup
allows it; using the same interface does not emulate different radio positions.

## Build the example

From the repository root:

```sh
g++ -std=c++17 -pthread \
  apps/crdt/common/lib_gossip/rapid/example.cpp \
  apps/crdt/common/lib_gossip/rapid/rapid.cpp \
  -o /tmp/rapid_example
/tmp/rapid_example
```

The example starts two independent instances in one process and prints
`hello` after the second instance receives the first one's dissemination.

## Configuration

Protocol defaults match the current published application: `beta = 2.5`,
60-second cache TTL, 5-second neighbor TTL, 1-second heartbeat and gossip,
10–40 ms short jitter, 200–600 ms long jitter and at most 50 message headers
per gossip packet.

`Stats::disseminated_messages` counts newly created logical messages, while
`Stats::explicit_retransmissions` counts successful `retransmit()` calls.
`rejected_disseminations` and `rejected_retransmissions` distinguish failures
of the two operations. `Stats` also reports packet/byte counters, malformed and
duplicate packets, socket errors, cache size, known neighbors and pending
deliveries.
