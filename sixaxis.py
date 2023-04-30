from asyncore import file_dispatcher, loop
from threading import Thread
from evdev import InputDevice, list_devices, ecodes


class SixAxisResource:
    """
    Resource class which will automatically connect and disconnect to and from a joystick, creating a new SixAxis
    object and passing it to the 'with' clause. Also binds a handler to the START button which resets the axis
    calibration, and to the SELECT button which centres the analogue sticks on the current position.
    """

    def __init__(self, bind_defaults=False, dead_zone=0.05, hot_zone=0.0, invert_axes=None):
        """
        Resource class, produces a :class:`triangula.input.SixAxis` for use in a 'with' binding.

        :param float dead_zone:
            See SixAxis class documentation
        :param float hot_zone:
            See SixAxis class documentation
        :param bind_defaults:
            Defaults to False, if True will automatically bind two actions to the START and SELECT buttons to
            reset the axis calibration and to set the axis centres respectively.
        """
        self.bind_defaults = bind_defaults
        self.dead_zone = dead_zone
        self.hot_zone = hot_zone
        self.invert_axes = invert_axes

    def __enter__(self):
        self.joystick = SixAxis(dead_zone=self.dead_zone, hot_zone=self.hot_zone, invert_axes=self.invert_axes)
        self.joystick.connect()
        if self.bind_defaults:
            self.joystick.register_button_handler(self.joystick.reset_axis_calibration, SixAxis.BUTTON_START)
            self.joystick.register_button_handler(self.joystick.set_axis_centres, SixAxis.BUTTON_SELECT)
        return self.joystick

    def __exit__(self, exc_type, exc_value, traceback):
        self.joystick.disconnect()


