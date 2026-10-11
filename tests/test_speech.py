import unittest

from funwithcam.speech import (
    FINISH,
    PAUSE,
    QUERY_COURSES,
    QUERY_HOMEWORK,
    RESUME,
    SPEECH_COMMAND_GUIDE,
    START,
    _course_message,
    _homework_message,
    parse_speech_command,
)


class SpeechTest(unittest.TestCase):
    def test_parses_supported_commands(self):
        cases = {
            "我 要 开始 写作业": START,
            "我想休息一下": PAUSE,
            "继续学习": RESUME,
            "作业写完了": FINISH,
            "今天有什么作业？": QUERY_HOMEWORK,
            "今天几点上课": QUERY_COURSES,
        }

        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(expected, parse_speech_command(text))

        self.assertIsNone(parse_speech_command("帮我打开窗户"))

    def test_every_documented_phrase_is_recognized(self):
        for item in SPEECH_COMMAND_GUIDE:
            for phrase in item["phrases"]:
                with self.subTest(command=item["command"], phrase=phrase):
                    self.assertEqual(
                        item["command"],
                        parse_speech_command(phrase),
                    )

    def test_builds_homework_and_course_answers(self):
        items = [
            {
                "item_type": "homework",
                "title": "数学练习册",
                "status": "pending",
                "scheduled_time": None,
            },
            {
                "item_type": "course",
                "title": "围棋课",
                "status": "pending",
                "scheduled_time": "18:30",
            },
        ]
        courses = [{"name": "英语课", "start_time": "19:00"}]

        self.assertEqual(
            "小明，今天的作业有数学练习册。",
            _homework_message("小明", items),
        )
        self.assertEqual(
            "小明，今天的课程有18点30分 围棋课、19点 英语课。",
            _course_message("小明", items, courses),
        )


if __name__ == "__main__":
    unittest.main()
