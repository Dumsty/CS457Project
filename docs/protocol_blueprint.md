# Protocol Blueprint: Tic-Tac-Toe

**Student:** Dustin Headington | **Course:** CS 457

## 1. Overview

- **Transport:** TCP
- **Serialization:** JSON (UTF-8)
- **Framing:** newline-delimited (`\n`)
- The server holds the game state. Clients send moves and show what the server sends.
- The first client to connect is `Player_1` (`X`) and the second is `Player_2` (`O`). The server picks who moves first at random.
- Cells are `row` and `col`, each 0 to 2. Row 0 is the top row and col 0 is the left column.

## 2. Framing

TCP is a byte stream, so one `recv()` is not one message. Messages end with `\n`.

**Rules**

1. One message is one compact JSON object on one line, ending in `\n` (0x0A).
2. The receiver adds each `recv()` chunk to a per-connection buffer and removes every complete line. Leftover bytes stay in the buffer.
3. Send messages with `sendall()`.

**Receiver logic**

```text
buffer += chunk
while "\n" in buffer:
    line, buffer = buffer.split("\n", 1)
    handle(line)
```

**Wire examples** (`⏎` marks a `\n` byte)

Two messages in one `recv()`:

```text
{"msg_type":"CONNECT","player_id":"Alice","payload":{},"timestamp":1790985600}⏎{"msg_type":"DISCONNECT","player_id":"Alice","payload":{},"timestamp":1790985601}⏎
```

One message split across three `recv()` calls:

```text
recv 1: {"msg_type":"MOVE","player_id":"Player_1","payl
recv 2: oad":{"row":0,"col":2},"timestamp":1790985642}⏎{"msg_type":"DISCON
recv 3: NECT","player_id":"Player_1","payload":{},"timestamp":1790985650}⏎
```

After recv 1, nothing is ready. After recv 2, the MOVE is handled. After recv 3, the DISCONNECT is handled.

## 3. Message Format

| Field | Type | Description |
|---|---|---|
| `msg_type` | string | One of the eight types below |
| `player_id` | string | The player's alias in `CONNECT`, then `"Player_1"` or `"Player_2"`. `"SERVER"` for server messages. |
| `payload` | object | Type-specific fields (`{}` if none) |
| `timestamp` | integer | Unix seconds |

Board: 3 rows of 3 cells, `board[row][col]`, each `"X"`, `"O"`, or `"-"` (empty).

## 4. Message Types

The samples below are indented to be easy to read. On the wire, each message is one line (see section 2).

| Type | Direction | Purpose |
|---|---|---|
| `CONNECT` | Client -> Server | Join the game |
| `LOBBY_WAIT` | Server -> Client | Tell the first player to wait |
| `GAME_START` | Server -> Client | Assign a role and say who moves first |
| `MOVE` | Client -> Server | Place a symbol on the board |
| `STATE_UPDATE` | Server -> both Clients | Send the board and whose turn it is |
| `ERROR` | Server -> one Client | Reject a bad message |
| `DISCONNECT` | Client -> Server | Leave on purpose (the opponent wins by forfeit) |
| `GAME_OVER` | Server -> both Clients | Send the result |

### `CONNECT` (Client -> Server)

`player_id` is the player's alias. `payload` is `{}`.

```json
{
  "msg_type": "CONNECT",
  "player_id": "Alice",
  "payload": {},
  "timestamp": 1790985600
}
```

### `LOBBY_WAIT` (Server -> Client)

- `message` (string): display text.

```json
{
  "msg_type": "LOBBY_WAIT",
  "player_id": "SERVER",
  "payload": {
    "message": "Waiting for an opponent"
  },
  "timestamp": 1790985600
}
```

### `GAME_START` (Server -> Client)

Sent once to each player.

- `assigned_id` (string): `"Player_1"` or `"Player_2"`. Use it as `player_id` from now on.
- `symbol` (string): `"X"` or `"O"`.
- `first_turn` (string): `"Player_1"` or `"Player_2"`, chosen at random.

```json
{
  "msg_type": "GAME_START",
  "player_id": "SERVER",
  "payload": {
    "assigned_id": "Player_1",
    "symbol": "X",
    "first_turn": "Player_2"
  },
  "timestamp": 1790985612
}
```

### `MOVE` (Client -> Server)

