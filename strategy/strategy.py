import sys
import threading
import numpy as np
import time
from datetime import datetime
# import gamestate file to use field dimension constants
# (as opposed to importing the class GameState)
sys.path.append('..')
from gamestate import gamestate as gs

# FIELD + ROBOT DIMENSIONS (mm) (HACK FOR NOW)
FIELD_X_LENGTH = 9000
FIELD_Y_LENGTH = 6000
FIELD_MIN_X = -FIELD_X_LENGTH / 2
FIELD_MAX_X = FIELD_X_LENGTH / 2
FIELD_MIN_Y = -FIELD_Y_LENGTH / 2
FIELD_MAX_Y = FIELD_Y_LENGTH / 2
CENTER_CIRCLE_RADIUS = 495
GOAL_WIDTH = 1000
DEFENSE_AREA_X_LENGTH = 1000
DEFENSE_AREA_Y_LENGTH = 2000
BALL_RADIUS = 21
ROBOT_RADIUS = 90


class Strategy(object):
    """Logic for playing the game. Uses data from gamestate to calculate desired
       robot actions, and enters commands into gamestate to be sent by comms"""
    def __init__(self, gamestate, team):
        assert(team in ['blue', 'yellow'])
        self._team = team
        self._gamestate = gamestate

        self._is_controlling = False
        self._control_thread = None
        self._control_loop_sleep = None
        self._last_control_loop_time = None
        self._mode = None

    def start_controlling(self, mode, loop_sleep):
        self._mode = mode
        self._control_loop_sleep = loop_sleep
        self._is_controlling = True
        self._control_thread = threading.Thread(target=self.control_loop)
        # set to daemon mode so it will be easily killed
        self._control_thread.daemon = True
        self._control_thread.start()

    def stop_controlling(self):
        if self._is_controlling:
            self._is_controlling = False
            self._control_thread.join()
            self._control_thread = None

    def control_loop(self):
        # wait until game begins (while other threads are initializing)
        self._gamestate.wait_until_game_begins()
        print("\nRunning strategy for {} team, mode: {}".format(
            self._team, self._mode)
        )
        while self._is_controlling:
            # run the strategy corresponding to the given mode
            if self._mode == "UI":
                self.UI()
            elif self._mode == "entry_video":
                self.entry_video()
            else:
                print('(unrecognized mode, doing nothing)')

            # tell all robots to refresh their speeds based on waypoints
            team_commands = self._gamestate.get_team_commands(self._team)
            team_commands = list(team_commands.items())
            for robot_id, robot_commands in team_commands:
                pos = self._gamestate.get_robot_position(self._team, robot_id)
                # stop the robot if we've lost track of it
                if self._gamestate.is_robot_lost(self._team, robot_id):
                    robot_commands.set_speeds(0, 0, 0)
                else:
                    # recalculate the speed the robot should be commanded at
                    robot_commands.derive_speeds(pos)

            if self._last_control_loop_time is not None:
                delta = time.time() - self._last_control_loop_time
                if delta > self._control_loop_sleep * 3:
                    print("Control loop large delay: " + str(delta))
            self._last_control_loop_time = time.time()
            # yield to other threads
            time.sleep(self._control_loop_sleep)

    # follow the user-input commands through visualizer
    def UI(self):
        gs = self._gamestate
        if gs.user_selected_robot is not None:
            team, robot_id = gs.user_selected_robot
            if team == self._team:
                commands = gs.get_robot_commands(self._team, robot_id)
                # set goal pos to click location on visualization window
                if gs.user_click_position is not None:
                    x, y = gs.user_click_position
                    if gs.user_drag_vector.any():
                        # face the dragged direction
                        dx, dy = gs.user_drag_vector
                        w = np.arctan2(dy, dx)
                    else:
                        w = None
                    robot_pos = self._gamestate.get_robot_position(
                        team, robot_id)
                    goal_pos = np.array([x, y, w])

                    if not any([np.array_equal(goal_pos[:2], wp[:2]) for wp in commands.waypoints]):
                        # self.move_straight(robot_ids[0], np.array(goal_pos))
                        if commands.waypoints:
                            s_pos = commands.waypoints[-1]
                        else:
                            s_pos = robot_pos

                        if self.is_path_blocked(s_pos, goal_pos, robot_id):
                            # TODO: still a little hacky
                            path = self.RRT_path_find(s_pos, goal_pos, robot_id)
                            for p in path:
                                p = np.append(np.array(p), 0)
                                self.append_waypoint(robot_id, p)
                            self.append_waypoint(robot_id, np.array(goal_pos))
                        else:
                            self.append_waypoint(robot_id, np.array(goal_pos))
                # apply other commands
                commands.is_charging = gs.user_charge_command
                commands.is_kicking = gs.user_kick_command
                commands.is_dribbling = gs.user_dribble_command
    def entry_video(self):
        intercept_range = self.intercept_range(0)
        intercept_point = (intercept_range[0]+intercept_range[1])/2
        self.append_waypoint(0, intercept_point)


    # tell specific robot to move straight towards given location
    def move_straight(self, robot_id, goal_pos):
        current_pos = self._gamestate.get_robot_position(self._team, robot_id)
        commands = self._gamestate.get_robot_commands(self._team, robot_id)
        commands.set_waypoints([goal_pos], current_pos)

    def append_waypoint(self, robot_id, goal_pos):
        current_pos = self._gamestate.get_robot_position(self._team, robot_id)
        commands = self._gamestate.get_robot_commands(self._team, robot_id)
        commands.append_waypoint(goal_pos, current_pos)

    def get_ball_interception_point(self, robot_id):
        robot_pos = self._gamestate.get_robot_position(self._team, robot_id)
        delta_t = .05
        time = 0
        while(True):
            interception_pos = self._gamestate.predict_ball_pos(time)
            separation_distance = np.linalg.norm(robot_pos[:2] - interception_pos)
            max_speed = self._gamestate.robot_max_speed(self._team, robot_id)
            if separation_distance <= time * max_speed:
                return interception_pos
            else:
                time += delta_t

    def best_goalie_pos(self):
        ball_pos = self._gamestate.get_ball_position()
        goal_top, goal_bottom = self._gamestate.get_defense_goal(self._team)
        goal_center = (goal_top + goal_bottom) / 2
        # for now, look at vector from goal center to ball
        goal_to_ball = ball_pos - goal_center
        if not goal_to_ball.any():
            # should never happen, but good to prevent crash, and for debugging
            print('ball is exactly on goal center w0t')
            return np.array([*goal_center, 0])
        angle_to_ball = np.arctan2(goal_to_ball[1], goal_to_ball[0])
        norm_to_ball = goal_to_ball / np.linalg.norm(goal_to_ball)
        GOALIE_OFFSET = 600  # goalie stays this far from goal center
        x, y = goal_center + norm_to_ball * GOALIE_OFFSET
        best_pos = np.array([x, y, angle_to_ball])
        return best_pos

    # Return angle (relative to the x axis) for robot to face a position
    def face_pos(self, robot_id, pos):
        robot_pos = self._gamestate_get_robot_position(self._team, robot_id)
        dx, dy = pos - robot_pos
        # use atan2 instead of atan because it takes into account x/y signs
        # to give angle from -pi to pi, instead of limiting to -pi/2 to pi/2
        return np.arctan2(dy, dx)

    def face_ball(self, robot_id):
        return self.face_pos(robot_id, self._gamestate.get_ball_position())

    def is_path_blocked(self, s_pos, g_pos, robot_id=None):
        s_pos = np.array(s_pos)[:2]
        g_pos = np.array(g_pos)[:2]

        if (g_pos == s_pos).all():
            return False
        # Check endpoint first to avoid worrying about step size in the loop
        if not self._gamestate.is_position_open(g_pos, robot_id=robot_id):
            return True
        path = g_pos - s_pos
        norm_path = path / np.linalg.norm(path)
        STEP_SIZE = gs.ROBOT_RADIUS

        # step along the path and check if any points are blocked
        steps = int(np.floor(np.linalg.norm(path) / STEP_SIZE))
        for i in range(1, steps + 1):
            intermediate_pos = s_pos + norm_path * STEP_SIZE * i
            np.append(intermediate_pos, 0)
            if not self._gamestate.is_position_open(intermediate_pos, robot_id=robot_id):
                return True
        return False

    # RRT
    def RRT_path_find(self, start_pos, goal_pos, robot_id, lim=1000):
        goal_pos = np.array(goal_pos)
        start_pos = np.array(start_pos)
        graph = [tuple(start_pos)]
        prev = {tuple(start_pos): None}
        cost = {tuple(start_pos): 0}
        cnt = 0
        while True:
            # use gamestate.random_position()
            new_pos = np.array(
                [np.random.randint(FIELD_MIN_X, FIELD_MAX_X),
                 np.random.randint(FIELD_MIN_Y, FIELD_MAX_Y),
                 0.0])
            if np.random.random() < 0.05:
                new_pos = goal_pos

            if not self._gamestate.is_position_open(new_pos, robot_id=robot_id) or tuple(new_pos) in graph:
                continue

            nearest_pos = self.get_nearest_pos(graph, tuple(new_pos))
            extend_pos, dist = self.extend(nearest_pos, new_pos, robot_id=robot_id)
            if extend_pos is None:
                continue

            # graph.append(tuple(extend_pos[:2]))
            # cost[tuple(extend_pos[:2])] = cost[nearest_pos] + dist
            # prev[tuple(extend_pos[:2])] = nearest_pos

            # self.rewire(graph, cost, prev, extend_pos[:2])
            graph.append(tuple(extend_pos[:2]))
            # BUG: nearest neighbors is the problem! What if nothing is that close?

            if np.linalg.norm(extend_pos[:2] - goal_pos[:2]) < gs.ROBOT_RADIUS:
                break

            cnt += 1

        pos = self.get_nearest_pos(graph, goal_pos)  # get nearest position to goal in graph
        path = []
        print('pos: {}'.format(pos))
        print('start_pos: {}'.format(start_pos))
        while not list(pos[:2]) == list(start_pos[:2]):
            path.append(pos)
            pos = prev[pos]
            print('new pos: {}'.format(pos))
        path.reverse()
        return path

    def rewire(self, graph, cost, prev, pos, d=gs.ROBOT_RADIUS * 4):
        neighbors = self.get_nearest_neighbors(graph, pos)

        # Find s_min
        s_min = (None, float('inf'))
        for node in neighbors:
            total_cost = cost[node] + np.linalg.norm(
                np.array(pos[:2]) - np.array(node[:2]))
            if total_cost < s_min[1]:
                s_min = (node, total_cost)

        # Add edge s_min --> pos
        graph.append(tuple(pos[:2]))
        prev[tuple(pos[:2])] = s_min[0]
        cost[tuple(pos[:2])] = s_min[1]

        # Rewire other neighbors
        for node in neighbors:
            new_cost = cost[tuple(pos[:2])] + np.linalg.norm(
                np.array(pos[:2]) - np.array(node[:2]))
            if new_cost < cost[node]:
                # rewire
                cost[node] = new_cost
                prev[node] = tuple(pos[:2])

    def get_nearest_neighbors(self, graph, pos, d=gs.ROBOT_RADIUS * 4):
        neighbors = []
        for node in graph:
            dist = np.linalg.norm(np.array(node[:2]) - np.array(pos[:2]))
            if dist < d:
                neighbors.append(node)
        return neighbors

    def get_nearest_pos(self, graph, new_pos):
        rtn = None
        min_dist = float('inf')
        for pos in graph:
            dist = np.linalg.norm(np.array(new_pos[:2]) - np.array(pos[:2]))
            if dist < min_dist:
                min_dist = dist
                rtn = pos
        return rtn

    def extend(self, s_pos, g_pos, robot_id=None):
        s_pos = np.array(s_pos)[:2]
        g_pos = np.array(g_pos)[:2]

        if (g_pos == s_pos).all():
            return False

        path = g_pos - s_pos
        norm_path = path / np.linalg.norm(path)
        STEP_SIZE = gs.ROBOT_RADIUS

        # step along the path and check if any points are blocked
        poses = [None]
        steps = int(np.floor(np.linalg.norm(path) / STEP_SIZE))
        for i in range(1, steps + 1):
            intermediate_pos = s_pos + norm_path * STEP_SIZE * i
            np.append(intermediate_pos, 0)
            if not self._gamestate.is_position_open(intermediate_pos, robot_id=robot_id):
                break
            if np.linalg.norm(intermediate_pos - s_pos) > 4 * STEP_SIZE:
                break
            poses.append(intermediate_pos)

        dist = None
        if poses[-1] is not None:
            dist = np.linalg.norm(poses[-1] - s_pos)
        return poses[-1], dist

