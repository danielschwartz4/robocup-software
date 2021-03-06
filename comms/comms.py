import threading
import time
try:
    from radio import Radio
    from robot_commands import RobotCommands
except (SystemError, ImportError):
    from .radio import Radio
    from .robot_commands import RobotCommands


class Comms(object):
    """Comms class spins a thread to repeated send the commands stored in
       gamestate to the robots via radio"""
    def __init__(self, gamestate, team, is_second_comms=False):
        self._gamestate = gamestate
        assert(team in ['blue', 'yellow'])
        self._team = team

        self._is_second_comms = is_second_comms
        self._radio = None

        self._send_loop_sleep = Radio.MESSAGE_DELAY
        self._is_sending = False
        self._sending_thread = None
        self._last_send_loop_time = None

        self._receive_loop_sleep = Radio.MESSAGE_DELAY
        self._is_receiving = False
        self._receiving_thread = None
        # self._messages_received = []
        self._last_receive_loop_time = None

    def die(self):
        if self._radio is not None:
            self._radio.close()

    def start_sending(self, loop_sleep):
        self._loop_sleep = loop_sleep
        if self._loop_sleep < Radio.MESSAGE_DELAY:
            print("WARNING: Comms loop sending faster than Radio can send")
        if self._radio is None:
            self._radio = Radio(self._is_second_comms)
        self._is_sending = True
        self._sending_thread = threading.Thread(target=self.sending_loop)
        # set to daemon mode so it will be easily killed
        self._sending_thread.daemon = True
        self._sending_thread.start()

    def start_receiving(self, loop_sleep):
        self._receive_loop_sleep = loop_sleep
        if self._radio is None:
            self._radio = Radio(self._is_second_comms)
        self._is_receiving = True
        self._receiving_thread = threading.Thread(target=self.receiving_loop)
        # set to daemon mode so it will be easily killed
        self._receiving_thread.daemon = True
        self._receiving_thread.start()

    def sending_loop(self):
        self._gamestate.wait_until_game_begins()
        while self._is_sending:
            delta_time = 0
            if self._last_send_loop_time is not None:
                delta_time = time.time() - self._last_send_loop_time
                if delta_time > self._send_loop_sleep * 3:
                    print("Comms sending loop large delay: " + str(delta_time))
            self._last_send_loop_time = time.time()

            team_commands = self._gamestate.get_team_commands(self._team)
            # send serialized message for whole team
            message = RobotCommands.get_serialized_team_command(team_commands)
            self._radio.send(message)
            for robot_id, commands in team_commands.items():
                # print(commands)
                # simulate charge of capacitors according to commands
                if commands.is_charging:
                    commands.simulate_charge(delta_time)
                # TODO: UNTESTED
                if commands.is_kicking:
                    commands.is_kicking = False
                    commands.charge_level = 0
            # yield to other threads
            time.sleep(self._send_loop_sleep)

    def receiving_loop(self):
        self._gamestate.wait_until_game_begins()
        while self._is_receiving:
            # TODO: save messages for log
            print(self._radio.read())
            # TODO: update relevant data into gamestate
            if self._last_receive_loop_time is not None:
                delta = time.time() - self._last_receive_loop_time
                if delta > .3:
                    print("Comms receiving loop large delay: " + str(delta))
            self._last_receive_loop_time = time.time()
            # yield to other threads
            time.sleep(self._receive_loop_sleep)

    def stop_sending_and_receiving(self):
        if self._is_sending:
            self._is_sending = False
            self._sending_thread.join()
            self._sending_thread = None
        if self._is_receiving:
            self._is_receiving = False
            self._receiving_thread.join()
            self._receiving_thread = None
        self.die()
