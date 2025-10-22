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

import os, sys, traceback, time

class Tracer:

  def __init__(self, name, tag, simdir, header, report_folder):
    """_summary_

    Args:
        tag (_type_): _description_
    """
    #Creates folder
    self.Tag = tag
    self.simdir = simdir
    self.header = header
    self.name = name
    self.report_folder = report_folder
    #self.simdir = str(time.localtime().tm_year) + "_" + str(time.localtime().tm_mon) + "_" + str(time.localtime().tm_mday) + "_" + str(time.localtime().tm_hour) + "_" + str(time.localtime().tm_min)
    self._create_folders(self.simdir)

  def start(self):
    """_summary_
    """
    #Opens file
    path = os.path.join(self.report_folder, self.simdir, self.name + "_" + self.Tag + ".csv")
    self.tracefile = open(path,"w")
    if self.header[-1:] != "\n":
      self.header.append("\n")
    self.tracefile.write(self.header)

  def shutdown(self):
    """_summary_
    """
    #Closes file
    self.tracefile.flush()
    self.tracefile.close()

  def add_trace(self, data):
    #Add entry to file
    self.tracefile.write(str(int(time.time()*1000)) + ';' + data + '\n')
    self.tracefile.flush()

  def _create_folders(self, simdir):
    try:
      os.mkdir(os.path.join(self.report_folder, simdir))
      #print("Tracer folder created.")
    except FileNotFoundError:
      os.mkdir(self.report_folder)
      os.mkdir(os.path.join(self.report_folder, simdir))
    except FileExistsError:
      pass
      #print("Tracer folder created.")
    except:
      traceback.print_exc()


