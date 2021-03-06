import cv2
import numpy as np
from copy import copy
import random
import time
import sys

from cv2_ui import *
from optimizer import *

# simulated PySerial port for debug use
class SimulatedSerialPort:
    @staticmethod
    def read(_):
        time.sleep(5)
        choice = random.choice(['./ticket_samples/bytes1', './ticket_samples/bytes2', './ticket_samples/bytes3'])
        with open(choice, 'rb') as ticket:
            return ticket.read()

#####################################
#   ESCPOS -> Code Object Classes   #
#####################################

# UNUSED CURRENTLY
# to support future features -> enables smarter parsing and comparing of items in the queue
class Modification:

    """
    node in unconstrained spanning tree of modifications

    * Modification(modification_string)
    * addChild(child)
    """

    def __init__(self, modification_string):
        self.modification = modification_string     
        self.children = set()                       #set of modification instances

    def addChild(self, child):
        self.children.add(child)


# there is an uneccesarry disconnect between this item and the optimzer ListItem -> these should be the same object
# this is relic of transition from OCR to PySerial as means of order retreival
# TODO marry functionality of both classes to reduce complexity/redundancy/LOC
class Item:

    """
    Discrete item parsed from ticket

    Item(string, quantity)
    * string -> string primary of item on ticket
    * quantity -> integer indicating quantity of this item on ticket
    """

    def __init__(self, string, quantity):
        if string:
            self.primary = string
            self.modifications = []
            self.quantity = quantity

    def __eq__(self, other):
        return self.primary == other.primary 
    
    def __str__(self):
        self_string = self.primary + '\n'
        for mod in self.modifications:
            self_string += mod + '\n'
        return self_string[:-1]
    
    def __repr__(self):
        return self.__str__()

    def addModification(self, mod):
        self.modifications.append(mod)

###############################
#   UI Touch Event Handlers   #
###############################

#global reference to switch between active optimizer cursor
selected_optimizer = None

def queueButtonCallback(text):
    print(text)
    global selected_optimizer

    if text == "plus":
        queue_pop_counter.increment(1)
    elif text == "minus":
        queue_pop_counter.increment(-1)
    elif text == "up_arrow":
        selected_optimizer.incrementCursor(-1)
    elif text == "down_arrow":
        selected_optimizer.incrementCursor(1)
    elif text == "DONE":
        if len(selected_optimizer.item_queue) > 0:
            selected_optimizer.remove(ListItem(selected_optimizer.item_queue[selected_optimizer.cursor].text, queue_pop_counter.val))
        queue_pop_counter.increment(-queue_pop_counter.val)
    elif text == 'swap':
        selected_optimizer = inhouse_optimizer if selected_optimizer == togo_optimizer else togo_optimizer

def queueMouseCallback(event, x, y, flags, param):
    if event == cv2.EVENT_LBUTTONDOWN:# or event == cv2.EVENT_LBUTTONDBLCLK:
        for element in queue_control_panel.elements:
            if x > element.x_pos and x < element.x_pos2 and y > element.y_pos and y < element.y_pos2:
                if isinstance(element, Button):
                    element.callback(element.text)

########################################
#   Create Widgets and Define Layout   #
########################################

