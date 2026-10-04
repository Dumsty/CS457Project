# AI Prompting and Constraint Strategy

**Student:** Dustin Headington | **Course:** CS 457 | **AI tool(s):** Claude 

## 1. Strategy

The protocol blueprint and FSM spec are the contract. AI only turns them into code. It does not design anything.

1. Every session starts with the system prompt below.
2. The prompt bans new message types, fields, error codes, and states. If the contract is silent, the AI must ask.
3. I ask for one small piece at a time (serializer, framing, then parser).
4. Each function names the blueprint section it implements, and the AI lists its assumptions.
5. I reject code that uses another framing method, has no buffer, or never checks for `b""`.

## 2. System Prompt

```text
You are helping me implement a custom protocol for a 2-player Tic-Tac-Toe game over TCP
in Python 3, standard library only. The protocol is already designed. Implement it
EXACTLY. No redesigning it.

RULES
1. Do not add, remove, or rename any message type, field, error code, or state below.
2. No third-party packages.
3. Output only what I ask for.
4. End with a list of your assumptions, or say "No assumptions".

FRAMING
- One message = one compact JSON object, UTF-8, ending in "\n". No length prefix.
- recv() is a byte stream. Keep a per-connection buffer, append each chunk, split on
  b"\n", keep the remainder.
- recv() returning b"" means the peer closed. Always check it.
- Use sendall(). Catch ConnectionResetError, ConnectionAbortedError, BrokenPipeError and
  treat them as a disconnect.

FORMAT: {"msg_type": str, "player_id": str, "payload": object, "timestamp": int}
player_id is the alias in CONNECT, then "Player_1" or "Player_2". "SERVER" on server
messages.

MESSAGES (exactly 8)
CONNECT      client->server  payload {}
LOBBY_WAIT   server->client  payload {message}
GAME_START   server->client  payload {assigned_id, symbol, first_turn}
MOVE         client->server  payload {row, col}  integers 0-2
STATE_UPDATE server->both    payload {board 3x3 of "X"/"O"/"-", next_turn}
ERROR        server->one     payload {code, message}
DISCONNECT   client->server  payload {}
GAME_OVER    server->both    payload {outcome WIN|DRAW|FORFEIT, winner, board, scores}

ERROR CODES (exactly 3): MALFORMED_MESSAGE, OUT_OF_TURN, INVALID_MOVE

FSM STATES (exactly 8)
INIT, WAITING_FOR_PLAYERS, GAME_START, PLAYER_TURN, EVALUATE_MOVE, CHECK_WIN_DRAW,
GAME_OVER, CLEANUP

Reply only "Contract loaded." and wait for my task.
```

## 3. Task Prompts

**Serializer and framing**

```text
Write protocol.py with:
1. encode_message(msg_type, player_id, payload) -> bytes: compact JSON plus "\n", UTF-8.
2. class LineReader with feed(chunk: bytes) -> list[bytes]: keeps a persistent buffer
   and returns every complete line (without "\n"). Partial lines stay in the buffer.
No sockets in this file.
```

**Parser**

```text
Add decode_message(line: bytes) -> dict to protocol.py. Decode UTF-8, parse JSON, check
the 4 fields, their types, and that msg_type is one of the 8 messages. Raise
MalformedMessage on any failure. Add validate_move(payload, board): raise InvalidMove
unless row and col are integers (not bool) from 0 to 2 and the cell is "-".
```