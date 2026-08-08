import os
import tempfile
import unittest

import pet


class ConversationMemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp_folder = tempfile.TemporaryDirectory()
        self.original_memory_file = pet.MEMORY_FILE
        pet.MEMORY_FILE = os.path.join(self.temp_folder.name, "conversations.json")

    def tearDown(self):
        pet.MEMORY_FILE = self.original_memory_file
        self.temp_folder.cleanup()

    def test_memory_is_separated_by_personality_and_can_be_cleared(self):
        history = [
            {"role": "user", "content": "Remember this."},
            {"role": "assistant", "content": "I will remember it."},
        ]
        pet.save_conversation("Muno", history)

        self.assertEqual(pet.load_conversation("Muno"), history)
        self.assertEqual(pet.load_conversation("Code Expert"), [])

        pet.clear_conversation("Muno")
        self.assertEqual(pet.load_conversation("Muno"), [])


if __name__ == "__main__":
    unittest.main()
