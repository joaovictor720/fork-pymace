#!/bin/env python3

__author__ = "Bruno Chianca Ferreira"
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Bruno Chianca Ferreira"
__email__ = "brunobcf@gmail.com"

import traceback, threading, time, sys


class TraceMobility():
  """_summary_
  """
  def __init__(self, tracerfile) -> None:
    """_summary_

    Args:
        tracerfile (_type_): _description_
    """
    self.number_of_nodes = 0
    self.ordered_position_updates = []
    self.read_file(tracerfile)
    self.callbacks = []
    self.mobility_thread = threading.Thread(target=self.update, args=())
    self.update_interval_ms = 10
    self.simulation_time_ms = 0
    self.old_time = time.time() / 1000

  def read_file(self, file):
    """_summary_

    Args:
        file (_type_): _description_
    """
    self.tracefile = open(file,"r").readlines()
    for line in self.tracefile:
      # time, node, x,y,z
      self.ordered_position_updates.append([line[0], line[1], [line[2], line[3], line[4]]])
    self.ordered_position_updates = self.ordered_position_updates.sort(key=0)

  def register_callback(self, callback):
    """_summary_

    Args:
        callback (function): _description_
    """
    self.callbacks.append(callback)

  def start(self):
    """_summary_
    """
    self.mobility_thread.start()

  def shutdown(self):
    """_summary_
    """
    self.mobility_thread.join()

  def update(self):
    """_summary_
    """
    if (time.time() / 1000) > (self.old_time + self.update_interval_ms):
      self.simulation_time_ms += time.time() / 1000 - self.old_time
      self.old_time = time.time() / 1000
      self.check_list()

  def check_list(self):
    """_summary_
    """
    updates = []
    for i in range(0,len(self.ordered_position_updates)):
      #not so heavy, only loops until it needs
      if self.ordered_position_updates[i][0] >= self.simulation_time_ms:
        updates.append(self.ordered_position_updates[i])
      else:
        break
    #now remove the traces from the list
    self.ordered_position_updates = self.ordered_position_updates[len(updates):]
    #now callback
    for update in updates:
      for callback in self.callbacks:
        callback(update[1], update[2])