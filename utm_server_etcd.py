#!/usr/bin/env python3

""" 
Network class is part of a thesis work about distributed systems 
"""
__author__ = "Bruno Chianca Ferreira"
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Bruno Chianca Ferreira"
__email__ = "brunobcf@gmail.com"

# Python stdlib
import math
from os import execl
import random
import sys
import pickle
import json
import time
import argparse
import threading
import logging
from collections import deque
#etcd

import etcd3

#local
import network_sockets

#Ulysses imports
import tzlocal
from apscheduler.schedulers.background import BackgroundScheduler
from gps_bridge import GPSBridge
import subprocess
import requests
from datetime import datetime, timedelta

class UTMServer():
  """
  Emulates a simple implementation of the UAS endpoint on a UTM data service provider

  .. note::

      Each instance will be a separate thread with a open socket, thread runs in infinit loop and self.running must be set to False to end the thread.

  """
  Inspected_windTurbines= {} #to store the windturbines ids that were inspected i.e. its data was collected
  
  all_keys_list = []

  
  def __init__(self, tag, timer):
    """UTMServer UAS endpoint

    Args:
        tag (str) - A unique identifier for the UTM server
        timer (int) - For how long the session should run in seconds

    Kwargs:
        

    """
    self.tag = tag
  
    self.start = int(time.time())
    self.timer = timer
    self.sensortimer = timer
    #print(f"self.timer now is: {self.timer}")
    self.cache = deque([], maxlen=1000)
    #self.battery=(random.randint(1,200));#Setting the remaining time in the battery in seconds, the maximum value should be less than timer
    
    if self.tag == "uav0":
    #if ((self.tag == "uav1") or (self.tag == "uav2")):
    #if self.timer > self.battery: #Setting the timer to the remaining amount in the battery when the battery is not enough
       #self.timer =  2700
       self.sensortimer = 900
       print(f"{self.tag}, self.battery is limited: {self.timer}, self.sensor is limited: {self.sensortimer} ")
    else:
      print(f"{self.tag}, self.battery is perfect: {self.timer}")
    #print(f"{self.tag}, self.battery: {self.timer}")
    

    #etcd_server_url = 'http://localhost:2379'
    #version_info = self.get_etcd_version(etcd_server_url)
    #print(f"etcdserver version: {version_info['etcdserver']}")    #3.2.26
    #print(f"etcdcluster version: {version_info['etcdcluster']}")

    #self.get_etcd_metrics()


    self._setup()
    

    try:
        while int(time.time()) < (self.start + self.timer):
            time.sleep(0.001)
    except KeyboardInterrupt:
        logging.info("Keyboard interrupt received, exiting UTM Server and saving file.")
        self.save_to_file()
        raise
    else:
        print("Session ended normally")
        self.save_to_file()

    #while int(time.time()) < (self.start + self.timer):  #Ulysses Replacement
    # #while (int(time.time()) < (self.start + self.battery)) and  (int(time.time()) < (self.start + self.timer)):
    #  time.sleep(0.001)
    #print("Session ended")

   # self.save_to_file()
   ##self.save_position() #UlyssesAddition





  def _setup(self):
    """ 
    Runs initial configuration

    Parameters
    ----------

    Returns
    --------

    """
    
    self.data_bank_etcd = {}
    self.data_bank = {}

    

    self.report_file = open("/home/mace/pymace/reports/wind_farm/results_temp/" + self.tag + ".csv", "w")
    #self.report_file.write('time;created;id;aircraft;position;vel;status;inspector_UAV;inspector_UAV_position\n')
    self.report_file.write('time;inspected_WT;inspected_WT_position;inspector_UAV;inspector_UAV_position;distance;status;visited_wt;received_bytes;sent_bytes;leader_changes;is_leader;server_has_leader;health_failures;health_success;heartbeart_send_failures;data_bank;get_all_values_from_etcd\n')

    #Ulysses Additions

    self.wt_coordinates = {
        "wt1": (100, 100),  
        "wt2": (700, 100),  
        "wt3": (1300, 100),
        "wt4": (100, 460),
        "wt5": (700, 460),
        "wt6": (1300, 460),
        "wt7": (100, 820),
        "wt8": (700, 820),
        "wt9": (1300, 820)  
    }

    self.visited_wt = 0
    self.etcd_availability = 0
    self.flying_distance = 0
    self.previous_position = [0,0]
    self.position_skip = False
    self.interval = 10
    self.uav_position_tracker = [] #store the positions of the current object (uav) - Ulysses addition
    self.position_file = open("/home/mace/pymace/reports/wind_farm/results_temp/" + self.tag + "position.csv","w") #file to store UAV positions
    self.position_file.write('time;created;uav;uav_position;flying_distance;visited_wt;etcd_availability;received_bytes;sent_bytes;leader_changes;is_leader;server_has_leader;health_failures;health_success;heartbeart_send_failures\n')
    self.scheduler = BackgroundScheduler(timezone=str(tzlocal.get_localzone()))
    logging.getLogger('apscheduler.executors.default').propagate = False
    self.scheduler.start()
    self.scheduler.add_job(self.uav_position_update, 'interval', seconds=self.interval, id="uav_position_update", args=[])
    #self.set_uav_status("OK")
    self.set_uav_position([0,0])
    #self.set_velocity = 0
    self.gps = GPSBridge(self.tag)

    #End of Ulysses


    #try:
    self.etcd = etcd3.client()
    print(f"Testing etcd object content: {self.etcd}")

    # 将 self.start 和 self.timer 转换为 datetime 对象
    start_datetime = datetime.fromtimestamp(self.start)
    end_datetime = start_datetime + timedelta(seconds=self.sensortimer)
    
    self.scheduler.add_job(self.compare_distances, 'interval', seconds=5, start_date=start_datetime, end_date=end_datetime, id="compare_distances", args=[], max_instances=3)
    self.scheduler.add_job(self.write_to_etcd, 'interval', seconds=8, id="write_to_etcd", args=[], max_instances=3)

    #with open("log.txt", 'a') as f:
    #  f.write(f"Callback triggered with event: {self.etcd_callback}\n")


    self.etcd.add_watch_callback('wt', self.etcd_callback, range_end='wt99')
    #etcd will watch for the key wt, and when it sees it created, it will run the function self.etcd_callback. 
    
    #except:
    #  logging.info("Running UTM server without etcd")


    
  #Ulysses adding for compare the distance to decide if the UAV is inspecting one WT

  def calculate_distance(self, x1, y1, x2, y2):
    """
    Calculate the Euclidean distance between two points
    """
    return math.sqrt((x2 - x1)**2 + (y2 - y1)**2)



  def compare_distances(self):
    """
    Compare the distance of coordinates_uav to a series of coordinates_wt.
    If the distance is less than 5, print the message.
    
    """
    inspector = self.tag
    inspector_position = self.get_uav_position()
    x2, y2, _ = inspector_position

    # 存储需要写入的数据
    to_write = []

    for aircraft_id, (x1, y1) in self.wt_coordinates.items():
        distance = self.calculate_distance(x1, y1, x2, y2)
        #print(f"The distance between {self.tag} and wind turbine {aircraft_id} is {distance} m.")

        if distance < 65:
            self.visited_wt = self.visited_wt + 1
            #print(f"{self.tag}, Visited wt number is: {self.visited_wt}")

            if aircraft_id not in self.data_bank:
                data = json.dumps({
                                   "inspected_WT" : aircraft_id,
                                   "inspected_WT_position" : (x1, y1), 
                                   "inspector_UAV" : inspector,
                                   "inspector_UAV_position" : inspector_position,
                                   "distance" : distance,
                                   "status": False
                })
                # 将需要写入的数据添加到列表中
                to_write.append((str(aircraft_id), data))
            else:
                print(f"{self.tag}, {aircraft_id} is already inspected, skipping.")
        else:
           pass

    # 对需要写入的数据进行处理
    for aircraft_id, data in to_write:
        #print(inspector, "is trying to inspect", aircraft_id)
        #self.save_to_local_storage(aircraft_id, data)
        #self.write_to_etcd(aircraft_id, data)
        self.data_bank[aircraft_id] = data
        print(f"{self.tag}, Data for {aircraft_id} added to data_bank. {data}")
        
                 
            
  #End of Ulysses  


  def write_to_etcd(self):
    """ 
    Write data to ETCD and count the visited_wt number

    Parameters
    ----------
    aircraft_id (str) - unique aircraft ID
    data (list) - data

    Returns
    --------
    
    """
    
    
    try:    
        
        # 检查 data_bank_etcd 中是否有未同步到 ETCD 的数据（状态为 False）
        unsynced_data = []
        for key, value in self.data_bank.items():
            data_dict = json.loads(value)
            if data_dict.get("status") == False:
                unsynced_data.append((key, data_dict))
                print(f"{self.tag}, unsynced_data exists: {key}, {data_dict}")


        for key, data_dict in unsynced_data:
            try:  
                # 检查etcd集群的可用性
                status = self.etcd.status()
                if status.leader is not None:
                    self.etcd_availability = self.etcd_availability + 1
                    #print(self.tag, "ETCD is available.")
                    #print(self.tag, "Leader ID:", status.leader)
                    #print(self.tag, "Cluster version:", status.version) #cluster version is always the sam
                    try:
                        if self.etcd.get(key) == (None, None):                                                                     
                            try:
                                # 更新 data_bank 中的状态为 True
                                data_dict["status"] = True
                                data = json.dumps(data_dict)                           
                        
                                self.etcd.put(key, data)
                                print(f"{self.tag}, ETCD SAVED FOR {key} : {data}")                  
                            except Exception as e:
                                print(f"{self.tag}, Error while adding data to ETCD: {str(e)}")
                                # 写入失败时，重新设置状态为 False
                                data_dict["status"] = False
                                # 更新回 data_bank
                                self.data_bank[key] = json.dumps(data_dict)
                                self.etcd_availability = self.etcd_availability - 1
                        else:
                            # 更新 data_bank 中的状态为 True
                            data_dict["status"] = True
                            self.data_bank[key] = json.dumps(data_dict)
                            #print(self.tag, "The data of this windturbine was already stocked in etcd")
                    except Exception as e:
                        print(f"{self.tag}, Error while searching for key in ETCD, {str(e)}")
                        self.etcd_availability = self.etcd_availability - 1
                else:
                    #print(self.tag, "ETCD is not available, trying to save data locally")
                    #self.save_to_local_storage(key, data)
                    pass
            except Exception as e:
                print(f"{self.tag}, UTMServer>write_to_etcd>Error while checking ETCD status: {str(e)}")

    except Exception as e:
                    print(f"{self.tag}, Error while generating unsynced_data: {str(e)}")
                     

  def check_data_bank_completion(self):
    """
    Checks if the data_bank contains all required keys from wt1 to wt9.
    Prints a message once all keys are present.
    """
    required_keys = {f"wt{i}" for i in range(1, 10)}  # 使用集合生成从 wt1 到 wt9 的所有 key
    current_keys = set(self.data_bank_etcd.keys())

    # 检查是否所有必需的 keys 都存在
    if required_keys <= current_keys:
      
        print("==========================================================================================")
        print(f"{self.tag} finished its mission because we got 9 wt data through watch_callback()")
        #print(self.tag, "finished its mission.")
        print("==========================================================================================")


    # 检查 all_keys_list 的元素数量
    if len(self.all_keys_list) == 9:
        print("==========================================================================================")
        print(f"{self.tag} finished its mission because we got 9 wt data through etcd.get_all()")
        print(f"{self.tag} finished its mission at {str(int(time.time()*1000000))}")
        print("==========================================================================================")
      

  def etcd_callback(self, _event):
    """ 
    Callback function called everytime etcd senses data change in configure key

    Parameters
    ----------
    _event (list) - ETCD event list

    Returns
    --------

    """
    

    try:
      print(f"{self.tag}: etcd_callback>>1")
      for event in _event.events:
        print(f"{self.tag}: etcd_callback>>2")
        aircraft_data, _ = self.etcd.get(event.key)
        aircraft_data = aircraft_data.decode()
        #print(f"{self.tag}, aircraft_data of in JSON string format got from ETCD is {aircraft_data}")

        data_dict = json.loads(aircraft_data)
        print(f"{self.tag}, new data sensed from ETCD appearing: {data_dict}")
        aircraft_id = event.key.decode()
        
        # use get_all() to get the latest data stored by etcd 
        self.all_keys_list = []
        try:
            all_keys = self.etcd.get_all()
            print(f"{self.tag}, get_all() print start")
            for value, metadata in all_keys:
                value = value.decode()
                self.all_keys_list.append(value)
                print(f"{self.tag}, in etcd_callback: ETCD_get_all: Key: {value}")
            print(f"{self.tag}, get_all() print finish")
        except Exception as e:
            logging.error("UTMServer>etcd_callback>Error using get_all function to capture data from ETCD: " + str(e))
            print(f"{self.tag}: etcd_callback>>7")
        pass

        # Check if this data already exists in data_bank_etcd
        if aircraft_id not in self.data_bank_etcd:
            print(f"{self.tag}: etcd_callback>>3")
            # Extract data from the dictionary and generate a data string only if needed
            inspected_WT_position = data_dict['inspected_WT_position']
            inspector_UAV = data_dict['inspector_UAV']
            inspector_UAV_position = data_dict['inspector_UAV_position']
            inspecting_distance = data_dict['distance']
            #data = str(int(time.time()*1000000)) + ";" + str(created) + ";" + str(unique_id) + ";" + str(aircraft_id) + ";" + str(inspected_WT_position)+ ";" +str(inspector_UAV) + ";" + str(inspector_UAV_position)
            status = data_dict['status']
            received_bytes, sent_bytes, leader_changes, is_leader, server_has_leader, health_failures, health_success, heartbeart_send_failures = self.get_etcd_metrics()
            

            data_str = str(int(time.time()*1000000))+";"+ str(aircraft_id)+";"+str(inspected_WT_position)+";"+str(inspector_UAV)+";"+str(inspector_UAV_position)+";"+str(inspecting_distance)+";"+str(status)+';'+str(self.visited_wt)+";"+str(received_bytes)+";"+str(sent_bytes)+";"+str(leader_changes)+";"+str(is_leader)+";"+str(server_has_leader)+";"+str(health_failures)+";"+str(health_success)+";"+str(heartbeart_send_failures)+";"+str(self.data_bank)+";"+str(self.all_keys_list)

            
            self.data_bank_etcd[aircraft_id] = data_str
            print(f"{self.tag}, Data for {aircraft_id} got from ETCD is saving to data_bank_etcd: {data_str}")
            
      
            self.check_data_bank_completion()
        else:
            print(f"{self.tag}: etcd_callback>>4")
            print(f"{self.tag}, Data for {aircraft_id} got from ETCD has already existed in data_bank_etcd: {data_str}  ")    
        # Check if the data needs to be updated in data_bank
        if aircraft_id not in self.data_bank or json.loads(self.data_bank[aircraft_id]).get('status') == False:
            print(f"{self.tag}: etcd_callback>>5")
            self.data_bank[aircraft_id] = aircraft_data
            print(f"{self.tag}, Data for {aircraft_id} got from ETCD sensing has been added in data_bank: {aircraft_data}")
        
    except Exception as e:
        logging.error("UTMServer>etcd_callback>Error getting data from ETCD: " + str(e))
        print(f"{self.tag}: etcd_callback>>6")
        pass 
  
  def set_uav_status(self, status):#Added for Ulysses 
    """ 
    Sets the status

    Parameters
    ----------
    status (string) - Current status
    TODO:create ENUM of valid statuses

    Returns
    --------
    

    """     
    self.status = status
  
  def set_uav_position(self, pos):
    """ 
    Set the position

    Parameters
    ----------
    pos (list) - Coordinates X, Y

    Returns
    --------

    """
    self.surface_position = pos

  def uav_position_update(self): #Ulysses Addition
    """ 
    Broadcasts UAS information.
    Pools the position from the GPS and calls broadcast

    Parameters
    ----------

    Returns
    --------

    """
    self.previous_position=self.get_uav_position()
    #print("The previous position of ",self.tag," is",self.previous_position)

    position = self.gps.get_position()
    position = pickle.loads(position)

    if position == [-1, -1, -1]:
      self.set_uav_position([100,100,100])
    else:
      self.set_uav_position(position)

    created = str(int(time.time()*1000000))
    #print("the creation time is ", time.time())
    identification = self.tag
    position = self.get_uav_position()
    
    #print("The current position of ",self.tag," is",position)
    if self.previous_position != [0,0,0]:
      #last_distance_travelled=math.sqrt(((self.previous_position[0]- position[0])**2) + ((self.previous_position[1]- position[1])**2))
      last_distance_travelled = math.sqrt((pow((self.previous_position[0]- position[0]),2))+(pow((self.previous_position[1]- position[1]),2)))
      #print("The last distance travelled ",self.tag," is",last_distance_travelled)
      self.flying_distance += last_distance_travelled
      self.save_position(created, identification, position, self.flying_distance)
     
  def save_position(self, created, identification,current_position,flying_distance):#Ulysses Addition
    """ 
    Save positions to file
    --------
    
    """   
    try:
      if not self.position_skip:
        received_bytes, sent_bytes, leader_changes, is_leader, server_has_leader, health_failures, health_success, heartbeart_send_failures = self.get_etcd_metrics()           
        pos= str(int(time.time()*1000000))+';'+ str(created)+';'+str(identification)+';'+str(current_position)+';'+str(flying_distance)+';'+str(self.visited_wt)+';'+str(self.etcd_availability)+";"+str(received_bytes)+";"+str(sent_bytes)+";"+str(leader_changes)+";"+str(is_leader)+";"+str(server_has_leader)+";"+str(health_failures)+";"+str(health_success)+";"+str(heartbeart_send_failures)

        self.uav_position_tracker.append(pos)
     
    except:
      pass

  def get_uav_position(self): #Added for Ulysses 
    """ 
    Returns the current position

    Parameters
    ----------

    Returns
    --------
    pos (list) - Coordinates X, Y

    """
    return self.surface_position
  
  def get_uav_status(self):
    """ 
    Gets the current status

    Parameters
    ----------


    Returns
    --------
    status (string) - Current status
    
    """     
    return self.status
  
  def save_to_file(self):
    print("=============The final save method is being executed================")
    """ 
    Save databank to file

    Parameters
    ----------

    Returns
    --------
    
    """
    try: 
      for data in self.data_bank_etcd.values():
        self.report_file.write(data + '\n')
    except:
      logging.error("UTMServer>save_to_file>Error saving databank to file")

    self.report_file.flush()
    self.report_file.close()

    try: 
      #self.position_file.flush()
      #self.position_file.close()
      for uav_position in self.uav_position_tracker:
        self.position_file.write(uav_position + '\n')
      self.position_file.flush()
      self.position_file.close()
      self.etcd.close()
    except:
      logging.error("UTMServer>save_to_file>Error saving databank to position file")

  def utm_packet_handler(self, payload, sender_ip, connection):
    """ 
    UTM packet handler
    Called when data arrives on UTM endpoint

    Parameters
    ----------
    payload (bin) - Pickled payload
    sender_ip (str) - Sender's IP address 
    connection (socket) - Connection open socket

    Returns
    --------

    """
    pass

  def uas_packet_handler(self, payload, sender_ip, connection):

    """
    UAS packet handler
    Called when data arrives on UAS endpoint

    Parameters
    ----------
    payload (bin) - Pickled payload
    sender_ip (str) - Sender's IP address 
    connection (socket) - Connection open socket

    Returns
    --------
    
    
    try:
      payload = pickle.loads(payload)
    except:
      logging.error("UTMServer>utm_packet_handler>Received invalid UDP packet")

    unique_id = payload[0]

    created = payload[1][0]
    aircraft_id = payload[1][1]
    position = payload[1][2]
    velocity = payload[1][3]
    status = payload[1][4]
    inspector = self.tag
    inspector_position = self.get_uav_position()

    data = json.dumps({"created" : created,
                       "msg-id" : unique_id,
                       "position" : position,
                       "velocity" : velocity,
                       "status" : status,
                       "inspector_UAV" : inspector,
                       "inspector_UAV_position" : inspector_position,
    })
    #wt_found=False
    #for wt in UTMServer.Inspected_windTurbines:
      #if wt==aircraft_id:
        #wt_found=True
        #break

    #if (UTMServer.Inspected_windTurbines.get(aircraft_id)==None): #added by us 
    #if wt_found==False:
      #UTMServer.Inspected_windTurbines[aircraft_id]= self.tag
      #print("This is the list of read windturbines: ",UTMServer.Inspected_windTurbines)
    self.write_to_etcd(aircraft_id, data)
    """ 
    pass

  def fetch_etcd_metrics(self, etcd_server_url):
      """
      Fetch metrics from the etcd server's /metrics endpoint.

      :param etcd_server_url: URL of the etcd server (e.g., 'http://localhost:2379')
      :return: A dictionary of metrics
      """
      metrics_url = f"{etcd_server_url}/metrics"
      #print("============================INside")
      response = requests.get(metrics_url)

      if response.status_code != 200:
          raise Exception(f"Failed to fetch metrics: {response.status_code}")

      metrics_data = response.text
      metrics = parse_metrics(metrics_data)
      return metrics
  
  def get_etcd_metrics(self):
        metrics = self.fetch_etcd_metrics("http://localhost:2379")
        #print(f"{metrics}")

        received_bytes = metrics.get('etcd_network_client_grpc_received_bytes_total', 'N/A')
        sent_bytes = metrics.get('etcd_network_client_grpc_sent_bytes_total', 'N/A')
        leader_changes = metrics.get('etcd_server_leader_changes_seen_total', 'N/A')
        is_leader = metrics.get('etcd_server_is_leader', 'N/A')
        server_has_leader = metrics.get('etcd_server_has_leader', 'N/A')
        health_failures = metrics.get('etcd_server_health_failures', 'N/A')
        health_success = metrics.get('etcd_server_health_success', 'N/A')
        heartbeart_send_failures = metrics.get('etcd_server_heartbeat_send_failures_total', 'N/A')


        return received_bytes, sent_bytes, leader_changes, is_leader, server_has_leader, health_failures, health_success, heartbeart_send_failures

  def get_etcd_version(self, etcd_server_url):
    version_url = f"{etcd_server_url}/version"
    response = requests.get(version_url)

    if response.status_code != 200:
        raise Exception(f"Failed to fetch version: {response.status_code}")

    version_data = response.json()
    return version_data

