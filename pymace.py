#!/usr/bin/env python3

""" 
When studying distributed systems, it is usefull to play with concepts and create prototype applications.
This main runner aims in helping with the prototyping, so that an application can be created as a class 
in ./classes/apps. This code contains the main runner that will run in each node. This is called by the 
pymace main emulation script, but can be called manually when running on real hardware or when running 
manually for testing.

Other support classes are also in ./classes to bootstrap some basic funcionality, but are completelly 
optional since most is already covered by better python libraries.

"""
__author__ = "Bruno Chianca Ferreira"
__license__ = "MIT"
__version__ = "0.7"
__maintainer__ = "Bruno Chianca Ferreira"
__email__ = "brunobcf@gmail.com"

import  sys, traceback, json, os, argparse, logging, shutil, signal, threading
from classes.runner.emulator import Emulator
from classes.interfaces.daemon_socket import Daemon

#TODO: Remove
from classes.runner.runner import Runner
from classes.runner.deprecated.termrunner import TERMRunner
from classes.runner.deprecated.etcdrunner import ETCDRunner
from classes.runner.deprecated.vmrunner import VMRunner
from classes.runner.deprecated.armrunner import ARMRunner
from classes.runner.deprecated.rasprunner import RaspRunner
from classes.runner.deprecated.dockerrunner import DockerRunner
from classes.runner.deprecated.riotrunner import RIOTRunner

class Mace():
    def __init__(self, settings):
      self.settings = settings
      self.scenario_folder = self.settings["scenarios_folder"]
      if self.scenario_folder[-1:] != "/": self.scenario_folder += "/"
      self.scenario_loaded = False
      self.scenarios = []

    def main(self):
      """
      Main function
      """
      try:
        #TODO: remove and just run as HET
        self.emulator = Emulator(args.daemon)
        self.emulation_data = self.fetch_data()
        self.daemon = Daemon(self.emulator, self.emulation_data, self.daemon_callback)
        if args.daemon == True:
          self.emulator.set_daemon_socket(self.daemon.get_socket())
          logging.info("Starting MACE daemon")
          self.daemon.start()
        else:
          if args.scenario != None:
            self.socket_thread = threading.Thread(target=self.daemon.start, args=())
            self.socket_thread.start()
            self.emulator.set_daemon_socket(self.daemon.get_socket())
            self._setup_emulation(self.emulator, args.scenario)
            self._start(self.emulator, 1)
          else:
            logging.error("Not running in daemon mode and no scenario was passed as argument. Exiting.")
        self._shutdown()
      except KeyboardInterrupt:
        logging.error("Interrupted by ctrl+c")
        os._exit(1)
      except SystemExit:
        logging.info("Quiting")
        os.kill(os.getpid(), signal.SIGINT)
      except:
        logging.error("General error!")
        traceback.print_exc()

    def load_scenarios(self):
      files = []
      for (dirpath, dirnames, filenames) in os.walk(self.scenario_folder):
        files.extend(filenames)
        break
      self.scenarios = files

    def daemon_callback(self, command):
      if command[0] == "911":
        logging.info("Got a shutdown from HMI. Exiting.")
      elif command[0] == "RUN":
        if self.scenario_loaded == True:
          logging.info("Running simulation")
          #run this on a thread so that it can continue
          self.emulation_thread = threading.Thread(target=self._start, args=(self.emulator, 1, self.daemon))
          self.emulation_thread.start()
          self.emulation_thread.join()
          #self._start(self.emulator, 1)
        else:
          return [-1]
      elif command[0] == "LOAD":
        scenario = str(command[1])
        scenario = self.scenario_folder + scenario
        logging.info("Loading simulation scenario: " + scenario)
        self._setup_emulation(self.emulator, scenario)
      elif command[0] == "DATA":
        self.emulation_data = self.fetch_data()
        return [1, self.emulation_data]
      return [0]

    def fetch_data(self):
      self.load_scenarios()
      data = {}
      data["scenarios"] = self.scenarios
      return data

    def _setup_emulation(self, emulator, scenario):
      """ 
      Method for reading the docker settings

      Parameters
      ----------

      Returns
      --------
      runner - The runner that will be used


      """
      emulation_file = open(scenario,"r").read()
      emulation_config = json.loads(emulation_file)
      emulator.setup(emulation_config)
      #TODO wait answer
      self.scenario_loaded = True

    def _start(self, emulator, repetitions, daemon=None):
      """ 
      Method for starting the session

      Parameters
      ----------

      Returns
      --------

      """
      logging.info("Starting ...")
      for i in range(0,int(repetitions)):
        emulator.start()

    def _shutdown(self):
      """ 
      Method for shuting down the session

      Parameters
      ----------

      Returns
      --------

      """
      logging.info("Exiting.")
      pid = os.popen("ps aux  |grep \"pymace.py\" | grep -v \"grep\" | awk '{print $2}'").readlines()
      for p in pid:
        os.system("sudo kill -s 9 " + str(p))

    def _clean(self):
      """ 
      Method for cleaning up

      Parameters
      ----------

      Returns
      --------

      """
      confirm = input("This will erase all old reports. Proceed? [y/N]")
      if confirm.upper() == "Y":
        logging.info("Cleaning all reports in reports folder.")
        try:
          shutil.rmtree("./reports")
          os.mkdir("reports")
          print(username)
          os.system("chown -R " + username + ":" + username + " ./reports")
        except:
          #traceback.print_exc()
          logging.error("Could not clean or recreate report folder")
          return
        logging.info("Done.")
      else:
        logging.info("Skiped.")

    def _new(self, application):
      """Method for creating a new application

      Args:
          application (_type_): _description_
      """
      if application == None:
        logging.error("Application name required.")
      else:
        logging.info("Scafolding: " + str(application))
        try:
          os.mkdir("./classes/apps/" + str(application))
        except FileExistsError:
          logging.error("Another application with same name already exists.")
          pass
        except:
          traceback.print_exc()
        
        try:
          files = []
          for (dirpath, dirnames, filenames) in os.walk(localdir + "/scaffold/"):
            files.extend(filenames)
          for file in files:
            shutil.copyfile(localdir +"/scaffold/" + file,localdir + "/classes/apps/" + str(application) + "/" + file)
          shutil.move(localdir + "/classes/apps/" + str(application) + "/app.py", localdir + "/classes/apps/" + str(application) + "/" + str(application) + ".py")
        except:
          traceback.print_exc()

