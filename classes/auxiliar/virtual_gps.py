import math
import os
import pickle
import socket
import struct
import threading
import traceback


GPS_V1_MAGIC = b"GPS1"
GPS_V1_RESPONSE = struct.Struct("!4sBddd")
LEGACY_LENGTH = struct.Struct("!I")
MAX_LEGACY_PAYLOAD = 64 * 1024
ACCEPT_TIMEOUT_S = 0.2
CONNECTION_TIMEOUT_S = 0.5
START_TIMEOUT_S = 2.0


class VirtualGPS:
  """
  Report the current position over a UNIX stream socket.

  The original length-prefixed pickle protocol remains available for Python
  consumers. C++ consumers can send the four-byte ``GPS1`` request and receive
  a fixed 29-byte ``!4sBddd`` response (magic, validity, x, y, z).
  """

  def __init__(self, tag, node_number):
    self.tag = tag
    self.node_number = node_number
    self.position = []
    self.state = "DISABLED"
    self.sock_path = "/tmp/" + self.tag + "_gps.sock"
    self.virtual_gps_thread = None
    self._server_socket = None
    self._position_lock = threading.Lock()
    self._lifecycle_lock = threading.Lock()
    self._stop_event = threading.Event()
    self._ready_event = threading.Event()
    self._startup_error = None
    self._setup()

  def _setup(self):
    pass

  def _update_position(self):
    pass

  @staticmethod
  def _recv_exact(conn, size):
    data = bytearray()
    while len(data) < size:
      try:
        chunk = conn.recv(size - len(data))
      except InterruptedError:
        continue
      except (OSError, socket.timeout):
        return None
      if not chunk:
        return None
      data.extend(chunk)
    return bytes(data)

  @staticmethod
  def _coerce_xyz(position):
    try:
      if isinstance(position, dict):
        x = float(position["x"])
        y = float(position["y"])
        z = float(position.get("z", 0.0))
      elif isinstance(position, (list, tuple)):
        if len(position) == 2:
          x = float(position[0])
          y = float(position[1])
          z = 0.0
        elif len(position) >= 3:
          x = float(position[0])
          y = float(position[1])
          z = float(position[2])
        else:
          return None
      else:
        return None
    except (KeyError, TypeError, ValueError, OverflowError):
      return None

    if not all(math.isfinite(value) for value in (x, y, z)):
      return None
    return x, y, z

  def _gps_v1_response(self):
    xyz = self._coerce_xyz(self.get_position())
    if xyz is None:
      return GPS_V1_RESPONSE.pack(GPS_V1_MAGIC, 0, 0.0, 0.0, 0.0)
    return GPS_V1_RESPONSE.pack(GPS_V1_MAGIC, 1, xyz[0], xyz[1], xyz[2])

  def interface_callback(self, data):
    data = pickle.loads(data)
    try:
      if data[0] == self.tag:
        if data[1].upper() == 'GET_POSITION':
          return self.get_position()
    except Exception:
      traceback.print_exc()
    return None

  def start(self):
    with self._lifecycle_lock:
      if self.virtual_gps_thread is not None and self.virtual_gps_thread.is_alive():
        thread = self.virtual_gps_thread
      else:
        self.state = 'ENABLED'
        self._stop_event.clear()
        self._ready_event.clear()
        self._startup_error = None
        self.virtual_gps_thread = threading.Thread(
          target=self._interface_listener,
          name="virtual-gps-" + str(self.tag),
        )
        thread = self.virtual_gps_thread
        thread.start()

    # Returning only after listen() closes the bind/listen race for callers.
    # Do not hold _lifecycle_lock here: the listener takes it while publishing
    # the server socket and startup status.
    if not self._ready_event.wait(START_TIMEOUT_S):
      self._stop()
      if thread is not threading.current_thread():
        thread.join(timeout=START_TIMEOUT_S)
      raise TimeoutError(
        "VirtualGPS listener did not become ready: " + self.sock_path
      )
    with self._lifecycle_lock:
      startup_error = self._startup_error
      server_ready = self._server_socket is not None
    if startup_error is not None:
      raise startup_error
    if not server_ready:
      raise RuntimeError(
        "VirtualGPS listener stopped before becoming ready: " + self.sock_path
      )

  def _stop(self):
    with self._lifecycle_lock:
      self.state = "DISABLED"
      self._stop_event.set()
      gps_socket = self._server_socket
    if gps_socket is not None:
      try:
        gps_socket.shutdown(socket.SHUT_RDWR)
      except OSError:
        pass
      try:
        gps_socket.close()
      except OSError:
        pass

  def shutdown(self):
    self._stop()
    thread = self.virtual_gps_thread
    if thread is not None and thread is not threading.current_thread():
      thread.join(timeout=2)

  def _handle_connection(self, conn):
    prefix = self._recv_exact(conn, 4)
    if prefix is None:
      return

    if prefix == GPS_V1_MAGIC:
      try:
        conn.sendall(self._gps_v1_response())
      except OSError:
        pass
      return

    length, = LEGACY_LENGTH.unpack(prefix)
    if length <= 0 or length > MAX_LEGACY_PAYLOAD:
      return
    data = self._recv_exact(conn, length)
    if data is None:
      return

    try:
      response = self.interface_callback(data)
      payload = pickle.dumps(response)
      conn.sendall(LEGACY_LENGTH.pack(len(payload)))
      conn.sendall(payload)
    except Exception:
      # A malformed legacy pickle must only fail this request, not terminate
      # the shared GPS listener.
      return

  def _interface_listener(self):
    gps_socket = None
    socket_path_claimed = False
    try:
      gps_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
      gps_socket.settimeout(ACCEPT_TIMEOUT_S)
      try:
        os.remove(self.sock_path)
      except FileNotFoundError:
        pass

      gps_socket.bind(self.sock_path)
      socket_path_claimed = True
      gps_socket.listen(1000)
      with self._lifecycle_lock:
        if self._stop_event.is_set():
          return
        self._server_socket = gps_socket
      self._ready_event.set()

      while not self._stop_event.is_set():
        try:
          conn, _ = gps_socket.accept()
        except socket.timeout:
          continue
        except OSError:
          if self._stop_event.is_set():
            break
          traceback.print_exc()
          break

        try:
          conn.settimeout(CONNECTION_TIMEOUT_S)
          self._handle_connection(conn)
        except OSError:
          pass
        finally:
          try:
            conn.close()
          except OSError:
            pass
    except OSError as error:
      with self._lifecycle_lock:
        startup_failed = not self._ready_event.is_set()
        if startup_failed:
          self._startup_error = error
      if not self._stop_event.is_set() and not startup_failed:
        traceback.print_exc()
    finally:
      # Wake start() on every early-return/error path.  On successful startup
      # the event was already set immediately after listen().
      self._ready_event.set()
      with self._lifecycle_lock:
        if gps_socket is not None and self._server_socket is gps_socket:
          self._server_socket = None
        self.state = "DISABLED"
      if gps_socket is not None:
        try:
          gps_socket.close()
        except OSError:
          pass
      if socket_path_claimed:
        try:
          os.remove(self.sock_path)
        except FileNotFoundError:
          pass
        except OSError:
          if not self._stop_event.is_set():
            traceback.print_exc()

  def emmit_to_gps_socket(self, data):
    gps = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    gps.settimeout(1)
    try:
      gps.connect(self.sock_path)
      payload = pickle.dumps(data)
      gps.sendall(LEGACY_LENGTH.pack(len(payload)))
      gps.sendall(payload)
    finally:
      gps.close()

  def get_position(self):
    with self._position_lock:
      if isinstance(self.position, list):
        return list(self.position)
      if isinstance(self.position, tuple):
        return tuple(self.position)
      if isinstance(self.position, dict):
        return dict(self.position)
      return self.position

  def set_position(self, pos):
    with self._position_lock:
      if isinstance(pos, list):
        self.position = list(pos)
      elif isinstance(pos, tuple):
        self.position = tuple(pos)
      elif isinstance(pos, dict):
        self.position = dict(pos)
      else:
        self.position = pos
