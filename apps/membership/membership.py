#!/usr/bin/env python3

""" 
Memebership service classes

Used to test the behaviour of creating a neighbour list using hello packets in a dynamic network



"""
__author__ = "Bruno Chianca Ferreira"
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Bruno Chianca Ferreira"
__email__ = "brunobcf@gmail.com"

import sys, json, traceback, zlib, threading, time, pickle, random, argparse
from apscheduler.schedulers.background import BackgroundScheduler
from classes import network_sockets

sys.path.append('../../classes')

from auxiliar.generic_prompt import Prompt
from auxiliar.generic_tracer import Tracer

class Neighbourhood():

  def __init__(self, tag, runtime):
    """_summary_

    Args:
        Node (_type_): _description_
        ip (_type_): _description_
    """
    self.scheduler = BackgroundScheduler(timezone="Europe/Paris")
    self.runtime = runtime
    #### NODE ##############################################################################################
    self.visible = [] #our visibble neighbours
    self.ever_visible = [] #our visibble neighbours
    self.visibility_lost = [] #our visibble neighbours
    self.messages_created = [] #messages created by each node
    self.messages = []
    self.average = 0
    self.topology = [] #our visibble neighbours
    self.simulation_seconds = 0
    self.tag = tag
    #### UTILITIES ##########################################################################################
    self.protocol_stats = [0,0,0,0] #created, forwarded, delivered, discarded
    self.errors = [0,0,0]
    self.myip = ''
    #### Layer specific #####################################################################################
    self.ttl = 16 #not used now
    self.fanout_max = 3
    self.mode = "NDM" #close neighbouhood discover mode
    #### Default settings ###################################################################################
    self.helloInterval = 0.2
    self.visible_timeout =  self.helloInterval * 5
    self.dynamic = True
    ##################### END OF DEFAULT SETTINGS ###########################################################
    self._setup()
    self.prompt_thread = Prompt(self.tag, self.prompt)
    self.visible_tracer = Tracer("visible", self.tag, self.simdir, 'time;visible\n', self.report_folder)
    if self.dynamic:
      self.scheduler.add_job(self.sendHello, 'interval', seconds=self.helloInterval, id='membership')
      self.scheduler.add_job(self.analyse_visible, 'interval', seconds=0.1, id='membership_analysis')
      self.scheduler.add_job(self.increment_time, 'interval', seconds=1, id='time')
      #self.scheduler.add_job(self.checkruntime, 'interval', seconds=0.5, id='runtime')
    #self.prompt_thread = threading.Thread(target=self.prompt.prompt, args=())
    self.network_interface = network_sockets.UdpInterface(self.packet_handler, debug=False, port=self.network_port, interface='')
    
  def increment_time(self):
    """_summary_
    """
    self.simulation_seconds += 1

  def checkruntime(self):
    """_summary_
    """
    while self.running:
      if self.simulation_seconds >= self.runtime:
        self.running = False
        self.shutdown()
      time.sleep(0.5)


  def start(self):
    """_summary_
    """
    self.running = True
    self.scheduler.start()
    self.network_interface.start()
    self.visible_tracer.start()
    #self.prompt_thread.start()
    self.checkruntime()

  def shutdown(self):
    """_summary_
    """
    self.scheduler.remove_all_jobs()
    self.scheduler.shutdown()
    self.network_interface.shutdown()
    self.visible_tracer.shutdown()
    #self.prompt_thread.shutdown()

  def printinfo(self):
    """_summary_
    """
    'Prints general information about the node'
    print()
    print("STUB - Using the OS routing and network")
    print("Broadcast IP: " + self.bcast_group)
    print()

  def get_servers(self):
    return self.visible

  def printvisible(self):
    """_summary_
    """
    print("Visible neighbours at:" + str(self.simulation_seconds) )
    print("===============================================================================")
    print("|IP\t\t|Node ID\t|Load\t\t|Last seen\t|")
    print("-------------------------------------------------------------------------------")
    for member in range(len(self.visible)):
      print ("|"+self.visible[member][0]+"\t|"+str(self.visible[member][1])+"\t\t|"+str(self.visible[member][2])+"\t\t|"+str(self.visible[member][3])+"\t\t|")
    print("===============================================================================")
  
  ############### Private methods ##########################

  def _setup(self):
    """_summary_
    """
    settings_file = open("settings.json","r").read()
    settings = json.loads(settings_file)
    self.network_port = settings['networkPort']
    self.helloInterval = settings['helloInterval']
    self.visible_timeout = settings['visibleTimeout']
    self.report_folder = settings['report_folder']
    self.dynamic = True if settings['dynamic'] == "True" else False
    self.simdir = str(time.localtime().tm_year) + "_" + str(time.localtime().tm_mon) + "_" + str(time.localtime().tm_mday) + "_" + str(time.localtime().tm_hour) + "_" + str(time.localtime().tm_min)

  def packet_handler(self, payload, sender_ip):
    """_summary_

    Args:
        payload (_type_): _description_
        sender_ip (_type_): _description_
    """
    'When a message of type gossip is received from neighbours this method unpacks and handles it'
    pickleload = payload
    payload = pickle.loads(pickleload)
    msg_id = payload[2]
    pdu = payload[0]
    if pdu == 0: # Got a hello package
      #self.tracer.add_trace(msg_id+';'+'RECV' + ';' + 'HELLO' + ';' + str(sys.getsizeof(pickleload)) + ';' + str(sender_ip))
      node = payload[1]
      if (node != self.tag):
        if len(self.visible) > 0: #list no empty, check if already there
          not_there = 1
          for element in range(len(self.visible)):
            if sender_ip == self.visible[element][0]: #if there...
              self.visible[element][2] = self.simulation_seconds # refresh timestamp
              not_there = 0
              break
          if not_there:
            self.visible.append([sender_ip, node, self.simulation_seconds, 0])
        else: #Empty neighbours list, add 
          self.visible.append([sender_ip, node, self.simulation_seconds, 0])
    else:
      pass
      #print('not hello')

  def createHello(self):
    """_summary_

    Returns:
        _type_: _description_
    """
    msg_id = self.new_id()
    helloPack = [0, self.tag, msg_id]
    return helloPack

  def new_id(self):
    """_summary_

    Returns:
        _type_: _description_
    """
    return zlib.crc32(str(int(time.time()) + random.randint(0,1000)).encode())

  def sendHello(self):
    """_summary_
    """
    _buffer = self.createHello()
    #_buffer = pickle.dumps([hex(msg_id), _buffer])
    #self._broadcaster(_buffer)
    #self.tracer.add_trace(';'+'SEND' + ';' + 'HELLO' + ';' + str(sys.getsizeof(_buffer)) + ';' + self.bcast_group)
    r = self.network_interface.broadcast(_buffer)
    self.update_visible()
  
  def setbcast(self, bcast):
    """_summary_

    Args:
        bcast (_type_): _description_
    """
    self.bcast_group = bcast

  def update_visible(self):
    """_summary_
    """
    for member in range(len(self.visible)):
      if (self.simulation_seconds - self.visible[member][3] > self.visible_timeout):
        del self.visible[member]
        break
    self.visible_tracer.add_trace(str(len(self.visible)))

  def analyse_visible(self):
    """_summary_
    """
    for member in range(len(self.visible)):
      found = 0
      for ever_member in range(len(self.ever_visible)):
        if self.visible[member][0] == self.ever_visible[ever_member][0]:
          found = 1
          if self.ever_visible[ever_member][3] != 0:
            #calculate absense
            abesense = int(time.time() * 1000) - self.ever_visible[ever_member][3]
            #store absense for report
            self.visibility_lost.append([self.ever_visible[ever_member][0], abesense])
            self.ever_visible[ever_member][3] = 0
      if found == 0:
        self.ever_visible.append([self.visible[member][0], self.visible[member][1], 0, 0])

    for ever_member in range(len(self.ever_visible)):
      found = 0
      for member in range(len(self.visible)):
        if self.visible[member][0] == self.ever_visible[ever_member][0]:
          found = 1
      if found == 0:
        if self.ever_visible[ever_member][3] == 0:
          self.ever_visible[ever_member][2] += 1
          self.ever_visible[ever_member][3] = int(time.time() * 1000)

  def print_ever(self):
    """_summary_
    """
    for member in self.ever_visible:
      print(member)

  def prompt(self, command):
    """_summary_

    Args:
        command (_type_): _description_
    """
    if (len(command)) >= 1:
      if command[0] == 'help':
        self.printhelp()
      elif command[0] == 'info':
        self.printinfo()
      elif command[0] == 'bcast':
        self.setbcast(command[2])
      elif command[0] == 'ever':
        self.print_ever()
      elif command[0] == 'list':
        self.print_list()
      elif command[0] == 'quit':
        self.shutdown()
      elif command[0] == 'lost':
        for member in self.visibility_lost:
          print(member)
      else:
        print("Invalid Option")
        self.printhelp()
    elif (len(command))==1:
      self.printhelp()

  def printhelp(self):
    """_summary_
    """
    'Prints help message'
    print()
    print("bcast\t- Clear the display")
    print("ever\t- Diplay this help message")
    print("lost\t- Exit the agent")
    print("info\t- desc")
    print("list\t- list")
    print("quit\t- Exit")

  def printinfo(self):
    pass

  def print_list(self):
    """_summary_
    """
    print("Visible neighbours at:" + str(self.simulation_seconds) )
    print("===============================================================================")
    print("|IP\t\t|Node ID\t|Last seen (s) ago\t|")
    print("-------------------------------------------------------------------------------")
    for member in self.visible:
      print ("|"+member[0]+"\t|"+str(member[1])+"\t\t|"+str(self.simulation_seconds - member[2])+"\t\t\t|")
    print("===============================================================================")

if __name__ == "__main__":
  """_summary_
  """
  #print("Neighbourhood test")
  parser = argparse.ArgumentParser(description='Some arguments are obligatory and must follow the correct order as indicated')
  parser.add_argument("-r", "--runtime", type=int, help="Runtime limit", default=100)
  parser.add_argument("-t", "--tag", type=str, help="Node name", default="node")
  parser.add_argument("-n", "--number", type=int, help="Node number", default=0)
  args = parser.parse_args()
  main = Neighbourhood(args.tag, args.runtime)
  main.start()
  