# T7 - Protocol-Level Message Counting

## Goal

The current overhead metric counts frames captured on the network interface. That remains the best proxy for channel cost, but it mixes application traffic, dissemination control, routing control, forwarding, retransmission, and encapsulation.

This task adds a second view: count the messages or classified frames that can be attributed to the dissemination layer. The result is intentionally a decomposition, not a single universal "number of messages".

## Taxonomy

### Application-level synchronization

- `APP_UPDATE_CREATED`: local CRDT operation (`op_create`).
- `APP_SYNC_SEND`: one application `sendto()` carrying CRDT state/delta.
- `APP_SYNC_RECV`: one application-level CRDT sync received and parsed.

For BATMAN apps, this is the cleanest application-level metric because the application hands one packet to `bat0`, while `batman-adv` may emit multiple lower-layer frames.

### RAPID protocol messages

- `RAPID_DATA`: CRDT payload or recovery response.
- `RAPID_GOSSIP`: cache/header advertisement.
- `RAPID_REQUEST`: request for missing data.
- `RAPID_HEARTBEAT`: neighbor liveness signal.

`RAPID_DATA` is protocol data. The other three are protocol control.

### Trickle protocol messages and events

- `TRICKLE_SUMMARY`: summary vector sent by the Trickle timer or implicit request.
- `TRICKLE_REPAIR`: repair/update data carrying newer counter entries.
- `TRICKLE_SUPPRESSED`: timer decided not to transmit; this is not a network message.
- `TRICKLE_INTERVAL_RESET`: interval reset due to local or remote new information; this is not a network message.

`TRICKLE_SUMMARY` is protocol control. `TRICKLE_REPAIR` is protocol data.

### BATMAN-adv frame classification

Frames captured on `eth0` with ethertype `0x4305` can be classified by the BATMAN-adv packet type exposed by tshark:

- control: `BATADV_IV_OGM`, `BATADV_ELP`, `BATADV_OGM2`, `BATADV_ICMP`, `BATADV_UNICAST_TVLV`, and DAT subtypes inside `BATADV_UNICAST_4ADDR`.
- data/encapsulation: `BATADV_BCAST`, `BATADV_UNICAST`, `BATADV_UNICAST_FRAG`, `BATADV_UNICAST_4ADDR_DATA`, `BATADV_CODED`, `BATADV_MCAST`.
- ambiguous: `BATADV_UNICAST_4ADDR` without subtype is reported as `data_or_dat`.
- unclassified: reserved or unknown packet types.

The implementation also has a raw pcap fallback for Ethernet/SLL/SLL2 captures, because some tshark builds expose BATADV fields but do not automatically bind ethertype `0x4305` to the BATADV dissector.

This is a frame classification, not a perfect logical message count. In particular, `BATADV_BCAST` and `BATADV_UNICAST` do not reliably tell whether the frame is original local emission or forwarding by an intermediate node using the current capture pipeline.

### Link-layer frames

- `LINK_FRAME_TOTAL`: total frames selected by the run's pcap filter.
- `LINK_BYTES_TOTAL`: total bytes for those frames.

This remains the comparable channel-cost metric across all algorithms.

## Instrumentation Plan

### RAPID

Count in `apps/crdt/rapid/crdt_rapid.cpp` at successful `sendto()` and after packet type parsing on receive.

Fields logged:

- `event`: `rapid_data_send`, `rapid_gossip_send`, `rapid_request_send`, `rapid_heartbeat_send`, plus matching receive events.
- `node`
- `bytes`
- type-specific fields such as `msgid`, `payload_bytes`, `entries`, `reason`.

### Trickle

Use existing protocol events in `apps/crdt/trickle/crdt_trickle.cpp`:

- `trickle_summary_send`
- `trickle_summary_recv`
- `trickle_repair_send`
- `trickle_repair_recv`
- `trickle_suppressed`
- `trickle_reset`

No pcap inference is needed for new runs, although the pcap first-byte fallback remains useful for old runs.

### BATMAN Flooding

Count application-level sends/receives in `apps/crdt/broadcast/crdt_broadcast.cpp`:

- `app_sync_send`
- `app_sync_recv`

Count BATMAN-adv frames from `eth0` pcaps:

- `protocol_type_frames_json`
- `protocol_group_frames_json`

The current pcap path does not reliably split `BATADV_BCAST` into original broadcast versus forwarded broadcast.

### BATMAN Multiunicast

Count application-level sends/receives in `apps/crdt/multiunicast/crdt_multiunicast.cpp`:

- `app_sync_send`
- `app_sync_recv`

Each peer `sendto()` is one application sync message. BATMAN-adv unicast frames are classified from pcap, but intermediate forwarding is not reliably separated from local generation with the current capture fields.

## Aggregation

`evaluation/parse_message_counts.py` produces per-run fields:

- `app_updates_created`
- `app_sync_msgs`
- `app_received_sync_msgs`
- `protocol_control_msgs`
- `protocol_data_msgs`
- `protocol_suppressed_events`
- `protocol_interval_reset_events`
- `batadv_control_frames`
- `batadv_data_frames`
- `batadv_unclassified_frames`
- `link_frames`
- `link_bytes`
- `unknown`
- `message_metric_scope`

`evaluation/parse_metrics.py` includes these fields in `summary.csv`. `evaluation/run_scenario.sh` also writes `message_counts.json` and `message_counts.csv` inside each run directory.

## Questions Answered

1. RAPID can count data, gossip, request, and heartbeat messages precisely in user space.
2. Trickle can count summary and repair messages precisely in user space; suppression and reset are protocol events but not transmitted messages.
3. BATMAN flooding can separate application sync sends from BATMAN-adv frames and classify many BATMAN-adv frames as control or data encapsulation. It cannot reliably separate original broadcast frames from forwarded broadcast frames using only the current pcap data.
4. BATMAN multiunicast can separate application per-peer sends from BATMAN-adv frames and classify unicast/fragment/control frames. It cannot reliably separate local unicast encapsulation from forwarded unicast hops using only the current pcap data.
5. Unreliable or impossible with the current pipeline: MAC retransmissions below the capture point, original-vs-forwarded BATMAN frames, unique logical BATMAN routing events after aggregation across node pcaps, and DAT/control subtyping when tshark does not expose the subtype.
6. Comparable across all algorithms: `LINK_FRAME_TOTAL`/`link_frames`, `LINK_BYTES_TOTAL`/`link_bytes`, and `APP_UPDATE_CREATED`. `APP_SYNC_SEND` is comparable for BATMAN flooding/multiunicast and useful for RAPID/Trickle only if defined as protocol data sends rather than local app operations.
7. Family-specific metrics: RAPID and Trickle protocol messages are comparable inside user-space dissemination protocols. BATMAN-adv packet-type counts are comparable only inside BATMAN-based scenarios and should be named as frame classifications.

## Conclusion

The defensible conclusion is partial:

> We can count protocol messages precisely for RAPID and Trickle. For BATMAN, we can separate application-level sync messages and classify BATMAN-adv frames on the link, but internal separation between routing control, encapsulated data, forwarding, and retransmission remains partial.

The paper should avoid the ambiguous label "number of messages". Prefer:

- application-level synchronization messages
- protocol-level control messages
- protocol-level data messages
- BATMAN-adv frames classified by type
- link-layer frames