#######################Class END###############################################################################################

class PrintLogger:
    def __init__(self, logger, level):
        self.logger = logger
        self.level = level

    def write(self, message):
        if message.strip():  # 忽略空行
            self.logger.log(self.level, message.strip())

    def flush(self):
        pass  # 这里不需要实现，兼容文件接口

def set_logging(log_filename):
    """ 
    Sets the logging levels and directs output to a log file

    Parameters
    ----------
    log_filename (str) - The name of the log file

    Returns
    --------

    """
    # 定义日志格式
    formatter = logging.Formatter('%(asctime)s -> [%(levelname)s] %(message)s')

    # 获取根日志记录器
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    
    # 移除所有与根日志记录器关联的处理程序
    for handler in logging.root.handlers[:]:
        logging.root.removeHandler(handler)

    # 创建文件处理程序，将日志输出到指定文件
    fh = logging.FileHandler(log_filename)
    fh.setLevel(logging.INFO)
    fh.setFormatter(formatter)
    
    # 创建控制台处理程序，将日志输出到控制台
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(formatter)
    
    # 将处理程序添加到日志记录器
    logger.addHandler(fh)
    logger.addHandler(ch)

    # 将 stdout 和 stderr 重定向到日志
    sys.stdout = PrintLogger(logger, logging.INFO)
    sys.stderr = PrintLogger(logger, logging.ERROR)


