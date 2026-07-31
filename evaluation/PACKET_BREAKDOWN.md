# Packet breakdown metric

The existing `total_packets` metric and plot are unchanged. The packet
breakdown is a second view over the exact same filtered pcap rows:

```text
total = payload + control + unclassified + classification_residual
```

`classification_residual` is an accounting check and must be zero. An
unclassified frame is different: it was counted and inspected, but the
classifier could not assign application semantics safely.

## Categories

- **Payload**: a packet whose purpose is to carry CRDT application state.
- **Control**: protocol or network maintenance, such as RAPID gossip,
  requests and heartbeats; Trickle summaries; BATMAN OGMs; or encapsulated
  ARP, ICMP and DHCP.
- **Unclassified**: an unknown type/subtype or a data carrier without enough
  information to prove that it contains application traffic.

The per-application mapping lives in `evaluation/apps.json` under
`frame_classification`:

- RAPID: type `1` is payload; `2`, `3`, and `4` are control.
- Trickle: type `2` is payload; type `1` is control.
- USFD-1x/3x: every frame already selected by the UDP/5001 filter is payload.
- BATMAN applications: BATMAN packet type, subtype, and encapsulated protocol
  fields determine the category.

## Diagnostics and validity

`pcap_metrics.csv` records category counts, the residual, status, counts by
wire type, reasons for unclassified frames, and sample `frame.number` values.

Statuses are:

- `ok`: complete classification and zero residual;
- `warning_unclassified`: zero residual, but at least one frame is
  unclassified;
- `error_classification_residual`: category accounting does not match total;
- `unavailable`: no compatible classification data.

Aggregation uses one shared set of runs for payload, control, unclassified,
and their matching total. Groups with invalid, partial, or unavailable data
are not drawn in the breakdown plot. Unclassified frames remain visible in
gray and are annotated with their percentage.

The new figure is written as:

```text
results/plots/<scenario>__total_packets_by_class_<timestamp>.pdf
```

Run the focused regression tests with:

```bash
python3 -m unittest discover -s evaluation/tests -v
```