#initial canvas setup
queue_canvas_width = 800
queue_canvas_height = 480
queue_canvas = np.ones((queue_canvas_height, queue_canvas_width, 3), np.uint8)
cv2.line(queue_canvas, (585//2, 0), (585//2, queue_canvas_width), color=(255,255,255), thickness=2)
cv2.putText(queue_canvas, "TO-GO", (100, 15), 5, .9, color=(0, 0, 255))
cv2.putText(queue_canvas, "IN HOUSE", (585//2+100, 15), 5, .9, color=(0, 0, 255))

#create queue control panel
queue_control_panel = Panel(text="Queue Control Panel", x_pos=585, y_pos=0, width=215, height=queue_canvas_height, color=(0, 50, 0))

up_arrow = cv2.imread("./gui/textures/up_arrow.png")
down_arrow = cv2.imread("./gui/textures/down_arrow.png")
plus_sign = cv2.imread("./gui/textures/plus.png")
minus_sign = cv2.imread("./gui/textures/minus.png")
#logo = cv2.imread("./gui/textures/pj_logo.png")
swap = cv2.imread("./gui/textures/swap.png")

queue_pop_counter = Counter(0, 20001, 5, 110, 100, 100, text_color = (255, 255, 255))

queue_control_panel.addElement(Button("swap", 10001, x_pos=5, y_pos=5, width=100, height=100, texture = swap))
queue_control_panel.addElement(Button("up_arrow", 10002, x_pos = 110, y_pos = 5, width = 100, height = 100, texture = up_arrow))
queue_control_panel.addElement(Button("down_arrow", 10003, x_pos = 110, y_pos = 110, width = 100, height = 100, texture = down_arrow))
queue_control_panel.addElement(Button("plus", 10004, x_pos = 110, y_pos = 215, width = 100, height = 100, texture = plus_sign))
queue_control_panel.addElement(Button("minus", 10005, x_pos = 5, y_pos = 215, width = 100, height = 100, texture = minus_sign))
queue_control_panel.addElement(Button("DONE", 10006, x_pos = 5, y_pos = 320, width = 205, height = 150, color = (0, 0, 0), text_color = (255, 255, 255)))
queue_control_panel.addElement(queue_pop_counter)

#assign callback -> using same callback function with if/else check
for element in queue_control_panel.elements:
    if isinstance(element, Button):
        element.addCallback(queueButtonCallback)

#create GUI window
cv2.namedWindow("ORDER QUEUE", cv2.WINDOW_GUI_NORMAL)
cv2.setMouseCallback("ORDER QUEUE", queueMouseCallback)

#create and justify optimizers
togo_optimizer = OrderOptimizer("togo")
togo_optimizer.justify((10, 20))

inhouse_optimizer = OrderOptimizer("inhouse")
inhouse_optimizer.justify((10, 20))

#select TOGO optimizer to start
selected_optimizer = togo_optimizer

##############################
#   Decode and Parse Loops   #
##############################

import threading
import serial
import re

#globals -> not safe yet
raw_data = bytes()
current_ticket = str()

#non-printable ASCIIs -> we want to take these out of the ticket
control_characters = set([c for c in range(32)])
control_characters.remove(10)                       #remove LF (linefeed) from ignorable characters

# TODO implement safety measures
# decodes raw bytes from serial and builds current ticket; calls enqueueTicket if paper cut command is detected
def decodeESCPOS():
    t1 = time.time()
    global raw_data             #   these globals are technically unsafe BUT highly unlikely to race in this case because
    global current_ticket       #   decodeESCPOS function drastically outperforms the serial receive (100s times faster)

    #work on local of copy raw data possibly?           

    byte_iter = 0
    while byte_iter < len(raw_data):
        
        #if escape character 'ESC'
        if raw_data[byte_iter] == 27:
            #if not at end of data, see if next byte is an 'm' -> this 'ESC m' is ESCPOS command for cut paper; we know ticket is finished
            if byte_iter < len(raw_data)-1 and raw_data[byte_iter+1] == 109:
                enqueueTicket(current_ticket)                   #ticket is added to the queue
                current_ticket = str()                          #we can drop reference and start new one
            byte_iter += 2                          #increment past 'ESC' and following character
            continue
        
        #pass control characters; O(1) constant lookup in set
        if raw_data[byte_iter] in control_characters:
            byte_iter += 1
            continue

        #append character representation to current_ticket string
        current_ticket += chr(raw_data[byte_iter])
        byte_iter += 1
    
    t2 = time.time()
    print("took {} ms.\n".format(1000.0*(t2 - t1)))

# TODO could break if MISC is sent with space in first 2 spots; need to catch case where no primaries yet, but got mod somehow
# parses ticket as string and adds items to queue
def enqueueTicket(ticket):
    #remove header and footer of ticket
    header_end = ticket.find('Guests:') + 12
    footer_start = ticket.find('------------------------------------------')
    ticket = ticket[header_end:footer_start]

    # 1 space before DONT MAKE / TO GO line
    # 0 space before primary
    # >=2 space before secondary & character other than <SPACE>

    sub_queue = []              #sub_queue to build locally and add to the main queue
    empty_line = ' '*7          #anything with more than 6 spaces we want to trash

    #parsing the body of the ticket -> TOGO, DINE IN, DON'T MAKE options here also
    for line in ticket.splitlines():
        if not line.startswith(empty_line) and line.find("!!DON'T MAKE!!") == -1 and line.find("TO GO-TO GO-TO GO") == -1:       # rid empty lines and options
            if line.startswith('  '):                                                           #if line starts with spaces but is not empty
                sub_queue[len(sub_queue)-1].addModification(line.rstrip(' '))                   #add modification to last item
            else:
                match = re.search(r'\d+', line)                                                 #regex look for quantity in primary string

                if match:
                    line = line.lstrip(str(match.group() + ' '))
                    sub_queue.append(Item(line.rstrip(' '), int(match.group())))                #new item with primary from this line
                else:
                    sub_queue.append(Item(line.rstrip(' '), 1))                                 #new item with default quantity -> no quantity was listen on ticket

    if ticket.find("!!DON'T MAKE!!") != -1:             #want to ignore these tickets -> could be placed at top to optimize out these reads, but this is very negligible.  preferring ease of code read
        return
    elif ticket.find("TO GO-TO GO-TO GO") != -1:        #add to togo queue
        optimizer = togo_optimizer
    else:                                               #add to inhouse queue
        optimizer = inhouse_optimizer               

    for i in sub_queue:                                 #add items to appropriate queue
        optimizer.add(ListItem(str(i), i.quantity))     # TODO marry ListItem & Item classes -> see notes above Item class definition


def receiveSerial(serial_port):
    global raw_data
    
    while 1:
        #get raw data from appropriate location (serial port or simulated port function)
        raw_data = serial_port.read(128)
        
        print("Received {} bytes, decoding & parsing...".format(len(raw_data)))

        #create & start decode thread
        # TODO create or find custom encoding to use str.decode() method?
        threading.Thread(target=decodeESCPOS).start()

        # TODO pass as a reference the raw_data, then raw data is reassigned next loop
        # so handling thread has a unique raw data that it will drop when done? look into this


if __name__ == "__main__":

    if len(sys.argv) > 1 and sys.argv[1] == '--sim_serial':         #parse command line arg
        print("Received data being simulated.")
        serial_port = SimulatedSerialPort                           #make simulated port instance
    else:
        serial_port = serial.Serial("/dev/ttyAMA0", baudrate=9600, bytesize=serial.EIGHTBITS, parity=serial.PARITY_EVEN)    #open serial port

    #start receiving and parsing -> daemon exits with main thread
    threading.Thread(target=receiveSerial, daemon=True, args=(serial_port,)).start()

    #UI loop
    while 1:
        queue_frame = copy(queue_canvas)

        #grab queue frames
        togo_queue_frame = queue_frame[0:queue_canvas_height, 0:585//2]             #hardcode dimensions, could be improved using know width of control panel & screen
        inhouse_queue_frame = queue_frame[0:queue_canvas_height, 585//2+1:585]

        #update control panel
        queue_control_panel.drawSelf(queue_frame)

        #update queue frames
        togo_optimizer.drawSelf(togo_queue_frame, draw_cursor=selected_optimizer==togo_optimizer)
        inhouse_optimizer.drawSelf(inhouse_queue_frame, draw_cursor=selected_optimizer==inhouse_optimizer)

        #show frame
        cv2.imshow("ORDER QUEUE", queue_frame)

        #lock to max 20 Hz
        k = cv2.waitKey(50)   # effectively performant in all test cases with touchscreen; more FPS only means more heat on the uncooled RPi

        #q key exits program
        if k == ord('q'):
            break
    
    #close UI window
    cv2.destroyAllWindows()
