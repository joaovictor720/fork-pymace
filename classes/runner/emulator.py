""" 
Emulator class
"""
__author__ = "Bruno Chianca Ferreira"
__license__ = "MIT"
__version__ = "0.2"
__maintainer__ = "Bruno Chianca Ferreira"
__email__ = "brunobcf@gmail.com"


import traceback, os, logging, time, subprocess, threading, sys, requests
from classes.runner.runner import Runner

from ..interfaces import iosocket
from ..interfaces import emulation_interface

from core.emulator.coreemu import CoreEmu
from core.emulator.data import IpPrefixes, NodeOptions
from core.emulator.enumerations import NodeTypes, EventTypes
from core.location.mobility import BasicRangeModel
from core import constants
from core.nodes.base import CoreNode
from core.nodes.network import WlanNode

from core.emane.models.ieee80211abg import EmaneIeee80211abgModel
from core.emane.models.rfpipe import EmaneRfPipeModel
from core.emane.models.tdma import EmaneTdmaModel
from core.emane.nodes import EmaneNet

from classes.mobility import mobility

from classes.nodes.fixed_node import FixedNode
from classes.scenario.scenario import Scenario

class Emulator(Runner):
  """_summary_

  Args:
      Runner (_type_): _description_
  """
  def __init__(self, daemon):
    """_summary_

    Args:
        daemon (_type_): _description_
    """
    self.nodes_digest = {}
    self.iosocket_semaphore = False
    self.fixed_nodes = []
    self.mobile_nodes = []
    self.node_options_fixed = []
    self.node_options_mobile = []
    self.core_nodes_fixed = []
    self.core_nodes_mobile = []
    self.daemon_mode = daemon
    self.running = False
    self.try_to_clean()
    self.coreemu = CoreEmu()
    #self.setup(scenario)

  def setup(self, scenario_config):
    """_summary_

    Args:
        scenario_config (_type_): _description_
    """
    self.scenario = Scenario(scenario_config)

  def callback(self):
    pass

  def set_daemon_socket(self, socket):
    """_summary_

    Args:
        socket (_type_): _description_
    """
    self.daemon_socket = socket

  def start(self):
    """_summary_
    """
    #pass
    self.running = True
    self.run()

  def try_to_clean(self):
    """_summary_
    """
    os.system("core-cleanup")
    #os.system("sudo ip link delete ctrl0.1")
    #os.system("sudo ip link delete vetha.0.1")
    #os.system("sudo ip link delete vetha.1.1")  

  def setup_core(self):
    """_summary_
    """
    self.session = self.coreemu.create_session()
    # must be in configuration state for nodes to start, when using "node_add" below
    self.session.set_state(EventTypes.CONFIGURATION_STATE)

    #Called mobility by CORE, by it is actually more like the radio model
    self.modelname = BasicRangeModel.name

    #TODO: should only call setup wlans, and scenarion decides between pure core or emane
    self.scenario.setup_nodes(self.session)
    self.scenario.setup_wlans(self.session)
    #self.scenario.setup_wlan_emane(self.session)
    self.scenario.setup_links(self.session)

    self.session.instantiate()
    self.session.write_nodes()

  def configure_batman(self, network_prefix, list_of_nodes):
    """_summary_

    Args:
        network_prefix (_type_): _description_
        list_of_nodes (_type_): _description_
    """
    #Configure Batman only on fixed network
    network_prefix = network_prefix.split("/")[0]
    network_prefix = network_prefix.split(".")
    network_prefix[2] = str(int(network_prefix[2]) + 1)
    network_prefix = '.'.join(network_prefix)
    process = []
    ###TODO Change this to do only on fixed nodes
    for node in list_of_nodes:
      shell = self.session.get_node(node, CoreNode).termcmdstring(sh="/bin/bash")
      #command = "ip link set eth0 address 0A:AA:00:00:00:" + '{:02x}'.format(i+2) +  " && batctl if add eth0 && ip link set up bat0 && ip addr add 10.0.1." +str(i+2) + "/255.255.255.0 broadcast 10.0.1.255 dev bat0"
      command = "modprobe batman-adv && batctl ra BATMAN_IV && batctl if add eth0 && ip link set up bat0 && ip addr add " + network_prefix +str(node) + "/255.255.255.0 broadcast 10.0.1.255 dev bat0"
      shell += " -c '" + command + "'"
      node = subprocess.Popen([
                    "xterm",
                    "-e",
                    shell], stdin=subprocess.PIPE, shell=False)
      process.append(node)

  def server_thread(self):
    """ 
    """
    'Starts a thread with the Socket.io instance that will serve the HMI'
    #mobile_lan = self.scenario.get_wlans()['mobile']
    wlans = self.scenario.get_wlans()
    #print(wlans)
    #sys.exit(1)
    nodes = []
    corenodes = self.scenario.get_mace_nodes()
    for node in corenodes:
      nodes.append(node.corenode)
    self.iosocket = emulation_interface.Socket(nodes, wlans, self.session, self.modelname, self.nodes_digest, self.iosocket_semaphore, self, self.callback, self.scenario.get_networks(), self.scenario.mace_nodes, self.daemon_socket)

  def killsim(self):
    """_summary_
    """

    os.system("sudo killall xterm")
    self.try_to_clean()

    pid = os.popen("ps aux  |grep \"pymace.py\" | grep -v \"grep\" | awk '{print $2}'").readlines()
    for p in pid:
      os.system("sudo kill -s 9 " + str(p))

  def stop(self):
    """_summary_
    """
    print("emulator> #################################################STOP###################################################")
    self.scenario.stop()
    self.running = False

  def run(self):
    """Runs the emulation of a heterogeneous scenario
    """
    #Setup and Start core
    if self.scenario == None:
      logging.error("Load scenario before")
      return
    self.setup_core()

    #Setup mobility
    self.scenario.configure_mobility(self.session)

    #start dumps
    #if self.scenario.dump:
      #get simdir
    simdir = str(time.localtime().tm_year) + "_" + str(time.localtime().tm_mon) + "_" + str(time.localtime().tm_mday) + "_" + str(time.localtime().tm_hour) + "_" + str(time.localtime().tm_min)

    self.scenario.tcpdump(self.session, simdir)

    #Start socketio thread
    sthread = threading.Thread(target=self.server_thread, args=())
    sthread.start()

    #Start routing and applications
    self.scenario.start_routing(self.session)
    self.scenario.start_applications(self.session)

    while self.scenario.running:
      time.sleep(0.1)

    print("#################################################STOP###################################################")

    if not self.daemon_mode: 
      # shutdown session
      logging.info("Simulation finished. Killing all processes")
      requests.get('http://localhost:5000/sim/stop')

      sthread.join()
      self.session.shutdown()

      try:
        self.killsim()
        os.system("sudo killall xterm")
        os.system("chown -R " + self.scenario.username + ":" + self.scenario.username + " ./reports")
      except:
        pass
    else:
      self.running = False
      sthread.join()
      self.coreemu.shutdown()
      self.scenario = None
      #self.try_to_clean()


