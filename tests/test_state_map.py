import tempfile
import unittest
from pathlib import Path

from src.core import RepoMapEngine
from src.state_map import find_state_definitions


def write_file(root: str, relative_path: str, content: str) -> None:
    path = Path(root, relative_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class StateMapTests(unittest.TestCase):
    def test_rust_enum_reports_values_writers_and_readers(self) -> None:
        with tempfile.TemporaryDirectory() as project_root:
            write_file(
                project_root,
                "src/lib.rs",
                (
                    "enum Status {\n"
                    "    Ready,\n"
                    "    Done,\n"
                    "}\n\n"
                    "fn current() -> Status {\n"
                    "    Status::Ready\n"
                    "}\n\n"
                    "fn check(status: Status) {\n"
                    "    match status {\n"
                    "        Status::Ready => (),\n"
                    "        Status::Done => (),\n"
                    "    }\n"
                    "}\n"
                ),
            )

            engine = RepoMapEngine(project_root)
            engine.scan()

            definitions = find_state_definitions(engine, symbol="Status")

            self.assertEqual(len(definitions), 1)
            definition = definitions[0]
            self.assertEqual(
                {value.name for value in definition.values}, {"Ready", "Done"}
            )
            self.assertTrue(
                any(writer.name == "Status::Ready" for writer in definition.writers)
            )
            self.assertTrue(
                any("Status::Done" in reader.name for reader in definition.readers)
            )


if __name__ == "__main__":
    unittest.main()
