import pyglet
import time
import numpy as np
import threading
from collections import deque
from enum import Enum
from .robot_commands import RobotCommands

BALL_POS_HISTORY_LENGTH = 20
BALL_LOST_TIME = .1
ROBOT_POS_HISTORY_LENGTH = 20
ROBOT_LOST_TIME = .2

class GameState(object):
    """Game state contains all the relevant information in one place.
       Many threads can edit and use the game state at once, cuz Python GIL
       Since we are using python, data types are specified in the comments below.
    """
    def __init__(self):
        # NOTE: Fields starting with _underscore are "private" so
        # should be accessed through getter and setter methods

        # RAW POSITION DATA (updated by vision data or simulator)
        # [most recent data is stored at the front of the queue]
        # ball positions are in the form (x, y)
        self._ball_position = deque([], BALL_POS_HISTORY_LENGTH) # queue of (time, pos)
        # robot positions are (x, y, w) where w = rotation
        self._blue_robot_positions = dict() # Robot ID: queue of (time, pos)
        self._yellow_robot_positions = dict() # Robot ID: queue of (time, pos)
        # TODO: store both teams robots
        # TODO: include game states/events, such as time, score and ref events (see docs)

        # Commands data (desired robot actions)
        self._blue_robot_commands = dict() # Robot ID: command object
        self._yellow_robot_commands = dict() # Robot ID: command object

        # TODO: cached analysis data (i.e. ball trajectory)
        # this can be later, for now just build the functions
        self.ball_velocity = (0,0)

        # gamestate thread is for doing analysis on raw data (i.e. trajectory calcuations, etc.)
        self._is_analyzing = False
        self._analysis_thread = None

    def start_analyzing(self):
        self._is_analyzing = True
        self._analysis_thread = threading.Thread(target=self.analysis_loop)
        # set to daemon mode so it will be easily killed
        self._analysis_thread.daemon = True
        self._analysis_thread.start()

    def analysis_loop(self):
        while self._is_analyzing:
            # TODO: calculate from the position history
            #print(self._ball_position)
            time.sleep(1)
            # yield to other threads - run this loop at most 20 times per second
            # time.sleep(.05)

    def stop_analyzing(self):
        self._is_analyzing = False
        self._analysis_thread.join()
        self._analysis_thread = None

    # RAW DATA GET/SET FUNCTIONS
    # returns position ball was last seen at
    def get_ball_position(self):
        if len(self._ball_position) == 0:
            # print("getting ball position but ball never seen?!?")
            return None
        timestamp, pos = self._ball_position[0]
        return pos

    def update_ball_position(self, pos):
        self._ball_position.appendleft((time.time(), pos))

    def get_ball_last_update_time(self):
        if len(self._ball_position) == 0:
            # print("getting ball update time but ball never seen?!?")
            return None
        timestamp, pos = self._ball_position[0]
        return timestamp

    def is_ball_lost(self):
        last_update_time = self.get_ball_last_update_time()
        if last_update_time is None:
            return True
        return time.time() - last_update_time > BALL_LOST_TIME

    def get_robot_positions(self, team):
        if team == 'blue':
            return self._blue_robot_positions
        else:
            assert(team == 'yellow')
            return self._yellow_robot_positions

    def get_robot_ids(self, team):
        robot_positions = self.get_robot_positions(team)
        return tuple(robot_positions.keys())

    # returns position robot was last seen at
    def get_robot_position(self, team, robot_id):
        robot_positions = self.get_robot_positions(team)
        if robot_id not in robot_positions:
            # print("getting position of robot never seen?!?")
            return None
        timestamp, pos = robot_positions[robot_id][0]
        return pos

    def update_robot_position(self, team, robot_id, pos):
        robot_positions = self.get_robot_positions(team)
        if robot_id not in robot_positions:
            robot_positions[robot_id] = deque([], ROBOT_POS_HISTORY_LENGTH)
        robot_positions[robot_id].appendleft((time.time(), pos))

    def get_robot_last_update_time(self, team, robot_id):
        robot_positions = self.get_robot_positions(team)
        if robot_id not in robot_positions:
            # print("getting update time of robot never seen?!?")
            return None
        timestamp, pos = robot_positions[robot_id][0]
        return timestamp

    def is_robot_lost(self, team, robot_id):
        last_update_time = self.get_robot_last_update_time(team, robot_id)
        if last_update_time is None:
            return True
        return time.time() - last_update_time > ROBOT_LOST_TIME

    def get_robot_commands(self, team, robot_id):
        if team == 'blue':
            return self._blue_robot_commands.get(robot_id, RobotCommands())
        else:
            assert(team == 'yellow')
            return self._yellow_robot_commands.get(robot_id, RobotCommands())

    def set_robot_waypoints(self, pos):
        self._ball_position.appendleft((time.time(), pos))

    # ANALYSIS FUNCTIONS
    # basic helper functions - should these be elsewhere?
    def diff_pos(self, p1, p2):
        x = p1[0] - p2[0]
        y = p1[1] - p2[1]

        return (x,y)

    def sum_pos(self, p1, p2):
        x = p1[0] + p2[0]
        y = p1[1] + p2[1]

        return (x,y)

    def magnitude(self, veloc):
        return ((veloc[0] ** 2 + veloc[1] ** 2) ** .5)

    def scale_pos(self, pos, factor):
        return (pos[0] * factor, pos[1] * factor)

    # TODO - calculate based on robot locations and rules
    def is_position_open(self, pos):
        return True

    # Here we find ball velocities from ball position data
    def get_ball_velocity(self):

        prev_velocity = self.ball_velocity

        positions = self._ball_position
        MIN_TIME_INTERVAL = .05
        i = 0
        if len(positions) <= 1:
            return (0, 0)
        # 0 is most recent!!!
        while i < len(positions) - 1 and  positions[0][0] - positions[i][0] < MIN_TIME_INTERVAL:
            i += 1

        delta_pos = self.diff_pos(positions[0][1], positions[i][1])
        delta_time = (positions[0][0] - positions[i][0])

        self.ball_velocity = self.scale_pos(delta_pos, 1 / delta_time)

        return self.ball_velocity


    # TODO: test this function!
    def get_ball_pos_future(self, seconds):
        accel_magnitude = -.5
