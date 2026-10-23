import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"),
)

import unittest

import mcpassert
from support import PROJECT_ROOT, run_cli

PYPROJECT = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")

# tomllib arrived with 3.11 and the suite still runs on 3.9, so the tables that
# matter are picked out of the file by hand instead.
PROJECT_TABLE = re.compile(r"(?ms)^\[project\]\n(.*?)(?=^\[)")
SCRIPTS_TABLE = re.compile(r"(?ms)^\[project\.scripts\]\n(.*?)(?=^\[)")
VERSION_LINE = re.compile(r'(?m)^version\s*=\s*"([^"]+)"')


def pyproject_version():
    """The version the built distribution will carry."""
    body = PROJECT_TABLE.search(PYPROJECT).group(1)
    return VERSION_LINE.search(body).group(1)


class VersionTests(unittest.TestCase):
    def test_pyproject_and_package_agree_on_the_version(self):
        self.assertEqual(pyproject_version(), mcpassert.__version__)

    def test_both_commands_are_installed(self):
        body = SCRIPTS_TABLE.search(PYPROJECT).group(1)
        for name in ("mcp-test", "mcpassert"):
            self.assertIn('{0} = "mcpassert.cli:main"'.format(name), body)

    def test_the_version_flag_prints_the_package_version(self):
        code, out, err = run_cli(["--version"])
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "mcp-test {0}".format(mcpassert.__version__))


if __name__ == "__main__":
    unittest.main()
