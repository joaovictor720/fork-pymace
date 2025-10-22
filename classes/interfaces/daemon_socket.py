#!/usr/bin/env python3

__author__ = "Bruno Chianca Ferreira"
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Bruno Chianca Ferreira"
__email__ = "brunobcf@gmail.com"


from flask_socketio import SocketIO, emit

import flask, json, traceback, logging
from flask import request

class Daemon(flask.Flask):
    """_summary_

    Args:
        flask (_type_): _description_
    """
    def __init__(self, pymace, initial_data, callback):
        """_summary_

        Args:
            pymace (_type_): _description_
        Returns:
            _type_: _description_
        """
        self.app = flask.Flask(__name__)
        self.emulator = pymace
        self.socketio = SocketIO(self.app, cors_allowed_origins="*", engineio_logger=False, logger=False)
        log1 = logging.getLogger('werkzeug')
        log1.disabled = True
        log2 = logging.getLogger('socketio')
        log2.disabled = True
        log3 = logging.getLogger('engineio')
        log3.disabled = True
        self.lock = True
        self.emulation_data = initial_data
        self.emulation_callback = callback

        @self.app.route("/")
        def info():
          """_summary_

          Returns:
              _type_: _description_
          """
          data = json.dumps({'Hello':'world'})
          response = self.app.response_class(
              response=data,
              status=200,
              mimetype='application/json'
          )
          header = response.headers
          header['Access-Control-Allow-Origin'] = '*'
          return response

        @self.app.route("/shutdown")
        def stop():
          """_summary_

          Returns:
              _type_: _description_
          """
          try:
              self.shutdown()

          except:
              traceback.print_exc()
          response = self.app.response_class(
              response='OK',
              status=200,
              mimetype='application/json'
          )
          return response

        @self.app.route("/emulator/run")
        def start_sim():
          """_summary_

          Returns:
              _type_: _description_
          """
          try:
              self.emulator.running = True
              command = ["RUN"]
              r = self.emulation_callback(command)
              if r >= 0:
                response = self.app.response_class(
                    response='OK',
                    status=200,
                    mimetype='application/json'
                )
              else:
                response = self.app.response_class(
                  response ='Not configured',
                  status = 403,
                  mimetype='application/json'
                )
          except:
              traceback.print_exc()
              response = self.app.response_class(
                response ='ERROR',
                status = 403,
                mimetype='application/json'
              )
          return response

        @self.app.route("/emulator/load", methods = ['POST'])
        def load_sim():
          """_summary_

          Returns:
              _type_: _description_
          """
          try:
              if request.method == 'POST':
                data = request.get_json(force=True) # a multidict containing POST data
                scenario = data['scenario']
                command = ["LOAD", scenario]
                self.emulation_callback(command)
                response = self.app.response_class(
                  response='OK',
                  status=200,
                  mimetype='application/json'
                )
              else:
                response = self.app.response_class(
                  response='Only POST allowed',
                  status=405,
                  mimetype='application/json'
                )
          except:
              traceback.print_exc()
              response = self.app.response_class(
                response='ERROR',
                status=400,
                mimetype='application/json'
              )
          return response

        @self.socketio.on('connect', namespace='/sim')
        def test_connect():
          """_summary_
          """
          emit('my response', {'data': 'Connected'})

        @self.socketio.on('pingServer', namespace='/sim')
        def ping():
          """_summary_
          """
          emit('pong')

        @self.socketio.on('request_data', namespace='/sim')
        def request_data():
          """_summary_
          """
          command = ["DATA"]
          r = self.emulation_callback(command)
          #print(r)
          if r[0] == 1:
            emit('emulation_data', {'data': r[1]})

        @self.socketio.on('load', namespace='/sim')
        def load_scen(arg):
          """_summary_
          """
          scenario = arg["scenario"]
          command = ["LOAD", scenario]
          self.emulation_callback(command)
          emit('response', {'message': 'Loaded'})

        @self.socketio.on('shutdown', namespace='/sim')
        def shutdown():
          """_summary_
          """
          self.shutdown()
          emit('response', {'message': 'Shutdown'})

        @self.socketio.on('run', namespace='/sim')
        def run_emulation():
          """_summary_
          """
          command = ["RUN"]
          r = self.emulation_callback(command)
          emit('response', {'message': 'Started'})

        @self.socketio.on('disconnect', namespace='/sim')
        def test_disconnect():
          """_summary_
          """
          print('Client disconnected')


    
    def get_socket(self):
      return self.socketio

    def shutdown(self):
      """_summary_
      """
      self.emulator.running = False
      self.socketio.stop()
      self.lock=False
      command = ["911"]
      self.emulation_callback(command)

    def start(self):
      self.socketio.run(self.app, debug=False, port=5000)




