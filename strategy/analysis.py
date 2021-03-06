import numpy as np
import time
from typing import Optional, Tuple, Iterable

# Analysis functions for strategy
class Analysis:
    def get_future_ball_array(self):
        """Samples incrementally to return array of future predicted ball positions"""
        ball_pos = self._gs.get_ball_position()
        # this definition of new_ball_pos guarentees that they are not the same intitally
        new_ball_pos = ball_pos - np.array([1, 1])
        now = time.time()
        t = 0
        delta_t = .1
        future_ball_array = []
        while ((ball_pos != new_ball_pos).any() or t == 0) and self._gs.is_in_play(new_ball_pos):
            # here we make the previously generated point the reference
            ball_pos = new_ball_pos
            new_ball_pos = self._gs.predict_ball_pos(t)
            future_ball_array.append((t + now, new_ball_pos))
            t += delta_t
        return future_ball_array

    def intercept_range(self, robot_id: int
        ) -> Tuple[Tuple[float, float], Tuple[float, float]]:
        """find the range for which a robot can reach the ball in its trajectory

        @return position1, position2:
            returns the positions between which robots can intercept the ball.
            returns None if interception is not possible
        """
        # print(f"start time: {datetime.now()}")
        # variable at the time when the ball first gets within range.
        robot_pos = self._gs.get_robot_position(self._team, robot_id)
        delta_t = .1
        time = 0
        out_of_range = True
        while(out_of_range):
            interception_pos = self._gs.predict_ball_pos(time)
            separation_distance = np.linalg.norm(robot_pos[:2] - interception_pos)
            max_speed = self._gs.robot_max_speed(self._team, robot_id)
            if separation_distance <= time * max_speed:
                first_intercept_point = interception_pos
                if not self._gs.is_in_play(first_intercept_point):
                    return None
                out_of_range = False
            else:
                time += delta_t
        while(not out_of_range):
            # Starting at the time when the ball first got within range.
            interception_pos = self._gs.predict_ball_pos(time)
            separation_distance = np.linalg.norm(robot_pos[:2] - interception_pos)
            max_speed = self._gs.robot_max_speed(self._team, robot_id)
            last_intercept_point = self._gs.predict_ball_pos(time - delta_t)
            # Use opposite criteria to find the end of the window
            cant_reach = (separation_distance > time * max_speed)
            stopped_moving = (last_intercept_point == interception_pos).all()
            in_play = self._gs.is_in_play(interception_pos)
            if cant_reach or stopped_moving or not in_play:
                # we need to subtract delta_t because we found the last
                #print(f"end time: {datetime.now()}")
                return first_intercept_point, last_intercept_point
            else:
                time += delta_t

    def safest_intercept_point(self, robot_id: int) -> Tuple[float, float]:
        """determine the point in the ball's trajectory that the robot can reach
        soonest relative to the ball (even if it's too late)"""
        future_ball_array = self.get_future_ball_array()
        robot_pos = self._gs.get_robot_position(self._team, robot_id)
        max_speed = self._gs.robot_max_speed(self._team, robot_id)
        def buffer_time(data):
            timestamp, ball_pos = data
            ball_travel_time = timestamp - time.time()
            distance_robot_needs_to_travel = np.linalg.norm(ball_pos - robot_pos[:2])
            robot_travel_time = distance_robot_needs_to_travel / max_speed
            return ball_travel_time - robot_travel_time
        if len(future_ball_array) > 0:
            buffer_time, safest_pos = max(future_ball_array, key=buffer_time)
        else:
            # if the ball is not visible, return current position
            safest_pos = robot_pos
        return safest_pos

    def best_kick_pos(self, from_pos: Tuple[float, float], to_pos: Tuple[float, float]) -> Tuple[float, float, float]:
        """determine the best robot position to kick in desired direction"""
        dx, dy = to_pos[:2] - from_pos[:2]
        w = np.arctan2(dy, dx)
        return self._gs.dribbler_to_robot_pos(from_pos, w)

    # TODO: generalize for building walls and stuff
    # TODO: account for attacker orientation?
    def block_goal_center_pos(self, max_distance_from_goal: float, ball_pos=None, team=None):
        """Return position between the ball and the goal, at a particular distance from the goal"""
        if team is None:
            team = self._team
        if ball_pos is None:
            ball_pos = self._gs.get_ball_position()
        if not self._gs.is_in_play(ball_pos):
            return np.array([])
        goal_top, goal_bottom = self._gs.get_defense_goal(team)
        goal_center = (goal_top + goal_bottom) / 2
        ball_distance = np.linalg.norm(ball_pos - goal_center)
        distance_from_goal = min(max_distance_from_goal, ball_distance - self._gs.ROBOT_RADIUS)
        # for now, look at vector from goal center to ball
        goal_to_ball = ball_pos - goal_center
        if not goal_to_ball.any():
            # should never happen, but good to prevent crash, and for debugging
            print('ball is exactly on goal center w0t')
            return np.array([*goal_center, 0])
        angle_to_ball = np.arctan2(goal_to_ball[1], goal_to_ball[0])
        norm_to_ball = goal_to_ball / np.linalg.norm(goal_to_ball)
        x, y = goal_center + norm_to_ball * distance_from_goal
        block_pos = np.array([x, y, angle_to_ball])
        # TODO: THIS IS A HACK TO MAKE IT STAY WITHIN CAMERA RANGE
        # if block_pos[0] > self._gs.FIELD_MAX_X - self._gs.ROBOT_RADIUS * 3 or block_pos[0] < self._gs.FIELD_MIN_X + self._gs.ROBOT_RADIUS * 3:
        #    return np.array([])
        return block_pos

    def is_path_blocked(self, s_pos, g_pos, robot_id, buffer_dist=0):
        "incrementally check a linear path for obstacles"
        s_pos = np.array(s_pos)[:2]
        g_pos = np.array(g_pos)[:2]

        if (g_pos == s_pos).all():
            return False
        # Check endpoint first to avoid worrying about step size in the loop
        if not self._gs.is_position_open(g_pos, self._team, robot_id):
            return True
        path = g_pos - s_pos
        norm_path = path / np.linalg.norm(path)
        STEP_SIZE = self._gs.ROBOT_RADIUS

        # step along the path and check if any points are blocked
        steps = int(np.floor(np.linalg.norm(path) / STEP_SIZE))
        for i in range(1, steps + 1):
            intermediate_pos = s_pos + norm_path * STEP_SIZE * i
            np.append(intermediate_pos, 0)
            if not self._gs.is_position_open(intermediate_pos, self._team, robot_id, buffer_dist):
                return True
        return False

    # generate RRT waypoints
    def RRT_path_find(self, start_pos, goal_pos, robot_id, lim=1000):
        goal_pos = np.array(goal_pos)
        start_pos = np.array(start_pos)
        graph = {tuple(start_pos): []}
        prev = {tuple(start_pos): None}
        cnt = 0
        success = False
        for _ in range(lim):
            # use gamestate.random_position()
            new_pos = np.array(
                [np.random.randint(self._gs.FIELD_MIN_X, self._gs.FIELD_MAX_X),
                 np.random.randint(self._gs.FIELD_MIN_Y, self._gs.FIELD_MAX_Y),
                 0.0])
            if np.random.random() < 0.05:
                new_pos = goal_pos

            if not self._gs.is_position_open(new_pos, self._team, robot_id, buffer_dist=100) \
               or tuple(new_pos) in graph:
                continue

            nearest_pos = self.get_nearest_pos(graph, tuple(new_pos))
            extend_pos = self.extend(nearest_pos, new_pos, robot_id=robot_id)
            if extend_pos is None:
                continue

            graph[tuple(extend_pos)] = [nearest_pos]
            graph[nearest_pos].append(tuple(extend_pos))
            prev[tuple(extend_pos)] = nearest_pos

            if np.linalg.norm(extend_pos[:2] - goal_pos[:2]) < self._gs.ROBOT_RADIUS:
                success = True
                break

            cnt += 1

        if not success:
            return success

        pos = self.get_nearest_pos(graph, goal_pos)  # get nearest position to goal in graph
        path = []
        while not (pos[:2] == start_pos[:2]).all():
            path.append(pos)
            pos = prev[pos]
        path.reverse()

        # Smooth path to reduce zig zagging
        i = 0
        while i < len(path) - 2:
            if not self.is_path_blocked(path[i], path[i+2], robot_id):
                del path[i+1]
                continue
            i += 1

        # Cut out the "dead-weight" waypoints
        for i, pos in enumerate(path):
            if not self.is_path_blocked(pos, goal_pos, robot_id):
                path = path[:i+1]
                break

        self.set_waypoints(robot_id, path + [goal_pos])
        return success

    # RRT helper
    def get_nearest_pos(self, graph, new_pos):
        rtn = None
        min_dist = float('inf')
        for pos in graph:
            dist = np.sqrt((new_pos[0] - pos[0]) ** 2 + (new_pos[1] - pos[1]) ** 2)
            if dist < min_dist:
                min_dist = dist
                rtn = pos
        return rtn

    # RRT helper
    def extend(self, s_pos, g_pos, robot_id=None):
        s_pos = np.array(s_pos)[:2]
        g_pos = np.array(g_pos)[:2]

        if (g_pos == s_pos).all():
            return False

        path = g_pos - s_pos
        norm_path = path / np.linalg.norm(path)
        STEP_SIZE = self._gs.ROBOT_RADIUS

        # step along the path and check if any points are blocked
        poses = [None]
        steps = int(np.floor(np.linalg.norm(path) / STEP_SIZE))
        for i in range(1, steps + 1):
            intermediate_pos = s_pos + norm_path * STEP_SIZE * i
            np.append(intermediate_pos, 0)
            if not self._gs.is_position_open(intermediate_pos, self._team, robot_id, buffer_dist=100):
                break
            if np.linalg.norm(intermediate_pos - s_pos) > 4 * STEP_SIZE:
                break
            poses.append(intermediate_pos)

        return poses[-1]
