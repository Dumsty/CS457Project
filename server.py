"""Tic-Tac-Toe game server (CS 457).

Usage:
    python3 server.py -h
    python3 server.py -p 8080

Design documents this file implements:
  - Protocol Blueprint: messages, framing, error codes, termination rules.
  - FSM Specification: the 8 server states and their transitions.
  - Concurrency Design: one worker thread per client, one shared Room, one
    threading.Lock. recv() and framing run outside the lock; validation,
    state changes and sends run inside it.
"""

import argparse
import logging
import random
import socket
import sys
import threading

import protocol as p

# --- FSM specification: the 8 states ---
INIT = "INIT"
WAITING_FOR_PLAYERS = "WAITING_FOR_PLAYERS"
GAME_START = "GAME_START"
PLAYER_TURN = "PLAYER_TURN"
EVALUATE_MOVE = "EVALUATE_MOVE"
CHECK_WIN_DRAW = "CHECK_WIN_DRAW"
GAME_OVER = "GAME_OVER"
CLEANUP = "CLEANUP"

RECV_SIZE = 4096
MIN_PORT, MAX_PORT = 1024, 65535

# Errors that mean the peer is gone (blueprint section 6). OSError also covers
# a socket that was shut down by another thread during CLEANUP.
DISCONNECT_ERRORS = (ConnectionResetError, ConnectionAbortedError,
                     BrokenPipeError, TimeoutError, OSError)

log = logging.getLogger("server")

# Concurrency design section 1: one lock protects everything in the Room.
lock = threading.Lock()


class Player:
    """One registered client."""

    def __init__(self, sock, alias, player_id, symbol):
        self.sock = sock
        self.alias = alias
        self.player_id = player_id      # "Player_1" or "Player_2"
        self.symbol = symbol            # "X" or "O"
        self.started = False            # True once GAME_START was sent

    def expected_id(self):
        """The player_id this client must send (blueprint section 3).

        The alias is used until GAME_START assigns Player_1 / Player_2.
        """
        return self.player_id if self.started else self.alias


class Room:
    """All shared game data. Only touch it while holding `lock`."""

    def __init__(self):
        self.state = INIT
        self.players = {}               # socket -> Player
        self.board = p.new_board()
        self.turn = None                # "Player_1" or "Player_2"
        self.moves = 0
        self.scores = {p.PLAYER_1: 0, p.PLAYER_2: 0}

    def set_state(self, new_state):
        """Change FSM state and log the transition."""
        log.info("[FSM] %s -> %s", self.state, new_state)
        self.state = new_state

    def reset(self):
        """CLEANUP: clear the room for the next game."""
        self.players = {}
        self.board = p.new_board()
        self.turn = None
        self.moves = 0
        self.scores = {p.PLAYER_1: 0, p.PLAYER_2: 0}

    def by_id(self, player_id):
        """Return the Player with this id, or None."""
        for player in self.players.values():
            if player.player_id == player_id:
                return player
        return None


# --- Sending (caller holds the lock) ---

def hang_up(sock):
    """Shut a socket down so its worker's recv() wakes up with b"".

    Each worker closes its own socket when it exits, so a socket is never
    closed while another thread is still blocked in recv() on it.
    """
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass  # already shut down or never connected


def send(sock, msg_type, payload):
    """Send one message with sendall().

    Blueprint section 6, rule 3: a failed send counts as a disconnect. The
    socket is shut down, so its worker sees b"" and runs player_left() after
    the current critical section is finished (concurrency design section 3).
    """
    try:
        sock.sendall(p.encode_message(msg_type, p.SERVER_ID, payload))
        return True
    except DISCONNECT_ERRORS as exc:
        log.warning("Send of %s failed (%s); treating as disconnect",
                    msg_type, exc)
        hang_up(sock)
        return False


def send_error(sock, code, text):
    """Send ERROR to one client only. Game state does not change."""
    log.info("ERROR %s -> client: %s", code, text)
    send(sock, p.ERROR, {"code": code, "message": text})


def broadcast(room, msg_type, payload):
    """Send the same message to every connected player, in one lock hold."""
    for player in list(room.players.values()):
        send(player.sock, msg_type, payload)


def board_copy(room):
    """A copy of the board, so later moves do not change a built message."""
    return [row[:] for row in room.board]


def state_update(room):
    """Broadcast STATE_UPDATE with the board and whose turn is next."""
    broadcast(room, p.STATE_UPDATE,
              {"board": board_copy(room), "next_turn": room.turn})


