#!/usr/bin/env python3
"""
crdt_replica.py
Config JSON define:
    id,
    listen_port,
    peers (list of "ip:port"),
    mode (trace|random),
    mode params
"""

import ctypes, json, time, socket, threading, argparse, random
from pathlib import Path

# --- load lib
gcounter = ctypes.CDLL("/home/mace/git/fork-pymace/apps/crdt-replica/gcounter.so")
gcounter.gcounter_new.restype = ctypes.c_void_p
gcounter.gcounter_increment.argtypes = [ctypes.c_void_p, ctypes.c_int]
gcounter.gcounter_local.argtypes = [ctypes.c_void_p]
gcounter.gcounter_local.restype = ctypes.c_int
gcounter.gcounter_read.argtypes = [ctypes.c_void_p]
gcounter.gcounter_read.restype = ctypes.c_int
gcounter.gcounter_apply_remote.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
gcounter.gcounter_new.argtypes = [ctypes.c_char_p]
gcounter.gcounter_delete.argtypes = [ctypes.c_void_p]

# --- networking
MSG_MAX = 4096

def send_msg(sock, peer, msg_dict):
    b = json.dumps(msg_dict).encode()
    sock.sendto(b, peer)
    return len(b)

def recv_loop(sock, handle, stats):
    while True:
        try:
            data, addr = sock.recvfrom(MSG_MAX)
        except Exception:
            break
        try:
            msg = json.loads(data.decode())
        except Exception:
            continue
        # only handle inc messages for now
        if msg.get("type") == "inc":
            sender = msg.get("id")
            val = int(msg.get("value",1))
            gcounter.gcounter_apply_remote(handle, sender.encode(), val)
            stats['recv_msgs'] += 1
            stats['recv_bytes'] += len(data)

# --- workload runners
def run_trace_mode(cfg, handle, sock, peers, stats):
    # trace file contains list of {"time":t, "op":"inc", "value":n}
    trace_path = Path(cfg['trace_file'])
    with trace_path.open() as f:
        trace = json.load(f)
    start = time.time()
    for ev in trace:
        t = ev.get('time', 0)
        op = ev.get('op', 'inc')
        val = int(ev.get('value', 1))
        # wait until the scheduled time relative to start
        to_wait = start + t - time.time()
        if to_wait > 0:
            time.sleep(to_wait)
        # apply local increment
        gcounter.gcounter_increment(handle, val)
        # broadcast operation to peers
        msg = {"type":"inc", "id": cfg['id'], "value": val}
        sent_bytes = 0
        for p in peers:
            sent_bytes += send_msg(sock, p, msg)
        stats['sent_msgs'] += 1
        stats['sent_bytes'] += sent_bytes

def run_random_mode(cfg, handle, sock, peers, stats):
    # parameters: ops_per_sec, duration, distribution ("poisson" or "periodic"), seed
    ops_per_sec = float(cfg.get('ops_per_sec', 1.0))
    duration = float(cfg.get('duration', 60.0))
    distrib = cfg.get('distribution','poisson')
    seed = cfg.get('seed', None)
    if seed is not None:
        random.seed(seed)
    start = time.time()
    next_time = start
    while time.time() - start < duration:
        if distrib == 'periodic':
            next_time += 1.0/ops_per_sec
            to_wait = next_time - time.time()
            if to_wait>0:
                time.sleep(to_wait)
            val = 1
        else: # poisson: exponentially distributed interarrival
            inter = random.expovariate(ops_per_sec)
            time.sleep(inter)
            val = 1
        # local apply
        gcounter.gcounter_increment(handle, val)
        # broadcast
        msg = {"type":"inc", "id": cfg['id'], "value": val}
        sent_bytes = 0
        for p in peers:
            sent_bytes += send_msg(sock, p, msg)
        stats['sent_msgs'] += 1
        stats['sent_bytes'] += sent_bytes

# monitoring thread - logs periodically
def monitor_loop(handle, stats, interval, logfile):
    with open(logfile, "a") as f:
        while True:
            time.sleep(interval)
            local = gcounter.gcounter_local(handle)
            total = gcounter.gcounter_read(handle)
            s = f"{time.time():.3f}, local={local}, total={total}, sent_msgs={stats['sent_msgs']}, recv_msgs={stats['recv_msgs']}\n"
            f.write(s)
            f.flush()

# main
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, help="config json")
    args = parser.parse_args()
    cfg = json.load(open(args.config))

    node_id = cfg['id']
    listen_port = int(cfg['listen_port'])
    peers = []
    for p in cfg.get('peers', []):
        ip, port = p.split(":")
        peers.append((ip, int(port)))

    # create UDP socket and bind
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", listen_port))

    # create gcounter handle
    handle = gcounter.gcounter_new(node_id.encode())

    stats = {'sent_msgs':0, 'recv_msgs':0, 'sent_bytes':0, 'recv_bytes':0}

    # start receiver thread
    trecv = threading.Thread(target=recv_loop, args=(sock, handle, stats), daemon=True)
    trecv.start()

    # start monitor thread
    logf = cfg.get('log_file', f"/tmp/crdt_{node_id}.log")
    tmon = threading.Thread(target=monitor_loop, args=(handle, stats, cfg.get('monitor_interval',1.0), logf), daemon=True)
    tmon.start()

    mode = cfg.get('mode', 'random')
    try:
        if mode == 'trace':
            run_trace_mode(cfg, handle, sock, peers, stats)
        else:
            run_random_mode(cfg, handle, sock, peers, stats)
    except KeyboardInterrupt:
        pass

    # final sleep to allow messages to propagate (tune as needed)
    time.sleep(1.0)
    final_local = gcounter.gcounter_local(handle)
    final_total = gcounter.gcounter_read(handle)
    print(f"FINAL local={final_local} total={final_total} stats={stats}")
    gcounter.gcounter_delete(handle)
    sock.close()

if __name__ == "__main__":
    main()
