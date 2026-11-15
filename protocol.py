"""Wire protocol for the CS 457 Tic-Tac-Toe game.

Implements the Protocol Blueprint exactly:
  - Section 2 (Framing): one compact JSON object per line, UTF-8, ending in
    "\\n". The receiver keeps a per-connection buffer (LineReader).
  - Section 3 (Message Format): every message has msg_type, player_id,
    payload and timestamp.
  - Section 4 (Message Types): exactly 8 message types.
  - Section 5 (Error Codes): exactly 3 error codes.

This module has no sockets in it, so the client and the server share it and
it can be unit tested on its own.
"""

import json
import time

# --- Blueprint section 4: the 8 message types ---
CONNECT = "CONNECT"
LOBBY_WAIT = "LOBBY_WAIT"
GAME_START = "GAME_START"
MOVE = "MOVE"
STATE_UPDATE = "STATE_UPDATE"
ERROR = "ERROR"
DISCONNECT = "DISCONNECT"
GAME_OVER = "GAME_OVER"

MESSAGE_TYPES = frozenset({
    CONNECT, LOBBY_WAIT, GAME_START, MOVE,
    STATE_UPDATE, ERROR, DISCONNECT, GAME_OVER,
})

# Types a client may send (blueprint section 4, "Direction" column).
CLIENT_MESSAGE_TYPES = frozenset({CONNECT, MOVE, DISCONNECT})

# --- Blueprint section 5: the 3 error codes ---
MALFORMED_MESSAGE = "MALFORMED_MESSAGE"
OUT_OF_TURN = "OUT_OF_TURN"
INVALID_MOVE = "INVALID_MOVE"

# --- Blueprint section 3: identifiers and board cells ---
SERVER_ID = "SERVER"
PLAYER_1 = "Player_1"
PLAYER_2 = "Player_2"
EMPTY = "-"
BOARD_SIZE = 3

# Blueprint section 3: the four fields and their JSON types.
_REQUIRED_FIELDS = {
    "msg_type": str,
    "player_id": str,
    "payload": dict,
    "timestamp": int,
}


class MalformedMessage(Exception):
    """Raised when a line is not a valid protocol message."""


class InvalidMove(Exception):
    """Raised when a MOVE payload is out of range or the cell is taken."""


# --- Blueprint section 2: framing ---

def encode_message(msg_type, player_id, payload):
    """Build one wire message: compact JSON + "\\n", encoded as UTF-8.

    Blueprint sections 2 (rule 1) and 3. The timestamp is Unix seconds.
    """
    message = {
        "msg_type": msg_type,
        "player_id": player_id,
        "payload": payload,
        "timestamp": int(time.time()),
    }
    # separators=(",", ":") gives compact JSON with no spaces.
    text = json.dumps(message, separators=(",", ":"))
    return (text + "\n").encode("utf-8")


class LineReader:
    """Per-connection receive buffer (blueprint section 2, rule 2).

    TCP is a byte stream, so one recv() can hold part of a message, one
    message, or several. feed() appends each chunk and returns every complete
    line. Bytes after the last newline stay in the buffer for the next call.
    """

    def __init__(self):
        self._buffer = b""

    def feed(self, chunk):
        """Add a recv() chunk; return every complete line (no "\\n")."""
        self._buffer += chunk
        lines = []
        while b"\n" in self._buffer:
            line, self._buffer = self._buffer.split(b"\n", 1)
            lines.append(line)
        return lines


# --- Blueprint section 3: parsing ---

def decode_message(line):
    """Parse one line into a message dict.

    Checks UTF-8, JSON, the 4 required fields, their types, and that msg_type
    is one of the 8 message types. Raises MalformedMessage on any failure.
    Payload contents are checked later by the code that handles each type.
    """
    try:
        message = json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise MalformedMessage(f"Bad JSON: {exc}") from None

    if not isinstance(message, dict):
        raise MalformedMessage("Message must be a JSON object")

    for field, expected_type in _REQUIRED_FIELDS.items():
        if field not in message:
            raise MalformedMessage(f"Missing field '{field}'")
        value = message[field]
        # bool is a subclass of int in Python, so reject it explicitly.
        if isinstance(value, bool) or not isinstance(value, expected_type):
            raise MalformedMessage(
                f"Field '{field}' must be {expected_type.__name__}")

    if message["msg_type"] not in MESSAGE_TYPES:
        raise MalformedMessage(f"Unknown msg_type '{message['msg_type']}'")

    return message


# --- FSM specification section 3: move validation ---

def new_board():
    """Return an empty 3x3 board, indexed board[row][col]."""
    return [[EMPTY] * BOARD_SIZE for _ in range(BOARD_SIZE)]


def _is_cell_index(value):
    """True if value is an int (not bool) from 0 to 2."""
    return (isinstance(value, int) and not isinstance(value, bool)
            and 0 <= value < BOARD_SIZE)


def validate_move(payload, board):
    """Return (row, col) for a legal move, or raise InvalidMove.

    A move is legal when row and col are integers (not bool) from 0 to 2 and
    the cell is still "-". Missing row or col is also INVALID_MOVE
    (FSM specification section 4).
    """
    row = payload.get("row")
    col = payload.get("col")
    if not (_is_cell_index(row) and _is_cell_index(col)):
        raise InvalidMove("row and col must be integers from 0 to 2")
    if board[row][col] != EMPTY:
        raise InvalidMove(f"Cell ({row}, {col}) is already taken")
    return row, col


def has_won(board, symbol):
    """True if symbol fills a full row, column, or diagonal."""
    lines = []
    lines.extend(board)                                          # rows
    lines.extend([board[r][c] for r in range(BOARD_SIZE)]
                 for c in range(BOARD_SIZE))                     # columns
    lines.append([board[i][i] for i in range(BOARD_SIZE)])       # diagonal
    lines.append([board[i][BOARD_SIZE - 1 - i]
                  for i in range(BOARD_SIZE)])                   # anti-diag
    return any(all(cell == symbol for cell in line) for line in lines)