class SixAxis:
    """
    Class to handle the PS3 SixAxis controller

    This class will process events from the evdev event queue and calculate positions for each of the analogue axes on
    the SixAxis controller (motion sensing is not currently supported). It will also extract
    button press events and call any handler functions bound to those buttons.

    Once the connect() call is made, a thread is created which will actively monitor the device for events, passing them
    to the SixAxis class for processing. There is no need to poll the event queue manually.

    Consuming code can get the current position of any of the sticks from this class through the `axes` instance
    property. This contains a list of :class:`SixAxis.Axis` objects, one for each distinct axis on the
    controller. The list of axes is, in order: left x, left y, right x, right y.
    """

    BUTTON_SELECT = 0       #: The Select button
    BUTTON_LEFT_STICK = 1   #: Left stick click button
    BUTTON_RIGHT_STICK = 2  #: Right stick click button
    BUTTON_START = 3        #: Start button
    BUTTON_D_UP = 4         #: D-pad up
    BUTTON_D_RIGHT = 5      #: D-pad right
    BUTTON_D_DOWN = 6       #: D-pad down
    BUTTON_D_LEFT = 7       #: D-pad left
    BUTTON_L2 = 8           #: L2 lower shoulder trigger
    BUTTON_R2 = 9           #: R2 lower shoulder trigger
    BUTTON_L1 = 10          #: L1 upper shoulder trigger
    BUTTON_R1 = 11          #: R1 upper shoulder trigger
    BUTTON_TRIANGLE = 12    #: Triangle
    BUTTON_CIRCLE = 13      #: Circle
    BUTTON_CROSS = 14       #: Cross
    BUTTON_SQUARE = 15      #: Square
    BUTTON_PS = 16          #: PS button

    def __init__(self, dead_zone=0.05, hot_zone=0.0, connect=False, invert_axes=[False, False, False, False]):
        """
        Discover and initialise a PS3 SixAxis controller connected to this computer.

        :param float dead_zone:
            Creates a dead zone centred on the centre position of the axis (which may or may not be zero depending on
            calibration). The axis values range from 0 to 1.0, but will be locked to 0.0 when the measured value less
            centre offset is lower in magnitude than this supplied value. Defaults to 0.05, which makes the PS3 analogue
            sticks easy to centre but still responsive to motion. The deadzone is applies to each axis independently, so
            e.g. moving the stick far right won't affect the deadzone for that sticks Y axis.
        :param float hot_zone:
            Creates a zone of maximum value, any readings from the sensor which are within this value of the max or min
            values will be mapped to 1.0 and -1.0 respectively. This can be useful because, while the PS3 controllers
            sticks have independent axes, they are constrained to move within a circle, so it's impossible to have e.g.
            1.0,1.0 for both x and y axes. Setting this value to non-zero in effect partially squares the circle,
            allowing for controls which require full range control. Setting this value to 1/sqrt(2) will create a square
            zone of variability within the circular range of motion of the controller, with any stick motions outside
            this square mapping to the maximum value for the respective axis. The value is actually scaled by the max
            and min magnitude for upper and lower ranges respectively, so e.g. setting 0.5 will create a hot-zone at
            above half the maximum value and below half the minimum value, and not at +0.5 and -0.5 (unless max and
            min are 1.0 and -1.0 respectively). As with the dead zone, these are applied separately to each axis, so in
            the case where the hot zone is set to 1/sqrt(2), a circular motion of the stick will map to x and y values
            which trace the outline of a square of unit size, allowing for all values to be emitted from the stick.
        :param connect:
            If true, call connect(), otherwise you need to call it elsewhere. Note that connect() may raise IOError if
            it can't find a PS3 controller, so it's best to call it explicitly yourself and handle the error. Defaults
            to False.
        :param invert_axes:
            List of 4 boolean values to set wheter an axis should be inverted or not.
            Axes are in the following order: left X, left Y, right X, right Y
            No axis is inverted by default.
        :return: an initialised link to an attached PS3 SixAxis controller.
        """

        assert len(invert_axes) == 4, 'Wrong axes count, must be 4'
        self._stop_function = None
        self.axes = [
            SixAxis.Axis(ax, dead_zone=dead_zone, hot_zone=hot_zone, invert=invert_axes[i])
            for i, ax in enumerate(['left_x', 'left_y', 'right_x', 'right_y'])
        ]
        self.button_handlers = []
        self.buttons_pressed = 0
        if connect:
            self.connect()

    def is_connected(self):
        """
        Check whether we have a connection

        :return:
            True if we're connected to a controller, False otherwise.
        """
        if self._stop_function:
            return True
        else:
            return False

    def get_and_clear_button_press_history(self):
        """
        Return the button press bitfield, clearing it as we do.

        :return:
            A bit-field where bits are set to 1 if the corresponding button has been pressed since the last call to
            this method. Test with e.g. 'if button_press_field & SixAxis.BUTTON_CIRCLE:...'
        """
        old_buttons = self.buttons_pressed
        self.buttons_pressed = 0
        return old_buttons

    def _start_device_read_loop(self, device):
        parent = self
        
        class InputDeviceDispatcher(file_dispatcher):
            def __init__(self):
                self.device = device
                file_dispatcher.__init__(self, device)

            def recv(self, ign=None):
                return self.device.read()

            def handle_read(self):
                for event in self.recv():
                    parent.handle_event(event)

            def handle_error(self):
                pass

        class AsyncLoopThread(Thread):
            def __init__(self, channel):
                Thread.__init__(self, name='InputDispatchThread')
                self.daemon = True
                self.channel = channel

            def run(self):
                loop()

            def stop(self):
                self.channel.close()

        loop_thread = AsyncLoopThread(InputDeviceDispatcher())
        self._stop_function = loop_thread.stop
        loop_thread.start()

    def connect(self, dev=None, controller_name='PLAYSTATION(R)3 Controller'):
        """
        Connect to the specified controller device. If no device is specified, try a connection to
        the first PS3 controller available within /dev/inputX called 'PLAYSTATION(R)3 Controller'
        (this may mean that non-genuine PS3 controllers are not recognized). In this case, you have
        to provide your controller custom name as the second parameter.

        This also creates a new thread to run the asyncore loop and uses a file dispatcher monitoring
        the corresponding device to handle input events. All events are passed to the handle_event
        function in the parent, this is then responsible for interpreting the events and updating
        any internal state, calling button handlers etc.

        :return:
            True if a controller was found and connected, False if we already had a connection
        :raises IOError:
            If we didn't already have a controller but couldn't find a new one, this normally means
            there's no controller paired with the Pi
        """
        if self._stop_function:
            return False

        def find_controller_device():
            # find the sixaxis device by name
            for device in [InputDevice(fn) for fn in list_devices()]:
                if device.name == controller_name:
                    return device
            raise IOError('Cannot find a SixAxis controller named {}'.format(controller_name))
        
        if dev is None:
            device = find_controller_device()
        else:
            device = InputDevice(dev)
        
        self._start_device_read_loop(device)

    def disconnect(self):
        """
        Disconnect from any controllers, shutting down the channel and allowing the monitoring thread to terminate
        if there's nothing else bound into the evdev loop. Doesn't do anything if we're not connected to a controller
        """
        if self._stop_function:
            self._stop_function()
            self._stop_function = None

    def __str__(self):
        """
        Simple string representation of the state of the axes
        """
        return 'x1={}, y1={}, x2={}, y2={}'.format(
            self.axes[0].corrected_value(), self.axes[1].corrected_value(),
            self.axes[2].corrected_value(), self.axes[3].corrected_value())

    def set_axis_centres(self, *args):
        """
        Sets the centre points for each axis to the current value for that axis. This centre value is used when
        computing the value for the axis and is subtracted before applying any scaling.
        """
        for axis in self.axes:
            axis.centre = axis.value

    def reset_axis_calibration(self, *args):
        """
        Resets any previously defined axis calibration to 0.0 for all axes
        """
        for axis in self.axes:
            axis._reset()

    def register_button_handler(self, button_handler, buttons):
        """
        Register a handler function which will be called when a button is pressed

        :param handler: a function which will be called when any of the specified buttons are pressed. The function is
            called with the integer code for the button as the sole argument.
        :param [int] buttons: a list or one or more buttons which should trigger the handler when pressed. Buttons are
            specified as ints, for convenience the PS3 button assignments are mapped to names in SixAxis, i.e.
            SixAxis.BUTTON_CIRCLE. This includes the buttons in each of the analogue sticks. A bare int value is also
            accepted here and will be treated as if a single element list was supplied.
        :return: a no-arg function which can be used to remove this registration
        """
        mask = 0
        if isinstance(buttons, list):
            for button in buttons:
                mask += 1 << button
        else:
            mask += 1 << buttons
        h = {
            'handler': button_handler,
            'mask': mask
        }
        self.button_handlers.append(h)

        def remove():
            self.button_handlers.remove(h)

        return remove

    def is_pressed(self, button):
        """
        Return True if the button is pressed
        """
        return self.buttons_pressed & (1 << button) != 0

    def handle_event(self, event):
        """
        Handle a single evdev event, this updates the internal state of the Axis objects as well as calling any
        registered button handlers.

        :internal:

        :param event:
            The evdev event object to parse
        """
        if event.type == ecodes.EV_ABS:
            # ANALOG STICK
            value = float(event.value) / 255.0
            if value < 0:
                value = 0
            elif value > 1.0:
                value = 1.0
            if event.code == 0:
                # Left stick, X axis
                self.axes[0]._set(value)
            elif event.code == 1:
                # Left stick, Y axis
                self.axes[1]._set(value)
            elif event.code == 3:
                # Right stick, X axis
                self.axes[2]._set(value)
            elif event.code == 4:
                # Right stick, Y axis (yes, 5...)
                self.axes[3]._set(value)
            # if event.code > 1:
            #     print(event.code)
        elif event.type == ecodes.EV_KEY:
            # BUTTON
            if event.code == 314:
                button = SixAxis.BUTTON_SELECT
            elif event.code == 315:
                button = SixAxis.BUTTON_START
            elif event.code == 317:
                button = SixAxis.BUTTON_LEFT_STICK
            elif event.code == 318:
                button = SixAxis.BUTTON_RIGHT_STICK
            elif event.code == 546:
                button = SixAxis.BUTTON_D_LEFT
            elif event.code == 544:
                button = SixAxis.BUTTON_D_UP
            elif event.code == 547:
                button = SixAxis.BUTTON_D_RIGHT
            elif event.code == 545:
                button = SixAxis.BUTTON_D_DOWN
            elif event.code == 316:
                button = SixAxis.BUTTON_PS
            elif event.code == 308:
                button = SixAxis.BUTTON_SQUARE
            elif event.code == 307:
                button = SixAxis.BUTTON_TRIANGLE
            elif event.code == 305:
                button = SixAxis.BUTTON_CIRCLE
            elif event.code == 304:
                button = SixAxis.BUTTON_CROSS
            elif event.code == 311:
                button = SixAxis.BUTTON_R1
            elif event.code == 313:
                button = SixAxis.BUTTON_R2
            elif event.code == 310:
                button = SixAxis.BUTTON_L1
            elif event.code == 312:
                button = SixAxis.BUTTON_L2
            else:
                button = None
            
            if button is not None:
                bitmask = 1 << button
                if event.value == 1:
                    self.buttons_pressed |= bitmask
                    for button_handler in self.button_handlers:
                        if button_handler['mask'] & (bitmask) != 0:
                            button_handler['handler'](button)
                else:
                    # clear the button pressed bitfield
                    self.buttons_pressed &= ~bitmask

    class Axis():
        """A single analogue axis on the SixAxis controller"""

        def __init__(self, name, invert=False, dead_zone=0.0, hot_zone=0.0):
            self.name = name
            self.centre = 0.5
            self.max = 0.9
            self.min = 0.1
            self.value = 0.5
            self.invert = invert
            self.dead_zone = dead_zone
            self.hot_zone = hot_zone

        def corrected_value(self):
            """
            Get a centre-compensated, scaled, value for the axis, taking any dead-zone into account. The value will
            scale from 0.0 at the edge of the dead-zone to 1.0 (positive) or -1.0 (negative) at the extreme position of
            the controller or the edge of the hot zone, if defined as other than 1.0. The axis will auto-calibrate for
            maximum value, initially it will behave as if the highest possible value from the hardware is 0.9 in each
            direction, and will expand this as higher values are observed. This is scaled by this function and should
            always return 1.0 or -1.0 at the extreme ends of the axis.

            :return: a float value, negative to the left or down and ranging from -1.0 to 1.0
            """

            high_range = self.max - self.centre
            high_start = self.centre + self.dead_zone * high_range
            high_end = self.max - self.hot_zone * high_range

            low_range = self.centre - self.min
            low_start = self.centre - self.dead_zone * low_range
            low_end = self.min + self.hot_zone * low_range

            if self.value > high_start:
                if self.value > high_end:
                    result = 1.0
                else:
                    result = (self.value - high_start) / (high_end - high_start)
            elif self.value < low_start:
                if self.value < low_end:
                    result = -1.0
                else:
                    result = (self.value - low_start) / (low_start - low_end)
            else:
                result = 0

            if not self.invert:
                return result
            else:
                return -result

        def _reset(self):
            """
            Reset calibration (max, min and centre values) for this axis specifically. Not generally needed, you can just
            call the reset method on the SixAxis instance.

            :internal:
            """
            self.centre = 0.5
            self.max = 0.9
            self.min = 0.1

        def _set(self, new_value):
            """
            Set a new value, called from within the SixAxis class when parsing the event queue.

            :param new_value: the raw value from the joystick hardware
            :internal:
            """
            self.value = new_value
            if new_value > self.max:
                self.max = new_value
            elif new_value < self.min:
                self.min = new_value


