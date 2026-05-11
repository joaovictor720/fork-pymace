""" 
Scenario class
"""
__author__ = "Bruno Chianca Ferreira"
__license__ = "MIT"
__version__ = "0.2"
__maintainer__ = "Bruno Chianca Ferreira"
__email__ = "brunobcf@gmail.com"

from classes.auxiliar.tracer import Tracer
from classes.nodes.gen_node import GenericNode
from classes.mobility.node_mobility import Mobility
from classes.bus.bus import Bus

#Core libs
from core.emulator.data import IpPrefixes, NodeOptions
from core.nodes.base import CoreNode
from core.nodes.network import WlanNode
from core.location.mobility import BasicRangeModel
from classes.auxiliar.virtual_gps import VirtualGPS

from core.emane.models.ieee80211abg import EmaneIeee80211abgModel
from core.emane.models.rfpipe import EmaneRfPipeModel
from core.emane.models.tdma import EmaneTdmaModel
from core.emane.nodes import EmaneNet

#Other libs
import json, sys, subprocess, os, traceback, pprint, threading, time

SHARED_MOBILITY_MODELS = {
  'RANDOM_WAYPOINT',
  'RANDOM_WALK',
  'TRUNCATED_LEVY',
  'HETEROGENEOUS_TRUNCATED_LEVY',
  'GAUSS_MARKOV',
  'RANDOM_DIRECTION',
  'REFERENCE_POINT_GROUP',
  'TVC',
}


def _mobility_key(config):
  return json.dumps(config, sort_keys=True, separators=(',', ':'))

