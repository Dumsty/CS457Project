"""End-to-end tests: run server.py and talk to it over real TCP sockets.

Run: python3 -m unittest discover tests
"""

import json
import os
import socket
import subprocess
import sys
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
sys.path.insert(0, ROOT)

import protocol as p  # noqa: E402


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


OPEN_CLIENTS = []


class Client:
    """Tiny test client that uses the real framing code."""

    def __init__(self, port, alias):
        OPEN_CLIENTS.append(self)
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=5)
        self.reader = p.LineReader()
        self.pending = []
        self.id = alias
        self.send(p.CONNECT, {})

    def send(self, msg_type, payload, player_id=None):
        self.sock.sendall(p.encode_message(msg_type, player_id or self.id,
                                           payload))

    def raw(self, data):
        self.sock.sendall(data)

    def recv(self):
        while not self.pending:
            data = self.sock.recv(4096)
            if not data:
                return None
            self.pending += self.reader.feed(data)
        return json.loads(self.pending.pop(0))

    def expect(self, msg_type):
        msg = self.recv()
        assert msg is not None, f"connection closed, wanted {msg_type}"
        assert msg["msg_type"] == msg_type, msg
        return msg

    def closed(self):
        return self.recv() is None

    def close(self):
        self.sock.close()


class ServerTests(unittest.TestCase):

    def setUp(self):
        self.port = free_port()
        self.proc = subprocess.Popen(
            [sys.executable, "server.py", "-p", str(self.port)], cwd=ROOT,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(50):
            try:
                socket.create_connection(("127.0.0.1", self.port)).close()
                break
            except OSError:
                time.sleep(0.05)

    def tearDown(self):
        while OPEN_CLIENTS:
            OPEN_CLIENTS.pop().close()
        self.proc.terminate()
        self.proc.wait()

    def start_game(self):
        """Connect two clients; return (first_mover, other)."""
        a = Client(self.port, "Alice")
        a.expect(p.LOBBY_WAIT)
        b = Client(self.port, "Bob")
        for c in (a, b):
            start = c.expect(p.GAME_START)["payload"]
            c.id = start["assigned_id"]
            c.first = start["first_turn"]
            c.expect(p.STATE_UPDATE)
        self.assertEqual((a.id, b.id), (p.PLAYER_1, p.PLAYER_2))
        return (a, b) if a.first == a.id else (b, a)

    def play(self, mover, other, row, col):
        mover.send(p.MOVE, {"row": row, "col": col})

    def test_win_and_scores(self):
        x, y = self.start_game()
        for (mover, other, r, c) in [(x, y, 0, 0), (y, x, 1, 0),
                                     (x, y, 0, 1), (y, x, 1, 1)]:
            self.play(mover, other, r, c)
            for cl in (x, y):
                cl.expect(p.STATE_UPDATE)
        self.play(x, y, 0, 2)
        for cl in (x, y):
            over = cl.expect(p.GAME_OVER)["payload"]
            self.assertEqual(over["outcome"], "WIN")
            self.assertEqual(over["winner"], x.id)
            self.assertEqual(over["scores"][x.id], 1)
            self.assertEqual(over["scores"][y.id], 0)
            self.assertTrue(cl.closed())

    def test_draw(self):
        x, y = self.start_game()
        # X O X / X O O / O X X  -> no line, nine moves.
        moves = [(0, 0), (0, 1), (0, 2), (1, 1), (1, 0), (1, 2),
                 (2, 1), (2, 0), (2, 2)]
        players = [x, y]
        for i, (r, c) in enumerate(moves):
            players[i % 2].send(p.MOVE, {"row": r, "col": c})
            if i < 8:
                for cl in (x, y):
                    cl.expect(p.STATE_UPDATE)
        for cl in (x, y):
            over = cl.expect(p.GAME_OVER)["payload"]
            self.assertEqual(over["outcome"], "DRAW")
            self.assertIsNone(over["winner"])

    def test_errors_do_not_change_state(self):
        x, y = self.start_game()
        y.send(p.MOVE, {"row": 0, "col": 0})
        self.assertEqual(y.expect(p.ERROR)["payload"]["code"], p.OUT_OF_TURN)
        x.send(p.MOVE, {"row": 5, "col": 0})
        self.assertEqual(x.expect(p.ERROR)["payload"]["code"], p.INVALID_MOVE)
        x.send(p.MOVE, {"row": 1})
        self.assertEqual(x.expect(p.ERROR)["payload"]["code"], p.INVALID_MOVE)
        x.raw(b"{not json}\n")
        self.assertEqual(x.expect(p.ERROR)["payload"]["code"],
                         p.MALFORMED_MESSAGE)
        x.send(p.MOVE, {"row": 0, "col": 0}, player_id="Player_9")
        self.assertEqual(x.expect(p.ERROR)["payload"]["code"],
                         p.MALFORMED_MESSAGE)
        x.send(p.STATE_UPDATE, {})
        self.assertEqual(x.expect(p.ERROR)["payload"]["code"],
                         p.MALFORMED_MESSAGE)
        # Still X's turn; a good move now works and taking it again fails.
        x.send(p.MOVE, {"row": 0, "col": 0})
        board = x.expect(p.STATE_UPDATE)["payload"]["board"]
        y.expect(p.STATE_UPDATE)
        self.assertNotEqual(board[0][0], "-")
        y.send(p.MOVE, {"row": 0, "col": 0})
        self.assertEqual(y.expect(p.ERROR)["payload"]["code"], p.INVALID_MOVE)

    def test_split_and_batched_frames(self):
        x, y = self.start_game()
        data = p.encode_message(p.MOVE, x.id, {"row": 1, "col": 1})
        x.raw(data[:10])
        time.sleep(0.1)
        x.raw(data[10:])
        x.expect(p.STATE_UPDATE)
        y.expect(p.STATE_UPDATE)
        # Two messages in one send: an out-of-turn move then a good one.
        y.raw(p.encode_message(p.MOVE, x.id, {"row": 0, "col": 0})
              + p.encode_message(p.MOVE, y.id, {"row": 0, "col": 0}))
        self.assertEqual(y.expect(p.ERROR)["payload"]["code"],
                         p.MALFORMED_MESSAGE)
        y.expect(p.STATE_UPDATE)

    def test_forfeit_on_disconnect_message(self):
        x, y = self.start_game()
        x.send(p.DISCONNECT, {})
        over = y.expect(p.GAME_OVER)["payload"]
        self.assertEqual((over["outcome"], over["winner"]),
                         ("FORFEIT", y.id))
        self.assertEqual(over["scores"][y.id], 1)
        self.assertTrue(y.closed())

    def test_forfeit_on_abrupt_close(self):
        x, y = self.start_game()
        y.close()
        over = x.expect(p.GAME_OVER)["payload"]
        self.assertEqual((over["outcome"], over["winner"]),
                         ("FORFEIT", x.id))

    def test_lobby_leave_then_new_game(self):
        a = Client(self.port, "Alice")
        a.expect(p.LOBBY_WAIT)
        a.close()
        time.sleep(0.2)
        x, y = self.start_game()        # server kept waiting and still works
        self.assertIsNotNone(x.id)

    def test_third_client_rejected(self):
        x, y = self.start_game()
        c = Client(self.port, "Carol")
        self.assertEqual(c.expect(p.ERROR)["payload"]["code"],
                         p.MALFORMED_MESSAGE)
        self.assertTrue(c.closed())
        x.send(p.MOVE, {"row": 2, "col": 2})   # game unaffected
        x.expect(p.STATE_UPDATE)

    def test_second_game_after_cleanup(self):
        x, y = self.start_game()
        x.send(p.DISCONNECT, {})
        y.expect(p.GAME_OVER)
        time.sleep(0.2)
        x2, y2 = self.start_game()
        x2.send(p.MOVE, {"row": 0, "col": 0})
        x2.expect(p.STATE_UPDATE)

    def test_simultaneous_moves(self):
        x, y = self.start_game()
        x.send(p.MOVE, {"row": 0, "col": 0})
        y.send(p.MOVE, {"row": 0, "col": 0})
        # Whatever order the lock picks, exactly one move lands on (0, 0).
        seen = [y.recv()["msg_type"] for _ in range(2)]
        self.assertIn(p.STATE_UPDATE, seen)


if __name__ == "__main__":
    unittest.main()