#This is just a guess at the acceleration due to friction as the ball rolls. This number should be tuned empitically.
        velocity_initial = self.get_ball_velocity()
        accel_direction = self.scale_pos(velocity_initial, 1 / self.magnitude(velocity_initial))
        accel = self.scale_pos(accel_direction, accel_magnitude)
        if accel[0] * seconds + velocity_initial[0] < 0:
            time_to_stop = -1 * velocity_initial[0] / accel[0]
            return self.get_ball_pos_future(time_to_stop)
        predicted_pos_change = self.sum_pos(self.scale_pos(accel, seconds * seconds),
                                            self.scale_pos(velocity_initial, seconds))
        predicted_pos = self.sum_pos(predicted_pos_change, self.get_ball_position())
        return predicted_pos

    #deque([(1572297310.7425327, (1241.970703125, 260.6355285644531)), (1572297310.7324636, (1218.429443359375, 252.887451171875)), (1572297310.7223923, (1218.429443359375, 252.887451171875)), (1572297310.7123, (1182.462158203125, 244.92494201660156)), (1572297310.7022493, (1182.462158203125, 244.92494201660156)), (1572297310.6921763, (1125.27978515625, 245.63365173339844)), (1572297310.6820886, (1125.27978515625, 245.63365173339844)), (1572297310.6720102, (1108.6162109375, 254.580322265625)), (1572297310.6619406, (1108.6162109375, 254.580322265625)), (1572297310.6518548, (1071.0198974609375, 252.38209533691406)), (1572297310.6417856, (1071.0198974609375, 252.38209533691406)), (1572297310.631708, (1025.57373046875, 246.82630920410156)), (1572297310.6216369, (1025.57373046875, 246.82630920410156)), (1572297310.6115437, (985.090087890625, 248.5403594970703)), (1572297310.6014657, (985.090087890625, 248.5403594970703)), (1572297310.59138, (944.6881713867188, 246.3601837158203)), (1572297310.581291, (944.6881713867188, 246.3601837158203)), (1572297310.5712094, (896.4560546875, 241.21607971191406)), (1572297310.5611184, (855.451904296875, 246.6865997314453)), (1572297310.5510397, (855.451904296875, 246.6865997314453))], maxlen=20)

    
