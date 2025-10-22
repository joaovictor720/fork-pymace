#!/usr/bin/env python3

""" 
BUS class - Should be seen as a singleton by the system. 
Works in the publish subscribe paradigm

Bus messages should be a list with three elements.
Destination of the message: BROADCAST or NAME_APPLICATION
Type of message: Just an identifier so that the receiver can handle properly
Payload: Which should be anything the receiver understands according to the type.
T.ex: ['RSM', 'COMMIT' , ['1','WRITE', 'KEY', 'VALUE']]

"""
__author__ = "Bruno Chianca Ferreira"
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Bruno Chianca Ferreira"
__email__ = "brunobcf@gmail.com"

#TODO: Remove node from object

import socket, os, math, struct, sys, json, traceback, zlib, fcntl, threading, time, pickle, distutils

class Bus():
  """_summary_
  """
  def __init__(self, tag):
    """Initializes the properties of the object

    Args:
        Node (_type_): _description_
    """
    #### NODE ###############################################################################
    self.fulltag = tag
    #### UTILITIES ############################################################################
    self.callbacks = []
    ##################### END OF DEFAULT SETTINGS ###########################################################
    self._setup()

  ############### Public methods ###########################

  def start(self):
    """_summary_
    """
    self.state = 'ENABLED'
    self.bus_listener_thread = threading.Thread(target=self._start_event_listener, args=())
    self.bus_listener_thread.start()
    #self._start_event_listener()

  def shutdown(self):
    """_summary_
    """
    self._stop()
    self.bus_listener_thread.join(timeout = 2)

  def emmit(self, data):
    """_summary_

    Args:
        data (_type_): _description_
    """
    bus = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    bus.settimeout(1)
    bus.connect("/tmp/" + self.socket_stem + ".sock." + self.fulltag)
    payload = pickle.dumps(data)
    length = len(payload)
    bus.sendall(struct.pack('!I', length))
    bus.sendall(payload)
    bus.close()

  def register_cb(self, callback):
    """_summary_

    Args:
        callback (function): _description_
    """
    self.callbacks.append(callback)
  
  def deregister_cb(self, callback):
    """_summary_

    Args:
        callback (function): _description_
    """
    self.callbacks.remove(callback)

  ############### Private methods ##########################

  def _setup(self):
    """_summary_
    """
    settings_file = open("./classes/bus/settings.json","r").read()
    settings = json.loads(settings_file)
    self.socket_stem = settings['socket']

  def _printinfo(self):
    """_summary_
    """
    'Prints general information about the node'
    print()
    print("Network - Using the OS routing and network")
    print("Broadcast IP: " + self.bcast_group)
    print()

  def _prompt(self, command):
    """_summary_

    Args:
        command (_type_): _description_
    """
    if (len(command))>=2:
      if command[1] == 'help':
        self._printhelp()
      elif command[1] == 'info':
        self._printinfo()
      else:
        print("Invalid Option")
        self._printhelp()
    elif (len(command))==1:
      self._printinfo()  

  def _stop(self):
    """_summary_
    """
    self.state = 'DISABLED'
    self.emmit('BYE')

  def _start_event_listener(self):
    """_summary_

    Returns:
        _type_: _description_
    """
    #this section is a synchronizer so that all nodes can start ROUGHLY at the same time
    bus_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
      os.remove("/tmp/" + self.socket_stem + ".sock." + self.fulltag)
    except OSError:
      #traceback.print_exc()
      pass
    try:
      bus_socket.bind("/tmp/" + self.socket_stem + ".sock." + self.fulltag)
      bus_socket.listen(10000)
    except OSError:
      traceback.print_exc()
    except:
      traceback.print_exc()
    while self.state != "DISABLED":
      conn, addr = bus_socket.accept()
      lengthbuf = conn.recv(4)
      length, = struct.unpack('!I', lengthbuf)
      data = b''
      while length:
        newbuf = conn.recv(length)
        if not newbuf: return None
        data += newbuf
        length -= len(newbuf)
      #data = conn.recv(65500)
      for cb in self.callbacks:
        cb(data)
      conn.close()
    bus_socket.close()