- `row` (integer): 0 to 2.
- `col` (integer): 0 to 2.

```json
{
  "msg_type": "MOVE",
  "player_id": "Player_2",
  "payload": {
    "row": 1,
    "col": 1
  },
  "timestamp": 1790985620
}
```

### `STATE_UPDATE` (Server -> both Clients)

Sent when the game starts and after every move that does not end the game.

- `board` (3x3 array)
- `next_turn` (string): `"Player_1"` or `"Player_2"`

```json
{
  "msg_type": "STATE_UPDATE",
  "player_id": "SERVER",
  "payload": {
    "board": [
      ["-", "-", "-"],
      ["-", "O", "-"],
      ["-", "-", "-"]
    ],
    "next_turn": "Player_1"
  },
  "timestamp": 1790985620
}
```

### `ERROR` (Server -> one Client)

Sent only to the player who caused the error. The game state does not change.

- `code` (string): see section 5.
- `message` (string): display text.

```json
{
  "msg_type": "ERROR",
  "player_id": "SERVER",
  "payload": {
    "code": "OUT_OF_TURN",
    "message": "It is Player_2's turn"
  },
  "timestamp": 1790985615
}
```

### `DISCONNECT` (Client -> Server)

`payload` is `{}`. The server does not reply.

```json
{
  "msg_type": "DISCONNECT",
  "player_id": "Player_1",
  "payload": {},
  "timestamp": 1790985650
}
```

### `GAME_OVER` (Server -> both Clients)

- `outcome` (string): `"WIN"`, `"DRAW"`, or `"FORFEIT"`.
- `winner` (string or null): null on a draw.
- `board` (3x3 array): final board.
- `scores` (object): wins for each player. The winner has 1, and a draw is 0 to 0.

```json
{
  "msg_type": "GAME_OVER",
  "player_id": "SERVER",
  "payload": {
    "outcome": "WIN",
    "winner": "Player_2",
    "board": [
      ["X", "X", "O"],
      ["-", "O", "-"],
      ["O", "-", "-"]
    ],
    "scores": {
      "Player_1": 0,
      "Player_2": 1
    }
  },
  "timestamp": 1790985660
}
```

Forfeit (the opponent left):

```json
{
  "msg_type": "GAME_OVER",
  "player_id": "SERVER",
  "payload": {
    "outcome": "FORFEIT",
    "winner": "Player_2",
    "board": [
      ["X", "-", "-"],
      ["-", "O", "-"],
      ["-", "-", "-"]
    ],
    "scores": {
      "Player_1": 0,
      "Player_2": 1
    }
  },
  "timestamp": 1790985650
}
```

## 5. Error Codes

| Code | When |
|---|---|
| `MALFORMED_MESSAGE` | Bad JSON, a missing or wrong-type field, an unknown `msg_type`, a wrong `player_id`, or a message that is not allowed right now |
| `OUT_OF_TURN` | `MOVE` when it is not your turn |
| `INVALID_MOVE` | `row` or `col` is not an integer from 0 to 2, or the cell is already taken |

## 6. Connection Termination

Each case below becomes one internal event, `CLIENT_DISCONNECTED`.

| What happens | What the server sees |
|---|---|
| **Graceful:** client sends `DISCONNECT`, then closes | A `DISCONNECT` message, then `recv()` returns `b""` |
| **Graceful:** client closes without `DISCONNECT` (TCP FIN) | `recv()` returns `b""` (EOF) |
| **Abrupt:** the process is killed, the host crashes, or the link is cut (TCP RST or no FIN) | `ConnectionResetError`, `ConnectionAbortedError`, or `BrokenPipeError` on `recv()` or `sendall()` |
| **Timeout** (if a socket timeout is set) | `TimeoutError` |

**Rules**

1. `recv()` returning `b""` (0 bytes) is the EOF condition and means the peer closed the connection. Stop reading, or the loop will spin at 100% CPU.
2. Wrap every `recv()` and `sendall()` and catch `ConnectionResetError`, `ConnectionAbortedError`, and `BrokenPipeError`.
3. A failed send counts as a disconnect.
4. If a player disconnects mid-game, the player who stays gets `GAME_OVER` with `FORFEIT`. If both players are gone, nothing is sent.
5. If a player disconnects in the lobby, remove them and keep waiting.
6. After `GAME_OVER`, the server closes both sockets.