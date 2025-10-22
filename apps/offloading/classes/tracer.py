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

class GenericTracer:

  def __init__(self, tag, folder):
    #Creates folder
    self.Tag = tag
    self.report_folder = folder
    self.simdir = str(time.localtime().tm_year) + "_" + str(time.localtime().tm_mon) + "_" + str(time.localtime().tm_mday) + "_" + str(time.localtime().tm_hour) + "_" + str(time.localtime().tm_min)
    self._create_folders(self.simdir)
    self.reports = {}


  def add_report(self, report_name, header):
    filename = self.report_folder + "/" + self.simdir + "/" + report_name + "_" + self.Tag + ".csv" 
    self.reports[report_name] = open(filename,"w")
    if header[:2] != "\n":
      self.reports[report_name].write(header + "\n")
    else:
      self.reports[report_name].write(header)

  def add_to_report(self, report_name, entry):
    if report_name in self.reports.keys():
      self.reports[report_name].write(entry)
      self.reports[report_name].flush()
      return True
    return False

  def start(self):
    #Opens file
    self.tracefile = open(self.report_folder + "/"  + self.simdir + "/" + "/app_trace_" + self.Tag + ".csv","w")
    self.tracefile.write('time;id;direction;packet;size;sender/receiver\n')
    self.delivery = open(self.report_folder + "/"  + self.simdir + "/" + "/delivery_trace_" + self.Tag + ".csv","w")
    self.delivery.write('time;simulation_time;sumbytes\n')
    self.status_tracefile = open(self.report_folder + "/"  + self.simdir + "/" + "/status_trace_" + self.Tag + ".csv","w")
    self.consensus_report = open(self.report_folder + "/"  + self.simdir + "/consensus_trace_" + self.Tag +".csv","w")
    self.consensus_report.write("time;direction\n")


  def shutdown(self):
    #Closes file
    self.tracefile.flush()
    self.delivery.flush()
    self.status_tracefile.flush()
    self.consensus_report.flush()
    self.tracefile.close()
    self.delivery.close()
    self.status_tracefile.close()
    self.consensus_report.close()

    for report, file in self.reports.items():
      file.flush()
      file.close()


  def add_trace(self, data):
    #Add entry to file
    self.tracefile.write(str(int(time.time()*1000)) + ';' + data + '\n')

  def add_delivery(self, simulation_time, data):
    #Add entry to file
    self.delivery.write(str(int(time.time()*1000)) + ';' + str(simulation_time) + ';' + data + '\n')

  def add_status_trace(self, status):
    #Add entry to file
    self.status_tracefile.write(status + '\n')

  def add_consensus_trace(self, time, direction):
    self.consensus_report.write(str(time) + ";" + str(direction) + "\n")

  def _create_folders(self, simdir):
    try:
      os.mkdir(self.report_folder + "/" + simdir)
      #print("Tracer folder created.")
    except FileExistsError:
      pass
      #print("Tracer folder created.")
    except:
      traceback.print_exc()


