"""Tic-Tac-Toe game client (CS 457).

Usage:
    python3 client.py -h
    python3 client.py -i server.headington.edu -p 8080

The server holds all game state (Protocol Blueprint section 1). This client
sends CONNECT, MOVE and DISCONNECT, and draws whatever the server sends.

Threads:
  - The main thread reads from the socket and prints the board.
  - A daemon thread reads the keyboard and sends moves. When the game ends,
    the main thread returns and the daemon thread stops with the program.
"""

import argparse
import socket
import sys
import threading

import protocol as p

RECV_SIZE = 4096
CONNECT_TIMEOUT = 10        # seconds to wait for the TCP handshake
MIN_PORT, MAX_PORT = 1024, 65535
QUIT_WORDS = {"q", "quit", "exit"}

# Errors that mean the connection is gone (blueprint section 6).
DISCONNECT_ERRORS = (ConnectionResetError, ConnectionAbortedError,
                     BrokenPipeError, TimeoutError, OSError)

print_lock = threading.Lock()   # keeps lines from the two threads apart


def say(*lines):
    """Print lines from either thread without them getting mixed up."""
    with print_lock:
        for line in lines:
            print(line)
        sys.stdout.flush()


class Game:
    """What this client knows. Updated only from server messages."""

    def __init__(self, alias):
        self.alias = alias
        self.my_id = None           # set by GAME_START
        self.symbol = None
        self.board = p.new_board()
        self.next_turn = None
        self.over = False
        self.lock = threading.Lock()

    def player_id(self):
        """Alias until GAME_START, then the assigned id (blueprint sec. 3)."""
        with self.lock:
            return self.my_id or self.alias

    def label(self, player_id):
        """'Player_1 (X, you)' style label for display."""
        symbol = "X" if player_id == p.PLAYER_1 else "O"
        you = ", you" if player_id == self.my_id else ""
        return f"{player_id} ({symbol}{you})"


# --- Display ---

def render_board(board):
    """Return the board as text lines, with row and column numbers."""
    lines = ["", "       0   1   2"]
    for r, row in enumerate(board):
        cells = [" " if c == p.EMPTY else c for c in row]
        lines.append(f"   {r}   " + " | ".join(cells))
        if r < p.BOARD_SIZE - 1:
            lines.append("      ---+---+---")
    lines.append("")
    return lines


def turn_status(game):
    """'Your Turn' or 'Waiting for ...' line."""
    if game.next_turn == game.my_id:
        return (f">>> Your turn ({game.symbol}). "
                "Enter row and column, e.g. '1 2' (q to quit):")
    return f"... Waiting for {game.label(game.next_turn)} to move..."


def show_game_over(game, payload):
    """Print the final board, the result and the score summary."""
    outcome = payload.get("outcome")
    winner = payload.get("winner")
    scores = payload.get("scores", {})

    if outcome == "DRAW":
        result = "It's a draw!"
    elif outcome == "FORFEIT":
        result = ("Your opponent left. You win by forfeit!"
                  if winner == game.my_id
                  else f"{game.label(winner)} wins by forfeit.")
    elif winner == game.my_id:
        result = "You win!"
    else:
        result = f"{game.label(winner)} wins. Better luck next time."

    score_line = "Score: " + "  |  ".join(
        f"{game.label(pid)}: {scores.get(pid, 0)}"
        for pid in (p.PLAYER_1, p.PLAYER_2))

    say(*render_board(payload.get("board", game.board)),
        "=" * 40, f"GAME OVER - {result}", score_line, "=" * 40)


# --- Receiving (main thread) ---

def handle_server_message(game, message):
    """Update the view for one message from the server."""
    msg_type = message["msg_type"]
    payload = message["payload"]

    if msg_type == p.LOBBY_WAIT:
        say(f"[LOBBY] {payload.get('message', 'Waiting for an opponent')}")

    elif msg_type == p.GAME_START:
        with game.lock:
            game.my_id = payload["assigned_id"]
            game.symbol = payload["symbol"]
        first = payload["first_turn"]
        first_line = ("You move first." if first == game.my_id
                      else f"{game.label(first)} moves first.")
        say("", "=" * 40,
            f"GAME START - you are {game.my_id} ({game.symbol})",
            first_line, "=" * 40)

    elif msg_type == p.STATE_UPDATE:
        with game.lock:
            game.board = payload["board"]
            game.next_turn = payload["next_turn"]
        say(*render_board(game.board), turn_status(game))

    elif msg_type == p.ERROR:
        say(f"[ERROR] {payload.get('code')}: {payload.get('message')}")
        if game.my_id and game.next_turn == game.my_id:
            say(turn_status(game))

    elif msg_type == p.GAME_OVER:
        game.over = True
        show_game_over(game, payload)

    else:
        # CONNECT / MOVE / DISCONNECT only travel client -> server.
        say(f"[WARN] Unexpected {msg_type} from server; ignored.")


