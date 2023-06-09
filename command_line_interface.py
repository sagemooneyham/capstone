import inspect
import sys
import time
import logging
import shlex
import asyncio


from aioconsole import ainput
from bluepy import btle
from joycontrol.controller_state import button_push, ControllerState
from joycontrol.report import OutputReport
from joycontrol.transport import NotConnectedError

logger = logging.getLogger(__name__)

from gpiozero import Button
from signal import pause
import serial
import board
import busio

import adafruit_ads1x15.ads1015 as ADS
from adafruit_ads1x15.analog_in import AnalogIn

report = OutputReport()
#i2c = busio.I2C(board.SCL, board.SDA)
#ads = ADS.ADS1015(i2c)
#chan = AnalogIn(ads, ADS.P0)
#ads.gain = 2/3
#SPEED_BLE_ADDRESS = "2B:FB:05:0A:52:89"        #THESE WILL CHANGE WITH DIFFERENT SENSORS
#SPEED_SERVICE_UID = "a123f5cd-58ee-49e5-ad0e-976cb103556e"
#SPEED_CHARACTERISTIC_UID = "a123f5cd-58ee-49e5-ad0e-976cb103556f"
STEERING_BLE_ADDRESS = "73:BA:2D:92:39:C8"
STEERING_SERVICE_UID = "a123f5cd-58ee-49e5-ad0e-976cb10355fe"
STEERING_CHARACTERISTIC_UID = "a123f5cd-58ee-49e5-ad0e-976cb10355ff"



zl_button = Button(17,True,None,0.5)
zr_button = Button(4,True,None,0.5)
up_button = Button(23)
down_button = Button(24)
left_button = Button(25)
right_button = Button(8)
a_button = Button(7)
b_button = Button(12)

#speed_sensor = btle.Peripheral(SPEED_BLE_ADDRESS)
steering_sensor = btle.Peripheral(STEERING_BLE_ADDRESS)
#speedService = speed_sensor.getServiceByUUID(SPEED_SERVICE_UID)
steeringService = steering_sensor.getServiceByUUID(STEERING_SERVICE_UID)


#ser = serial.Serial('/dev/ttyUSB0', 9600, timeout=0.01)
#ser.reset_input_buffer()

#ser2 = serial.Serial('/dev/ttyUSB1', 9600, timeout=0.01)
#ser2.reset_input_buffer()

def _print_doc(string):
    """
    Attempts to remove common white space at the start of the lines in a doc string
    to unify the output of doc strings with different indention levels.

    Keeps whitespace lines intact.

    :param fun: function to print the doc string of
    """
    lines = string.split('\n')
    if lines:
        prefix_i = 0
        for i, line_0 in enumerate(lines):
            # find non empty start lines
            if line_0.strip():
                # traverse line and stop if character mismatch with other non empty lines
                for prefix_i, c in enumerate(line_0):
                    if not c.isspace():
                        break
                    if any(lines[j].strip() and (prefix_i >= len(lines[j]) or c != lines[j][prefix_i])
                           for j in range(i+1, len(lines))):
                        break
                break

        for line in lines:
            print(line[prefix_i:] if line.strip() else line)


class CLI:
    def __init__(self):
        self.commands = {}
    def add_command(self, name, command):
        if name in self.commands:
            raise ValueError(f'Command {name} already registered.')
        self.commands[name] = command

    async def cmd_help(self):
        print('Commands:')
        for name, fun in inspect.getmembers(self):
            if name.startswith('cmd_') and fun.__doc__:
                _print_doc(fun.__doc__)

        for name, fun in self.commands.items():
            if fun.__doc__:
                _print_doc(fun.__doc__)

        print('Commands can be chained using "&&"')
        print('Type "exit" to close.')

    async def run(self):
        while True:
            user_input = await ainput(prompt='cmd >> ')
            if not user_input:
                continue

            for command in user_input.split('&&'):
                cmd, *args = shlex.split(command)

                if cmd == 'exit':
                    return

                if hasattr(self, f'cmd_{cmd}'):
                    try:
                        result = await getattr(self, f'cmd_{cmd}')(*args)
                        if result:
                            print(result)
                    except Exception as e:
                        print(e)
                elif cmd in self.commands:
                    try:
                        result = await self.commands[cmd](*args)
                        if result:
                            print(result)
                    except Exception as e:
                        print(e)
                else:
                    print('command', cmd, 'not found, call help for help.')

    @staticmethod
    def deprecated(message):
        async def dep_printer(*args, **kwargs):
            print(message)

        return dep_printer


