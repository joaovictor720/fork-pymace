#!/usr/bin/env python3

""" 
Prompt class is part of a thesis work about distributed systems 
"""
__author__ = "Bruno Chianca Ferreira"
__license__ = "MIT"
__version__ = "0.8"
__maintainer__ = "Bruno Chianca Ferreira"
__email__ = "brunobcf@gmail.com"

import sys, traceback, time, threading
from collections import deque

class Prompt(threading.Thread):

  def __init__(self, tag, extra = None):
    threading.Thread.__init__(self)
    self.tag = tag
    self.lock = True
    self.history = deque([],100) #logbook of all messages received
    self.prompt_str = "#>"
    self.prompt_extra = extra

  def shutdown(self):
    self.lock = False

  def __del__(self):
    """_summary_
    """
    pass

  def run(self):
    try:
      while self.lock==True:
        inp = input(self.tag + self.prompt_str)
        command = inp.split()
        self.history.append(command)
        try:
          if (len(command)) >= 1:
            if command[0] == 'help':
              self.printhelp(command)
            elif command[0] == 'clear':
              print("\033c")
              print()
            elif command[0] == 'quit':
              self.lock = False
              sys.stdout.write('Quitting')
              while True:
                sys.stdout.write('.')
                sys.stdout.flush()
                time.sleep(1)
            else:
              self.prompt_extra(command)
              #self.print_error("Invalid command!")

        except:
            print('General error, probably the emulation have not started yet. When using CORE and BATMAN we need to wait 26s to start.')
    except:
        traceback.print_exc()
        self.lock = False
        self.print_alert("Exiting!")

  def printhelp(self, command):
      'Prints help message'
      print()
      print("clear\t- Clear the display")
      print("help\t- Diplay this help message")
      print("quit\t- Exit the agent")
      try:
        self.prompt_extra(command)
      except:
        pass
      print()

def print_error(text):
    'Print error message with special format'
    print()
    print("\033[1;31;40m"+text+"  \n")
    print("\033[0;37;40m")

def print_alert(text):
    'Print alert message with special format'
    print()
    print("\033[1;32;40m"+text+"  \n")
    print("\033[0;37;40m")

def print_info(text):
    'Print info message with special format'
    print()
    print("\033[1;34;40m"+text+"  \n")
    print("\033[0;37;40m")