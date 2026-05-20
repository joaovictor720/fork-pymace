# RAPID

Small C++17 implementation of RAPID: Reliable Probabilistic Dissemination.

Copy `rapid.hpp` and `rapid.cpp` into your project. The library does not open sockets, start threads, parse configs, or depend on any CRDT code.

```cpp
rapid::Config cfg;
cfg.node_id = 1;

rapid::Node node(cfg);
node.set_send_callback([](const rapid::Bytes& packet) {
    // Send packet using UDP, a simulator, radio, etc.
});
node.set_deliver_callback([](rapid::MessageId id, rapid::Bytes payload) {
    // Application payload delivered by RAPID.
});

node.publish(payload);
node.rebroadcast(message_id);
node.receive(packet, sender_node_id);
node.tick();
```

Build the example from the repository root:

```sh
g++ -std=c++17 example.cpp rapid.cpp -o rapid_example
./rapid_example
```

Main parameters: `beta`, `cache_ttl`, `neighbor_ttl`, `gossip_interval`, `heartbeat_interval`, `short_jitter_min/max`, `long_jitter_min/max`.