# --- FSM actions (caller holds the lock) ---

def register(room, sock, message):
    """WAITING_FOR_PLAYERS: handle a CONNECT from a new client."""
    alias = message["player_id"]
    if room.state != WAITING_FOR_PLAYERS or len(room.players) >= 2:
        # Concurrency design section 2, step 6: room full. The blueprint has
        # no room-full code, so MALFORMED_MESSAGE is reused.
        send_error(sock, p.MALFORMED_MESSAGE,
                   "The game is full. Try again later.")
        hang_up(sock)
        return

    if not room.players:
        player = Player(sock, alias, p.PLAYER_1, "X")
        room.players[sock] = player
        log.info("'%s' joined as %s (X)", alias, p.PLAYER_1)
        send(sock, p.LOBBY_WAIT,
             {"message": "Waiting for Player_2 to join..."})
        return

    # Second CONNECT: this player is Player_2 (O) and the game starts.
    player = Player(sock, alias, p.PLAYER_2, "O")
    room.players[sock] = player
    log.info("'%s' joined as %s (%s)", alias, player.player_id, player.symbol)
    start_game(room)


def start_game(room):
    """GAME_START: pick first turn, send GAME_START and STATE_UPDATE."""
    room.set_state(GAME_START)
    room.turn = random.choice([p.PLAYER_1, p.PLAYER_2])
    log.info("First turn: %s", room.turn)
    for player in list(room.players.values()):
        player.started = True
        send(player.sock, p.GAME_START, {
            "assigned_id": player.player_id,
            "symbol": player.symbol,
            "first_turn": room.turn,
        })
    room.set_state(PLAYER_TURN)
    state_update(room)


def handle_move(room, player, message):
    """PLAYER_TURN -> EVALUATE_MOVE -> CHECK_WIN_DRAW (FSM sections 3-4)."""
    room.set_state(EVALUATE_MOVE)

    # Check 2: is it the sender's turn? Checked before anything is written.
    if player.player_id != room.turn:
        send_error(player.sock, p.OUT_OF_TURN, f"It is {room.turn}'s turn")
        room.set_state(PLAYER_TURN)
        return

    # Check 3: row/col in range and the cell is empty.
    try:
        row, col = p.validate_move(message["payload"], room.board)
    except p.InvalidMove as exc:
        send_error(player.sock, p.INVALID_MOVE, str(exc))
        room.set_state(PLAYER_TURN)
        return

    room.board[row][col] = player.symbol
    room.moves += 1
    log.info("%s (%s) played (%d, %d)", player.player_id, player.symbol,
             row, col)
    room.set_state(CHECK_WIN_DRAW)

    # A win is checked first, so a ninth move that completes a line is a win.
    if p.has_won(room.board, player.symbol):
        end_game(room, "WIN", player.player_id)
    elif room.moves == p.BOARD_SIZE * p.BOARD_SIZE:
        end_game(room, "DRAW", None)
    else:
        room.turn = p.PLAYER_2 if room.turn == p.PLAYER_1 else p.PLAYER_1
        room.set_state(PLAYER_TURN)
        state_update(room)


def end_game(room, outcome, winner):
    """GAME_OVER then CLEANUP. Runs once per game (concurrency section 3)."""
    if room.state in (GAME_OVER, CLEANUP):
        return
    room.set_state(GAME_OVER)
    if winner is not None:
        room.scores[winner] += 1
    log.info("Game over: %s, winner=%s, scores=%s", outcome, winner,
             room.scores)
    # One message, built once, sent to every player still connected.
    broadcast(room, p.GAME_OVER, {
        "outcome": outcome,
        "winner": winner,
        "board": board_copy(room),
        "scores": dict(room.scores),
    })
    cleanup(room)


def cleanup(room):
    """CLEANUP: shut both sockets, reset the room, wait for new players."""
    room.set_state(CLEANUP)
    for sock in list(room.players):
        hang_up(sock)
    room.reset()
    room.set_state(WAITING_FOR_PLAYERS)


