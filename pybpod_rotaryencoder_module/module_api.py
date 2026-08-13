import time

from serial.tools import list_ports

from pybpodapi.com.arcom import ArCOM, ArduinoTypes

# Many Arduino-class boards (this one included) reset when a new serial connection is opened
# (DTR toggling), and need a moment to reboot and start responding before the handshake works.
BOOT_DELAY_S = 2.0
HANDSHAKE_RETRIES = 3


class RotaryEncoderModule(object):

    COM_HANDSHAKE        = 'C'
    COM_TOGGLEEVTTRANSM  = ord('V')
    COM_TOGGLEOUTPUTSTREAM = ord('O')
    COM_TOGGLESTREAM     = ord('S')
    COM_STARTLOGGING     = ord('L')
    COM_STOPLOGGING      = ord('F')
    COM_GETLOGDATA       = ord('R')
    COM_GETCURRENTPOS    = ord('Q')
    COM_SETZEROPOS       = ord('Z')
    COM_SETPOS           = ord('P')
    COM_ENABLETHRESHOLDS = ord(';')
    COM_SETPREFIX        = ord('I')
    COM_SETTHRESHOLDS    = ord('T')
    COM_SETWRAPPOINT     = ord('W')
    COM_ENABLE_ALLTHRESHOLDS = ord('E')

    def __init__(self, serialport=None):
        """
        Constructor of the RotaryEncoderModule object
        A serial connection to the Rotary Encoder board is opened at the construction of the object.

        :ivar str serialport: PC serial port where the module is connected
        """
        if serialport:
            self.open(serialport)

    def open(self, serialport):
        """
        Opens a serial connection to the Rotary Encoder board.

        :ivar str serialport: PC serial port where the module is connected
        """
        self.arcom = ArCOM().open(serialport, 115200)
        time.sleep(BOOT_DELAY_S)  # let the board finish resetting before we handshake

        for attempt in range(HANDSHAKE_RETRIES):
            self.arcom.write_char(self.COM_HANDSHAKE)
            if self.arcom.read_uint8() == 217:
                return

        raise Exception(
            "Could not connect to a Rotary Encoder module on {0} (no handshake response after "
            "{1}s boot delay + {2} retries -- check this is really the module's own USB port, "
            "not the Bpod's main state-machine port, and that nothing else has it open)".format(
                serialport, BOOT_DELAY_S, HANDSHAKE_RETRIES))

    def close(self):
        """
        Closes the serial connection to the Rotary Encoder board.
        """
        self.arcom.close()

    @classmethod
    def discover(cls, exclude_ports=(), timeout=1.0, verbose=True):
        """
        Scan available serial ports for a Rotary Encoder module by probing each with the module's
        own handshake byte, skipping any ports in exclude_ports (e.g. the Bpod state machine's
        own connection -- pass my_bpod.serial_port here so it's never probed).

        The Rotary Encoder module's own USB position/config port is a separate physical
        connection from the Bpod state machine's Serial1-5 relay -- nothing about it is reported
        over that relay, so there's no way to read it off the connected Bpod object.

        :param exclude_ports: iterable of port device names (e.g. ['COM4']) to skip probing.
        :param float timeout: per-port probe read timeout, in seconds.
        :param bool verbose: print each port tried and its result, for diagnosing a failed scan.
        :return: a connected RotaryEncoderModule on the first port that answers the handshake correctly.
        """
        candidates = [p.device for p in list_ports.comports() if p.device not in exclude_ports]
        if verbose:
            print("RotaryEncoderModule.discover: probing {0} (excluding {1})".format(
                candidates, list(exclude_ports)))

        for port in candidates:
            probe = None
            ack = None
            error = None
            try:
                probe = ArCOM().open(port, baudrate=115200, timeout=timeout)
                time.sleep(BOOT_DELAY_S)  # let the board finish resetting before we handshake
                for attempt in range(HANDSHAKE_RETRIES):
                    probe.write_char(cls.COM_HANDSHAKE)
                    ack = probe.read_uint8()
                    if ack == 217:
                        break
            except Exception as exc:
                error = exc
            finally:
                if probe is not None:
                    probe.close()

            if verbose:
                print("  {0}: {1}".format(
                    port, "error: {0}".format(error) if error else "ack={0}".format(ack)))

            if ack == 217:
                return cls(port)

        raise RuntimeError(
            "No Rotary Encoder module found on any serial port (probed {0}, excluding {1})".format(
                candidates, list(exclude_ports)))

    def __pos_2_degrees(self, pos):
        return round(((float(pos)/512.0)*180.0)*10.0)/10.0

    def __degrees_2_pos(self, degrees):
        return int(round((float(degrees)/180.0)*512.0, 0))

    def enable_evt_transmission(self):
        """
        Enables the transmission of threshold crossing events to the Bpod state machine.
        """
        self.arcom.write_array([self.COM_TOGGLEEVTTRANSM, 1])
        return self.arcom.read_uint8() == 1

    def disable_evt_transmission(self):
        """
        Disables the transmission of events.
        """
        self.arcom.write_array([self.COM_TOGGLEEVTTRANSM, 0])
        return self.arcom.read_uint8() == 1

    def enable_module_outputstream(self):
        """
        Enables the streaming of current position data directly to another Bpod module (e.g. DDS, AnalogOutput).
        """
        self.arcom.write_array([self.COM_TOGGLEOUTPUTSTREAM, 1])
        return self.arcom.read_uint8() == 1

    def disable_module_outputstream(self):
        """
        Disables the streaming of current position data directly to another Bpod module.
        """
        self.arcom.write_array([self.COM_TOGGLEOUTPUTSTREAM, 0])
        return self.arcom.read_uint8() == 1

    def enable_stream(self):
        """
        Enables the streaming of the position and the time measurements to the USB port.
        """
        self.arcom.write_array([self.COM_TOGGLESTREAM, 1])

    def disable_stream(self):
        """
        Disables the streaming of the position and the time measurements to the USB port.
        """
        self.arcom.write_array([self.COM_TOGGLESTREAM, 0])

    def read_stream(self):
        """
        Reads the data being streamed through the USB port.
        """
        res = []
        available = self.arcom.bytes_available()

        if available > 1:
            msg = self.arcom.read_bytes_array(available)

            while len(msg) >= 7:
                if msg[0] == b'P':
                    position = int.from_bytes(b''.join(msg[1:3]), byteorder='little', signed=True)
                    evt_time = float(int.from_bytes(b''.join(msg[3:7]), byteorder='little', signed=False))/1000.0
                    position_degrees = self.__pos_2_degrees(position)
                    res.append(['P', evt_time, position_degrees])
                elif msg[0] == b'E':
                    origin = msg[1]
                    event = msg[2]
                    evt_time = float(int.from_bytes(b''.join(msg[3:7]), byteorder='little', signed=False))/1000.0
                    res.append(['E', evt_time, origin, event])

                msg = msg[7:]

        return res

    def current_position(self):
        """
        Retrieves the current position.
        """
        self.arcom.write_array([self.COM_GETCURRENTPOS])
        data_in_bytes = b''.join(self.arcom.read_bytes_array(2))
        ticks = int.from_bytes(data_in_bytes, byteorder='little', signed=True)
        return self.__pos_2_degrees(ticks)

    def set_zero_position(self, tolerance_deg=1.0, retries=3):
        """
        Sets current position to zero and verifies it actually took by re-querying
        current_position() -- COM_SETZEROPOS has no acknowledgment byte, so a write that's dropped
        or misprocessed (e.g. racing another command on this connection from a different thread)
        would otherwise fail completely silently, leaving every later position/threshold check
        relative to the wrong baseline. Retries a bounded number of times before raising.
        """
        for attempt in range(retries):
            self.arcom.write_array([self.COM_SETZEROPOS])
            if abs(self.current_position()) <= tolerance_deg:
                return
        raise Exception(
            "set_zero_position() did not take effect after {0} attempts (still reading {1}deg "
            "from zero) -- check for concurrent access to this connection from another "
            "thread".format(retries, self.current_position()))

    def set_position(self, degrees):
        """
        Sets the current position in degrees.

        :ivar int degrees: current position in degrees.
        """
        ticks = self.__degrees_2_pos(degrees)
        data = ArduinoTypes.get_uint8_array([self.COM_SETPOS])
        data += ticks.to_bytes(2, byteorder='little', signed=True)

        self.arcom.write_array(data)
        return self.arcom.read_uint8() == 1

    def enable_thresholds(self, thresholds):
        """
        Enables the thresholds.

        :ivar list(boolean) thresholds: list of 6 booleans indicating which thresholds are active to trigger events.
        """
        if len(thresholds) != 8:
            raise Exception('Thresholds array has to be of length 8')

        string = ''.join(map(lambda x: str(int(x)), thresholds))
        bits = int(string, 2)
        self.arcom.write_array([self.COM_ENABLETHRESHOLDS, bits])

    def enable_all_thresholds(self):
        """
        Enables all 8 thresholds unconditionally -- same firmware command as the Bpod-relay
        RotaryEncoder class's create_resetpositions_trigger() (module.py's
        COM_ENABLE_ALLTHRESHOLDS), issued directly over USB instead of via a relay output_action.
        Prefer this over enable_thresholds() when only a subset is meant to be armed: building the
        bitmask by joining the list in index order and passing it to int(string, 2) puts list index
        0 at the most-significant bit, not bit 0 -- so a selective list like
        [True, True, True, True, False, False, False, False] likely arms slots 4-7, not 0-3, under
        the standard bit-N-selects-threshold-N firmware convention. This command has no such
        ambiguity since it just arms everything.
        """
        self.arcom.write_array([self.COM_ENABLE_ALLTHRESHOLDS])

    def enable_logging(self):
        """
        Enables the logging to the SD Card.
        """
        self.arcom.write_array([self.COM_STARTLOGGING])

    def disable_logging(self):
        """
        Disables the logging to the SD Card.
        """
        self.arcom.write_array([self.COM_STOPLOGGING])

    def get_logged_data(self):
        """
        Retrieves the logged data in the SD Card.
        """
        self.arcom.write_array([self.COM_GETLOGDATA])
        msg = self.arcom.read_bytes_array(4)
        n_logs = int.from_bytes(b''.join(msg), byteorder='little', signed=False)
        data = []

        for i in range(0, n_logs):
            msg = self.arcom.read_bytes_array(8)
            data_in_bytes = b''.join(msg)

            position = int.from_bytes(data_in_bytes[:4], byteorder='little', signed=True)
            evt_time = float(int.from_bytes(data_in_bytes[4:], byteorder='little', signed=False))/1000.0
            position_degrees = self.__pos_2_degrees(position)
            data.append((evt_time, position_degrees))

        return data

    def set_prefix(self, prefix):
        """
        Sets 1-character prefix for module output stream.

        :ivar char prefix: One character to be used as prefix.
        """
        self.arcom.write_array([self.COM_SETPREFIX, prefix])
        return self.arcom.read_uint8() == 1

    def set_thresholds(self, thresholds, retries=3):
        """
        Sets the thresholds values to trigger events, retrying a bounded number of times if the
        board doesn't acknowledge -- a wheel task may call this every trial (not just once at
        startup) to re-center thresholds on each trial's fresh zero, so a silently dropped write
        here means a whole trial's Hold/choice detection runs against stale bounds (see
        set_zero_position()).

        :ivar list(int) thresholds: List, in maximum, of 6 thresholds to trigger events.
        """
        data = ArduinoTypes.get_uint8_array([self.COM_SETTHRESHOLDS, len(thresholds)])
        data += ArduinoTypes.get_uint16_array([self.__degrees_2_pos(thresh) for thresh in thresholds])
        for attempt in range(retries):
            self.arcom.write_array(data)
            if self.arcom.read_uint8() == 1:
                return True
        raise Exception(
            "set_thresholds({0}) was not acknowledged after {1} attempts".format(
                thresholds, retries))

    def set_wrappoint(self, wrap_point):
        """
        Sets wrap point (number of tics in a half-rotation)

        :ivar int wrap_point: number of tics in a half-rotation.
        """
        ticks = self.__degrees_2_pos(wrap_point)
        self.arcom.write_array([self.COM_SETWRAPPOINT] + ArduinoTypes.get_uint16_array([ticks]))
        return self.arcom.read_uint8() == 1


if __name__ == '__main__':
    import time

    m = RotaryEncoderModule('/dev/ttyACM0')

    m.enable_logging()
    time.sleep(5)
    m.disable_logging()

    print(m.get_logged_data())

    """
    m.enable_stream()



    count = 0
    while count<100 or True:
        data = m.read_stream()
        if len(data)==0: continue

        print(data)
        count += 1

    m.disable_stream()

    print('set', m.set_position(179))
    m.set_zero_position()

    m.enable_thresholds([True, False, True, True, False, False, True, True])
    print(m.current_position())
    print(m.get_logged_data())
    """
    m.close()
