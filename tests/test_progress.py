import io
import unittest

from dup_stat.progress import NullProgressReporter, SpinnerProgressReporter


class NullProgressReporterTests(unittest.TestCase):
    def test_does_nothing_and_never_raises(self):
        reporter = NullProgressReporter()
        reporter.start(100, "test")
        reporter.advance()
        reporter.advance(5)
        reporter.finish()  # ничего из этого не должно бросать исключений


class SpinnerProgressReporterTests(unittest.TestCase):
    def _make_reporter(self):
        stream = io.StringIO()
        return SpinnerProgressReporter(stream=stream), stream

    def test_shows_percent_and_count(self):
        reporter, stream = self._make_reporter()
        reporter.start(4, "хеш")
        reporter.advance()
        reporter.advance()

        output = stream.getvalue()
        self.assertIn("(2/4)", output)
        self.assertIn(" 50%", output)
        self.assertIn("хеш", output)

    def test_reaches_100_percent(self):
        reporter, stream = self._make_reporter()
        reporter.start(3)
        reporter.advance()
        reporter.advance()
        reporter.advance()

        self.assertIn("(3/3)", stream.getvalue())
        self.assertIn("100%", stream.getvalue())

    def test_advance_never_exceeds_total(self):
        reporter, stream = self._make_reporter()
        reporter.start(2)
        reporter.advance()
        reporter.advance()
        reporter.advance()  # лишний advance — не должно уйти за 100%/2 из 2

        self.assertIn("(2/2)", stream.getvalue())
        self.assertIn("100%", stream.getvalue())

    def test_spinner_frame_changes_between_updates(self):
        reporter, stream = self._make_reporter()
        reporter.start(10)

        def last_spinner_char():
            # Строка вида "\r| хеш:  10% (1/10)" — символ колёсика сразу после "\r".
            return stream.getvalue().rsplit("\r", 1)[-1][0]

        reporter.advance()
        first_frame = last_spinner_char()
        reporter.advance()
        second_frame = last_spinner_char()

        self.assertNotEqual(first_frame, second_frame)

    def test_finish_writes_trailing_newline_when_something_was_shown(self):
        reporter, stream = self._make_reporter()
        reporter.start(1)
        reporter.advance()
        reporter.finish()

        self.assertTrue(stream.getvalue().endswith("\n"))

    def test_finish_without_start_writes_nothing(self):
        reporter, stream = self._make_reporter()
        reporter.finish()

        self.assertEqual(stream.getvalue(), "")

    def test_zero_total_does_not_divide_by_zero(self):
        reporter, stream = self._make_reporter()
        reporter.start(0, "пусто")
        reporter.advance()  # не должно бросить ZeroDivisionError
        reporter.finish()


if __name__ == "__main__":
    unittest.main()