# determine best robot position (including rotation) to shoot or pass ie kick given the position or desired future location
# of the ball (x,y) after the kick and before the kick (self, from_pos, to_pos)
# determine location of robot including rotation to recieve as pass given
# rotation to recieve the ball in current pos
# go to the ball (and rotate to receive it)
# assume the ball has already been kicked. add a buffer... "relax" parameter
# min and max distance to ball path while still intercept
# posssible interception points (first_intercept_point, last_intercept point) ****important edge case: ball stops in range
# determine best robot position (including rotation) to shoot or pass ie kick given the position or desired future location
# of the ball (x,y) after the kick and before the kick (self, from_pos, to_pos)
    def best_kick_pos(self, from_pos, to_pos):
        x = from_pos[0]
        y = from_pos[1]
        dx, dy = to_pos - from_pos
        w = np.arctan2(dy, dx)
        return self._gamestate.dribbler_to_robot_pos(from_pos, w)

    def intercept_range(self, robot_id):
        #print(f"start time: {datetime.now()}")
        #first_intercept_point = self.get_ball_interception_point(robot_id)
        # Now we find the last possible interception point.
        # We start with code very much like get_ball_interception_point so that we can start our time
        # variable at the time when the ball first gets within range.
        robot_pos = self._gamestate.get_robot_position(self._team, robot_id)
        delta_t = .1
        time = 0
        out_of_range = True
        while(out_of_range):
            interception_pos = self._gamestate.predict_ball_pos(time)
            separation_distance = np.linalg.norm(robot_pos[:2] - interception_pos)
            max_speed = self._gamestate.robot_max_speed(self._team, robot_id)
            if separation_distance <= time * max_speed:
                first_intercept_point = interception_pos
                if not self._gamestate.is_in_play(first_intercept_point):
                    return None
                out_of_range = False
            else:
                time += delta_t
        while(not out_of_range):
            # Note that time is starting at the time when the ball first got within range.
            interception_pos = self._gamestate.predict_ball_pos(time)
            separation_distance = np.linalg.norm(robot_pos[:2] - interception_pos)
            max_speed = self._gamestate.robot_max_speed(self._team, robot_id)
            last_intercept_point = self._gamestate.predict_ball_pos(time - delta_t)
            # We have the opposite criteria to find the end of the window than the beginning.
            cant_reach = (separation_distance > time * max_speed)
            stopped_moving = (last_intercept_point == interception_pos).all()
            in_play = self._gamestate.is_in_play(interception_pos)
            if cant_reach or stopped_moving or not in_play:
                # we need to subtract delta_t because we found the last
                #print(f"end time: {datetime.now()}")
                return first_intercept_point, last_intercept_point
            else:
                time += delta_t
