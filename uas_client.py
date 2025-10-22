#!/usr/bin/env python3

""" 
UAS Client side
This software is emulates the behaviour of a UAS that broadcasts its data to the UTM system
"""
__author__ = "Bruno Chianca Ferreira"
__license__ = "MIT"
__version__ = "0.2"
__maintainer__ = "Bruno Chianca Ferreira"
__email__ = "brunobcf@gmail.com"

# Python stdlib
import sys
import pickle
import argparse
import logging
import random
import traceback
import zlib
import time
import tzlocal
# Scheduler
from apscheduler.schedulers.background import BackgroundScheduler
# Local
import network_sockets
from gps_bridge import GPSBridge


class UASClient():
  """
  Emulates a simple implementation of the UAS Client that broadcasts its position to a UTM system

  .. note::

      The position is pooled to a fake GPS that is actually a UNIX Socket created by the mobile ad hoc computing emulator

  """
  def __init__(self, tag):
    """UTM Client

    Args:
        tag (str) - A unique identifier for the aircraft

    Kwargs:
        

    """
    self.start = int(time.time())
    breque = random.random() + 30
    time.sleep(breque) #waiting for routing to get stable

    self.tag = tag
    self.surface_position = []
    self.velocity = 0
    self.status = ""

    self._setup()


  def _setup(self):
    """ 
    Runs initial setup

    Parameters
    ----------

    Returns
    --------

    """
    #self.interval = 10 modified by Jiayi 20231215
    self.interval = 0.1
    self.timer = 300
    self.skip = False
    #print("uas_client> Current working directory: {0}".format(os.getcwd()))
    #self.report_file = open("/home/mace/pymace/reports/wind_farm/" + self.tag + ".csv","w")
    #self.report_file.write('time;created;id;aircraft;position;vel;status\n')
    self.scheduler = BackgroundScheduler(timezone=str(tzlocal.get_localzone()))
    #logging.getLogger('apscheduler').setLevel(logging.ERROR)
    logging.getLogger('apscheduler.executors.default').propagate = False
    self.scheduler.start()
    #self.scheduler.add_job(self.adsb_broadcaster, 'interval', seconds = self.interval, id="adsb_broadcaster", args=[])
    #self.uas_interface = network_sockets.UdpInterface(self.uas_packet_handler, debug=False, port=44444, interface='')
    #self.uas_interface.start()
    self.set_status("OK")
    self.set_position([0,0,0])
    self.set_velocity = 0
    self.gps = GPSBridge(self.tag)

  def set_position(self, pos):
    """ 
    Set the position

    Parameters
    ----------
    pos (list) - Coordinates X, Y

    Returns
    --------

    """
    self.surface_position = pos

  def get_position(self):
    """ 
    Returns the current position

    Parameters
    ----------

    Returns
    --------
    pos (list) - Coordinates X, Y

    """
    return self.surface_position

  def set_velocity(self, vel):
    """ 
    Sets the current velocity

    Parameters
    ----------
    vel (float) - Current velocity

    Returns
    --------
    

    """    
    self.velocity = vel

  def get_velocity(self):
    """ 
    Gets the current velocity

    Parameters
    ----------

    Returns
    --------
    vel (float) - Current velocity
    
    """    
    return self.velocity

  def set_status(self, status):
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

  def get_status(self):
    """ 
    Gets the current status

    Parameters
    ----------


    Returns
    --------
    status (string) - Current status
    
    """     
    return self.status

  def adsb_broadcaster(self):
    """ 
    Broadcasts UAS information.
    Pools the position from the GPS and calls broadcast

    Parameters
    ----------

    Returns
    --------

    """
    position = self.gps.get_position()
    position = pickle.loads(position)

    if position == [-1, -1, -1]:
      self.set_position([100,100,100])
    else:
      self.set_position(position)

    created = str(int(time.time()*1000000))
    unique_id = self._create_id()
    identification = self.tag
    position = self.get_position()
    velocity = self.get_velocity()
    status = self.get_status()

    data = str(created) + ";" + str(hex(unique_id)) + ";" + str(identification) + ";" + str(position) + ";" + str(velocity) + ";" + str(status)

    self.broadcast(unique_id, [created, identification, position, velocity, status])
    self.save_to_file(data)

  def uas_packet_handler(self, payload, sender_ip, connection):
    """ 
    UAS packet handler

    Parameters
    ----------
    payload (bin) - Pickled payload
    sender_ip (str) - Sender's IP address 
    connection (socket) - Connection open socket

    Returns
    --------

    """
    pass

  def save_to_file(self, data):
    """ 
    Save data to file

    Parameters
    ----------
    data (list) - List of CSV strings

    Returns
    --------
    
    """   
    if not self.skip:
      self.report_file.write(str(int(time.time()*1000000)) + ';' + data + '\n')
    if int(time.time()) > self.start + self.timer:
      if not self.skip:
        logging.info("UTMClient>save_to_file>Emulation session ended. Saving to file")
        self.report_file.flush()
        self.report_file.close()
        self.skip = True

  def broadcast(self, id, payload):
    """ 
    Broadcasts payload via UDP socket

    Parameters
    ----------
    id (bin) - unique CRC32 id. 
    payload (list) - List of attributes to be broadcast

    Returns
    --------

    """
   # try: 
   #   self.uas_interface.send('12.0.0.255', payload, id)
   # except:
   #   logging.error("UTMClient>broadcast>Failed to broadcast data")

  def _create_id(self):
    """ 
    Creates an unique CRC32 id

    Parameters
    ----------

    Returns
    --------
    id (bin) - unique CRC32 id. 

    """
    return zlib.crc32((str(int(time.time()*1000))+ str(self.tag) + str(random.randint(0,10000))).encode())


  def _printhelp(self):
    """ 
    Method for printing help information

    Parameters
    ----------

    Returns
    --------

    """
    print()

#######################Class END###############################################################################################

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

def set_logging():
  """ 
  Method for setting the logging levels

  Parameters
  ----------

  Returns
  --------

  """
  logging.Formatter('%(asctime)s -> [%(levelname)s] %(message)s')
  logger = logging.getLogger()
  logger.setLevel(logging.INFO)
  logging.basicConfig(level='INFO')

###########################Runner ################################################################################################


if __name__ == "__main__":
  set_logging()
  logging.info("Starting")
  args = parse_args()
  if args.tag == None:
    logging.error("UTMClient>Missing tag name")
    sys.exit(1)
  try:
    UASClient(args.tag)
  except KeyboardInterrupt:
    logging.info("UTMClient>Exiting UTM Client")
  