class ControllerCLI(CLI):
    
    def __init__(self, controller_state: ControllerState):
        super().__init__()
        self.controller_state = controller_state

    async def cmd_help(self):
        print('Button commands:')
        print(', '.join(self.controller_state.button_state.get_available_buttons()))
        print()
        await super().cmd_help()

    @staticmethod
    def _set_stick(stick, direction, value):
        if direction == 'center':
            stick.set_center()
        elif direction == 'up':
            stick.set_up()
        elif direction == 'down':
            stick.set_down()
        elif direction == 'left':
            stick.set_left()
        elif direction == 'right':
            stick.set_right()
        elif direction in ('h', 'horizontal'):
            if value is None:
                raise ValueError(f'Missing value')
            try:
                val = int(value)
            except ValueError:
                raise ValueError(f'Unexpected stick value "{value}"')
            stick.set_h(val)
        elif direction in ('v', 'vertical'):
            if value is None:
                raise ValueError(f'Missing value')
            try:
                val = int(value)
            except ValueError:
                raise ValueError(f'Unexpected stick value "{value}"')
            stick.set_v(val)
        else:
            raise ValueError(f'Unexpected argument "{direction}"')

        return f'{stick.__class__.__name__} was set to ({stick.get_h()}, {stick.get_v()}).'

    async def cmd_stick(self, side, direction, value=None):
        """
        stick - Command to set stick positions.
        :param side: 'l', 'left' for left control stick; 'r', 'right' for right control stick
        :param direction: 'center', 'up', 'down', 'left', 'right';
                          'h', 'horizontal' or 'v', 'vertical' to set the value directly to the "value" argument
        :param value: horizontal or vertical value
        """
        if side in ('l', 'left'):
            stick = self.controller_state.l_stick_state
            return ControllerCLI._set_stick(stick, direction, value)
        elif side in ('r', 'right'):
            stick = self.controller_state.r_stick_state
            return ControllerCLI._set_stick(stick, direction, value)
        else:
            raise ValueError('Value of side must be "l", "left" or "r", "right"')

    async def run(self):
        initial = 0
        zlFlag = 0
        zrFlag = 0
       
        while True:
            
            if(initial < 1):
                user_input = await ainput(prompt='cmd >> ')
                initial += 1
                line = 0
            
           # if ser.in_waiting > 0:   
              #  line = ser.readline().decode('utf-8').rstrip()
              #  if line == None:
                    #line = 0                
            #print(line)
            print(report.get_rumble_data())
            #test
           # rate = int(line)
            rate = 1
            #print(chan.value)
            
            #NEW METHOD SPEED(WIRELESS)
            #---------------------
            
            #speedCharacteristic = speedService.getCharacteristics(SPEED_CHARACTERISTIC_UID)
            #speedData = speedCharacteristic.read()
            #print(speedData)
            #-------
            #speed sensor broke
            
            if (zlFlag == 1) and (zrFlag == 1) and (not zl_button.is_pressed) and (not zr_button.is_pressed):
                user_input = 'release zl && release zr'
                zlFlag = 0
                zrFlag = 0
            elif (zlFlag == 1) and (not zl_button.is_pressed):
                user_input = 'release zl'
                zlFlag = 0
            elif (zrFlag == 1) and (not zr_button.is_pressed):
                user_input = 'release zr'
                zrFlag = 0
            elif (zlFlag == 0) and (zrFlag == 0) and (zl_button.is_pressed) and (zr_button.is_pressed):
                user_input = 'hold zl && hold zr'
                zlFlag = 1
                zrFlag = 1
            elif (zlFlag == 0) and (zl_button.is_pressed):
                user_input = 'hold zl'
                zlFlag = 1
            elif (zrFlag == 0) and (zr_button.is_pressed):
                user_input = 'hold zr'
                zrFlag = 1
            else:
                user_input = None
            
            if(user_input == None):
                if(not down_button.is_pressed):
                    user_input = 'down'
            else:
                if(not down_button.is_pressed):
                    user_input = user_input + ' && down'
                    
            if(user_input == None):
                if(not up_button.is_pressed):
                    user_input = 'up'
            else:
                if(not up_button.is_pressed):
                    user_input = user_input + ' && up'
                    
            if(user_input == None):
                if(not left_button.is_pressed):
                    user_input = 'left'
            else:
                if(not left_button.is_pressed):
                    user_input = user_input + ' && left'
                    
            if(user_input == None):
                if(not right_button.is_pressed):
                    user_input = 'right'
            else:
                if(not right_button.is_pressed):
                    user_input = user_input + ' && right'
                    
            if(user_input == None):
                if(not a_button.is_pressed):
                    user_input = 'a'
            else:
                if(not a_button.is_pressed):
                    user_input = user_input + ' && a'
                    
            if(user_input == None):
                if(not b_button.is_pressed):
                    user_input = 'b'
            else:
                if(not b_button.is_pressed):
                    user_input = user_input + ' && b'
            #Read wireless steering data    
            steeringCharacteristic = steeringService.getCharacteristics(STEERING_CHARACTERISTIC_UID)
            steeringData = steeringCharacteristic.read()
            print(steeringData)
            ## OLD:mapval = int(chan.value / 6.75)
            mapval = int(steeringData / 6.75)
            if(mapval < 2048):
                mapval = 2048 - ((((1.7 * mapval) - 3481.6) ** 2) / 2048)               #(((2048 - mapval) ** 2)/925)
            else:
                mapval = ((((1.7 * mapval) - 3481.6) ** 2) / 2048) + 2048
                
            if(mapval > 4096):
                mapval = 4095
            elif(mapval < 0):
                mapval = 0
            
            
            if(user_input == None):
                if (mapval >= 2030) and (mapval <= 2070):
                    user_input = 'stick l center'
                else:
                    user_input = 'stick l h ' + str(4096 - int(mapval))
            else:
                if (mapval >= 2030) and (mapval <= 2070):
                    user_input = user_input + ' && stick l center'
                else:
                    user_input = user_input + ' && stick l h ' + str(4096 - int(mapval))
                    
             
            if(user_input == None):
                if rate > 0:
                    user_input = 'hold a'
                else:
                    user_input = 'release a'
            else:
                if rate > 0:
                    user_input = user_input + ' && hold a'
                else:
                    user_input = user_input + ' && release a'
                 
            print(user_input)
            
            if not user_input:
                continue
            
            buttons_to_push = []

            for command in user_input.split('&&'):
                cmd, *args = shlex.split(command)

                if cmd == 'exit':
                    return

                available_buttons = self.controller_state.button_state.get_available_buttons()

                if hasattr(self, f'cmd_{cmd}'):
                    try:
                        result = await getattr(self, f'cmd_{cmd}')(*args)
                        if result:
                            print(result)
                    except Exception as e:
                        print(e)
                elif cmd in self.commands:
                    try:
                        result = await self.commands[cmd](*args)
                        if result:
                            print(result)
                    except Exception as e:
                        print(e)
                elif cmd in available_buttons:
                    buttons_to_push.append(cmd)
                else:
                    print('command', cmd, 'not found, call help for help.')

            if buttons_to_push:
                await button_push(self.controller_state, *buttons_to_push)
            else:
                try:
                    await self.controller_state.send()
                except NotConnectedError:
                    logger.info('Connection was lost.')
                    return