def receive_loop(sock, game):
    """Read the socket until the server closes it (main thread)."""
    reader = p.LineReader()             # per-connection buffer
    while True:
        try:
            data = sock.recv(RECV_SIZE)
        except DISCONNECT_ERRORS:
            data = b""
        if data == b"":                 # EOF: the server closed
            return
        for line in reader.feed(data):
            try:
                message = p.decode_message(line)
            except p.MalformedMessage as exc:
                say(f"[WARN] Bad message from server ignored: {exc}")
                continue
            handle_server_message(game, message)


# --- Sending (keyboard thread) ---

def send(sock, msg_type, player_id, payload):
    """sendall() one message. Returns False if the connection is gone."""
    try:
        sock.sendall(p.encode_message(msg_type, player_id, payload))
        return True
    except DISCONNECT_ERRORS:
        say("[ERROR] Lost connection to the server.")
        return False


def disconnect(sock, game):
    """Send DISCONNECT, then close our side so the main thread wakes up."""
    send(sock, p.DISCONNECT, game.player_id(), {})
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass


def parse_move(text):
    """Turn '1 2' or '1,2' into (1, 2). Returns None if it is not 2 ints.

    Range and empty-cell checks are left to the server, which owns the
    board (it replies INVALID_MOVE).
    """
    parts = text.replace(",", " ").split()
    if len(parts) != 2:
        return None
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return None


def input_loop(sock, game):
    """Read moves from the keyboard and send them (daemon thread)."""
    for raw in sys.stdin:
        text = raw.strip().lower()
        if not text:
            continue
        if text in QUIT_WORDS:
            say("Leaving the game...")
            disconnect(sock, game)
            return
        if game.my_id is None:
            say("The game has not started yet. Waiting for an opponent...")
            continue
        move = parse_move(text)
        if move is None:
            say("Please type two numbers: row then column, e.g. '0 2'.")
            continue
        row, col = move
        if not send(sock, p.MOVE, game.player_id(), {"row": row, "col": col}):
            return
    # stdin closed (Ctrl+D): leave the game cleanly.
    disconnect(sock, game)


# --- Startup ---

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
    """Parse -h, -i and -p (Sprint 3 section 2.1)."""
    parser = argparse.ArgumentParser(
        prog="client.py",
        description="Tic-Tac-Toe client. Connects to the game server and "
                    "lets you play from the console.",
        epilog="example: python3 client.py -i server.headington.edu -p 8080",
    )
    parser.add_argument("-i", "--hostname", required=True,
                        help="server DNS hostname (or IP address)")
    parser.add_argument("-p", "--port", type=port_number, required=True,
                        help=f"server port ({MIN_PORT}-{MAX_PORT})")
    return parser.parse_args(argv)


def resolve(hostname):
    """Resolve the server hostname via DNS (Sprint 3 section 2.2).

    Exits with status 1 and a friendly message if DNS fails.
    """
    try:
        return socket.gethostbyname(hostname)
    except (socket.gaierror, UnicodeError):
        print(f"[ERROR] Unable to resolve DNS hostname '{hostname}'.")
        print("Please check your Cisco router DNS configuration "
              "('ip dns server' / 'ip host') or verify domain spelling.")
        sys.exit(1)


def connect(server_ip, port, hostname):
    """Open the TCP connection, or exit with status 1 on failure."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(CONNECT_TIMEOUT)
    try:
        sock.connect((server_ip, port))
    except (ConnectionRefusedError, TimeoutError, OSError) as exc:
        print(f"[ERROR] Could not connect to {hostname} ({server_ip}) "
              f"on port {port}: {exc}")
        print("Make sure server.py is running and the port is correct.")
        sock.close()
        sys.exit(1)
    sock.settimeout(None)               # blocking from now on
    return sock


def ask_alias():
    """Ask for a display name to send in CONNECT."""
    try:
        alias = input("Enter your name: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(0)
    return alias or "Player"


def main(argv=None):
    args = parse_args(argv)
    server_ip = resolve(args.hostname)
    print(f"Resolved {args.hostname} -> {server_ip}")

    alias = ask_alias()
    sock = connect(server_ip, args.port, args.hostname)
    print(f"Connected to {args.hostname}:{args.port}")

    game = Game(alias)
    if not send(sock, p.CONNECT, alias, {}):
        sys.exit(1)

    threading.Thread(target=input_loop, args=(sock, game),
                     daemon=True).start()
    try:
        receive_loop(sock, game)
    except KeyboardInterrupt:
        say("", "Leaving the game...")
        disconnect(sock, game)
    finally:
        sock.close()

    if not game.over:
        say("Disconnected from the server.")
    sys.exit(0)


if __name__ == "__main__":
    main()
