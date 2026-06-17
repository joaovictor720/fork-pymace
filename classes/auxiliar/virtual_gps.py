import threading, pickle, socket, os, traceback, struct

class VirtualGPS:
  """
  Class that reports current position to a UNIX socket so that other applications can access nodes position
  """
  def __init__(self, tag, node_number):
    self.tag = tag
    self.position = []
    self.socket_path = "/tmp/" + self.tag + "_gps.sock"
    self.virtual_gps_thread = None
    self._setup()

  def _setup(self):
    pass

  def _update_position(self):
    pass

  def interface_callback(self, data):
    data = pickle.loads(data)
    #print(data)
    try:
      if data[0] == self.tag:
        if data[1].upper() == 'GET_POSITION':
          pos = self.get_position()
          #print(pos)
          return pos
    except:
      traceback.print_exc()
      pass

  def start(self):
    self.state = 'ENABLED'
    self.virtual_gps_thread = threading.Thread(target=self._interface_listener, args=(), daemon=True)
    self.virtual_gps_thread.start()

  def _stop(self):
    self.state = "DISABLED"

  def shutdown(self):
    self._stop()
    try:
      wakeup = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
      wakeup.settimeout(0.2)
      wakeup.connect(self.socket_path)
      wakeup.close()
    except:
      pass
    if self.virtual_gps_thread is not None and self.virtual_gps_thread.is_alive():
      self.virtual_gps_thread.join(timeout = 2)
    try:
      os.remove(self.socket_path)
    except OSError:
      pass

  def _interface_listener(self):
    #this section is a synchronizer so that all nodes can start ROUGHLY at the same time
    gps_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    gps_socket.settimeout(1.0)
    try:
      os.remove(self.socket_path)
    except OSError:
      #traceback.print_exc()
      pass
    try:
      gps_socket.bind(self.socket_path)
      gps_socket.listen(1000) #backlog
    except OSError:
      traceback.print_exc()
    except:
      traceback.print_exc()
    try:
      while self.state != "DISABLED":
        conn = None
        try:
          conn, addr = gps_socket.accept()
        except socket.timeout:
          continue
        except OSError:
          if self.state == "DISABLED":
            break
          traceback.print_exc()
          continue

        try:
          lengthbuf = conn.recv(4)
          if not lengthbuf:
            continue

          length, = struct.unpack('!I', lengthbuf)
          data = b''
          while length:
            newbuf = conn.recv(length)
            if not newbuf:
              data = None
              break
            data += newbuf
            length -= len(newbuf)

          if data is None:
            continue

          response = self.interface_callback(data)
          payload = pickle.dumps(response)
          payload_len = len(payload)
          conn.sendall(struct.pack('!I', payload_len))
          conn.sendall(payload)
        except BrokenPipeError:
          pass
        finally:
          if conn is not None:
            try:
              conn.close()
            except OSError:
              pass
    finally:
      gps_socket.close()
      try:
        os.remove(self.socket_path)
      except OSError:
        pass

  def emmit_to_gps_socket(self, data):
    gps = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    gps.settimeout(1)
    gps.connect(self.socket_path)
    payload = pickle.dumps(data)
    length = len(payload)
    gps.sendall(struct.pack('!I', length))
    gps.sendall(payload)
    gps.close()

  def get_position(self):
    return self.position

  def set_position(self, pos):
    self.position = pos
    