if __name__ == '__main__':
    import time
    import os

    # def callback(button):
    #     print('Button: {}'.format(button))

    def draw_controller(controller):
        def pressed_symbol(released, pressed, value):
            return released if not value else pressed
        scheme = r"""
                {}                                         {}
              _=====_                                    _=====_
             /___{}___\                                  / __{}__ \
           +.' _____ '.--------------------------------.' _____ '.+
          /   |     |  '.           S O N Y          .'  |  _  |   \
         / ___| {u} |___ \                          / ___| /{}\ |___ \
        / |      |      | ;    __             __   ; | _         _ | ;
        | | {l}-   -{r} | |   |{}|           |{}'> | ||{}|       ({})| |
        | |___   |   ___| ;  SELECT         START  ; |___       ___| ;
        |\    | {d} |    / _____     /{}\     _____ \    | ({}) |    /|
        | \   |_____|  .','     ',   \__/   ,'     ','.  |_____|  .' |
        |  '-.______.-' /         \        /         \ '-._____.-'   |
        |              |     {}     |------|     {}     |              |
        |              /\         /        \         /\              |
        |             /  '._____.'          '._____.'  \             |
        |            /                                  \            |
         \          /                                    \          /
          \________/                                      \________/
        """.format(
            pressed_symbol('L2', '@@', controller.is_pressed(SixAxis.BUTTON_L2)),
            pressed_symbol('R2', '@@', controller.is_pressed(SixAxis.BUTTON_R2)),
            pressed_symbol('L', '@', controller.is_pressed(SixAxis.BUTTON_L1)),
            pressed_symbol('R', '@', controller.is_pressed(SixAxis.BUTTON_R1)),

            pressed_symbol('_', '@', controller.is_pressed(SixAxis.BUTTON_TRIANGLE)),
            
            pressed_symbol('__', '@@', controller.is_pressed(SixAxis.BUTTON_SELECT)),
            pressed_symbol('__', '@@', controller.is_pressed(SixAxis.BUTTON_START)),

            pressed_symbol('_', '@', controller.is_pressed(SixAxis.BUTTON_SQUARE)),
            pressed_symbol('O', '@', controller.is_pressed(SixAxis.BUTTON_CIRCLE)),

            pressed_symbol('PS', '@@', controller.is_pressed(SixAxis.BUTTON_PS)),
            pressed_symbol('X', '@', controller.is_pressed(SixAxis.BUTTON_CROSS)),

            pressed_symbol('O', '@', controller.is_pressed(SixAxis.BUTTON_LEFT_STICK)),
            pressed_symbol('O', '@', controller.is_pressed(SixAxis.BUTTON_RIGHT_STICK)),

            u=pressed_symbol('/|\\', '@@@', controller.is_pressed(SixAxis.BUTTON_D_UP)),
            d=pressed_symbol('\\|/', '@@@', controller.is_pressed(SixAxis.BUTTON_D_DOWN)),
            l=pressed_symbol('<--', '@@@', controller.is_pressed(SixAxis.BUTTON_D_LEFT)),
            r=pressed_symbol('-->', '@@@', controller.is_pressed(SixAxis.BUTTON_D_RIGHT)),
        )
        os.system('clear')
        print(scheme)

    # controller = SixAxis(dead_zone=0.0, hot_zone=0.0)
    with SixAxisResource(invert_axes=[False, True, False, True]) as controller:
        # controller.register_button_handler(callback, SixAxis.BUTTON_CROSS)
        # controller.register_button_handler(callback, SixAxis.BUTTON_SQUARE)
        # controller.register_button_handler(callback, SixAxis.BUTTON_CIRCLE)
        # controller.register_button_handler(callback, SixAxis.BUTTON_TRIANGLE)

        def current_milli_time():
            return int(round(time.time() * 1000))
        
        last_time = current_milli_time()
        last_button_pressed = controller.buttons_pressed
        last_left_x = 0
        last_left_y = 0
        last_right_x = 0
        last_right_y = 0

        draw_controller(controller)
        print("LEFT STICK: {:.2f}, {:.2f}".format(last_left_x, last_left_y))
        print("RIGHT STICK: {:.2f}, {:.2f}".format(last_right_x, last_right_y))
        
        while 1:
            # controller.handle_events()
            now = current_milli_time()
            if now > (last_time + 200):
                # update at least after 200ms
                last_time = now
                
                x1 = round(controller.axes[0].corrected_value(), 2)
                y1 = round(controller.axes[1].corrected_value(), 2)
                x2 = round(controller.axes[2].corrected_value(), 2)
                y2 = round(controller.axes[3].corrected_value(), 2)

                if controller.buttons_pressed != last_button_pressed or \
                    x1 != last_left_x or y1 != last_left_y or \
                        x2 != last_right_x or y2 != last_right_y:
                    # update values
                    last_button_pressed = controller.buttons_pressed
                    last_left_x = x1
                    last_left_y = y1
                    last_right_x = x2
                    last_right_y = y2
                    # print controller state
                    draw_controller(controller)
                    print("LEFT STICK: {:.2f}, {:.2f}".format(x1, y1))
                    print("RIGHT STICK: {:.2f}, {:.2f}".format(x2, y2))