def print_header():
  """ 
  Method for printing application main header

  Parameters
  ----------

  Returns
  --------

  """
  print("pymace v." + __version__)
  print("Framework for testing distributed algorithms in dynamic networks")
  print()

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
  parser.add_argument("-c", "--command", help="Main command to execute: run a configured emulation or create a new application.", choices=['new', 'clean'])
  parser.add_argument("name", help="New application name", nargs='?')
  parser.add_argument("-s", "--scenario", help="Scenario name", type=str)
  parser.add_argument("-d", "--daemon", help="Daemon mode", default=False, action='store_true')
  parser.add_argument("-l", "--log", help="Log level", choices=['debug', 'info', 'warning', 'error', 'critical'],default="info")
  return parser.parse_args()

def set_logging():
  """ 
  Method for setting the logging levels

  Parameters
  ----------

  Returns
  --------

  """
  logging.basicConfig(filename='pymace.log',level=args.log.upper(), format='%(asctime)s -> [%(levelname)s] %(message)s')
  logging.Formatter('%(asctime)s -> [%(levelname)s] %(message)s')
  logger = logging.getLogger()
  logger.setLevel(logging.INFO)
  logging.basicConfig(level='INFO')
  #logging.info("Starting")

def check_root():
  """ 
  Method for checking if running as root. Exits if not

  Parameters
  ----------

  Returns
  --------

  """
  if os.geteuid() != 0:
    logging.error("This must be run as root or with sudo.")
    sys.exit(1)

def load_settings():
  """Method for laoding the main settings from a file

  Returns:
      object: Returns a settings object
  """
  main_settings_file = open("settings.json","r").read()
  main_settings = json.loads(main_settings_file)
  return main_settings

if __name__ == '__main__':  
  try:
    #Print app header
    print_header()
    #Parse arguments
    args = parse_args()
    directory = os.getcwd()
    #Setup logging
    set_logging()
    #Check if running as root
    check_root()
    #Load settings and get username
    main_settings = load_settings()
    username = main_settings['username']
    #Get local directory
    localdir = os.path.dirname(os.path.abspath(__file__))
    #############################################################################
    mace = Mace(main_settings)
    mace.main(); #call scheduler function
    sys.exit(1)
  except KeyboardInterrupt:
    logging.error("Interrupted by ctrl+c")
    os.kill(os.getpid(), signal.SIGINT)
  except SystemExit:
    logging.info("Quiting")
    os.kill(os.getpid(), signal.SIGINT)
  except:
    traceback.print_exc()
