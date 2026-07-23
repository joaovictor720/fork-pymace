# RAPID

C++17 implementation of RAPID as a high-level dissemination facade. Each
`Rapid` instance owns its UDP socket, workers, timers, message cache, neighbor
tracking and recovery state. It has no dependency on CRDT code.

## Application API

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
rapid.disseminate(std::move(payload));

while (auto message = rapid.receive()) {
    // receive() blocks until a remote message is available or stop() is called.
    application_receive(message->payload);
}

rapid.stop();
```

Each call to `disseminate()` creates a new logical message whose dissemination
domain is the entire swarm. The call returns after the local RAPID instance
accepts the message; it does not wait for remote delivery. RAPID suppresses
network duplicates while a message remains in its cache. Two calls with
byte-identical payloads are still two distinct logical messages. Locally
disseminated messages are not returned by that same instance's `receive()`.

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

`Stats` also reports packet/byte counters, malformed and duplicate packets,
socket errors, rejected disseminations, cache size, known neighbors and pending
deliveries.
