// Exercise the application's actual adapter, including network repair.
#define main spatial_application_main
#include "crdt_trickle.cpp"
#undef main

#include <cassert>

int main() {
    using namespace std::chrono_literals;
    using mace::coverage::CoverageWorkload;
    using mace::coverage::GridSpec;
    const GridSpec grid{0, 0, 130, 10, 1, 13};
    CoverageWorkload first(grid), second(grid);
    SpatialTrickleAdapter a(first, "1"), b(second, "2");
    assert(a.compare(b.summary()) == StateRelation::Equivalent);
    first.observe({1, 1, 0});
    assert(a.compare(b.summary()) == StateRelation::LocalNewer);
    assert(b.compare(a.summary()) == StateRelation::RemoteNewer);
    second.observe({129, 1, 0});
    // Equal cardinalities, different cells: repair is needed both ways.
    assert(a.compare(b.summary()) == StateRelation::Incomparable);
    assert(b.compare(a.summary()) == StateRelation::Incomparable);
    assert(a.make_update(b.summary()) == first.snapshot());
    assert(b.make_update(a.summary()) == second.snapshot());

    CoverageWorkload incompatible(GridSpec{0, 0, 130, 10, 13, 1});
    bool rejected = false;
    try {
        a.compare(incompatible.snapshot());
    } catch (const std::invalid_argument&) {
        rejected = true;
    }
    assert(rejected);

    gossip::trickle::Config config;
    config.port = static_cast<std::uint16_t>(40000 + ::getpid() % 10000);
    config.broadcast_address = "127.255.255.255";
    config.minimum_interval = 30ms;
    config.maximum_interval = 120ms;
    config.max_packet_size = 1400;
    config.local_peer_id = 1;
    config.seed = 1;
    Trickle t1(config, a);
    config.local_peer_id = 2;
    config.seed = 2;
    Trickle t2(config, b);
    assert(t1.start());
    assert(t2.start());
    assert(t1.notify_state_changed());
    assert(t2.notify_state_changed());
    const auto deadline = std::chrono::steady_clock::now() + 3s;
    while ((first.size() != 2 || second.size() != 2) &&
           std::chrono::steady_clock::now() < deadline) {
        std::this_thread::sleep_for(5ms);
    }
    t1.stop();
    t2.stop();
    assert(first.size() == 2 && second.size() == 2);
    assert(first.snapshot() == second.snapshot());
    assert(a.compare(b.summary()) == StateRelation::Equivalent);
    // A later local trigger includes the cell learned through repair.
    const auto next = first.observe({11, 1, 0});
    assert(next.update.changed && next.update.replica_size == 3);
    assert(mace::coverage::deserialize_bitmap(next.update.snapshot, grid) ==
           mace::coverage::Bytes({3, 16}));
    std::cout << "spatial Trickle adapter and bitmap repair passed\n";
}
