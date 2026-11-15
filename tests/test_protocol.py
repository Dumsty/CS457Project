"""Unit tests for protocol.py. Run: python3 -m unittest discover tests"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import protocol as p  # noqa: E402


class FramingTests(unittest.TestCase):

    def test_encode_is_compact_json_line(self):
        data = p.encode_message(p.MOVE, p.PLAYER_1, {"row": 0, "col": 2})
        self.assertTrue(data.endswith(b"\n"))
        self.assertEqual(data.count(b"\n"), 1)
        self.assertNotIn(b" ", data)
        msg = json.loads(data)
        self.assertEqual(set(msg), {"msg_type", "player_id", "payload",
                                    "timestamp"})

    def test_two_messages_in_one_recv(self):
        # Blueprint section 2, wire example 1.
        chunk = (b'{"msg_type":"CONNECT","player_id":"Alice","payload":{},'
                 b'"timestamp":1790985600}\n'
                 b'{"msg_type":"DISCONNECT","player_id":"Alice","payload":{},'
                 b'"timestamp":1790985601}\n')
        lines = p.LineReader().feed(chunk)
        self.assertEqual([p.decode_message(x)["msg_type"] for x in lines],
                         [p.CONNECT, p.DISCONNECT])

    def test_message_split_across_three_recvs(self):
        # Blueprint section 2, wire example 2.
        reader = p.LineReader()
        self.assertEqual(
            reader.feed(b'{"msg_type":"MOVE","player_id":"Player_1","payl'),
            [])
        lines = reader.feed(b'oad":{"row":0,"col":2},"timestamp":1790985642}'
                            b'\n{"msg_type":"DISCON')
        self.assertEqual(p.decode_message(lines[0])["payload"],
                         {"row": 0, "col": 2})
        lines = reader.feed(b'NECT","player_id":"Player_1","payload":{},'
                            b'"timestamp":1790985650}\n')
        self.assertEqual(p.decode_message(lines[0])["msg_type"], p.DISCONNECT)

    def test_byte_at_a_time(self):
        data = p.encode_message(p.CONNECT, "Bob", {}) * 2
        reader = p.LineReader()
        lines = []
        for i in range(len(data)):
            lines += reader.feed(data[i:i + 1])
        self.assertEqual(len(lines), 2)


class DecodeTests(unittest.TestCase):

    def good(self, **changes):
        msg = {"msg_type": "MOVE", "player_id": "Player_1",
               "payload": {"row": 1, "col": 1}, "timestamp": 1}
        msg.update(changes)
        return json.dumps(msg).encode()

    def test_valid(self):
        self.assertEqual(p.decode_message(self.good())["msg_type"], "MOVE")

    def test_rejections(self):
        bad_lines = [
            b"not json",
            b"\xff\xfe",
            b"[1, 2, 3]",
            b'{"msg_type":"MOVE"}',
            self.good(msg_type="JUMP"),
            self.good(player_id=5),
            self.good(payload=[]),
            self.good(timestamp="now"),
            self.good(timestamp=True),
        ]
        for line in bad_lines:
            with self.subTest(line=line):
                with self.assertRaises(p.MalformedMessage):
                    p.decode_message(line)


class MoveTests(unittest.TestCase):

    def test_valid_move(self):
        self.assertEqual(p.validate_move({"row": 2, "col": 0},
                                         p.new_board()), (2, 0))

    def test_invalid_moves(self):
        board = p.new_board()
        board[1][1] = "X"
        for payload in [{}, {"row": 1}, {"row": 3, "col": 0},
                        {"row": -1, "col": 0}, {"row": "1", "col": 1},
                        {"row": True, "col": 0}, {"row": 1.0, "col": 1},
                        {"row": 1, "col": 1}]:
            with self.subTest(payload=payload):
                with self.assertRaises(p.InvalidMove):
                    p.validate_move(payload, board)

    def test_has_won(self):
        b = [["X", "X", "X"], ["-"] * 3, ["-"] * 3]
        self.assertTrue(p.has_won(b, "X"))
        b = [["O", "-", "-"], ["O", "-", "-"], ["O", "-", "-"]]
        self.assertTrue(p.has_won(b, "O"))
        b = [["-", "-", "X"], ["-", "X", "-"], ["X", "-", "-"]]
        self.assertTrue(p.has_won(b, "X"))
        self.assertFalse(p.has_won(p.new_board(), "X"))


if __name__ == "__main__":
    unittest.main()
