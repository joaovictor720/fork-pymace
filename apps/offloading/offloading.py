#!/usr/bin/env python3

""" 
This app is intended to do two things, broadcasts hello to build neighbours list and offloads data
16/02/2023 - Changed the payload to me inmemory and not a file. Payload follows normal distribution
"""
__author__ = "Bruno Chianca Ferreira"
__license__ = "MIT"
__version__ = "0.2"
__maintainer__ = "Bruno Chianca Ferreira"
__email__ = "brunobcf@gmail.com"

import random, json, traceback, zlib,  time, sys, pickle, asyncio, argparse, threading, os, socket, struct, statistics,math
import numpy as np
from tqdm import tqdm
from apscheduler.schedulers.background import BackgroundScheduler
from ping3 import ping, verbose_ping

#from classes.auxiliar import prompt
from classes.network import network_sockets
from classes.tracer import GenericTracer
from classes.prompt import Prompt

class Offloading:

  def __init__(self, tag, number, runtime, persona):
    'Initializes the properties of the Node object'
    #random.seed(tag + str(number))
    random.seed(tag + str(time.time_ns()))
    self.persona = persona
    self.max_runtime = runtime
    self.tag_number = number
    self.tag = tag
    self.fulltag = self.tag + str(self.tag_number)
    self.lock = True
    self.debug = False
    self.scheduler = BackgroundScheduler(timezone="Europe/Paris")
    self.simulation_time_s = 0 
    #### NETWORK ############################################################################################
    self.bcast_group = '10.0.0.255' #broadcast ip address
    self.port = 56555 # UDP/TCP port
    self.hello_port = 57444 # UDP port
    self.max_packet = 65536 #max packet size to listen
    #### APP ################################################################################################
    self.job_queue = {}
    self.job_hist = []
    self.max_count = 5
    self.payload_size = self.max_packet
    self.udp_stat = [0,0]
    self.tcp_stat = [0,0]
    self.pack_pool = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789@#$%&"
    self.stats = [0,0,0,0,0]
    self.state = "IDLE" #current state
    self.delivery_bucket = 0
    self.neighbours = {}
    self.state = {
      'cost' : 20,
      'load' : 10
    }
    ##################### END OF DEFAULT SETTINGS ###########################################################
    self._setup()
    ##################### Sub classes and threads ###########################################################
    self.tracer = GenericTracer(self.fulltag, self.report_folder)
    self.prepare_offload_report()
    self.prompt = Prompt(self)
    self.udp_interface = network_sockets.UdpInterface(self._packet_handler, debug=False, port=self.port, interface='')
    self.hello_interface = network_sockets.UdpInterface(self._packet_handler, debug=False, port=self.hello_port, interface='')
    self.data_interface = network_sockets.TcpInterface(self._receiver, debug=False, port=self.port, interface='')
    self.scheduler.add_job(self._increment_time, "interval", seconds= 0.5, id="lamport_clock")
    self.scheduler.add_job(self._broadcast_hello, "interval", seconds= 2, id="broadcast")
    self.runtime_thread = threading.Thread(target=self._checkruntime, args=())
    self.prompt_thread = threading.Thread(target=self.prompt.prompt, args=())
    #self.loop = asyncio.get_event_loop()

  ############### Private methods ###########################

  def _setup(self):
    'Called by constructor. Finish the initial setup'
    settings_file = open("./settings.json","r").read()
    settings = json.loads(settings_file)
    self.port = settings['dataPort']
    self.hello_port = settings['helloPort']
    self.max_packet = settings['maxUDPPacket']
    self.bcast_group = settings['ipv4bcast']
    self.report_folder = settings['report_folder']
    self.flops = float(settings['FLOPS']) #reading edge servers' computing capacity
    self.beta = float(settings['beta']) #reading computational complexity of payload
    self.mean_payload = settings['mean_payload'] #reading payload's mean size in KiB
    self.stddev_payload = settings['stddev_payload'] #reading payload's stddev in KiB
    self.logfile = open(self.tag + "_target_report.csv","w")
    self.tests = ["increasing", "decreasing", "random"]

  def _checkruntime(self):
    while self.simulation_time_s/1000 < self.max_runtime:
      time.sleep(0.1)
    self.scheduler.shutdown()
    self.udp_interface.shutdown()
    self.hello_interface.shutdown()
    self.data_interface.shutdown()
    self.tracer.shutdown()
    self.shutdown()

  def _increment_time(self):
    self.simulation_time_s += 500

  def _auto_job(self):
    'Loads batch jobs from files. File must correspond to node name'
    tasks = []
    try:
      jobs_file = open("./job_" + self.fulltag + ".json","r").read()
      jobs_batch = json.loads(jobs_file)
      for job in jobs_batch["jobs"]:
        print(job)
        tasks.append(self.loop.create_task(self._auto_job_add(job['start'],job['dest'],job['count'],job['size'],job['interval'],job['type'],job['stop'])))
      #pending = asyncio.Task.all_tasks()
      #print(pending)
      #self.loop.run_until_complete(asyncio.gather(*pending))
      self.loop.run_until_complete(asyncio.wait(tasks))
      #self.loop.run_forever()
      self.loop.close()
    except:
      #traceback.print_exc()
      #print("No jobs batch for me")
      pass

  async def _auto_job_add(self, delay, dest, count, size, interval, jobtype, stop):
    'Adds batch jobs to the scheduler'
    await asyncio.sleep(delay)
    print("adding: " +str (interval))
    self._add_job(dest, count, size, interval, jobtype, stop)

  def _add_job(self, dest, max_count, size, interval, jobtype, stop):
    'Adds manual jobs'
    job_id = hex(self._new_id())
    self.max_count = max_count
    if jobtype == 'udp':
      try:
        self.job_queue[job_id] = [int(time.time()), job_id, dest, max_count, size, interval,"IDLE", "U", 0, stop]
        self._udp_sender(job_id, dest, size)
        self.scheduler.add_job(self._udp_sender, 'interval', seconds = interval, id=job_id, args=[job_id, dest, size])
      except:
        traceback.print_exc()
    elif jobtype == 'tcp':
      try:
        self.job_queue[job_id] = [int(time.time()), job_id, dest, max_count, size, interval,"IDLE", "T", 0, stop]
        self.scheduler.add_job(self._tcp_sender, 'interval', seconds = interval, id=job_id, args=[job_id, dest, size])
      except:
        traceback.print_exc()

  def _cancel_job(self, jobid):
    'Cancel a job'
    try:
      self.scheduler.remove_job(jobid)
      self.job_queue[jobid][6] = 'CANCELLED'
    except:
      traceback.print_exc()
      self.prompt.print_info("Job had already finished.")

  def _broadcast_hello(self):
    'Broadcasts hello packet'
    msg_id = self._new_id()
    payload = ''
    msg = [2, self.fulltag, int(time.time()), payload]
    msg_bin, _size = self._encode(msg)
    r = self.hello_interface.send(self.bcast_group, msg_bin, msg_id)

  def _send_state(self, uav):
    msg_id = self._new_id()
    payload = self.state
    msg = [3, self.fulltag, int(time.time()), payload]
    msg_bin, _size = self._encode(msg)
    self.hello_interface.send(uav, msg_bin, msg_id)

  def _handle_hello_packet(self, packet, sender_ip):
    self._send_state(sender_ip)

  def _handle_cloudlet_state_packet(self, packet, sender):
    #print(packet)
    if sender != self.myip:
      self.neighbours[packet[1]] = [packet[2] , packet[3], sender]

  def _write_report(self):
    self.tracer.add_consensus_trace(self.simulation_time_s, self.direction)

  def _receiver(self, payload, sender):
    pass

  def _packet_handler(self, payload, sender_ip):
    'Handles received packet'
    #Double encoded for now
    try:
      payload = pickle.loads(payload)
    except:
      payload = json.loads(payload.decode())
    try:
      payload = pickle.loads(payload[1])
    except:
      payload = json.loads(payload[1].decode())

    pdu = payload[0]

    if pdu == 2:
      self._handle_hello_packet(payload, sender_ip)

    if pdu == 3:
      self._handle_cloudlet_state_packet(payload, sender_ip)     

    self.delivery_bucket += sys.getsizeof(payload[1])
    try:
      self.tracer.add_delivery(self.simulation_time_s, str(self.delivery_bucket))
    except:
      pass

  def _encode(self, object):
    'Pickle encode object'
    data = pickle.dumps(object)
    size = len(data)
    return data, size

  def _new_id(self):
    return zlib.crc32(str(int(time.time()) + random.randint(0,1000)).encode())

  def _udp_sender(self, jobid, dest, size):
    'Creates UDP packet'
    self.state = "SENDING"
    self.job_queue[jobid][6] = 'RUNNING'
    msg_id = self._new_id()
    payload = ''
    msg = [1, self.job_queue[jobid][8], int(time.time()), payload]
    current_size = sys.getsizeof(pickle.dumps(msg))
    
    if size > self.max_packet:
      size = 1500

    while (size> current_size):
      msg[3] = msg[3] + random.choice(self.pack_pool)
      current_size = sys.getsizeof(pickle.dumps(msg))

    msg_bin, _size = self._encode(msg)
    self.udp_interface.send(dest, msg_bin, msg_id)
    #self._sender(dest, upd_pack)
    self.job_queue[jobid][8] += 1
    self.udp_stat[0] += 1
    self.stats[0] += 1
    if self.simulation_time_s / 1000 >= self.job_queue[jobid][9]: 
      self.state = "IDLE"
      self.job_queue[jobid][6] = 'FINISHED'
      self.scheduler.remove_job(jobid)

  def _tcp_sender(self, jobid, dest, size):
    'Creates TCP packet'
    self.state = "SENDING"
    self.job_queue[jobid][6] = 'RUNNING'
    msg_id = self._new_id()
    payload = ''
    msg = [1, self.job_queue[jobid][8], int(time.time()), payload]
    current_size = len(str(msg))
    if (size > current_size + 42):
      for i in range(0,size - current_size - 42):
        #add padding to reach size of packet
        payload = payload + random.choice(self.pack_pool)
    msg[3] = payload
    msg_bin = pickle.dumps(msg)
    self.tcp_interface.send(dest, msg_bin, msg_id)
    #self._t_sender(dest, tcp_pack)
    self.job_queue[jobid][8] += 1
    self.tcp_stat[0] += 1
    self.stats[0] += 1
    if self.simulation_time_s /1000 >= self.job_queue[jobid][9]:
      self.state = "IDLE"
      self.job_queue[jobid][6] = 'FINISHED'
      self.scheduler.remove_job(jobid)

  def _ping(self, destination):
    'Pings a node'
    verbose_ping(destination)

  def _prompt(self, command):
    'Application command prompt options. Called from main prompt'
    try:
      if (len(command))>=2:
        if command[1] == 'help':
          self.printhelp()
        elif command[1] == 'ping':
          self._ping(command[2])
        elif command[1] == 'info':
          self.printinfo()
        elif command[1] == 'visible':
          self.print_neighbours()
        elif command[1] == 'poff':
          if self.persona == 'uav':
            if (len(command)) < 3:
              n = 1
            else: n = int(command[2])
            self._parallel_offload(n)
          else:
            self.prompt.print_alert("not an UAV")
        elif command[1] == 'soff':
          if self.persona == 'uav':
            if (len(command)) < 3:
              n = 1
            else: n = int(command[2])
            self._serial_offload(n)
          else:
            self.prompt.print_alert("not an UAV")
        else:
          print("Invalid Option")
          self.printhelp()
      elif (len(command))==1:
        self.printhelp()
    except:
      traceback.print_exc()
      print("Malformed command")
      self.printhelp()

  def offload(self, neighbour, state, payload_size):
    #file = "./files/off"+ size + ".bin"
    #file_to_send = open (file, "rb")
    #filesize = os.path.getsize(file)
    print("sending to: " + neighbour)
    split_size = 1400 # goinf for MTU

    sender_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sender_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sender_socket.connect((state[2], self.port))

    payload_size_b = payload_size * 1024
    payload = [0xFF] * payload_size_b
    progress = tqdm(range(payload_size_b), f"Sending {payload_size_b}", unit="Bytes", unit_scale=True, unit_divisor=1024)

    #send via socket total size that will be sent to prepare edge server
    sender_socket.sendall(struct.pack('!I', len(payload))) # bytes
    #sender_socket.sendall(struct.pack('!I', filesize)) # bytes
    sent = 0

    #finally send data
    n = math.ceil(payload_size_b / split_size)
    for i in range(0, n+1):
      #print(type(payload[0 + i*split_size:i*split_size + split_size]))
      tosend = bytes(payload[0 + i*split_size:i*split_size + split_size])
      sent += len(tosend)
      sender_socket.sendall(tosend)
      progress.update(len(tosend))
    
    #progress = tqdm(range(filesize), f"Sending {file}", unit="B", unit_scale=True, unit_divisor=1024)
    #sender_socket.sendall(struct.pack('!I', filesize))
    ##bytes_read = file_to_send.read()
    #sent = 0
    #while True:
    #  bytes_read = file_to_send.read(4096)
    #  if not bytes_read:
    #      break
    #  sender_socket.sendall(bytes_read)
    #  progress.update(len(bytes_read))

    progress.close()
    print("sent: " + str(sent))
    sender_socket.close()
    #file_to_send.close()

  def prepare_offload_report(self):
    for test in self.tests:
      self.tracer.add_report("offload_sequential_" + test, "payload_size(kB);mean (s);stdev; wait(s); trans(s); comp(s); total(s); beta(Ops/B)")
      self.tracer.add_report("offload_parallel_" + test, "payload_size(kB);mean (s);stdev; wait(s); trans(s); comp(s); total(s); beta(Ops/B)")

  def _serial_offload(self, rep):
    #sizes = ["100KB", "250KB", "500KB", "600KB", "750KB", "1MB"]
    #sizes = ["600KB", "500KB", "250KB", "100KB"]
    #sizes = ["100KB", "250KB", "500KB", "600KB"]

    for test in self.tests:
      for k in range(5): #run more times with different partition
        sizes = np.random.normal(self.mean_payload, self.stddev_payload, len(self.neighbours))#, self.stddev_payload)) #convert to integer
        sizes = [int(x) for x in sizes]
        if test == "increasing":
          sizes = sorted(sizes,reverse=False)
        elif test == "decreasing":
          sizes = sorted(sizes,reverse=True)
        else:
          pass
        reps = {}
        for i in range(rep):
          for j, (neighbour, state) in enumerate(self.neighbours.items()):
            start = time.time()
            self.offload(neighbour, state, sizes[j])
            end = time.time()
            total = end - start
            try:
              reps[sizes[j]].append(total)
            except:
              reps[sizes[j]]=[]
              reps[sizes[j]].append(total)
        #print(reps)

        if len(reps) > 1:
          for beta in [1,2,5,10,20,50,80,100,150]:
            toff_ant = 0
            for j, (size, measurements) in enumerate(reps.items()):
              bytes = size * 1024
              time_proc = (bytes * beta)/self.flops
              t_wait = toff_ant
              t_total = statistics.mean(measurements) + toff_ant + time_proc
              self.tracer.add_to_report("offload_sequential_" + test, str(size) + ";" + str(statistics.mean(measurements)) + ";" + 
                                        str(statistics.stdev(measurements)) + ";" +  str(t_wait)+ ";" +  str(statistics.mean(measurements)) +
                                        ";" +  str(time_proc)+ ";" +  str(t_total) + ";" +  str(beta)+ "\n")
              toff_ant += statistics.mean(measurements)
        else:
          for size, measurements in reps.items():
            self.tracer.add_to_report("offload_sequential_" + test, str(size) + ";" + str(measurements[0]) + ";" + str(0) + "\n")

  def _parallel_offload(self, rep):
    for test in self.tests:
      for k in range(5): #run more times with different partition
        sizes = np.random.normal(self.mean_payload, self.stddev_payload, len(self.neighbours))#, self.stddev_payload)) #convert to integer
        sizes = [int(x) for x in sizes]
        if test == "increasing":
          sizes = sorted(sizes,reverse=False)
        elif test == "decreasing":
          sizes = sorted(sizes,reverse=True)
        else:
          pass
        self.preps = {}
        for i in range(rep):
          off_threads = []
          for j, (neighbour, state) in enumerate(self.neighbours.items()):
            off_threads.append(threading.Thread(target=self.poffload, args=(neighbour,state, sizes[j])))
          for t in off_threads:
            t.start()
          for t in off_threads:
            t.join()

        if len(self.preps) > 1:
          for beta in [1,2,5,10,20,50,80,100,150]:
            for j, (size, measurements) in enumerate(self.preps.items()):
              #bytes = float(size[:-2]) * 1024
              bytes = size * 1024
              time_proc = (bytes * beta)/self.flops
              t_total = statistics.mean(measurements) + time_proc
              self.tracer.add_to_report("offload_parallel_" + test, str(size) + ";" + 
                str(statistics.mean(measurements)) + ";" + str(statistics.stdev(measurements)) + 
                ";0" + ";" +  str(statistics.mean(measurements)) + ";" +  str(time_proc)+ ";" +  
                str(t_total) + ";" +  str(beta)+ "\n")
            #self.tracer.add_to_report("offload_parallel", sizes[i] + ";" + str(statistics.mean(reps)) + ";" + str(statistics.stdev(reps)) + "\n")
        else:
          self.tracer.add_to_report("offload_parallel_" + test, "stub" + ";" + str(0) + ";" + str(0) + "\n")

  def poffload(self, neighbour, state, payload_size):
    print("sending to: " + neighbour)
    start = time.time()
    split_size = 1400 # goinf for MTU
    
    #setup local socket to send data
    sender_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sender_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sender_socket.connect((state[2], self.port))
    
    #prepare data to send
    #file = "./files/off"+ payload_size + ".bin"
    #file_to_send = open (file, "rb")
    #filesize = os.path.getsize(file)
    
    payload_size_b = payload_size * 1024
    payload = [0xFF] * payload_size_b
    #payload = b''
    #while len(payload) < payload_size_b:
      #pass
    #  payload += bytearray(chr(random.randint(1,128)), 'utf-8')
    #print(sys.getsizeof(payload))

    #setup progress bar
    #progress = tqdm(range(filesize), f"Sending {filesize}", unit="Bytes", unit_scale=True, unit_divisor=1024)
    progress = tqdm(range(payload_size_b), f"Sending {payload_size_b}", unit="Bytes", unit_scale=True, unit_divisor=1024)

    #send via socket total size that will be sent to prepare edge server
    sender_socket.sendall(struct.pack('!I', len(payload))) # bytes
    #sender_socket.sendall(struct.pack('!I', filesize)) # bytes
    sent = 0

    #print("payload size: " + str(payload_size_b))
    #print("payload size calc: " + str(sys.getsizeof(payload)))

    #finally send data
    n = math.ceil(payload_size_b / split_size)
    for i in range(0, n+1):
      #print(type(payload[0 + i*split_size:i*split_size + split_size]))
      tosend = bytes(payload[0 + i*split_size:i*split_size + split_size])
      sent += len(tosend)
      sender_socket.sendall(tosend)
      progress.update(len(tosend))

    #finally send data
    #while True:
    #  bytes_read = file_to_send.read(4096)
    #  if not bytes_read:
    #      break
    #  sender_socket.sendall(bytes_read)
    #  progress.update(len(bytes_read))

    #closing up
    #file_to_send.close()
    progress.close()
    sender_socket.close()
    end = time.time()
    #print("sent: " + str(sent))

    #total time
    total = end - start
    try:
      self.preps[payload_size].append(total)
    except:
      self.preps[payload_size]=[]
      self.preps[payload_size].append(total)

  ############### Public methods ###########################

  def start(self):
    'Called by main. Starts the application'
    self.scheduler.start()
    self.udp_interface.start()
    self.hello_interface.start()
    self.data_interface.start()
    self.runtime_thread.start()
    self.tracer.start()
    self.prompt_thread.start()
    self.myip = self.hello_interface.myip()
    # add batch jobs
    self._auto_job()
    self.shutdown()

  def shutdown(self):
    'Called by main. Stops the application'
    self.runtime_thread.join()

  def printinfo(self):
    'Prints general information about the application'
    print()
    print("Application stats (Traffic)")
    print("State: \t\t" + self.state)
    print("Payload size: \t" + str(self.payload_size))
    print("Sent UDP packets: \t" + str(self.udp_stat[0]))
    print("Received UDP packets: \t" + str(self.udp_stat[1]))
    print()

  def printhelp(self):
    'Prints help information about the application'
    print()
    print("Options for Offloading")
    print()
    print("help                - Print this help message")
    print("info                - Print information regarding application")
    print("ping [destination]  - Send a ping no a IP with timeout of 5")
    print("visible             - Print list of neighbours")
    if self.persona == 'uav': 
      print("poff [n]            - Offload in parallel")
      print("soff [n]            - Offload in series")
    print()

  def print_neighbours(self):
    'Prints current queue'
    print("Current neighbours at:" + str(int(time.time())) )
    print("===============================================================================")
    print("|ID\t\t | Last seen  |State\t\t\t |IP")
    print("-------------------------------------------------------------------------------")
    for neighbour, state in self.neighbours.items():
      print ("|" + neighbour + "\t | " + str(state[0]) + " | " + str(state[1])+ " | " + str(state[2]))
    print("===============================================================================")

if __name__ == "__main__":
  parser = argparse.ArgumentParser(description='Some arguments are obligatory and must follow the correct order as indicated')
  parser.add_argument("-r", "--runtime", type=int, help="Runtime limit", default=100)
  parser.add_argument("-t", "--tag", type=str, help="Node name", default="node")
  parser.add_argument("-p", "--persona", type=str, help="Am I a drone or a cloudlet?", default="cloudlet")
  parser.add_argument("-n", "--number", type=int, help="Node number", default=0)
  args = parser.parse_args()
  main = Offloading(args.tag, args.number, args.runtime, args.persona)
  main.start()
  