def parse_args():
  """ 
  Method for parsing command line arguments

  Parameters
  ----------

  Returns
  --------
  args - Arguments

  """
  parser = argparse.ArgumentParser(description='Some arguments are obligatory and must follow the correct order as indicated')
  parser.add_argument("-t", "--tag", help="Tag name", type=str)
  return parser.parse_args()

#def set_logging():
  """ 
  Sets the logging levels

  Parameters
  ----------

  Returns
  --------

  """
  logging.Formatter('%(asctime)s -> [%(levelname)s] %(message)s')
  logger = logging.getLogger()
  logger.setLevel(logging.INFO)
  logging.basicConfig(level='INFO')


def parse_metrics(metrics_data):
    """
    Parse the Prometheus metrics format into a dictionary.

    :param metrics_data: Raw metrics data as a string
    :return: A dictionary of metrics
    """
    metrics = {}
    for line in metrics_data.splitlines():
        if line.startswith('#') or not line.strip():
            continue  # Skip comments and empty lines

        key, value = line.split(' ', 1)
        metrics[key] = float(value)

    return metrics

###########################Runner ################################################################################################


if __name__ == '__main__':
  args = parse_args()
  log_filename = f"/home/mace/pymace/reports/wind_farm/results_temp/{args.tag}_server.log"
  set_logging(log_filename)
  logging.info("Starting UTM server")
  
  #set_logging()
  #logging.info("Starting UTM server")
  #args = parse_args()
  try:   
    UTMServer(args.tag, 7200)

  except KeyboardInterrupt:
    logging.info("Exiting UTM Server")



