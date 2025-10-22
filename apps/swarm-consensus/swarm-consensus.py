#!/usr/bin/env python3

""" 
This app is intended to do two things, enable nodes to achieve swarm consensus on direction while
performing traffic injection. 
"""
__author__ = "Bruno Chianca Ferreira"
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Bruno Chianca Ferreira"
__email__ = "brunobcf@gmail.com"

import random, json, traceback, zlib,  time, sys, pickle, asyncio, argparse, threading, math
from apscheduler.schedulers.background import BackgroundScheduler
from classes import prompt
from classes.network import network_sockets
from classes.tracer import Tracer
from ping3 import ping, verbose_ping

class SwarmConsensus:

  def __init__(self, tag, number, runtime):
    'Initializes the properties of the Node object'
    random.seed(tag + str(number))
    random.seed(tag + str(time.time_ns()))
    self.python_overhead = 29
    self.max_runtime = runtime
    self.tag_number = number
    self.tag = tag
    self.fulltag = self.tag + str(self.tag_number)
    self.lock = True
    self.debug = False
    self.scheduler = BackgroundScheduler(timezone="Europe/Paris")
    #### NETWORK ############################################################################################
    self.bcast_group = '10.0.0.255' #broadcast ip address
    self.port = 56555 # UDP/TCP port
    self.data_port = 56444 # UDP port
    self.consensus_port = 57444 # UDP port
    self.max_packet = 65536 #max packet size to listen
    self.simulation_time_s = 0 
    #### APP ################################################################################################
    self.job_queue = {}
    self.job_hist = []
    self.interval = 1
    self.destination = ''
    self.max_count = 5
    self.counter = 0
    self.payload_size = self.max_packet
    self.udp_stat = [0,0]
    self.tcp_stat = [0,0]
    self.pack_pool = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789@#$%&"
    self.stats = [0,0,0,0,0]
    self.state = "IDLE" #current state
    self.delivery_bucket = 0
    self.direction = random.uniform(0,2 * math.pi)   #Chooses a random angle in radians
    self.neighbours = {}
    self.k = 0.2
    ##################### END OF DEFAULT SETTINGS ###########################################################
    self._setup()
    self.tracer = Tracer(self.fulltag)
    self.udp_interface       = network_sockets.UdpInterface(self._packet_handler, debug=False, port=self.port,           interface='')
    self.consensus_interface = network_sockets.UdpInterface(self._packet_handler, debug=False, port=self.consensus_port, interface='')
    self.scheduler.add_job(self._increment_time, "interval", seconds= 0.1, id="clock")
    self.scheduler.add_job(self._broadcast_my_direction, "interval", seconds= 0.5, id="broadcast")
    self.scheduler.add_job(self._run_swarm_consensus, "interval", seconds= 0.5, id="consensus")
    self.runtime_thread = threading.Thread(target=self._checkruntime, args=(self.shutdown,))
    self.loop = asyncio.get_event_loop()

  ############### Private methods ###########################

  def _setup(self):
    'Called by constructor. Finish the initial setup'
    settings_file = open("./settings.json","r").read()
    settings = json.loads(settings_file)
    self.port = settings['dataPort']
    self.consensus_port = settings['consensusPort']
    self.max_packet = settings['maxUDPPacket']
    self.bcast_group = settings['ipv4bcast']
    self.report_folder = settings['report_folder']
    self.logfile = open(self.tag + "_target_report.csv","w")

  def _checkruntime(self, shutdown):
    while self.simulation_time_s/1000 < self.max_runtime:
      time.sleep(0.1)
    self.scheduler.shutdown()
    self.udp_interface.shutdown()
    self.consensus_interface.shutdown()
    self.tracer.shutdown()

  def start(self):
    'Called by main. Starts the application'
    self.scheduler.start()
    self.udp_interface.start()
    self.consensus_interface.start()
    self.runtime_thread.start()
    self.tracer.start()
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
    print("Destination: \t" + self.destination)
    print("Payload size: \t" + str(self.payload_size))
    print("Sent UDP packets: \t" + str(self.udp_stat[0]))
    print("Received UDP packets: \t" + str(self.udp_stat[1]))
    print()

  def _printhelp(self):
    'Prints help information about the application'
    print()
    print("Options for Traffic")
    print()
    print("help                - Print this help message")
    print("info                - Print information regarding application")
    print("ping [destination]  - Send a ping no a IP with timeout of 5")
    print("cancelJob [jobid]   - Cancel a job")
    print("send [dest] [count] [size] [interval] [udp or tcp]\n- Starts sending packets to destination")
    print()

  ############### Public methods ###########################

  def _increment_time(self):
    self.simulation_time_s += 100

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
      print("No jobs batch for me")
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
      prompt.print_info("Job had already finished.")

  def _broadcast_my_direction(self):
    'Broadcasts my direction'
    #print(self.bcast_group)
    msg_id = self._new_id()
    payload = self.direction
    msg = [2, self.fulltag, msg_id, int(time.time()), payload]
    msg_bin, _size = self._encode(msg)
    r = self.consensus_interface.send(self.bcast_group, msg_bin, msg_id)
    #print(r)

  def _handle_direction_packet(self, packet):
    self.neighbours[packet[1]] = packet[4]
    #print(packet)

  def _run_swarm_consensus(self):
    _theta = 0
    for node, direction in self.neighbours.items():
      _theta += direction - self.direction
    _theta *= self.k
    self.direction += _theta
    self._write_report()

  def _write_report(self):
    self.tracer.add_consensus_trace(self.simulation_time_s, self.direction)

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
      self._handle_direction_packet(payload)
    self.delivery_bucket += sys.getsizeof(payload[1])
    try:
      self.tracer.add_delivery(self.simulation_time_s, str(self.delivery_bucket))
    except:
      pass
    #print()

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
    if (len(command))>=2:
      if command[1] == 'help':
        self._printhelp()
      elif command[1] == 'ping':
        self._ping(command[2])
      elif command[1] == 'queue':
        self._print_queue()             
      elif command[1] == 'hist':
        self.print_hist()
      elif command[1] == 'bcast':
        self._broadcast_my_direction()
      elif command[1] == 'debug':
        self.debug = not self.debug
      elif command[1] == 'info':
        self.printinfo()
      elif command[1] == 'dest':
         self._set_destination(command[2])
      elif command[1] == 'payload':
         self._set_payload(command[2])
      elif command[1] == 'interval':
         self._set_interval(command[2])
      elif command[1] == 'cancelJob':
         self._cancel_job(command[2])
      elif command[1] == 'send':
        if (len(command)) == 7:
          #TODO add more satination
          size = int(command[4])
          interval = self._check_interval(command[5])
          self._add_job(command[2], int(command[3]), size, interval, command[6])
        else:
          prompt.print_error("Malformed command")
          self._printhelp()
      else:
        print("Invalid Option")
        self._printhelp()
    elif (len(command))==1:
      self._printhelp()

  def _print_queue(self):
    'Prints current queue'
    print("Current jobs at:" + str(int(time.time())) )
    print("===============================================================================")
    print("|T|St:\t|Job ID\t\t|Dest\t\t|Count\t|Size\t|Int\t|Status\t\t|")
    print("-------------------------------------------------------------------------------")
    for jobid, job in self.job_queue.items():
      print ("|" + job[7] +"|"+str(job[0])+"\t|"+str(job[1])+"\t|"+str(job[2])+"\t|"+str(job[3])+"\t|"+str(job[4])+"\t|"+str(job[5])+"\t|"+job[6]+"\t|")
    print("===============================================================================")

if __name__ == "__main__":
  parser = argparse.ArgumentParser(description='Some arguments are obligatory and must follow the correct order as indicated')
  parser.add_argument("-r", "--runtime", type=int, help="Runtime limit", default=100)
  parser.add_argument("-t", "--tag", type=str, help="Node name", default="node")
  parser.add_argument("-n", "--number", type=int, help="Node number", default=0)
  args = parser.parse_args()
  main = SwarmConsensus(args.tag, args.number, args.runtime)
  main.start()
  

