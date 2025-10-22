#!/usr/bin/env python3

""" 
Tracer class is part of a thesis work about distributed systems 

This appliaction is a tracer that keeps logs that can be used later for analysis
"""
__author__ = "Bruno Chianca Ferreira"
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Bruno Chianca Ferreira"
__email__ = "brunobcf@gmail.com"

import os, struct, sys, traceback, time

class Tracer:
  """_summary_
  """
  def __init__(self, Node, tag, folder):
    """_summary_

    Args:
        Node (_type_): _description_
        tag (_type_): _description_
        folder (_type_): _description_
    """
    #Creates folder
    self.Node = Node
    self.Tag = tag
    self.destination_folder = folder
    self.simdir = str(time.localtime().tm_year) + "_" + str(time.localtime().tm_mon) + "_" + str(time.localtime().tm_mday) + "_" + str(time.localtime().tm_hour) + "_" + str(time.localtime().tm_min)
    self._create_folders(self.simdir)
    #self.start()

  def start(self):
    """_summary_
    """
    #Opens file
    self.tracefile = open(self.destination_folder + self.simdir + "/" + "tracer/net_trace_" + self.Tag + ".csv","w")
    self.tracefile.write('time;id;direction;packet;size;sender/receiver\n')
    self.app_tracefile = open(self.destination_folder + self.simdir + "/" + "tracer/app_trace_" + self.Tag + ".csv","w")
    self.app_tracefile.write('time;text\n')
    self.status_tracefile = open(self.destination_folder + self.simdir + "/" + "tracer/status_trace_" + self.Tag + ".csv","w")
    self.position_tracefile = open(self.destination_folder + self.simdir + "/" + "tracer/position_trace_" + self.Tag + ".csv","w")
    self.position_tracefile.write('time(ms);x;y;z\n')


  def shutdown(self):
    """_summary_
    """
    #Closes file
    self.tracefile.flush()
    self.app_tracefile.flush()
    self.status_tracefile.flush()
    self.position_tracefile.flush()
    self.tracefile.close()
    self.app_tracefile.close()
    self.status_tracefile.close()
    self.position_tracefile.close()

  def add_postition_trace(self, time, position):
    """_summary_

    Args:
        time (_type_): _description_
        position (_type_): _description_
    """
    try:
      self.position_tracefile.write(str(time) + ';' + str(position[0]) + ';' + str(position[1]) + ';' + str(position[2]) + '\n')
      self.position_tracefile.flush()
    except:
      pass

  def add_trace(self, data):
    #Add entry to file
    self.tracefile.write(str(int(time.time()*1000)) + ';' + data + '\n')

  def add_app_trace(self, data):
    #Add entry to file
    self.app_tracefile.write(str(int(time.time()*1000)) + ';' + data + '\n')

  def add_status_trace(self, status):
    #Add entry to file
    self.status_tracefile.write(status + '\n')

  def _create_folders(self, simdir):
    try:
      os.mkdir(self.destination_folder  + simdir)
      #print("Tracer folder created.")
    except FileExistsError:
      pass
      #print("Tracer folder created.")
    except:
      traceback.print_exc()

    try:
      os.mkdir(self.destination_folder  + simdir + "/tracer")
      #print("Tracer folder created.")
    except FileExistsError:
      pass
      #print("Tracer folder created.")
    except:
      traceback.print_exc()
      sys.exit(1)

