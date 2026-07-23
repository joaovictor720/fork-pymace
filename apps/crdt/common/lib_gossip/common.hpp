#ifndef GOSSIP_COMMON_HPP
#define GOSSIP_COMMON_HPP

#include <cstdint>
#include <vector>

namespace gossip {

using Bytes = std::vector<std::uint8_t>;
using PeerId = std::uint64_t;

}  // namespace gossip

#endif  // GOSSIP_COMMON_HPP