def player_left(room, sock):
    """CLIENT_DISCONNECTED event (FSM specification section 5).

    Does nothing if `sock` is not in the current room, so a worker that wakes
    up late after CLEANUP cannot change a new game.
    """
    player = room.players.get(sock)
    if player is None:
        return
    log.info("%s ('%s') disconnected", player.player_id, player.alias)
    del room.players[sock]
    hang_up(sock)

    if room.state == WAITING_FOR_PLAYERS:
        # Lobby: remove the player and keep waiting.
        return
    if room.state in (GAME_OVER, CLEANUP):
        return

    # Mid-game (PLAYER_TURN; the other states finish inside one lock hold
    # before a disconnect can be handled). The player who stayed wins.
    remaining = next(iter(room.players.values()), None)
    if remaining is None:
        # Both players gone: send nothing and clean up.
        cleanup(room)
        return
    end_game(room, "FORFEIT", remaining.player_id)


def handle_message(room, sock, line):
    """Route one framed line. Caller holds the lock.

    Check order follows FSM specification section 3: well formed first,
    then turn, then the move itself.
    """
    try:
        message = p.decode_message(line)
    except p.MalformedMessage as exc:
        send_error(sock, p.MALFORMED_MESSAGE, str(exc))
        return

    msg_type = message["msg_type"]
    player = room.players.get(sock)

    if msg_type not in p.CLIENT_MESSAGE_TYPES:
        send_error(sock, p.MALFORMED_MESSAGE,
                   f"{msg_type} is a server-only message")
        return

    if player is None:
        # Not registered yet: the only allowed message is CONNECT.
        if msg_type == p.CONNECT and message["player_id"]:
            register(room, sock, message)
        elif msg_type == p.DISCONNECT:
            hang_up(sock)
        else:
            send_error(sock, p.MALFORMED_MESSAGE,
                       "Send CONNECT with your alias first")
        return

    if message["player_id"] != player.expected_id():
        send_error(sock, p.MALFORMED_MESSAGE,
                   f"Wrong player_id; expected '{player.expected_id()}'")
        return

    if msg_type == p.DISCONNECT:
        player_left(room, sock)
    elif msg_type == p.MOVE and room.state == PLAYER_TURN:
        handle_move(room, player, message)
    else:
        send_error(sock, p.MALFORMED_MESSAGE,
                   f"{msg_type} is not allowed right now")


# --- Threads ---

def handle_client(sock, addr, room):
    """Worker thread: one per client (concurrency design section 3)."""
    reader = p.LineReader()             # private to this worker, no lock
    log.info("Connection from %s:%d", *addr)
    try:
        while True:
            try:
                data = sock.recv(RECV_SIZE)     # blocks outside the lock
            except DISCONNECT_ERRORS:
                data = b""                      # error == peer closed
            if data == b"":                     # EOF: stop reading
                with lock:
                    player_left(room, sock)
                return
            for line in reader.feed(data):
                with lock:
                    handle_message(room, sock, line)
    finally:
        sock.close()
        log.info("Closed connection from %s:%d", *addr)


def port_number(text):
    """argparse type: an integer port from 1024 to 65535."""
    try:
        port = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"port must be a number, got '{text}'") from None
    if not MIN_PORT <= port <= MAX_PORT:
        raise argparse.ArgumentTypeError(
            f"port must be between {MIN_PORT} and {MAX_PORT}, got {port}")
    return port


def parse_args(argv=None):
    """Parse -h and -p (Sprint 3 section 2.1)."""
    parser = argparse.ArgumentParser(
        prog="server.py",
        description="Tic-Tac-Toe game server. Listens on 0.0.0.0 and runs "
                    "one 2-player game at a time.",
        epilog="example: python3 server.py -p 8080",
    )
    parser.add_argument("-p", "--port", type=port_number, required=True,
                        help=f"listening port ({MIN_PORT}-{MAX_PORT})")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(message)s",
                        datefmt="%H:%M:%S")

    room = Room()                       # INIT: create the room...
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        listener.bind(("0.0.0.0", args.port))   # ...and listen on the port
        listener.listen()
    except OSError as exc:
        print(f"[ERROR] Cannot listen on port {args.port}: {exc}")
        sys.exit(1)

    log.info("Server listening on 0.0.0.0:%d", args.port)
    with lock:
        room.set_state(WAITING_FOR_PLAYERS)

    try:
        while True:                     # main thread only accepts
            sock, addr = listener.accept()
            worker = threading.Thread(target=handle_client,
                                      args=(sock, addr, room), daemon=True)
            worker.start()
    except KeyboardInterrupt:
        log.info("Shutting down")
    finally:
        listener.close()


if __name__ == "__main__":
    main()
