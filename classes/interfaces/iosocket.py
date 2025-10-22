#!/usr/bin/env python3

__author__ = "Bruno Chianca Ferreira"
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Bruno Chianca Ferreira"
__email__ = "brunobcf@gmail.com"


from flask_socketio import SocketIO, emit

import flask, json, traceback, threading, time, logging

class Socket(flask.Flask):
    """_summary_

    Args:
        flask (_type_): _description_
    """
    def __init__(self, corenodes, wlans, session, modelname, digest, semaphore, pymace, callback, networks, macenodes):
        """_summary_

        Args:
            corenodes (_type_): _description_
            wlans (_type_): _description_
            session (_type_): _description_
            modelname (_type_): _description_
            digest (_type_): _description_
            semaphore (_type_): _description_
            pymace (_type_): _description_
            callback (function): _description_
            networks (_type_): _description_
            macenodes (_type_): _description_

        Returns:
            _type_: _description_
        """
        app = flask.Flask(__name__)
        self.Pymace = pymace
        self.socketio = SocketIO(app, cors_allowed_origins="*", engineio_logger=False, logger=False)
        log1 = logging.getLogger('werkzeug')
        log1.disabled = True
        log2 = logging.getLogger('socketio')
        log2.disabled = True
        log3 = logging.getLogger('engineio')
        log3.disabled = True
        self.digest = digest
        self.semaphore = semaphore
        self.lock = True
        self.list_nodes = []
        self.corenodes = corenodes
        self.macenodes = macenodes
        self.initial_nodes = {}
        self.wlans = wlans
        self.session = session
        self.modelname = modelname
        self.pymace_callback = callback
        self.networks = networks
        self.network_time_div = 10
        self.counters = {}
        self.counters['networks'] = 0

        for node in self.corenodes:
            self.initial_nodes[node.id] = node.getposition()
        @app.route("/")
        def info():
            """_summary_

            Returns:
                _type_: _description_
            """
            data = json.dumps({'Hello':'world'})
            response = app.response_class(
                response=data,
                status=200,
                mimetype='application/json'
            )
            header = response.headers
            header['Access-Control-Allow-Origin'] = '*'
            return response

        @app.route("/sim/stop")
        def stop():
            """_summary_

            Returns:
                _type_: _description_
            """
            print('Shutting down web server')
            try:
                self.Pymace.running = False
                self.socketio.stop()
            except:
                pass
            response = app.response_class(
                response='OK',
                status=200,
                mimetype='application/json'
            )
            return response
        
        @self.socketio.on('update_pos', namespace='/sim')
        def handle_message(updated_node):
            """_summary_

            Args:
                updated_node (_type_): _description_
            """
            for node in self.corenodes:
                if int(node.id) - 1 == int(updated_node['node']['id']):
                    #print(node.id)
                    #print(updated_node['node']['id'])
                    node.setposition(int(updated_node['node']['x']),int(updated_node['node']['y']))
            #print('received message: ' + str(node))

        @self.socketio.on('reset_pos', namespace='/sim')
        def handle_message():
            """_summary_
            """
            for node in self.corenodes:
                node.setposition(self.initial_nodes[node.id][0],self.initial_nodes[node.id][1])

        @self.socketio.on('pingServer', namespace='/sim')
        def handle_my_custom_event(arg1, arg2):
            """_summary_

            Args:
                arg1 (_type_): _description_
                arg2 (_type_): _description_
            """
            print('received json: ' + str(arg1) + " " + str(arg2))
            emit('ponggg')

        @self.socketio.on('connect', namespace='/sim')
        def test_connect():
            """_summary_
            """
            emit('my response', {'data': 'Connected'})
            #emit('nodes', {'nodes': self.list_nodes})

        @self.socketio.on('disconnect', namespace='/sim')
        def test_disconnect():
            """_summary_
            """
            print('Client disconnected')

        
        self.nthread = threading.Thread(target=self.nodes_thread, args=())
        self.dthread = threading.Thread(target=self.emmit_digest, args=())
        self.netthread = threading.Thread(target=self.network_thread, args=())
        
        self.nthread.start()
        self.dthread.start()
        self.netthread.start()
        
        self.socketio.run(app, debug=False)
        
        self.shutdown()

    def shutdown(self):
        """_summary_
        """
        #self.socketio.stop(namespace='/sim')
        self.lock=False
        self.nthread.join()
        self.dthread.join()
        self.netthread.join()


    def emmit_digest(self):
        """_summary_
        """
        while self.lock:
            if self.Pymace.iosocket_semaphore == True:
                print('IOSOCKET -> semaphore')
                self.socketio.emit('digest', {'data': self.Pymace.nodes_digest}, namespace='/sim')
                self.Pymace.iosocket_semaphore = False
            time.sleep(0.5)

    ###TODO: Separate nodes from wlan and for now on, different threads with different rate

    def network_thread(self):
        """_summary_
        """
        while self.lock:
            self.socketio.emit('networks', {'data': self.networks}, namespace='/sim')
            time.sleep(1)

    def nodes_thread(self):
        """_summary_
        """
        data = {}
        data['nodes'] = []
        _wlans = []

        while self.lock:
            data['nodes'].clear()
            for node in self.macenodes:
                nodedata = {}
                nodedata['position'] = node.corenode.getposition()
                nodedata['id'] = node.corenode.id
                nodedata['networks'] = node.network
                nodedata['range'] = 250
                data['nodes'].append(nodedata)
            self.socketio.emit('nodes', {'data': data}, namespace='/sim')

            try:
                if self.counters['networks'] == 0:
                    _wlans.clear()
                    for wlan, core_obj in self.wlans.items():
                        network = {}
                        network['id'] = wlan
                        network['model'] = self.modelname
                        network['range'] = self.session.mobility.get_model_config(core_obj.id, self.modelname)['range']
                        network['bandwidth'] = self.session.mobility.get_model_config(core_obj.id, self.modelname)['bandwidth']
                        network['jitter'] = self.session.mobility.get_model_config(core_obj.id, self.modelname)['jitter']
                        network['delay'] = self.session.mobility.get_model_config(core_obj.id, self.modelname)['delay']
                        network['error'] = self.session.mobility.get_model_config(core_obj.id, self.modelname)['error']
                        _wlans.append(network)
                    self.socketio.emit('wlans', {'data': _wlans}, namespace='/sim')
                    #print(self.session.mobility.get_model_config(core_obj.id, self.modelname)['range'])
            except:
                pass
            for key in self.counters.keys():
                self.counters[key] += 1
            if self.counters['networks'] > self.network_time_div:
                self.counters['networks'] = 0
            time.sleep(0.1)

    def add_node(self, node):
        """_summary_

        Args:
            node (_type_): _description_
        """
        self.corenodes.append(node)