class Scenario():
  """ The Scenario class has the function of reading the scenario file
      It has the function of managing the Scenario itself, setting up nodes and applications
      Starting applications, checking if we should stop running
      TODO: every type of application should be a class, this way it is easy for a user to create new types.
  """
  def __init__(self, scenario_json) -> None:
    """_summary_

    Args:
        scenario_json (_type_): _description_
    """
    self.fixed_wlan = WlanNode
    self.mobile_wlan = WlanNode
    self.cwd = os.getcwd() #TODO, create an option so that we don't need this

    self._setup(scenario_json)

    self.list_of_fixed_nodes = []
    self.wlans = {}
    self.node_options_fixed = []
    self.node_options_mobile = []
    self.networks = {}
    self.core_nodes = {}
    self.core_nodes_by_name= {}
    self.node_options = {}
    self.prefixes = {}
    self.etcd_cluster = {}
    self.mace_nodes = []
    self.simulation_time = 0
    self.running = False
    self._shutdown_started = False
    self.load_networks(scenario_json)
    self.load_nodes(scenario_json)

    self.start() #started from emulatior

  def start(self):
    """_summary_
    """
    self.running = True

  def _setup(self, scenario_json):
    """_summary_

    Args:
        scenario_json (_type_): _description_
    """
    try:
      self.number_of_nodes = scenario_json['settings']['number_of_nodes']
      self.username = scenario_json['settings']['username']
      self.runtime = scenario_json['settings']['runtime']
      self.report_folder = scenario_json['settings']['report_folder']
      self.disks_folder = scenario_json['settings']['disks_folder']
      self.dump = True if scenario_json['settings']['dump'] == "True" else False
      self._nodes = scenario_json['nodes']
      self.emane_location = scenario_json['settings']['emane_location']
      self.emane_scale = float(scenario_json['settings']['emane_scale'])
    except:
      print("Error loading configurations. Check traceback log for more information")
      traceback.print_exc()

  def load_networks(self, scenario_json):
    """_summary_

    Args:
        scenario_json (_type_): _description_
    """
    networks = scenario_json['networks']
    for network in networks:
      self.networks[network['name']] = {}
      self.networks[network['name']] = network
      self.prefixes[network['name']] = IpPrefixes(network['prefix'])

  def load_nodes(self, scenario_json):
    """_summary_

    Args:
        scenario_json (_type_): _description_
    """
    nodes = scenario_json['nodes']
    for node in nodes:
      try: 
        self.mace_nodes.append(GenericNode(
          coordinates = [node['settings']['x'], node['settings']['y'], 0],
          tagname = node['settings']['type'] + str(node['settings']['_id']),
          tag_number = node['settings']['_id'],
          node_type = node['type'],
          function = node['function'],
          name = node['name'],
          nodetype = node['type'],
          disks = True if (node['extra']['disks'] == "True") else False,
          dump = True if (node['extra']['dump']['start'] == "True") else False,
          dump_delay = int(node['extra']['dump']['delay']),
          dump_duration = int(node['extra']['dump']['duration']),
          mobility = node['extra']['mobility'],
          network = node['extra']['network'],
          max_position = None if node['extra']['mobility'] == "none" else [node['extra']['mobility']['zone_x'], node['extra']['mobility']['zone_y'], node['extra']['mobility']['zone_z']],
          velocity = None if node['extra']['mobility'] == "none" else [node['extra']['mobility']['velocity_lower'], node['extra']['mobility']['velocity_upper']]
        ))
      except:
        traceback.print_exc()

  def setup_nodes(self, session):
    """_summary_

    Args:
        session (_type_): _description_
    """
    for node in self.mace_nodes:
      node.options = NodeOptions(name=node.name, x=node.coordinates[0], y=node.coordinates[1])
      core_node = session.add_node(CoreNode, options=node.options)
      node.corenode = core_node
      node.gps = VirtualGPS(node.name, node.tag_number)
      node.gps.start()
      node.set_position([node.coordinates[0],node.coordinates[1]])
      node.time_thread = threading.Thread(target=node.increment_time, daemon=True)
      node.tracer = Tracer(node, node.tagname, self.report_folder)
      #node.tracer.start()
      #node.time_thread.start()
    self.timer_thread = threading.Thread(target=self.check_runtime, daemon=True)
    self.timer_thread.start()

  def _shutdown_nodes(self):
    if self._shutdown_started:
      return

    self._shutdown_started = True
    current = threading.current_thread()
    stopped_mobility = set()

    for node in self.mace_nodes:
      try:
        if node.mobility_model is not None:
          mobility_id = id(node.mobility_model)
          if mobility_id not in stopped_mobility:
            stopped_mobility.add(mobility_id)
            node.mobility_model.shutdown()
      except:
        pass

      try:
        node.stop()
      except:
        pass

      try:
        if node.time_thread is not None and node.time_thread is not current and node.time_thread.is_alive():
          node.time_thread.join(timeout=2)
      except:
        pass

      try:
        if node.gps is not None:
          node.gps.shutdown()
      except:
        pass

      try:
        if node.tracer is not None:
          node.tracer.shutdown()
      except:
        pass

  def check_runtime(self):
    """_summary_
    """
    while((self.runtime >= self.simulation_time) and self.running):
      time.sleep(1)
      self.simulation_time += 1
    self.running = False
    self._shutdown_nodes()

  def stop(self):
    print("scenario> #################################################STOP###################################################")
    self.running = False
    self._shutdown_nodes()

  def setup_links(self, session):
    """_summary_

    Args:
        session (_type_): _description_
    """
    for node in self.mace_nodes:
      for net in node.network:
        interface = self.prefixes[net].create_iface(node.corenode)
        session.add_link(node.corenode.id, self.wlans[net].id, iface1_data=interface)

  def setup_wlans(self, session):
    """_summary_

    Args:
        session (_type_): _description_
    """
    for network in self.networks:
      options = NodeOptions(name=network, x=0, y=0)
      lan = session.add_node(WlanNode,options=options)
      self.wlans[network] = lan
      session.mobility.set_model_config(lan.id, BasicRangeModel.name,self.networks[network]['settings'])
      #config = session.emane.get_model_config(lan.id, EmaneIeee80211abgModel.name)
      #pp = pprint.PrettyPrinter(indent=4)
      #pp.pprint (config)

  def setup_wlan_emane(self, session):
    """_summary_

    Args:
        session (_type_): _description_
    """
    session.location.setrefgeo(self.emane_location[0], 
                               self.emane_location[1], 
                               self.emane_location[2])
    session.location.refscale = 150.0
    for network in self.networks:
      options = NodeOptions(x=200, y=200, emane=EmaneIeee80211abgModel.name)
      lan = session.add_node(EmaneNet, options=options)
      modelname = EmaneIeee80211abgModel.name
      #config = session.emane.get_configs()
      #config.update({"eventservicettl": "2"})
      wifi_options = {
        "unicastrate": "12",
        "multicastrate": "12",
        "mode":"1",
      }
      self.wlans[network] = lan
      session.emane.set_config(lan.id, EmaneIeee80211abgModel.name, wifi_options)
      #session.mobility.set_model_config(lan.id, BasicRangeModel.name,self.networks[network]['settings'])
      #config = session.emane.get_model_config(lan.id, EmaneIeee80211abgModel.name)
      #pp = pprint.PrettyPrinter(indent=4)
      #pp.pprint (config)
      #sys.exit(1)

  def start_applications(self, session):
    """_summary_

    Args:
        session (_type_): _description_
    """
    for node in self.mace_nodes:
      for function in node.function:
        if function == 'terminal':
          self.start_terminal(session, node.corenode.id, node.name)
        elif function == 'disk':
          disk = self.create_disk(node.corenode.id, node.name)
        elif function == 'etcd':
          self.etcd_cluster[node.name] = {}
          prefix = self.networks['fixed']['prefix'] #TODO fix
          prefix = prefix.split("/")[0]
          prefix = prefix.split(".")
          prefix[2] = str(int(prefix[2]) + 1)
          ip = prefix.copy()
          ip[3] = str(node.corenode.id)
          ip = '.'.join(ip)
          self.etcd_cluster[node.name]["ip"] = ip
          self.etcd_cluster[node.name]["prefix"] = prefix
          self.etcd_cluster[node.name]["disk"] = disk
          self.etcd_cluster[node.name]["id"] = node.corenode.id
        else:
          self.custom_application(session, node.corenode.id, function)
      node.tracer.start()

    for node in self.mace_nodes:
      node.time_thread.start()
          
    if len(self.etcd_cluster) > 0:
      self.start_etcd(session)

  def start_terminal(self, session, i, node):
    """_summary_

    Args:
        session (_type_): _description_
        i (_type_): _description_
        node (_type_): _description_

    Returns:
        _type_: _description_
    """
    ### TODO: add all application starts to inside node class
    nodes = {}
    shell = session.get_node(i, CoreNode).termcmdstring(sh="/bin/bash")
    command = ""
    node_process = subprocess.Popen([
                    "xterm",
                    "-e",
                    shell], stdin=subprocess.PIPE, shell=False)
    nodes[node] = node_process
    return nodes

  def create_disk(self,  i, node):
    """_summary_

    Args:
        i (_type_): _description_
        node (_type_): _description_

    Returns:
        _type_: _description_
    """
    #create virtual disk for each node
    disk = self.disks_folder + node
    os.system("umount " + disk + "  > /dev/null 2>&1 || /bin/true")
    os.system("rm -rf " + disk)
    os.system("mkdir -p " + disk)
    command = "mount -t tmpfs -o size=512m tmpfs " + disk + " &"
    node = subprocess.Popen([
                    "bash",
                    "-c",
                    command])
    return disk

  def start_routing(self, session):
    """_summary_

    Args:
        session (_type_): _description_
    """
    for node in self.mace_nodes:
      for net in node.network:
        if self.networks[net]['routing'].upper() == 'NONE':
          continue
        elif self.networks[net]['routing'].upper() == 'BATMAN':
          self.configure_batman(session, self.networks[net]['prefix'], node.corenode.id)

  def configure_batman(self, session, network_prefix, id):
    """_summary_

    Args:
        session (_type_): _description_
        network_prefix (_type_): _description_
        id (_type_): _description_
    """
    #Configure Batman only on fixed network
    network_prefix = network_prefix.split("/")[0]
    network_prefix = network_prefix.split(".")
    network_prefix[2] = str(int(network_prefix[2]) + 1)
    ip = network_prefix.copy()
    ip[3] = str(id)
    ip = '.'.join(ip)
    broadcast = network_prefix.copy()
    broadcast[3] = '255'
    broadcast = '.'.join(broadcast)
    network_prefix = '.'.join(network_prefix)
    ###TODO Change this to do only on fixed nodes
    shell = session.get_node(id, CoreNode).termcmdstring(sh="/bin/bash")
    command = "modprobe batman-adv && batctl ra BATMAN_V && batctl if add eth0 && ip link set up bat0 && ip addr add " + ip + "/255.255.255.0 broadcast " + broadcast + " dev bat0"
    shell += " -c '" + command + "'"
    node = subprocess.Popen([
                  "bash",
                  "-c",
                  shell], stdin=subprocess.PIPE, shell=False)

  def start_etcd(self, session):
    """_summary_

    Args:
        session (_type_): _description_
    """
    cluster_opt = "--initial-cluster "
    cluster = []
    for node in self.etcd_cluster:
      cluster.append(node + "=http://" + self.etcd_cluster[node]['ip']+":2380")
    cluster = ','.join(cluster)
    cluster = cluster_opt + cluster
    for node in self.etcd_cluster:
      shell = session.get_node(self.etcd_cluster[node]['id'], CoreNode).termcmdstring(sh="/bin/bash")
      command = "/opt/etcd/bin/etcd --data-dir=" + self.etcd_cluster[node]['disk'] 
      command += " --name " + node
      command += " --initial-advertise-peer-urls http://" + self.etcd_cluster[node]['ip']+":2380 "
      command += "--listen-peer-urls http://" + self.etcd_cluster[node]['ip']+":2380 "
      command += "--advertise-client-urls http://" + self.etcd_cluster[node]['ip']+":2379 "
      command += "--listen-client-urls http://" + self.etcd_cluster[node]['ip']+":2379,http://127.0.0.1:2379 "
      command += cluster
      command += " --initial-cluster-state new "
      command += "--initial-cluster-token token-01"
      shell += " -c '" + command + "'"
      node = subprocess.Popen([
                      "xterm",
                      "-e",
                      shell], stdin=subprocess.PIPE, shell=False)

  def custom_application(self, session, i, application):
    """_summary_

    Args:
        session (_type_): _description_
        i (_type_): _description_
        application (_type_): _description_
    """
    shell = session.get_node(i, CoreNode).termcmdstring(sh="/bin/bash")
    shell = shell.split(" ")
    shell.append("-c")
    command = application
    shell.append(command)
    node = subprocess.Popen(shell, stdin=subprocess.PIPE, shell=False)

  def tcpdump(self, session, simdir):
    """ 
    Method for starting a tcpdump session

    Parameters
    ----------
    session - CORE session created in runner
    dir - 

    Returns
    --------

    """
    path = os.path.join(self.report_folder, simdir)
    
    try:
      os.mkdir(path)
      os.mkdir(os.path.join(path, "tracer"))
    except FileNotFoundError:
      os.mkdir(self.report_folder)
      os.mkdir(path)
      os.mkdir(os.path.join(path, "tracer"))
    except FileExistsError:
        pass
        #print("Report folder already created.")
    except:
        traceback.print_exc()

    dir = os.path.join(path, "tracer")

    for node in self.mace_nodes:
      if node.dump:
        shell = session.get_node(node.corenode.id, CoreNode).termcmdstring(sh="/bin/bash")
        command = "cd " + self.cwd + " && "
        #TODO: change eth0 to be configurable
        command += "sleep " + str(node.dump_delay) + " && timeout " + str(node.dump_duration) + " tcpdump -i eth0 -w "+ dir + node.name + ".pcap"
        shell += " -c '" + command + "'"
        node = subprocess.Popen([
              "xterm",
              "-hold",
              "-e",
              shell]
              ,stdin=subprocess.PIPE, shell=False)

  def get_core_nodes(self):
    return self.core_nodes    

  def get_wlans(self):
    return self.wlans

  def get_networks(self):
    return self.networks

  def get_mace_nodes(self):
    return self.mace_nodes
    
  def configure_mobility(self, session):
    """_summary_

    Args:
        session (_type_): _description_
    """
    shared_mobility = {}
    one_to_one_mobility = []

    for node in self.mace_nodes:
      if node.mobility == "none":
        continue

      model = node.mobility['model'].upper()

      # Group-aware pymobility models need to see every node in the group.
      if model in SHARED_MOBILITY_MODELS:
        mobility_key = _mobility_key(node.mobility)
        if mobility_key not in shared_mobility:
          shared_mobility[mobility_key] = Mobility(
            self,
            node.mobility['model'],
            node.max_position,
            node.velocity,
            node.coordinates,
            node.mobility,
          )
        node.mobility_model = shared_mobility[mobility_key]
      else:
        node.mobility_model = Mobility(
          self,
          node.mobility['model'],
          node.max_position,
          node.velocity,
          node.coordinates,
          node.mobility,
        )
        one_to_one_mobility.append(node.mobility_model)

      node.mobility_model.register_core_node(node.corenode)
      node.mobility_model.register_mace_node(node)

    for mobility_model in shared_mobility.values():
      mobility_model.configure_mobility()

    for mobility_model in one_to_one_mobility:
      mobility_model.configure_mobility()
