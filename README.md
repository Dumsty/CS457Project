# Tic-Tac-Toe (3x3 grid turn-based strategy)

CS 457 Computer Networks project, Dustin Headington.

- Game will choose 1 or 2 at random for who starts first
- Player wins when they get 3 of their shape in a row, column, or diagonal.
- Tie happens when there is no more space for player input and no one has the win condition

## Files

| File | Purpose |
|---|---|
| `server.py` | Game server: CLI, worker thread per client, shared room + lock, FSM |
| `client.py` | Console client: CLI, DNS resolution, board display, keyboard input |
| `protocol.py` | Shared wire protocol: message types, newline framing, parsing, move checks |
| `tests/` | Unit tests (`test_protocol.py`) and end-to-end socket tests (`test_server.py`) |
| `execution_log.txt` | Sprint 3 gameplay log: 2 clients, 5 turns, error handling, win |

Python 3 standard library only. No packages to install.

## Running

Start the server (binds to `0.0.0.0`):

```bash
python3 server.py -p 8080
```

Start two clients, each in its own terminal:

```bash
python3 client.py -i server.headington.edu -p 8080
python3 client.py -i localhost -p 8080        # local testing
```

`-h` on either program shows help. Ports must be 1024-65535.

## Playing

- Enter your name when asked. The first player waits in the lobby until the second joins.
- On your turn, type the row and column, e.g. `1 2` (row 1, column 2). Row 0 is the top and column 0 is the left.
- Type `q` (or press Ctrl+C) to leave. Your opponent wins by forfeit.

## Testing

```bash
python3 -m unittest discover tests
```

## Protocol summary

Newline-delimited compact JSON over TCP (see the Protocol Blueprint). Every
message has `msg_type`, `player_id`, `payload` and `timestamp`. Message types:
`CONNECT`, `LOBBY_WAIT`, `GAME_START`, `MOVE`, `STATE_UPDATE`, `ERROR`,
`DISCONNECT`, `GAME_OVER`. Error codes: `MALFORMED_MESSAGE`, `OUT_OF_TURN`,
`INVALID_MOVE`.
